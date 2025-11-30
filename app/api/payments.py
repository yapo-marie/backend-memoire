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
        f"Le reçu et le tableau de bord sont accessibles via le bouton ci-dessous.\n"
        f"Merci pour votre paiement."
      )
      cta_url = receipt_url or settings.app_url or ""
      primary_label = "Voir mon reçu" if receipt_url else "Accéder au tableau de bord"
      html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
</head>
<body style="margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Roboto','Helvetica','Arial',sans-serif; background-color:#f7fafc;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7fafc; padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="640" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.07); overflow:hidden;">
          <tr>
            <td style="background:linear-gradient(135deg,#0ea5e9 0%,#0284c7 100%); padding:36px 32px 28px 32px; text-align:center;">
              <div style="background-color:#ffffff; width:58px; height:58px; border-radius:12px; margin:0 auto 12px auto; line-height:58px; box-shadow:0 4px 12px rgba(0,0,0,0.12); overflow:hidden;">
                {"<img src='"+(settings.email_logo_url or '')+"' alt='Locatus' style='width:58px;height:58px;object-fit:contain;' />" if settings.email_logo_url else "💳"}
              </div>
              <h1 style="margin:0; color:#ffffff; font-size:24px; font-weight:800; letter-spacing:-0.5px;">Paiement confirmé</h1>
              <p style="margin:8px 0 0 0; color:#e0f2fe; font-size:14px;">Merci pour votre règlement.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:36px 32px; color:#1a202c;">
              <h2 style="margin:0 0 20px 0; color:#1a202c; font-size:20px; font-weight:700;">Bonjour {tenant_name},</h2>
              <p style="margin:0 0 20px 0; color:#4a5568; font-size:15px; line-height:1.7;">Nous avons bien reçu votre paiement pour <strong>{property_name}</strong>.</p>
              <div style="background:linear-gradient(135deg,#eef2ff 0%,#e0e7ff 100%); border-left:4px solid #4f46e5; padding:20px; border-radius:10px; margin-bottom:26px; text-align:center;">
                <div style="color:#6366f1; font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">Montant réglé</div>
                <div style="color:#1a202c; font-size:30px; font-weight:800; letter-spacing:-0.5px;">{amount}</div>
              </div>
              <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px; border:1px solid #e2e8f0; border-radius:10px; background:#f8fafc; font-size:14px; color:#0f172a;">
                <tr><td style="padding:12px 14px; color:#4a5568; width:45%;">Période</td><td style="padding:12px 14px; font-weight:700;">{payment_months} mois</td></tr>
                <tr><td style="padding:12px 14px; color:#4a5568;">Échéance</td><td style="padding:12px 14px; font-weight:700;">{due_date}</td></tr>
                <tr><td style="padding:12px 14px; color:#4a5568;">Date de paiement</td><td style="padding:12px 14px; font-weight:700;">{datetime.utcnow().date().isoformat()}</td></tr>
              </table>
              {f'<div style="text-align:center; margin:0 0 26px 0;"><a href="{cta_url}" style="display:inline-block; padding:16px 38px; background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%); color:#ffffff; text-decoration:none; border-radius:8px; font-size:16px; font-weight:700; box-shadow:0 10px 25px rgba(79,70,229,0.30); letter-spacing:0.3px;">🔒 {primary_label}</a></div>' if cta_url else ''}
              <div style="background-color:#f7fafc; border:1px solid #e2e8f0; padding:18px; border-radius:10px; margin-bottom:24px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  <tr>
                    <td width="34" style="vertical-align:top; padding-right:12px; font-size:22px;">🛡️</td>
                    <td style="vertical-align:top;">
                      <h3 style="margin:0 0 6px 0; color:#2d3748; font-size:15px; font-weight:700;">Paiement 100% sécurisé</h3>
                      <p style="margin:0; color:#718096; font-size:13px; line-height:1.6;">Ce paiement est traité par Stripe. Vos informations sont protégées et cryptées.</p>
                    </td>
                  </tr>
                </table>
              </div>
              <p style="margin:0; color:#718096; font-size:13px; line-height:1.6;">Si vous rencontrez une difficulté ou n’êtes pas à l’origine de ce paiement, contactez-nous.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px; text-align:center; background-color:#f8fafc; border-top:1px solid #e2e8f0;">
              <p style="margin:0 0 6px 0; color:#4a5568; font-size:13px;">Email envoyé par <strong style="color:#2d3748;">Locatus</strong></p>
              <p style="margin:0 0 6px 0; color:#a0aec0; font-size:12px;">Support : <a href="mailto:{settings.mail_reply_to or settings.smtp_user or ''}" style="color:#4f46e5; text-decoration:none;">{settings.mail_reply_to or settings.smtp_user or ''}</a></p>
              <p style="margin:0; color:#cbd5e0; font-size:11px;">© 2024 Locatus. Tous droits réservés.</p>
            </td>
          </tr>
        </table>
        <table width="640" cellpadding="0" cellspacing="0" style="margin-top:14px;">
          <tr>
            <td style="text-align:center; padding:0 16px;">
              <p style="margin:0; color:#a0aec0; font-size:12px; line-height:1.5;">Ce message contient des informations confidentielles destinées au destinataire.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
      try:
        await send_mail(settings, to=tenant_email, subject=subject, text=body, html=html)
      except Exception as exc:
        print(f"[Webhook] Erreur email: {exc}")
  return Response(content='{"received": true}', media_type="application/json")
