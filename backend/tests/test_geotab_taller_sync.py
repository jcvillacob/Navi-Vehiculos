from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from redis import Redis

from app.core.config import settings
from app.services import geotab_taller
from app.services.geotab_taller import _redis_client, _read_state, _KEY_ACTIVE_SET
from app.services.geotab_taller_sync import reconcile_taller_vehicles_with_geotab
from tests.test_geotab_taller import (
    _truncate_all,
    _insert_customer,
    _insert_geotab_db,
    _insert_vehicle,
    _insert_motor,
)

_UTC = timezone.utc

@pytest.fixture
def sync_db():
    _truncate_all()
    # Reset Redis
    r = _redis_client()
    r.flushdb()
    yield
    _truncate_all()
    r.flushdb()


@pytest.fixture
def mock_geotab_api():
    with patch("app.services.geotab_taller_sync.get_authenticated_client") as mock_auth, \
         patch("app.services.geotab_taller_sync.get_cached_devices") as mock_devices, \
         patch("app.services.geotab_taller_sync.multi_call_with_retry") as mock_multicall:
        api = MagicMock()
        mock_auth.return_value = api
        
        yield {
            "api": api,
            "mock_devices": mock_devices,
            "mock_multicall": mock_multicall,
        }


def test_reconcile_missed_entry(sync_db, mock_geotab_api):
    # 1. Setup database: insert customer and vehicle with category "Flota Administrada"
    cid = _insert_customer("Cliente Test", "Flota Administrada")
    did = _insert_geotab_db(cid)
    _insert_motor("TEC-1", "ISD")
    _insert_vehicle(
        plate="TLK240",
        vin="LDYCCS8D0V0000035",
        customer_id=cid,
        database_id=did,
        category="Flota Administrada",
    )
    
    # 2. Setup mock data
    now_time = datetime.now(_UTC)
    active_from_time = now_time - timedelta(hours=2)
    
    # Mock cached devices
    mock_geotab_api["mock_devices"].return_value = [
        {"id": "b348", "name": "TLK240", "vin": "LDYCCS8D0V0000035"}
    ]
    
    # Mock exceptions from Geotab: one active exception for b348
    mock_geotab_api["api"].call.side_effect = lambda method, *a, **k: {
        "Get": [
            {
                "activeFrom": active_from_time,
                "activeTo": now_time,  # activeTo matches device's last communication time
                "device": {"id": "b348"},
                "id": "ex_123"
            }
        ] if k.get("typeName") == "ExceptionEvent" else [
            # For Zone query
            {"id": "b391BE", "name": "Taller Penta", "zoneTypes": [{"id": "bD"}]}
        ] if k.get("typeName") == "Zone" else [],
        
        "GetAddresses": [
            {
                "city": "Cota",
                "formattedAddress": "Address",
                "zones": [{"id": "b391BE"}]
            }
        ]
    }.get(method, [])
    
    # Mock DeviceStatusInfo returning now_time as dateTime (matches exception's activeTo)
    mock_geotab_api["mock_multicall"].return_value = [
        [
            {
                "device": {"id": "b348"},
                "dateTime": now_time,
                "latitude": 4.7566,
                "longitude": -74.1506
            }
        ]
    ]
    
    # 3. Execute reconciliation
    stats = reconcile_taller_vehicles_with_geotab(did)
    
    # 4. Assertions
    assert stats["active_exceptions_found"] == 1
    assert stats["added_entries"] == 1
    assert stats["added_exits"] == 0
    
    # Check state in Redis
    state = _read_state("TLK240")
    assert state is not None
    assert state.get("status") == "in"
    assert state.get("manual") == "false"
    assert state.get("zone_name") == "Taller Penta"


def test_reconcile_missed_exit(sync_db, mock_geotab_api):
    # 1. Setup database: insert customer and vehicle with category "Flota Administrada"
    cid = _insert_customer("Cliente Test", "Flota Administrada")
    did = _insert_geotab_db(cid)
    _insert_motor("TEC-1", "ISD")
    _insert_vehicle(
        plate="TLK240",
        vin="LDYCCS8D0V0000035",
        customer_id=cid,
        database_id=did,
        category="Flota Administrada",
    )
    
    # 2. Setup Redis state: vehicle currently inside ("in")
    cleaned = {
        "asset_info": {"device_id": "b348", "device_name": "TLK240", "vin": "LDYCCS8D0V0000035"},
        "telemetry_info": {"zone_id": "b391BE", "zone_name": "Taller Penta", "latitude": 4.7, "longitude": -74.1}
    }
    veh_dict = {"category": "Flota Administrada"}
    geotab_taller.apply_enter(
        plate="TLK240",
        event_ts_utc=datetime.now(_UTC) - timedelta(hours=3),
        cleaned=cleaned,
        vehicle=veh_dict,
    )
    
    # Check that it's correctly set up in Redis
    state_before = _read_state("TLK240")
    assert state_before.get("status") == "in"
    
    # 3. Setup mock data for exit: exception ended 1 hour ago
    now_time = datetime.now(_UTC)
    active_from_time = now_time - timedelta(hours=3)
    active_to_time = now_time - timedelta(hours=1)
    
    # Mock cached devices
    mock_geotab_api["mock_devices"].return_value = [
        {"id": "b348", "name": "TLK240", "vin": "LDYCCS8D0V0000035"}
    ]
    
    # Mock exceptions from Geotab: completed exception (activeTo = active_to_time)
    mock_geotab_api["api"].call.side_effect = lambda method, *a, **k: {
        "Get": [
            {
                "activeFrom": active_from_time,
                "activeTo": active_to_time,  # ended 1h ago
                "device": {"id": "b348"},
                "id": "ex_123"
            }
        ] if k.get("typeName") == "ExceptionEvent" else []
    }.get(method, [])
    
    # Mock DeviceStatusInfo returning now_time as dateTime (diff of 1h > 2s compared to activeTo)
    mock_geotab_api["mock_multicall"].return_value = [
        [
            {
                "device": {"id": "b348"},
                "dateTime": now_time,
                "latitude": 4.7566,
                "longitude": -74.1506
            }
        ]
    ]
    
    # 4. Execute reconciliation
    stats = reconcile_taller_vehicles_with_geotab(did)
    
    # 5. Assertions
    assert stats["active_exceptions_found"] == 0
    assert stats["added_entries"] == 0
    assert stats["added_exits"] == 1
    
    # Check state in Redis: should now be "grace"
    state_after = _read_state("TLK240")
    assert state_after is not None
    assert state_after.get("status") == "grace"
    assert state_after.get("exit_ts") == active_to_time.isoformat()


def test_reconcile_ignores_ninguna_category(sync_db, mock_geotab_api):
    # 1. Setup database: insert vehicle with category "Ninguna"
    cid = _insert_customer("Cliente Neutro", "Ninguna")
    did = _insert_geotab_db(cid)
    _insert_motor("TEC-1", "ISD")
    _insert_vehicle(
        plate="TLK240",
        vin="LDYCCS8D0V0000035",
        customer_id=cid,
        database_id=did,
        category="Ninguna",
    )
    
    # 2. Setup mock data (active exception)
    now_time = datetime.now(_UTC)
    active_from_time = now_time - timedelta(hours=2)
    
    # Mock cached devices
    mock_geotab_api["mock_devices"].return_value = [
        {"id": "b348", "name": "TLK240", "vin": "LDYCCS8D0V0000035"}
    ]
    
    # Mock exceptions from Geotab
    mock_geotab_api["api"].call.side_effect = lambda method, *a, **k: {
        "Get": [
            {
                "activeFrom": active_from_time,
                "activeTo": now_time,
                "device": {"id": "b348"},
                "id": "ex_123"
            }
        ] if k.get("typeName") == "ExceptionEvent" else []
    }.get(method, [])
    
    # Mock DeviceStatusInfo
    mock_geotab_api["mock_multicall"].return_value = [
        [
            {
                "device": {"id": "b348"},
                "dateTime": now_time,
                "latitude": 4.7566,
                "longitude": -74.1506
            }
        ]
    ]
    
    # 3. Execute reconciliation
    stats = reconcile_taller_vehicles_with_geotab(did)
    
    # 4. Assertions
    assert stats["ignored_category"] == 1
    assert stats["added_entries"] == 0
    
    # Check that Redis remains empty for this plate
    assert _read_state("TLK240") is None


def test_reconcile_respects_manual_entries(sync_db, mock_geotab_api):
    # 1. Setup database: insert vehicle
    cid = _insert_customer("Cliente Test", "Flota Administrada")
    did = _insert_geotab_db(cid)
    _insert_motor("TEC-1", "ISD")
    _insert_vehicle(
        plate="TLK240",
        vin="LDYCCS8D0V0000035",
        customer_id=cid,
        database_id=did,
        category="Flota Administrada",
    )
    
    # 2. Setup Redis state: manual enter
    r = _redis_client()
    key = f"taller:state:TLK240"
    r.hset(key, mapping={
        "plate": "TLK240",
        "status": "in",
        "manual": "true",
        "hidden": "false",
        "enter_ts": (datetime.now(_UTC) - timedelta(hours=5)).isoformat()
    })
    r.sadd(_KEY_ACTIVE_SET, "TLK240")
    
    # 3. Setup mock data: exception is closed (ended 2 hours ago)
    now_time = datetime.now(_UTC)
    active_from_time = now_time - timedelta(hours=5)
    active_to_time = now_time - timedelta(hours=2)
    
    # Mock cached devices
    mock_geotab_api["mock_devices"].return_value = [
        {"id": "b348", "name": "TLK240", "vin": "LDYCCS8D0V0000035"}
    ]
    
    # Mock exceptions from Geotab
    mock_geotab_api["api"].call.side_effect = lambda method, *a, **k: {
        "Get": [
            {
                "activeFrom": active_from_time,
                "activeTo": active_to_time,
                "device": {"id": "b348"},
                "id": "ex_123"
            }
        ] if k.get("typeName") == "ExceptionEvent" else []
    }.get(method, [])
    
    # Mock DeviceStatusInfo
    mock_geotab_api["mock_multicall"].return_value = [
        [
            {
                "device": {"id": "b348"},
                "dateTime": now_time,
                "latitude": 4.7566,
                "longitude": -74.1506
            }
        ]
    ]
    
    # 4. Execute reconciliation
    stats = reconcile_taller_vehicles_with_geotab(did)
    
    # 5. Assertions: manual entry should NOT be changed
    state_after = _read_state("TLK240")
    assert state_after.get("status") == "in"
    assert state_after.get("manual") == "true"
    assert stats["added_exits"] == 0
