import functools
from typing import List, Optional

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: Optional[str]) -> List[str]:
  if not value:
    return []
  return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
  model_config = SettingsConfigDict(env_file=".env", extra="ignore")

  port: int = Field(4000, alias="PORT")
  jwt_secret: str = Field("locatus_dev_secret", alias="JWT_SECRET")
  jwt_expire_days: int = Field(7, alias="JWT_EXPIRE_DAYS")
  client_origin: str = Field("http://localhost:3000", alias="CLIENT_ORIGIN")

  firebase_database_url: Optional[AnyHttpUrl] = Field(None, alias="FIREBASE_DATABASE_URL")
  firebase_database_secret: Optional[str] = Field(None, alias="FIREBASE_DATABASE_SECRET")
  default_owner_id: str = Field("admin-1", alias="DEFAULT_OWNER_ID")

  stripe_secret_key: Optional[str] = Field(None, alias="STRIPE_SECRET_KEY")
  stripe_webhook_secret: Optional[str] = Field(None, alias="STRIPE_WEBHOOK_SECRET")

  smtp_host: Optional[str] = Field(None, alias="SMTP_HOST")
  smtp_port: int = Field(587, alias="SMTP_PORT")
  smtp_user: Optional[str] = Field(None, alias="SMTP_USER")
  smtp_password: Optional[str] = Field(None, alias="SMTP_PASSWORD")
  smtp_secure: Optional[bool] = Field(None, alias="SMTP_SECURE")
  mail_from: Optional[str] = Field(None, alias="MAIL_FROM")
  mail_reply_to: Optional[str] = Field(None, alias="MAIL_REPLY_TO")
  app_url: str = Field("http://localhost:3000", alias="APP_URL")
  email_logo_url: str = Field("http://localhost:3000/logo.png", alias="EMAIL_LOGO_URL")

  reminder_enabled: Optional[bool] = Field(None, alias="REMINDER_ENABLED")
  reminder_cron_tz: str = Field("UTC", alias="REMINDER_CRON_TZ")

  allowed_origins: List[str] = Field(default_factory=list)

  @field_validator("allowed_origins", mode="before")
  @classmethod
  def fill_origins(cls, value, info):
    if value:
      return value
    client_origin = info.data.get("client_origin") or "http://localhost:3000"
    return _split_csv(client_origin)

  @field_validator("smtp_secure", mode="before")
  @classmethod
  def normalize_bool(cls, value):
    if value is None:
      return None
    if isinstance(value, bool):
      return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
      return True
    if normalized in {"0", "false", "no", "off"}:
      return False
    return None

  @property
  def mailer_configured(self) -> bool:
    return bool(self.smtp_host)

  @property
  def reminder_active(self) -> bool:
    if self.reminder_enabled is None:
      return self.mailer_configured
    return self.reminder_enabled


@functools.lru_cache
def get_settings() -> Settings:
  return Settings()  # type: ignore[arg-type]
