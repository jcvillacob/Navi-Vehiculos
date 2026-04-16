import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.api.routes.proxy import router as proxy_router
from app.core.config import settings
from app.core.rate_limit import limiter
from app.middleware.audit import AuditMiddleware
from app.middleware.security import SecurityHeadersMiddleware
from app.services.auth_service import cleanup_expired_refresh_tokens

logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title=settings.app_name)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
cleanup_expired_refresh_tokens()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(proxy_router)
