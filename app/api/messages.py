from datetime import datetime
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
import re

from ..core.email_utils import send_mail
from ..core.firebase import firebase_request
from ..core.settings import Settings, get_settings
from ..services.reminder_service import fetch_tenants_and_properties, render_template

router = APIRouter(prefix="/api/messages", tags=["messages"])


def get_client(request: Request) -> httpx.AsyncClient:
  return request.app.state.http_client


def build_html_email(body: str, recipient_name: str | None, cta_url: str | None) -> str:
  import re

  # Supprime les liens bruts du corps (on les remplace par le CTA)
  cleaned_body = re.sub(r"https?://\\S+", "", body or "").strip()
  safe_body = cleaned_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
  safe_body = safe_body.replace("\n", "<br>")
  cta_block = (
    f'<div style="text-align:center; margin:24px 0;"><a href="{cta_url}" '
    'style="display:inline-block;padding:16px 28px;background:linear-gradient(135deg,#0ea5e9 0%,#0284c7 100%);'
    'color:#fff;text-decoration:none;border-radius:10px;font-weight:700;'
    'box-shadow:0 10px 25px rgba(14,165,233,0.28);">🔒 Procéder au paiement sécurisé</a></div>'
  ) if cta_url else ""
  name_line = f"Bonjour {recipient_name}," if recipient_name else ""
  return f"""
<!DOCTYPE html>
<html lang="fr">
  <head><meta charset="UTF-8" /></head>
  <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Roboto','Helvetica','Arial',sans-serif;background:#f7fafc;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f7fafc;padding:32px 16px;">
      <tr>
        <td align="center">
          <table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.07);overflow:hidden;">
            <tr>
              <td style="background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%);padding:28px 28px 20px 28px;text-align:center;color:#fff;">
                <div style="background:#fff;width:56px;height:56px;border-radius:12px;margin:0 auto 12px auto;line-height:56px;font-size:26px;box-shadow:0 4px 12px rgba(0,0,0,0.12);">📍</div>
                <h1 style="margin:0;font-size:22px;font-weight:800;letter-spacing:-0.4px;">Locatus</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 28px 32px 28px;color:#1a202c;">
                <p style="margin:0 0 18px 0;font-size:15px;">{name_line}</p>
                <p style="margin:0 0 20px 0;color:#4a5568;font-size:15px;line-height:1.7;">{safe_body}</p>
                {cta_block}
                <div style="background:#f8fafc;border:1px solid #e2e8f0;padding:16px;border-radius:10px;margin-top:12px;">
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td width="32" style="vertical-align:top;padding-right:10px;font-size:22px;">🛡️</td>
                      <td style="vertical-align:top;">
                        <p style="margin:0 0 4px 0;color:#2d3748;font-size:14px;font-weight:700;">Lien sécurisé</p>
                        <p style="margin:0;color:#718096;font-size:13px;line-height:1.6;">Ce lien est protégé. Si vous n’êtes pas à l’origine de cette demande, ignorez ce message.</p>
                      </td>
                    </tr>
                  </table>
                </div>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px;text-align:center;background:#f8fafc;border-top:1px solid #e2e8f0;color:#a0aec0;font-size:12px;">
                <p style="margin:0 0 6px 0;color:#4a5568;font-size:13px;">Email envoyé par <strong style="color:#2d3748;">Locatus</strong></p>
                <p style="margin:0;color:#cbd5e0;font-size:11px;">© 2024 Locatus</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
  """
@router.get("")
async def list_messages(
  request: Request,
  ownerId: str | None = None,
  tenantId: str | None = None,
  limit: int = 50,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  _, snapshot = await firebase_request(client, settings, "messages")
  if not isinstance(snapshot, dict):
    return []
  messages = []
  for message_id, value in snapshot.items():
    msg = {"id": message_id, **(value or {})}
    messages.append(msg)
  if ownerId:
    messages = [m for m in messages if m.get("ownerId") == ownerId]
  if tenantId:
    messages = [m for m in messages if m.get("tenantId") == tenantId]
  messages.sort(key=lambda m: m.get("sentAt") or "", reverse=True)
  capped = max(1, min(100, limit))
  return messages[:capped]


@router.post("/send")
async def send_messages(
  request: Request,
  payload: dict,
  settings: Settings = Depends(get_settings),
):
  if not settings.mailer_configured:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SMTP non configuré.")
  client = get_client(request)
  subject = (payload.get("subject") or "").strip()
  body = (payload.get("body") or "").strip()
  tenant_ids = payload.get("tenantIds") or []
  owner_id = (payload.get("ownerId") or settings.default_owner_id).strip()
  if not subject or not body:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sujet et message requis.")
  if not tenant_ids:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Veuillez sélectionner au moins un locataire.")
  tenants, properties = await fetch_tenants_and_properties(client, settings)
  tenant_set = set(tenant_ids)
  recipients = [t for t in tenants if t.get("id") in tenant_set and t.get("ownerId") == owner_id and t.get("email")]
  if not recipients:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun locataire correspondant pour cet envoi.")
  results = []
  sent_count = 0
  for tenant in recipients:
    prop = properties.get(tenant.get("propertyId") or "")
    first_name = (tenant.get("name") or "").split(" ")[0]
    context = {
      "nom": tenant.get("name") or "",
      "name": tenant.get("name") or "",
      "prenom": first_name,
      "first": first_name,
      "email": tenant.get("email") or "",
      "logement": prop.get("name") if prop else "",
      "property": prop.get("name") if prop else "",
      "adresse": prop.get("address") if prop else "",
      "address": prop.get("address") if prop else "",
    }
    personalized_subject = render_template(subject, context)
    personalized_body = render_template(body, context)
    try:
      cta_url = None
      match = re.search(r"https?://\S+", personalized_body)
      if match:
        cta_url = match.group(0)
      html_body = build_html_email(personalized_body, tenant.get("name"), cta_url)
      await send_mail(settings, to=tenant["email"], subject=personalized_subject, text=personalized_body, html=html_body)
      sent_count += 1
      await firebase_request(
        client,
        settings,
        "messages",
        method="POST",
        body={
          "tenantId": tenant["id"],
          "tenantName": tenant.get("name"),
          "channel": "email",
          "subject": personalized_subject,
          "body": personalized_body,
          "ownerId": owner_id,
          "sentAt": payload.get("sentAt") or datetime.utcnow().isoformat(),
        },
      )
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
  return {"total": len(results), "sent": sent_count, "failed": len(results) - sent_count, "results": results}


@router.post("")
async def log_message(
  request: Request,
  payload: dict,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  tenant_id = payload.get("tenantId")
  tenant_name = payload.get("tenantName")
  subject = payload.get("subject")
  body = payload.get("body")
  if not tenant_id or not tenant_name or not subject or not body:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST, detail="tenantId, tenantName, subject et body sont requis."
    )
  message_data = {
    "tenantId": tenant_id,
    "tenantName": tenant_name,
    "channel": payload.get("channel") or "email",
    "subject": subject,
    "body": body,
    "ownerId": payload.get("ownerId") or settings.default_owner_id,
    "sentAt": payload.get("sentAt") or datetime.utcnow().isoformat(),
  }
  _, snapshot = await firebase_request(client, settings, "messages", method="POST", body=message_data)
  msg_id = snapshot.get("name") if isinstance(snapshot, dict) else str(uuid4())
  return {"id": msg_id, **message_data}
