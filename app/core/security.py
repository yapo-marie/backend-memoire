from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from .settings import Settings, get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, password_hash: str) -> bool:
  return pwd_context.verify(plain_password, password_hash)


def hash_password(password: str) -> str:
  return pwd_context.hash(password)


def create_access_token(data: dict, settings: Settings) -> str:
  to_encode = data.copy()
  expire = datetime.utcnow() + timedelta(days=settings.jwt_expire_days)
  to_encode.update({"exp": expire})
  return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, settings: Settings) -> dict:
  try:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
  except JWTError as exc:  # pragma: no cover - handled by caller
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide") from exc


def get_users_file() -> Path:
  base_dir = Path(__file__).resolve().parents[2]
  return base_dir / "data" / "users.json"


async def get_current_user(
  credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
  settings: Settings = Depends(get_settings),
):
  if not credentials or credentials.scheme.lower() != "bearer":
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token manquant")
  payload = decode_token(credentials.credentials, settings)
  return payload
