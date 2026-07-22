from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.clients.cloudfleet_client import (
    CloudFleetAuthError,
    CloudFleetUnavailableError,
)
from app.core.dependencies import require_permission
from app.services import taller_ordenes

router = APIRouter(prefix="/taller-ordenes", tags=["taller-ordenes"])


@router.get(
    "/active",
    summary="Ordenes de taller activas (CloudFleet)",
    description=(
        "Devuelve las ordenes de trabajo activas de CloudFleet enriquecidas con "
        "flota local, dias transcurridos e indicador temporal. Solo incluye "
        "vehiculos de Flota Administrada o Experiencia Superior. Cacheado en "
        "memoria 10 minutos; usar force_refresh=true para invalidar el cache."
    ),
)
def get_active_taller_orders(
    force_refresh: bool = Query(default=False, description="Invalida el cache y vuelve a consultar CloudFleet"),
    _user: dict = Depends(require_permission("rendimientos.view")),
) -> dict:
    try:
        return taller_ordenes.get_active_orders(force_refresh=force_refresh)
    except CloudFleetAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except CloudFleetUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
