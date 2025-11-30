from datetime import datetime

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..core.email_utils import send_mail
from ..core.settings import Settings, get_settings
from ..core.stripe_utils import compute_unit_amount
from ..models.payment import CheckoutRequest, PaymentHistoryItem, PaymentHistoryQuery
from ..services.payment_service import STRIPE_CURRENCY, STRIPE_MAX_AMOUNT, build_metadata, create_checkout_session, list_checkout_sessions

router = APIRouter(prefix="/api/payments", tags=["payments"])


def get_stripe(request: Request):
  stripe = getattr(request.app.state, "stripe", None)
  if not stripe:
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Stripe n'est pas configuré.")
  return stripe


@router.post("/checkout", response_model=dict)
async def create_checkout(
  payload: CheckoutRequest,
  request: Request,
  settings: Settings = Depends(get_settings),
):
  stripe = get_stripe(request)
  amount = float(payload.amount)
  if amount <= 0:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Montant invalide.")
  if amount > STRIPE_MAX_AMOUNT:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Le montant total doit être inférieur ou égal à 655 959 993 F CFA pour Stripe.",
    )
  months = max(1, min(12, int(payload.paymentMonths or 1)))
  metadata = build_metadata(
    {
      "tenantName": payload.tenantName,
      "tenantEmail": payload.tenantEmail.lower(),
      "tenantId": payload.tenantId,
      "ownerId": payload.ownerId or settings.default_owner_id,
      "propertyName": payload.propertyName,
      "propertyId": payload.propertyId,
      "dueDate": payload.dueDate,
      "paymentMonths": months,
    }
  )
  session = await create_checkout_session(
    stripe,
    {
      "mode": "payment",
      "payment_method_types": ["card"],
      "customer_email": payload.tenantEmail.lower(),
      "client_reference_id": payload.tenantId,
      "metadata": metadata,
      "payment_intent_data": {"metadata": metadata},
      "line_items": [
        {
          "price_data": {
            "currency": STRIPE_CURRENCY,
            "product_data": {
              "name": f"Loyer {payload.propertyName}" if payload.propertyName else "Paiement loyer",
              "description": f"Locataire: {payload.tenantName}" if payload.tenantName else None,
            },
            "unit_amount": compute_unit_amount(amount, STRIPE_CURRENCY),
          },
          "quantity": 1,
        }
      ],
      "success_url": payload.successUrl or f"{settings.client_origin.rstrip('/')}/dashbord/paiements?status=success",
      "cancel_url": payload.cancelUrl or f"{settings.client_origin.rstrip('/')}/dashbord/paiements?status=cancel",
    },
  )
  return {"sessionId": session.get("id"), "url": session.get("url")}


@router.get("/history", response_model=list[PaymentHistoryItem])
async def payment_history(
  request: Request,
  query: PaymentHistoryQuery = Depends(),
):
  stripe = get_stripe(request)
  sessions = await list_checkout_sessions(stripe)
  filtered = []
  for session in sessions.get("data", []):
    metadata = session.get("metadata") or {}
    if query.ownerId and metadata.get("ownerId") != query.ownerId:
      continue
    if query.tenantId and metadata.get("tenantId") != query.tenantId:
      continue
    if query.tenantEmail and metadata.get("tenantEmail", "").lower() != query.tenantEmail.lower():
      continue
    payment_intent = session.get("payment_intent") if isinstance(session.get("payment_intent"), dict) else None
    charge = (payment_intent or {}).get("charges", {}).get("data", [None])[0] or {}
    receipt_url = charge.get("receipt_url")
    paid_at = (
      datetime.utcfromtimestamp(charge.get("created")).isoformat()
      if session.get("payment_status") == "paid" and charge.get("created")
      else None
    )
    filtered.append(
      PaymentHistoryItem(
        id=session.get("id"),
        amount=(session.get("amount_total") or 0) / 100 if session.get("amount_total") else None,
        currency=session.get("currency") or STRIPE_CURRENCY,
        paymentStatus=session.get("payment_status"),
        sessionStatus=session.get("status"),
        tenantName=metadata.get("tenantName"),
        tenantEmail=metadata.get("tenantEmail") or (session.get("customer_details") or {}).get("email"),
        tenantId=metadata.get("tenantId"),
        propertyName=metadata.get("propertyName"),
        propertyId=metadata.get("propertyId"),
        ownerId=metadata.get("ownerId"),
        dueDate=metadata.get("dueDate"),
        paymentMonths=int(metadata.get("paymentMonths")) if metadata.get("paymentMonths") else None,
        createdAt=datetime.utcfromtimestamp(session.get("created")).isoformat(),
        paidAt=paid_at,
        receiptUrl=receipt_url,
      ).model_dump()
    )
  return filtered[: query.limit]


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
  request: Request,
  settings: Settings = Depends(get_settings),
):
  stripe = get_stripe(request)
  if not settings.stripe_webhook_secret:
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Configuration webhook manquante.")
  payload = await request.body()
  sig_header = request.headers.get("stripe-signature")
  if not sig_header:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature Stripe manquante.")
  try:
    event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
  except Exception as exc:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Signature webhook invalide.") from exc
  if event.get("type") == "checkout.session.completed":
    session = event["data"]["object"]
    metadata = session.get("metadata") or {}
    tenant_email = metadata.get("tenantEmail") or (session.get("customer_details") or {}).get("email")
    if tenant_email:
      amount = (session.get("amount_total") or 0) / 100
      payment_intent_id = session.get("payment_intent")
      receipt_url = None
      if payment_intent_id and isinstance(payment_intent_id, str):
        try:
          payment_intent = await anyio.to_thread.run_sync(stripe.PaymentIntent.retrieve, payment_intent_id)
          charges = payment_intent.get("charges", {}).get("data", [])
          if charges:
            receipt_url = charges[0].get("receipt_url")
        except Exception:
          receipt_url = None
      tenant_name = metadata.get("tenantName") or "Locataire"
      property_name = metadata.get("propertyName") or "votre logement"
      due_date = metadata.get("dueDate") or datetime.utcnow().date().isoformat()
      payment_months = int(metadata.get("paymentMonths") or 1)
      subject = f"Facture - Paiement reçu pour {property_name}"
      body = (
        f"Bonjour {tenant_name},\n\n"
        f"Nous vous confirmons la réception de votre paiement de {amount} pour {property_name}.\n\n"
        f"Détails du paiement :\n"
        f"- Montant : {amount}\n"
        f"- Période : {payment_months} mois\n"
        f"- Date d'échéance : {due_date}\n"
        f"- Date de paiement : {datetime.utcnow().date().isoformat()}\n\n"
        f"{f'Votre reçu est disponible ici : {receipt_url}\\n\\n' if receipt_url else ''}"
        f"Merci pour votre paiement.\n\n"
        f"{f'Accéder au tableau de bord : {settings.app_url}' if settings.app_url else ''}"
      )
      try:
        await send_mail(settings, to=tenant_email, subject=subject, text=body)
      except Exception as exc:
        print(f"[Webhook] Erreur email: {exc}")
  return Response(content='{"received": true}', media_type="application/json")
