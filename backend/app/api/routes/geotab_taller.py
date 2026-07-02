from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import (
    get_current_user,
    require_geotab_webhook_key,
    require_permission,
)
from app.schemas.geotab_taller import ManualTallerAction
from app.services import geotab_taller


router = APIRouter(tags=["geotab-taller"])


@router.post(
    "/geotab/taller",
    summary="Webhook Geotab para eventos de entrada/salida de taller",
    description=(
        "Endpoint publico que recibe webhooks de Geotab. Autenticacion por "
        "API key en query (?api_key=...) o header X-API-Key. La respuesta es "
        "siempre 200 (Geotab no puede reenviar)."
    ),
    dependencies=[Depends(require_geotab_webhook_key)],
)
async def receive_geotab_taller_webhook(request: Request) -> dict:
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=422, detail="Body vacio")
    return geotab_taller.process_webhook(raw_body)


@router.get(
    "/mapa/taller",
    summary="Snapshot de vehiculos en taller para el mapa",
    description=(
        "Devuelve los vehiculos que llevan >= TALLER_MIN_MINUTES dentro del "
        "taller (estado in, no ocultos), con timestamps convertidos a "
        "America/Bogota y zonas derivadas. Cacheado en Redis con ETag."
    ),
)
def get_mapa_taller(
    if_none_match: str | None = None,
    _user: dict = Depends(get_current_user),
) -> JSONResponse:
    snapshot, etag = geotab_taller.get_mapa_snapshot_cacheable()
    headers = {
        "ETag": etag,
        "Cache-Control": (
            f"private, max-age={settings.mapa_snapshot_ttl_seconds}"
        ),
    }
    if if_none_match and if_none_match.strip().strip('"') == etag:
        return JSONResponse(status_code=304, content=None, headers=headers)
    return JSONResponse(content=snapshot, headers=headers)


@router.post(
    "/mapa/taller/manual",
    summary="Gestion manual del estado de un vehiculo en el mapa",
    description=(
        "Permite agregar, ocultar, des-ocultar o cerrar manualmente el "
        "estado de un vehiculo. Un enter real de Geotab reemplaza cualquier "
        "estado manual (fresh start). Requiere permiso mapa.taller.manage."
    ),
)
def manual_taller_action(
    payload: ManualTallerAction,
    _user: dict = Depends(require_permission("mapa.taller.manage")),
) -> dict:
    enter_ts_utc = None
    if payload.enter_ts:
        try:
            enter_ts_utc = datetime.fromisoformat(payload.enter_ts)
            if enter_ts_utc.tzinfo is None:
                enter_ts_utc = enter_ts_utc.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"enter_ts invalido: {exc}"
            ) from exc
    try:
        return geotab_taller.apply_manual_action(
            plate=payload.plate,
            action=payload.action,
            enter_ts_utc=enter_ts_utc,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
