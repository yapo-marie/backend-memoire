import contextlib
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth, messages, payments, properties, reminders, tenants
from .core.security import get_users_file
from .core.settings import get_settings
from .cron.scheduler import create_scheduler
from .models.user import add_default_admin
from .core.stripe_utils import init_stripe


def create_app() -> FastAPI:
  settings = get_settings()
  app = FastAPI(title="Locatus API", version="2.0.0")

  app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
  )

  http_client = httpx.AsyncClient(timeout=15)
  app.state.http_client = http_client
  app.state.stripe = init_stripe(settings.stripe_secret_key) if settings.stripe_secret_key else None
  app.state.scheduler = None

  @app.on_event("startup")
  async def startup_event():
    add_default_admin()
    scheduler = create_scheduler(settings, http_client)
    if scheduler:
      scheduler.start()
      app.state.scheduler = scheduler

  @app.on_event("shutdown")
  async def shutdown_event():
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
      scheduler.shutdown(wait=False)
    await http_client.aclose()

  app.include_router(auth.router)
  app.include_router(tenants.router)
  app.include_router(properties.router)
  app.include_router(payments.router)
  app.include_router(reminders.router)
  app.include_router(messages.router)

  @app.get("/api/health")
  async def health():
    return {"status": "ok"}

  return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
  import uvicorn

  settings = get_settings()
  uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=True)
