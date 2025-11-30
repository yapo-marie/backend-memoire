from datetime import date
from typing import Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..core.settings import Settings
from ..services.reminder_service import emit_monthly_reminder
from ..services.late_payment_service import check_and_update_late_payments


def create_scheduler(settings: Settings, http_client: httpx.AsyncClient) -> Optional[AsyncIOScheduler]:
  if not settings.reminder_active:
    return None
  scheduler = AsyncIOScheduler(timezone=settings.reminder_cron_tz)

  async def reminder_job():
    today = date.today()
    next_month = today.month % 12 + 1
    year = today.year + (1 if today.month == 12 else 0)
    first_next_month = date(year, next_month, 1)
    last_day = first_next_month - date.resolution
    target_day = last_day - date.resolution * 7
    if today != target_day:
      return
    try:
      await emit_monthly_reminder(last_day, http_client, settings)
    except Exception as exc:  # pragma: no cover - logged only
      print(f"[Reminders] Erreur cron: {exc}")

  async def late_payment_job():
    """Check and update late payment statuses daily."""
    try:
      result = await check_and_update_late_payments(http_client, settings)
      print(f"[Late Payments] Checked {result['checked']} tenants, updated {result['updated']}")
    except Exception as exc:  # pragma: no cover - logged only
      print(f"[Late Payments] Erreur cron: {exc}")

  # Run reminder job daily at 9:00 AM
  scheduler.add_job(reminder_job, CronTrigger(hour=9, minute=0))
  
  # Run late payment check daily at 1:00 AM
  scheduler.add_job(late_payment_job, CronTrigger(hour=1, minute=0))
  
  return scheduler
