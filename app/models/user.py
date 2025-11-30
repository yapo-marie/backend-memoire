from datetime import datetime
from pathlib import Path
from typing import List

from pydantic import BaseModel, EmailStr, Field

from ..core.security import get_users_file, hash_password, verify_password


class UserCreate(BaseModel):
  name: str
  email: EmailStr
  password: str


class UserLogin(BaseModel):
  email: EmailStr
  password: str


class UserOut(BaseModel):
  id: str
  name: str
  email: EmailStr
  role: str = "user"
  phone: str | None = None
  createdAt: str | None = None


class UserDB(UserOut):
  passwordHash: str = Field(..., alias="passwordHash")

  class Config:
    populate_by_name = True


def _ensure_store(path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  if not path.exists():
    path.write_text("[]", encoding="utf-8")


def read_users() -> List[UserDB]:
  path = get_users_file()
  _ensure_store(path)
  raw = path.read_text(encoding="utf-8")
  return [UserDB.model_validate(item) for item in __import__("json").loads(raw)]


def write_users(users: List[UserDB]) -> None:
  path = get_users_file()
  _ensure_store(path)
  data = [user.model_dump(by_alias=True) for user in users]
  path.write_text(__import__("json").dumps(data, indent=2), encoding="utf-8")


def add_default_admin() -> None:
  users = read_users()
  admin_email = "admin@locatus.com"
  if any(user.email.lower() == admin_email for user in users):
    return
  from uuid import uuid4

  users.append(
    UserDB(
      id=str(uuid4()),
      name="Emilie Aubert",
      email=admin_email,
      passwordHash=hash_password("Admin!2024"),
      role="admin",
      phone="+33 6 88 77 11 22",
      createdAt=datetime.utcnow().isoformat(),
    )
  )
  write_users(users)


def authenticate(email: str, password: str) -> UserDB | None:
  users = read_users()
  for user in users:
    if user.email.lower() == email.lower() and verify_password(password, user.passwordHash):
      return user
  return None
