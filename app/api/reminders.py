from datetime import date, datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.email_utils import send_mail
from ..core.firebase import firebase_request
from ..core.settings import Settings, get_settings
from ..models.reminder import ReminderHistoryItem, ReminderSendRequest, UpcomingReminder, UpcomingResponse
from ..services.reminder_service import compute_next_due_date, default_template, fetch_tenants_and_properties, format_currency, render_template

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def get_client(request: Request) -> httpx.AsyncClient:
  return request.app.state.http_client


@router.get("/upcoming", response_model=UpcomingResponse)
async def upcoming_reminders(
  request: Request,
  ownerId: str | None = None,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  tenants, properties = await fetch_tenants_and_properties(client, settings)
  filtered = [t for t in tenants if not ownerId or t.get("ownerId") == ownerId]
  today = date.today()
  default_due = date(today.year, today.month + 1, 1) - date.resolution
  reminders: list[UpcomingReminder] = []
  for tenant in filtered:
    if not tenant.get("email"):
      continue
    prop = properties.get(tenant.get("propertyId") or "")
    cycle = int(tenant.get("paymentMonths") or 1)
    computed = compute_next_due_date(tenant.get("entryDate"), cycle) or default_due
    amount_value = (prop.get("rent") if prop else 0) * cycle
    reminders.append(
      UpcomingReminder(
        tenantId=tenant["id"],
        tenantName=tenant["name"],
        tenantEmail=tenant["email"],
        propertyName=prop.get("name") if prop else "Logement",
        amount=amount_value,
        amountFormatted=format_currency(amount_value),
        paymentMonths=cycle,
        dueDate=computed.isoformat(),
      )
    )
  ordered = sorted(reminders, key=lambda r: r.dueDate)
  next_due = ordered[0].dueDate if ordered else default_due.isoformat()
  summary_date = datetime.fromisoformat(next_due).date() if isinstance(next_due, str) else default_due
  reminder_date = (summary_date - date.resolution * 7).isoformat()
  return UpcomingResponse(
    reminderDate=reminder_date,
    dueDate=summary_date.isoformat(),
    totalRecipients=len(reminders),
    reminders=ordered,
  )


@router.post("/send")
async def send_reminders(
  payload: ReminderSendRequest,
  request: Request,
  settings: Settings = Depends(get_settings),
):
  if not settings.reminder_active:
    raise HTTPException(
      status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
      detail="Le service de relance est désactivé (configuration SMTP manquante).",
    )
  client = get_client(request)
  normalized_owner = payload.ownerId or settings.default_owner_id
  tenants, properties = await fetch_tenants_and_properties(client, settings)
  eligible = [t for t in tenants if t.get("ownerId") == normalized_owner and t.get("email")]
  if payload.tenantIds:
    wanted = set(payload.tenantIds)
    eligible = [t for t in eligible if t.get("id") in wanted]
  if not eligible:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun locataire correspondant pour cette relance.")
  due_date_obj = None
  if payload.dueDate:
    try:
      due_date_obj = datetime.fromisoformat(payload.dueDate).date()
    except Exception:
      raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Date d'échéance invalide.")
  else:
    today = date.today()
    due_date_obj = date(today.year, today.month + 1, 1) - date.resolution
  due_date_text = due_date_obj.strftime("%d/%m/%Y")
  template = payload.message.strip() if payload.message else default_template()
  pay_url = f"{settings.app_url.rstrip('/')}/dashbord/paiements" if settings.app_url else None
  results = []
  sent_count = 0
  for tenant in eligible:
    prop = properties.get(tenant.get("propertyId") or "")
    amount_value = (prop.get("rent") if prop else 0) * (tenant.get("paymentMonths") or 1)
    context = {
      "locataire": tenant.get("name") or "",
      "montant": format_currency(amount_value),
      "date": due_date_text,
      "logement": prop.get("name") if prop else "votre logement",
      "prenom": (tenant.get("name") or "").split(" ")[0],
    }
    message_body = render_template(template, context)
    html_body = f"""
    <div style="font-family:Arial, sans-serif; color:#0f172a; line-height:1.6;">
      <h2 style="color:#0ea5e9; margin-bottom:8px;">Rappel de paiement</h2>
      <p>Bonjour {context['locataire'] or 'Locataire'},</p>
      <p>{message_body.replace(chr(10), '<br>')}</p>
      <p style="font-size:16px; font-weight:600; color:#0b5ed7;">Montant : {context['montant']}</p>
      {f'<p><a href=\"{pay_url}\" style=\"display:inline-block;padding:12px 20px;background:#0ea5e9;color:#fff;text-decoration:none;border-radius:999px;font-weight:700;\">Payer en ligne</a></p>' if pay_url else ''}
      <p style="font-size:12px; color:#6b7280;">Si vous avez déjà payé, ignorez ce message.</p>
    </div>
    """
    try:
      await send_mail(
        settings,
        to=tenant["email"],
        subject=f"Rappel de paiement - échéance du {due_date_text}",
        text=f"{message_body}\n\nMontant dû : {format_currency(amount_value)}\n{settings.app_url or ''}",
        html=html_body,
      )
      sent_count += 1
      results.append({"tenantId": tenant["id"], "tenantEmail": tenant["email"], "status": "sent"})
    except Exception as exc:
      results.append(
        {
          "tenantId": tenant["id"],
          "tenantEmail": tenant["email"],
          "status": "failed",
          "message": str(exc),
        }
      )
  log_entry = {
    "ownerId": normalized_owner,
    "total": len(results),
    "sent": sent_count,
    "failed": len(results) - sent_count,
    "dueDate": due_date_obj.isoformat(),
    "templatePreview": template[:280],
    "createdAt": datetime.utcnow().isoformat(),
    "results": results[:25],
  }
  try:
    _, snapshot = await firebase_request(client, settings, "remindersLogs", method="POST", body=log_entry)
    log_entry["id"] = snapshot.get("name") if isinstance(snapshot, dict) else str(uuid4())
  except Exception:
    log_entry["id"] = str(uuid4())
  return {
    "total": log_entry["total"],
    "sent": log_entry["sent"],
    "failed": log_entry["failed"],
    "dueDate": log_entry["dueDate"],
    "results": results,
    "logId": log_entry.get("id"),
  }


@router.get("/history", response_model=list[ReminderHistoryItem])
async def reminder_history(
  request: Request,
  ownerId: str | None = None,
  limit: int = 20,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  _, snapshot = await firebase_request(client, settings, "remindersLogs")
  if not isinstance(snapshot, dict):
    return []
  entries = []
  for log_id, value in snapshot.items():
    entry = {
      "id": log_id,
      "ownerId": value.get("ownerId") or settings.default_owner_id,
      "total": int(value.get("total") or 0),
      "sent": int(value.get("sent") or 0),
      "failed": int(value.get("failed") or 0),
      "dueDate": value.get("dueDate"),
      "templatePreview": value.get("templatePreview"),
      "createdAt": value.get("createdAt"),
    }
    entries.append(entry)
  if ownerId:
    entries = [entry for entry in entries if entry["ownerId"] == ownerId]
  entries.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
  return [ReminderHistoryItem.model_validate(item).model_dump() for item in entries[: max(1, min(50, limit))]]
