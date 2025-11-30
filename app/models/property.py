from typing import Optional

from pydantic import BaseModel


class PropertyBase(BaseModel):
  name: str
  address: str
  status: str = "vacant"
  type: str
  bedrooms: int = 0
  rent: float = 0
  charges: float = 0
  ownerId: str


class PropertyCreate(PropertyBase):
  pass


class PropertyUpdate(BaseModel):
  name: Optional[str] = None
  address: Optional[str] = None
  status: Optional[str] = None
  type: Optional[str] = None
  bedrooms: Optional[int] = None
  rent: Optional[float] = None
  charges: Optional[float] = None


class PropertyOut(PropertyBase):
  id: str
