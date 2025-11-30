from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException, status

from .settings import Settings


def build_firebase_url(settings: Settings, resource: str, record_id: Optional[str] = None) -> str:
  if not settings.firebase_database_url:
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Firebase non configuré.")
  base = str(settings.firebase_database_url).rstrip("/")
  path = f"{base}/{resource}{f'/{record_id}' if record_id else ''}.json"
  if settings.firebase_database_secret:
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}auth={settings.firebase_database_secret}"
  return path


async def firebase_request(
  client: httpx.AsyncClient,
  settings: Settings,
  resource: str,
  method: str = "GET",
  record_id: Optional[str] = None,
  body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Any]:
  url = build_firebase_url(settings, resource, record_id)
  response = await client.request(method, url, json=body)
  if response.status_code >= 400:
    raise HTTPException(
      status_code=status.HTTP_502_BAD_GATEWAY,
      detail=f"Firebase error {response.status_code}: {response.text}",
    )
  if response.status_code == status.HTTP_204_NO_CONTENT:
    return response.status_code, None
  data = response.json() if response.text else None
  return response.status_code, data
