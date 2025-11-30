from email.message import EmailMessage
from typing import Iterable, Optional

import aiosmtplib
from fastapi import HTTPException, status

from .settings import Settings


def format_html_from_text(text: str) -> str:
  return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;").replace("\n", "<br>")


async def send_mail(
  settings: Settings,
  *,
  to: Iterable[str] | str,
  subject: str,
  text: str,
  html: Optional[str] = None,
  reply_to: Optional[str] = None,
):
  if not settings.mailer_configured:
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SMTP non configuré.")
  recipients = list(to) if isinstance(to, (list, tuple, set)) else [to]
  if not recipients:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Destinataire manquant.")
  message = EmailMessage()
  message["From"] = settings.mail_from or settings.smtp_user or "no-reply@locatus.local"
  message["To"] = ", ".join(recipients)
  message["Subject"] = subject
  if reply_to or settings.mail_reply_to:
    message["Reply-To"] = reply_to or settings.mail_reply_to
  message.set_content(text)
  message.add_alternative(html or format_html_from_text(text), subtype="html")

  use_tls = settings.smtp_secure if settings.smtp_secure is not None else settings.smtp_port == 465
  start_tls = not use_tls
  smtp = aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port, use_tls=use_tls, timeout=15)
  await smtp.connect()
  if start_tls and not use_tls:
    try:
      await smtp.starttls()
    except Exception as exc:  # catch wide to ignore TLS-already-active cases
      if "already using tls" not in str(exc).lower():
        raise
  if settings.smtp_user and settings.smtp_password:
    await smtp.login(settings.smtp_user, settings.smtp_password)
  try:
    await smtp.send_message(message)
  finally:
    await smtp.quit()
