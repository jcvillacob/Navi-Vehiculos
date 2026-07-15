import logging
from datetime import datetime, timedelta, timezone
from typing import Any
import mygeotab

from app.core.config import load_geotab_config, settings
from app.clients.geotab_client import get_authenticated_client, get_cached_devices, multi_call_with_retry
from app.services.geotab_taller import (
    apply_enter,
    apply_exit,
    resolve_vehicle,
    _redis_client,
    _read_state,
    _KEY_ACTIVE_SET,
)

logger = logging.getLogger(__name__)

_UTC = timezone.utc

# Zone types used in the "En taller - Proyecto" rule condition tree:
# bD = Taller Autorizado, b6 = Navitrans
VALID_ZONE_TYPE_IDS = {"bD", "b6"}
TALLER_RULE_ID = "aD6IJFudHx06EXirc7yXxrQ"

def reconcile_taller_vehicles_with_geotab() -> dict[str, Any]:
    """
    Job de reconciliacion periodico (fallback) que sincroniza el estado local
    en Redis con los eventos de geocercas en vivo de Geotab.
    """
    logger.info("Iniciando Job de reconciliacion Geotab Fallback...")
    stats = {
        "geotab_exceptions_fetched": 0,
        "active_exceptions_found": 0,
        "added_entries": 0,
        "added_exits": 0,
        "ignored_category": 0,
        "errors": 0
    }

    try:
        cfg = load_geotab_config()
        if not cfg.username or not cfg.password or not cfg.database:
            logger.warning("Credenciales de Geotab no configuradas. Omitiendo reconciliacion.")
            return stats

        api = get_authenticated_client(cfg.username, cfg.password, cfg.database)

        # 1. Obtener todos los dispositivos registrados (mapeo id -> vin/placa)
        devices = get_cached_devices(cfg.username, cfg.password, cfg.database)
        devices_by_id = {d["id"]: d for d in devices}

        # 2. Consultar ExceptionEvents de taller de las ultimas X horas
        hours_back = max(24, settings.taller_exited_map_hours)
        from_date = (datetime.now(_UTC) - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        logger.info("Consultando ExceptionEvents de taller desde %s...", from_date)
        exceptions = api.call("Get", typeName="ExceptionEvent", search={
            "ruleSearch": {"id": TALLER_RULE_ID},
            "fromDate": from_date
        })
        stats["geotab_exceptions_fetched"] = len(exceptions)
        logger.info("Se encontraron %d excepciones de taller en Geotab.", len(exceptions))

        # 3. Filtrar excepciones mas recientes por cada dispositivo
        latest_exceptions_by_device: dict[str, dict[str, Any]] = {}
        for ex in exceptions:
            dev_id = ex.get("device", {}).get("id")
            if not dev_id:
                continue
            prev = latest_exceptions_by_device.get(dev_id)
            if not prev or ex.get("activeFrom") > prev.get("activeFrom"):
                latest_exceptions_by_device[dev_id] = ex

        dev_ids = list(latest_exceptions_by_device.keys())
        if not dev_ids:
            logger.info("No hay excepciones de taller recientes para evaluar.")
            return stats

        # 4. Obtener DeviceStatusInfo en lote para ver la ultima comunicacion (dateTime y posicion)
        status_calls = [
            ("Get", {"typeName": "DeviceStatusInfo", "search": {"deviceSearch": {"id": did}}})
            for did in dev_ids
        ]
        logger.info("Consultando DeviceStatusInfo para %d dispositivos...", len(dev_ids))
        status_results = multi_call_with_retry(api, status_calls)

        device_statuses: dict[str, dict[str, Any]] = {}
        for res_list in status_results:
            if res_list and isinstance(res_list, list):
                sinfo = res_list[0]
                did = sinfo.get("device", {}).get("id")
                if did:
                    device_statuses[did] = sinfo

        # 5. Obtener vehiculos marcados actualmente como "in" en Redis (local)
        r = _redis_client()
        local_plates = r.smembers(_KEY_ACTIVE_SET)
        local_in_vehicles = {}
        for lp in local_plates:
            state = _read_state(lp)
            if state and state.get("status") == "in" and state.get("manual") != "true":
                local_in_vehicles[lp.upper()] = state

        # 6. Reconciliacion
        geotab_active_plates = set()
        processed_plates = set()

        for dev_id, ex in latest_exceptions_by_device.items():
            dev = devices_by_id.get(dev_id)
            if not dev:
                continue
            device_name = dev.get("name")
            vin = dev.get("vin")

            vehicle = resolve_vehicle(device_name=device_name, vin=vin)
            if not vehicle:
                continue

            # Regla de negocio: Solo Flota Administrada y Experiencia Superior
            if vehicle["category"] not in ("Flota Administrada", "Experiencia Superior"):
                stats["ignored_category"] += 1
                continue

            plate = vehicle["plate"].upper()

            sinfo = device_statuses.get(dev_id)
            if not sinfo:
                continue
            device_time = sinfo.get("dateTime")
            if not isinstance(device_time, datetime):
                continue

            active_to = ex.get("activeTo")
            if not isinstance(active_to, datetime):
                continue

            # Evaluar si la excepcion esta activa: activeTo coincide con el ultimo log de GPS
            time_diff = abs((device_time - active_to).total_seconds())
            is_active = time_diff <= 2.0

            if is_active:
                stats["active_exceptions_found"] += 1
                geotab_active_plates.add(plate)

                # Escenario A: Entrada perdida (Esta en Geotab pero no en Redis "in")
                if plate not in local_in_vehicles:
                    logger.info("Reconciliacion fallback: Entrada perdida detectada para placa %s", plate)
                    
                    # Resolver zona del taller de forma dinamica
                    lat = sinfo.get("latitude")
                    lng = sinfo.get("longitude")
                    zone_id, zone_name = _resolve_taller_zone(api, lat, lng)

                    cleaned = {
                        "asset_info": {
                            "device_id": dev_id,
                            "device_name": device_name,
                            "vin": vin,
                        },
                        "telemetry_info": {
                            "zone_id": zone_id,
                            "zone_name": zone_name,
                            "latitude": lat,
                            "longitude": lng,
                            "odometer": None, # Odometro opcional
                        }
                    }

                    # Registrar la entrada en Redis
                    apply_enter(
                        plate=plate,
                        event_ts_utc=ex.get("activeFrom"),
                        cleaned=cleaned,
                        vehicle=vehicle,
                    )
                    stats["added_entries"] += 1
                    processed_plates.add(plate)
            else:
                # La excepcion esta completada/cerrada.
                # Si esta en Redis local, debemos procesar su salida (salida perdida)
                if plate in local_in_vehicles:
                    logger.info("Reconciliacion fallback: Salida perdida detectada para placa %s", plate)
                    apply_exit(plate=plate, event_ts_utc=active_to)
                    stats["added_exits"] += 1
                    processed_plates.add(plate)

        # Escenario B: Salida perdida general
        # Todos los vehiculos marcados localmente como "in" que Geotab ya no considera activos
        for lp, state in local_in_vehicles.items():
            if lp not in geotab_active_plates and lp not in processed_plates:
                # El vehiculo no tiene excepcion activa en Geotab, pero sigue "in" en Redis
                # Buscamos si la ultima excepcion de la ventana nos da el timestamp de salida
                # Si no, caemos a una salida con fecha actual
                exit_ts = datetime.now(_UTC)
                
                # Intentar buscar la excepcion del dispositivo mapeado localmente
                for dev_id, ex in latest_exceptions_by_device.items():
                    dev = devices_by_id.get(dev_id)
                    if dev and resolve_vehicle(device_name=dev.get("name"), vin=dev.get("vin")):
                        v_match = resolve_vehicle(device_name=dev.get("name"), vin=dev.get("vin"))
                        if v_match and v_match["plate"].upper() == lp:
                            active_to = ex.get("activeTo")
                            if isinstance(active_to, datetime) and active_to.year != 1900:
                                exit_ts = active_to
                            break

                logger.info("Reconciliacion fallback: Forzando salida perdida para placa %s", lp)
                apply_exit(plate=lp, event_ts_utc=exit_ts)
                stats["added_exits"] += 1

    except Exception as exc:
        logger.exception("Error durante la ejecucion de la reconciliacion Geotab Fallback")
        stats["errors"] += 1

    logger.info("Fin del Job de reconciliacion Geotab. Estadisticas: %s", stats)
    return stats

def _resolve_taller_zone(api: mygeotab.API, lat: float | None, lng: float | None) -> tuple[str | None, str | None]:
    """
    Deduce el zone_id y zone_name del taller resolviendo las coordenadas
    mediante GetAddresses y filtrando por los zoneTypes del taller (bD y b6).
    """
    if lat is None or lng is None:
        return None, None
        
    try:
        addr_res = api.call("GetAddresses", coordinates=[{"y": lat, "x": lng}])
        if not addr_res or not addr_res[0].get("zones"):
            return None, None
            
        zones_to_check = [z["id"] for z in addr_res[0]["zones"]]
        
        # Evaluar cada zona hasta encontrar una que coincida con el tipo de taller
        for zid in zones_to_check:
            zone_info = api.call("Get", typeName="Zone", search={"id": zid})
            if zone_info:
                zone = zone_info[0]
                ztypes = zone.get("zoneTypes", [])
                
                # Normalizar los tipos de zona
                ztype_ids = []
                for zt in ztypes:
                    if isinstance(zt, dict):
                        ztype_ids.append(zt.get("id"))
                    elif isinstance(zt, str):
                        ztype_ids.append(zt)
                        
                if any(zt_id in VALID_ZONE_TYPE_IDS for zt_id in ztype_ids):
                    return zone["id"], zone["name"]
                    
        # Fallback: retornar la primera zona si ninguna coincide con los tipos
        first_zone_id = addr_res[0]["zones"][0]["id"]
        return first_zone_id, first_zone_id
        
    except Exception:
        logger.exception("Error resolviendo zona para coordenadas %s, %s", lat, lng)
        
    return None, None
