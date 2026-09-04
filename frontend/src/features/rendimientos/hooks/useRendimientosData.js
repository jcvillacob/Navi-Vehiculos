import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchConnectionStatsRange,
  fetchMonthlyAvailability,
  fetchMonthlyPerformance,
} from "../../../api/vehicleApi";

const EMPTY_PAYLOAD = { summary: null, rows: [] };

/**
 * Una placa puede tener varios meses en el rango: nos quedamos con el mas
 * reciente (last_calculated_at) para mostrar un valor "actual".
 */
function indexAvailabilityByPlate(rows) {
  const byPlate = {};
  for (const row of rows || []) {
    const prev = byPlate[row.plate];
    if (!prev) {
      byPlate[row.plate] = row;
      continue;
    }
    const prevTs = new Date(prev.last_calculated_at).getTime();
    const nextTs = new Date(row.last_calculated_at).getTime();
    if (nextTs >= prevTs) byPlate[row.plate] = row;
  }
  return byPlate;
}

function mergeConnectionStats(results) {
  const merged = {};
  for (const rows of results) {
    for (const row of rows || []) {
      const prev = merged[row.plate];
      if (!prev) {
        merged[row.plate] = { ...row };
        continue;
      }
      prev.days_checked += row.days_checked;
      prev.days_connected += row.days_connected;
      prev.days_disconnected += row.days_disconnected;
      prev.days_not_found = (prev.days_not_found || 0) + (row.days_not_found || 0);
      prev.days_error = (prev.days_error || 0) + (row.days_error || 0);
      prev.connection_pct = prev.days_checked > 0
        ? Math.round((prev.days_connected / prev.days_checked) * 1000) / 10
        : 0;
      prev.consecutive_disconnected = Math.max(prev.consecutive_disconnected, row.consecutive_disconnected);
    }
  }
  return merged;
}

/**
 * Una sola llamada de rango al backend (`{ months: { "YYYY-MM": [...] } }`);
 * se recorre en orden cronologico para que el merge sea determinista.
 */
async function loadConnectionStatsRange(monthFrom, monthTo) {
  const response = await fetchConnectionStatsRange(monthFrom, monthTo);
  const byMonth = response?.months && typeof response.months === "object" ? response.months : {};
  const results = Object.keys(byMonth).sort().map((m) => byMonth[m]);
  return mergeConnectionStats(results);
}

/**
 * Carga en un solo lugar: filas de rendimiento + disponibilidad + stats de
 * conexion para el rango [monthFrom, monthTo] (ya normalizado).
 * - requestId descarta respuestas viejas (race guard).
 * - Si falla la carga principal se conserva la data anterior marcada `stale`.
 */
export function useRendimientosData(monthFrom, monthTo) {
  const [payload, setPayload] = useState(EMPTY_PAYLOAD);
  const [availabilityByPlate, setAvailabilityByPlate] = useState({});
  const [connStats, setConnStats] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const requestIdRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestIdRef.current += 1;
    };
  }, []);

  const load = useCallback(async () => {
    if (!monthFrom || !monthTo) return;
    const requestId = ++requestIdRef.current;
    const isCurrent = () => mountedRef.current && requestIdRef.current === requestId;

    setLoading(true);
    setError("");
    try {
      const [perfResult, availResult, connResult] = await Promise.allSettled([
        fetchMonthlyPerformance({ month_from: monthFrom, month_to: monthTo }),
        fetchMonthlyAvailability({ month_from: monthFrom, month_to: monthTo }),
        loadConnectionStatsRange(monthFrom, monthTo),
      ]);
      if (!isCurrent()) return;

      if (perfResult.status === "fulfilled") {
        const response = perfResult.value;
        setPayload({ ...response, rows: Array.isArray(response?.rows) ? response.rows : [] });
        setStale(false);
      } else {
        const err = perfResult.reason;
        setError(err instanceof Error ? err.message : "No fue posible cargar rendimientos");
        setStale(true);
      }
      setAvailabilityByPlate(availResult.status === "fulfilled" ? indexAvailabilityByPlate(availResult.value) : {});
      setConnStats(connResult.status === "fulfilled" ? connResult.value : {});
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [monthFrom, monthTo]);

  useEffect(() => {
    load();
  }, [load]);

  return {
    rows: payload.rows,
    summary: payload.summary,
    availabilityByPlate,
    connStats,
    loading,
    error,
    stale,
    reload: load,
  };
}
