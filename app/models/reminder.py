from typing import List, Optional

from pydantic import BaseModel


class ReminderSendRequest(BaseModel):
  ownerId: Optional[str] = None
  tenantIds: Optional[List[str]] = None
  dueDate: Optional[str] = None
  message: Optional[str] = None


class ReminderHistoryItem(BaseModel):
  id: str
  ownerId: str
  total: int
  sent: int
  failed: int
  dueDate: Optional[str] = None
  templatePreview: Optional[str] = None
  createdAt: Optional[str] = None


class UpcomingReminder(BaseModel):
  tenantId: str
  tenantName: str
  tenantEmail: str
  propertyName: str
  amount: float
  amountFormatted: str
  paymentMonths: int
  dueDate: str


class UpcomingResponse(BaseModel):
  reminderDate: str
  dueDate: str
  totalRecipients: int
  reminders: List[UpcomingReminder]
