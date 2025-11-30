from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class TenantBase(BaseModel):
  name: str
  email: EmailStr
  phone: str
  status: str = "pending"
  propertyId: Optional[str] = None
  ownerId: str
  note: Optional[str] = None
  entryDate: str
  paymentMonths: int = 1

  @field_validator("paymentMonths")
  @classmethod
  def validate_months(cls, value: int) -> int:
    return max(1, min(12, int(value)))


class TenantCreate(TenantBase):
  pass


class TenantUpdate(BaseModel):
  name: Optional[str] = None
  email: Optional[EmailStr] = None
  phone: Optional[str] = None
  status: Optional[str] = None
  propertyId: Optional[str | None] = Field(default=None)
  note: Optional[str | None] = None
  entryDate: Optional[str | None] = None
  paymentMonths: Optional[int] = None

  @field_validator("paymentMonths")
  @classmethod
  def validate_months(cls, value: Optional[int]) -> Optional[int]:
    if value is None:
      return None
    return max(1, min(12, int(value)))


class TenantOut(TenantBase):
  id: str
