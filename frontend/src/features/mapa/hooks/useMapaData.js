import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchMapaTaller } from "../../../api/mapaApi";

const DEFAULT_POLL_MS = 10 * 60 * 1000; // 10 min (req #5)
const envPollMs = Number(import.meta.env.VITE_MAPA_POLL_MS);
const POLL_MS = Number.isFinite(envPollMs) && envPollMs > 0 ? envPollMs : DEFAULT_POLL_MS;

/**
 * Hook que reemplaza el mock de mapa por datos reales del backend.
 *
 * - Carga inicial: spinner (loading=true) solo cuando NO hay datos previos.
 * - Refrescos silenciosos cada POLL_MS: NO spinner; actualiza markers en sitio.
 * - refresh() manual: NO spinner; marca `refreshing=true` para deshabilitar
 *   acciones sin perder los datos visibles (sin flash).
 * - ETag/304: si el backend dice "sin cambios", no re-renderiza.
 * - Manejo de errores: expone `error` y permite reintentar (`refresh()`).
 * - Al desmontar, cancela el fetch en vuelo y limpia el interval.
 */
export function useMapaData({ pollMs = POLL_MS } = {}) {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [vehicles, setVehicles] = useState([]);
  const [exited, setExited] = useState([]);
  const [zones, setZones] = useState([]);
  const [generatedAt, setGeneratedAt] = useState(null);

  const etagRef = useRef(null);
  const cancelledRef = useRef(false);

  const applySnapshot = useCallback((snapshot) => {
    if (!snapshot) return;
    setVehicles(Array.isArray(snapshot.vehicles) ? snapshot.vehicles : []);
    setExited(Array.isArray(snapshot.exited) ? snapshot.exited : []);
    setZones(Array.isArray(snapshot.zones) ? snapshot.zones : []);
    setGeneratedAt(snapshot.generated_at || null);
  }, []);

  const load = useCallback(async () => {
    try {
      const { snapshot, etag, notModified } = await fetchMapaTaller({
        etag: etagRef.current,
      });
      if (cancelledRef.current) return;
      if (!notModified && snapshot) {
        etagRef.current = etag;
        applySnapshot(snapshot);
      }
      setError("");
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err?.message || "No fue posible cargar el mapa");
    } finally {
      if (!cancelledRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [applySnapshot]);

  useEffect(() => {
    cancelledRef.current = false;
    load();
    const id = window.setInterval(load, pollMs);
    return () => {
      cancelledRef.current = true;
      window.clearInterval(id);
    };
  }, [load, pollMs]);

  const refresh = useCallback(() => {
    if (!cancelledRef.current) {
      setRefreshing(true);
      load();
    }
  }, [load]);

  const vehiclesByZoneId = useMemo(() => {
    const map = Object.fromEntries(zones.map((z) => [z.id, []]));
    for (const v of vehicles) {
      if (v.zone_id && map[v.zone_id]) map[v.zone_id].push(v);
    }
    return map;
  }, [zones, vehicles]);

  return {
    loading,
    refreshing,
    error,
    zones,
    vehicles,
    exited,
    vehiclesByZoneId,
    generatedAt,
    refresh,
  };
}
