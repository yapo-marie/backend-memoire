import stripe as stripe_module

ZERO_DECIMAL_CURRENCIES = {
  "bif",
  "clp",
  "djf",
  "gnf",
  "jpy",
  "kmf",
  "krw",
  "mga",
  "pyg",
  "rwf",
  "ugx",
  "vnd",
  "xaf",
  "xof",
  "xpf",
}


def init_stripe(api_key: str) -> stripe_module:
  stripe_module.api_key = api_key
  stripe_module.api_version = "2024-06-20"
  return stripe_module


def compute_unit_amount(amount: float, currency: str) -> int:
  if currency.lower() in ZERO_DECIMAL_CURRENCIES:
    return round(amount)
  return round(amount * 100)
