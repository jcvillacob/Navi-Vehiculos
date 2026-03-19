from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from jose import JWTError, jwt

from app.core.config import settings

_MUTATION_METHODS = {"POST", "PUT", "DELETE"}
_AUTH_PREFIX = "/api/v1/auth"


def _extract_user_id(request: Request) -> int | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


def _method_to_action(method: str) -> str:
    return {"POST": "CREATE", "PUT": "UPDATE", "DELETE": "DELETE"}.get(method, method)


def _parse_resource(path: str) -> tuple[str, str | None]:
    """
    Extrae resource_type y resource_id del path.
    Ej: /api/v1/motors/5/attachments -> ("motor", "5")
        /api/v1/customers            -> ("customer", None)
    """
    # Quitar prefix /api/v1/
    segments = [s for s in path.split("/") if s]
    # Buscar el segmento después de api/v1
    try:
        idx = segments.index("v1")
        resource_segments = segments[idx + 1:]
    except ValueError:
        resource_segments = segments

    if not resource_segments:
        return ("unknown", None)

    # Mapeo singular
    resource_map = {
        "motors": "motor",
        "customers": "customer",
        "vehicle": "vehicle",
        "users": "user",
        "audit": "audit",
        "dashboard": "dashboard",
        "health": "health",
    }

    resource_type = resource_map.get(resource_segments[0], resource_segments[0])

    resource_id: str | None = None
    if len(resource_segments) > 1:
        candidate = resource_segments[1]
        # Solo usar como ID si parece un identificador (número o placa), no una sub-ruta
        if candidate not in ("attachments", "databases", "rules", "rule-groups",
                              "refresh", "database", "revalidate-customer-geotab",
                              "resolve", "inspection", "migrate-attachments"):
            resource_id = candidate

    return resource_type, resource_id


def _get_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        if request.method not in _MUTATION_METHODS:
            return response

        # No loguear las rutas de auth en sí mismas
        if request.url.path.startswith(_AUTH_PREFIX):
            return response

        user_id = _extract_user_id(request)
        action = _method_to_action(request.method)
        resource_type, resource_id = _parse_resource(request.url.path)
        ip_address = _get_ip(request)
        detail = {
            "path": request.url.path,
            "query": str(request.query_params),
            "status_code": response.status_code,
        }

        # Importación diferida para evitar ciclo de importación
        from app.services.auth_service import write_audit_log
        try:
            write_audit_log(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail,
                ip_address=ip_address,
            )
        except Exception:
            pass  # El audit log nunca debe romper la respuesta al cliente

        return response
