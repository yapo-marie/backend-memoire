from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


class CheckoutRequest(BaseModel):
  amount: float
  tenantName: Optional[str] = None
  tenantEmail: EmailStr
  tenantId: str
  ownerId: Optional[str] = None
  propertyId: Optional[str] = None
  propertyName: Optional[str] = None
  dueDate: Optional[str] = None
  paymentMonths: int = 1
  successUrl: Optional[str] = None
  cancelUrl: Optional[str] = None

  @field_validator("paymentMonths")
  @classmethod
  def validate_months(cls, value: int) -> int:
    return max(1, min(12, int(value)))


class PaymentHistoryQuery(BaseModel):
  ownerId: Optional[str] = None
  tenantEmail: Optional[str] = None
  tenantId: Optional[str] = None
  limit: int = 20

  @field_validator("limit")
  @classmethod
  def cap_limit(cls, value: int) -> int:
    return max(1, min(50, int(value)))


class PaymentHistoryItem(BaseModel):
  id: str
  amount: Optional[float]
  currency: str
  paymentStatus: str
  sessionStatus: str
  tenantName: Optional[str]
  tenantEmail: Optional[str]
  tenantId: Optional[str]
  propertyName: Optional[str]
  propertyId: Optional[str]
  ownerId: Optional[str]
  dueDate: Optional[str]
  paymentMonths: Optional[int]
  createdAt: str
  paidAt: Optional[str]
  receiptUrl: Optional[str]
