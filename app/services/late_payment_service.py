from datetime import date, datetime
from typing import List, Dict

import httpx

from ..core.firebase import firebase_request
from ..core.settings import Settings


def normalize_entry_date(value: str | None) -> date | None:
    """Normalize entry date to date object."""
    if not value:
        return None
    try:
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return datetime.fromisoformat(value).date()
        if len(value) == 10 and value[2] == "/" and value[5] == "/":
            day, month, year = value.split("/")
            return date(int(year), int(month), int(day))
        parsed = datetime.fromisoformat(value)
        return parsed.date()
    except Exception:
        return None


def add_months_safe(dt: date, months: int) -> date:
    """Add months to a date safely."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def compute_next_due_date(entry_date: date, cycle_months: int) -> date:
    """Calculate the next payment due date."""
    today = date.today()
    due = add_months_safe(entry_date, cycle_months)
    
    while due < today:
        due = add_months_safe(due, cycle_months)
    
    return due


async def check_and_update_late_payments(client: httpx.AsyncClient, settings: Settings) -> Dict[str, int]:
    """
    Check all tenants and update their status to 'late' if payment is overdue.
    Returns a dict with counts of updated tenants.
    """
    _, tenants_snapshot = await firebase_request(client, settings, "locataires")
    
    if not isinstance(tenants_snapshot, dict):
        return {"checked": 0, "updated": 0}
    
    today = date.today()
    checked = 0
    updated = 0
    
    for tenant_id, raw in tenants_snapshot.items():
        checked += 1
        record = raw or {}
        
        # Skip if no entry date
        entry_date_str = record.get("entryDate")
        if not entry_date_str:
            continue
        
        entry_date = normalize_entry_date(entry_date_str)
        if not entry_date:
            continue
        
        # Get payment cycle (default 1 month)
        payment_months = max(1, min(12, int(record.get("paymentMonths") or 1)))
        
        # Calculate next due date
        next_due = compute_next_due_date(entry_date, payment_months)
        
        # Check if payment is late (due date has passed)
        current_status = record.get("status", "pending")
        
        if next_due < today and current_status != "late":
            # Update status to late
            await firebase_request(
                client,
                settings,
                "locataires",
                method="PATCH",
                record_id=tenant_id,
                body={"status": "late"}
            )
            updated += 1
        elif next_due >= today and current_status == "late":
            # Payment is no longer late, set back to active
            await firebase_request(
                client,
                settings,
                "locataires",
                method="PATCH",
                record_id=tenant_id,
                body={"status": "active"}
            )
            updated += 1
    
    return {"checked": checked, "updated": updated}
