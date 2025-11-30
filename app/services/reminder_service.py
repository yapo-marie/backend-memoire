from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx

from ..core.email_utils import send_mail
from ..core.firebase import firebase_request
from ..core.settings import Settings

DISPLAY_CURRENCY = "F CFA"
STRIPE_CURRENCY = "xof"


def to_clean_string(value: Optional[str]) -> Optional[str]:
  if value is None:
    return None
  stripped = str(value).strip()
  return stripped or None


def normalize_entry_date(value: Optional[str]) -> Optional[str]:
  value = to_clean_string(value)
  if not value:
    return None
  try:
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
      return value
    if len(value) == 10 and value[2] == "/" and value[5] == "/":
      day, month, year = value.split("/")
      return f"{year}-{month}-{day}"
    parsed = datetime.fromisoformat(value)
    return parsed.date().isoformat()
  except Exception:
    return None


def add_months_safe(dt: date, months: int) -> date:
  month = dt.month - 1 + months
  year = dt.year + month // 12
  month = month % 12 + 1
  day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
  return date(year, month, day)


def compute_next_due_date(entry_date_str: Optional[str], cycle_months: int) -> Optional[date]:
  if not entry_date_str:
    return None
  normalized = normalize_entry_date(entry_date_str)
  if not normalized:
    return None
  try:
    start = datetime.fromisoformat(normalized).date()
  except ValueError:
    return None
  cycle = max(1, min(12, int(cycle_months or 1)))
  due = add_months_safe(start, cycle)
  today = date.today()
  while due < today:
    due = add_months_safe(due, cycle)
  return due


def format_currency(amount: float) -> str:
  return f"{amount:,.0f} {DISPLAY_CURRENCY}".replace(",", " ")


async def fetch_tenants_and_properties(
  client: httpx.AsyncClient, settings: Settings
) -> Tuple[List[Dict], Dict[str, Dict]]:
  _, tenants_snapshot = await firebase_request(client, settings, "locataires")
  _, properties_snapshot = await firebase_request(client, settings, "proprietes")
  tenants = []
  if isinstance(tenants_snapshot, dict):
    for tenant_id, raw in tenants_snapshot.items():
      record = raw or {}
      tenants.append(
        {
          "id": tenant_id,
          "name": record.get("name"),
          "email": (record.get("email") or "").lower(),
          "phone": record.get("phone"),
          "status": record.get("status") or "pending",
          "propertyId": record.get("propertyId"),
          "ownerId": record.get("ownerId") or settings.default_owner_id,
          "note": record.get("note"),
          "entryDate": record.get("entryDate"),
          "paymentMonths": max(1, min(12, int(record.get("paymentMonths") or 1))),
        }
      )
  properties = {}
  if isinstance(properties_snapshot, dict):
    for prop_id, raw in properties_snapshot.items():
      record = raw or {}
      properties[prop_id] = {
        "id": prop_id,
        "name": record.get("name"),
        "address": record.get("address"),
        "status": record.get("status") or "vacant",
        "type": record.get("type"),
        "bedrooms": int(record.get("bedrooms") or 0),
        "rent": float(record.get("rent") or 0),
        "charges": float(record.get("charges") or 0),
        "ownerId": record.get("ownerId") or settings.default_owner_id,
      }
  return tenants, properties


def render_template(template: str, context: Dict[str, str]) -> str:
  import re

  def _replace(match):
    token = match.group(1).strip().lower()
    return context.get(token, "")

  return re.sub(r"{{\s*(\w+)\s*}}", _replace, template)


async def emit_monthly_reminder(last_day: date, client: httpx.AsyncClient, settings: Settings):
  tenants, property_dict = await fetch_tenants_and_properties(client, settings)
  if not tenants:
    return
  target_month = last_day.month
  target_year = last_day.year
  for tenant in tenants:
    if not tenant.get("email"):
      continue
    cycle = max(1, min(12, int(tenant.get("paymentMonths") or 1)))
    computed_due = compute_next_due_date(tenant.get("entryDate"), cycle)
    if not computed_due or computed_due.month != target_month or computed_due.year != target_year:
      continue
    property_obj = property_dict.get(tenant.get("propertyId") or "")
    due_text = computed_due.strftime("%d/%m/%Y")
    amount_value = (property_obj.get("rent", 0) if property_obj else 0) * cycle
    pay_url = f"{settings.app_url.rstrip('/')}/dashbord/paiements" if settings.app_url else None
    body = (
      f"Ceci est un rappel automatique : votre prochain loyer doit être réglé avant le {due_text}.\n\n"
      f"Montant dû : {format_currency(amount_value)}\n"
      f"Merci d'anticiper le paiement afin d'éviter toute pénalité."
    )
    html = f"""
    <div style="font-family:Arial, sans-serif; color:#0f172a; line-height:1.6;">
      <h2 style="color:#0ea5e9; margin-bottom:8px;">Rappel de paiement</h2>
      <p>Bonjour {tenant.get('name') or 'Locataire'},</p>
      <p>Votre loyer est dû avant le <strong>{due_text}</strong> pour {property_obj.get('name') if property_obj else 'votre logement'}.</p>
      <p style="font-size:16px; font-weight:600; color:#0b5ed7;">Montant : {format_currency(amount_value)}</p>
      {f'<p><a href="{pay_url}" style="display:inline-block;padding:12px 20px;background:#0ea5e9;color:#fff;text-decoration:none;border-radius:999px;font-weight:700;">Payer en ligne</a></p>' if pay_url else ''}
      <p style="font-size:12px; color:#6b7280;">Si vous avez déjà payé, ignorez ce message.</p>
    </div>
    """
    await send_mail(
      settings,
      to=tenant["email"],
      subject=f"Rappel de paiement - échéance du {due_text}",
      text=body,
      html=html,
    )


def default_template() -> str:
  return (
    "Bonjour {{locataire}},\n\nCeci est un rappel concernant votre loyer de {{montant}} pour {{logement}}, "
    "dû avant le {{date}}.\nMerci de procéder au paiement dès que possible.\n"
  )
