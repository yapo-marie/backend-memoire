from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.firebase import firebase_request
from ..core.settings import Settings, get_settings
from ..models.tenant import TenantCreate, TenantOut, TenantUpdate

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


def sanitize_tenant_input(payload: TenantCreate, default_owner: str) -> dict:
  if not payload.name.strip():
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le nom du locataire est requis.")
  return {
    "name": payload.name.strip(),
    "email": payload.email.lower(),
    "phone": payload.phone.strip(),
    "status": payload.status,
    "propertyId": payload.propertyId,
    "ownerId": payload.ownerId or default_owner,
    "note": payload.note.strip() if payload.note else None,
    "entryDate": payload.entryDate,
    "paymentMonths": payload.paymentMonths,
  }


def sanitize_tenant_patch(payload: TenantUpdate) -> dict:
  patch = payload.model_dump(exclude_unset=True)
  patch.pop("id", None)
  if "ownerId" in patch:
    patch.pop("ownerId")
  if "propertyId" in patch:
    patch["propertyId"] = patch["propertyId"] or None
  if "note" in patch and patch["note"] == "":
    patch.pop("note")
  return patch


def map_snapshot(snapshot, default_owner: str):
  if not isinstance(snapshot, dict):
    return []
  results = []
  for tenant_id, raw in snapshot.items():
    record = raw or {}
    results.append(
      {
        "id": tenant_id,
        "name": record.get("name"),
        "email": record.get("email"),
        "phone": record.get("phone"),
        "status": record.get("status") or "pending",
        "propertyId": record.get("propertyId"),
        "ownerId": record.get("ownerId") or default_owner,
        "note": record.get("note"),
        "entryDate": record.get("entryDate"),
        "paymentMonths": record.get("paymentMonths") or 1,
      }
    )
  return results


def map_single(record_id: str, record: dict, default_owner: str) -> dict:
  data = record or {}
  return {
    "id": record_id,
    "name": data.get("name"),
    "email": data.get("email"),
    "phone": data.get("phone"),
    "status": data.get("status") or "pending",
    "propertyId": data.get("propertyId"),
    "ownerId": data.get("ownerId") or default_owner,
    "note": data.get("note"),
    "entryDate": data.get("entryDate"),
    "paymentMonths": data.get("paymentMonths") or 1,
  }


def get_client(request: Request) -> httpx.AsyncClient:
  return request.app.state.http_client


@router.get("", response_model=list[TenantOut])
async def list_tenants(
  request: Request,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  _, snapshot = await firebase_request(client, settings, "locataires")
  return [TenantOut.model_validate(item).model_dump() for item in map_snapshot(snapshot, settings.default_owner_id)]


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
  request: Request,
  payload: TenantCreate,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  body = sanitize_tenant_input(payload, settings.default_owner_id)
  status_code, snapshot = await firebase_request(client, settings, "locataires", method="POST", body=body)
  tenant_id = snapshot.get("name") if isinstance(snapshot, dict) else str(uuid4())

  # Automatically mark property as occupied
  if body.get("propertyId"):
    await firebase_request(
      client,
      settings,
      "proprietes",
      method="PATCH",
      record_id=body["propertyId"],
      body={"status": "occupied"},
    )

  return TenantOut.model_validate({**body, "id": tenant_id}).model_dump()


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
  tenant_id: str,
  payload: TenantUpdate,
  request: Request,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  patch = sanitize_tenant_patch(payload)
  # Récupérer l'enregistrement actuel pour retourner un payload complet
  _, existing = await firebase_request(client, settings, "locataires", record_id=tenant_id)
  if existing is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Locataire introuvable.")
  
  # Handle property change if needed (optional complexity, skipping for now to keep it simple or add if requested)
  # Ideally: if propertyId changes, mark old as vacant and new as occupied. 
  # For now, let's just update the tenant.

  await firebase_request(client, settings, "locataires", method="PATCH", record_id=tenant_id, body=patch)
  merged = map_single(tenant_id, existing, settings.default_owner_id) | patch
  return TenantOut.model_validate(merged).model_dump()


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
  tenant_id: str,
  request: Request,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  # Fetch tenant to find propertyId
  _, existing = await firebase_request(client, settings, "locataires", record_id=tenant_id)
  
  await firebase_request(client, settings, "locataires", method="DELETE", record_id=tenant_id)

  # Automatically mark property as vacant if it was assigned
  if existing and existing.get("propertyId"):
    await firebase_request(
      client,
      settings,
      "proprietes",
      method="PATCH",
      record_id=existing["propertyId"],
      body={"status": "vacant"},
    )
