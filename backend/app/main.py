from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes.proxy import router as proxy_router
from app.core.config import settings
from app.middleware.audit import AuditMiddleware
from app.services.auth_service import ensure_auth_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_auth_tables()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(proxy_router)
