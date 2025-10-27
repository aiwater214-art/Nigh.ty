from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import api_router
from app.core.config import get_settings
from app.core.database import Base, engine
from dashboard.routes import router as dashboard_router
from dashboard.token import refresh_admin_bootstrap_token

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
app.include_router(api_router, prefix="/api")
app.include_router(dashboard_router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    refresh_admin_bootstrap_token()


@app.get("/")
def root():
    return RedirectResponse(url="/dashboard")
