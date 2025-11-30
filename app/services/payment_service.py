from typing import Dict, List, Optional

import anyio
import stripe as stripe_module

from ..core.stripe_utils import compute_unit_amount

STRIPE_CURRENCY = "xof"
STRIPE_MAX_AMOUNT = 655_959_993


def build_metadata(fields: Dict[str, Optional[str]]) -> Dict[str, str]:
  return {k: str(v).strip() for k, v in fields.items() if v is not None and str(v).strip()}


async def create_checkout_session(stripe: stripe_module, payload: Dict) -> Dict:
  # Stripe Python expose Session sous stripe.checkout.Session. on passe par un wrapper pour éviter les kwargs interdits par run_sync.
  def _create():
    return stripe.checkout.Session.create(**payload)
  return await anyio.to_thread.run_sync(_create)


async def list_checkout_sessions(stripe: stripe_module, limit: int = 100) -> Dict:
  return await anyio.to_thread.run_sync(
    lambda: stripe.checkout.Session.list(limit=limit, expand=["data.payment_intent"])
  )
