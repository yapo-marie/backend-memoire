from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.firebase import firebase_request
from ..core.settings import Settings, get_settings
from ..models.property import PropertyCreate, PropertyOut, PropertyUpdate

router = APIRouter(prefix="/api/properties", tags=["properties"])


def sanitize_property_input(payload: PropertyCreate, default_owner: str) -> dict:
  if not payload.name.strip() or not payload.address.strip() or not payload.type.strip():
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nom, adresse et type requis.")
  return {
    "name": payload.name.strip(),
    "address": payload.address.strip(),
    "status": payload.status,
    "type": payload.type.strip(),
    "bedrooms": payload.bedrooms,
    "rent": payload.rent,
    "charges": payload.charges,
    "ownerId": payload.ownerId or default_owner,
  }


def sanitize_property_patch(payload: PropertyUpdate) -> dict:
  patch = payload.model_dump(exclude_unset=True)
  patch.pop("id", None)
  patch.pop("ownerId", None)
  return patch


def map_snapshot(snapshot, default_owner: str):
  if not isinstance(snapshot, dict):
    return []
  results = []
  for prop_id, raw in snapshot.items():
    record = raw or {}
    results.append(
      {
        "id": prop_id,
        "name": record.get("name"),
        "address": record.get("address"),
        "status": record.get("status") or "vacant",
        "type": record.get("type"),
        "bedrooms": record.get("bedrooms") or 0,
        "rent": record.get("rent") or 0,
        "charges": record.get("charges") or 0,
        "ownerId": record.get("ownerId") or default_owner,
      }
    )
  return results


def get_client(request: Request) -> httpx.AsyncClient:
  return request.app.state.http_client

def map_single(record_id: str, record: dict, default_owner: str) -> dict:
  data = record or {}
  return {
    "id": record_id,
    "name": data.get("name"),
    "address": data.get("address"),
    "status": data.get("status") or "vacant",
    "type": data.get("type"),
    "bedrooms": data.get("bedrooms") or 0,
    "rent": data.get("rent") or 0,
    "charges": data.get("charges") or 0,
    "ownerId": data.get("ownerId") or default_owner,
  }


@router.get("", response_model=list[PropertyOut])
async def list_properties(request: Request, settings: Settings = Depends(get_settings)):
  client = get_client(request)
  _, snapshot = await firebase_request(client, settings, "proprietes")
  return [PropertyOut.model_validate(item).model_dump() for item in map_snapshot(snapshot, settings.default_owner_id)]


@router.post("", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
async def create_property(
  request: Request,
  payload: PropertyCreate,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  body = sanitize_property_input(payload, settings.default_owner_id)
  _, snapshot = await firebase_request(client, settings, "proprietes", method="POST", body=body)
  prop_id = snapshot.get("name") if isinstance(snapshot, dict) else str(uuid4())
  return PropertyOut.model_validate({**body, "id": prop_id}).model_dump()


@router.patch("/{property_id}", response_model=PropertyOut)
async def update_property(
  property_id: str,
  payload: PropertyUpdate,
  request: Request,
  settings: Settings = Depends(get_settings),
):
  client = get_client(request)
  patch = sanitize_property_patch(payload)
  
  # Fetch existing record to ensure we have all fields for the response
  _, existing = await firebase_request(client, settings, "proprietes", record_id=property_id)
  if existing is None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Propriété introuvable.")

  await firebase_request(client, settings, "proprietes", method="PATCH", record_id=property_id, body=patch)
  
  merged = map_single(property_id, existing, settings.default_owner_id) | patch
  return PropertyOut.model_validate(merged).model_dump()


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property(property_id: str, request: Request, settings: Settings = Depends(get_settings)):
  client = get_client(request)
  await firebase_request(client, settings, "proprietes", method="DELETE", record_id=property_id)
