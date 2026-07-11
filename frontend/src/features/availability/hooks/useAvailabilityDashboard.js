import { useCallback, useEffect, useRef, useState } from "react";

import {
  calculateMonthlyPerformance,
  fetchAvailabilityOverview,
  fetchAvailabilityRanking,
  fetchAvailabilityTrend,
  fetchPerformanceJob,
  listCustomers,
} from "../../../api/vehicleApi";

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

const TREND_MONTHS = 6;
const RANKING_LIMIT = 25;
const POLL_INTERVAL_MS = 2500;
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);
const SYSTEM_CUSTOMER = "__navitrans_system__";

function isValidMonth(value) {
  return /^\d{4}-\d{2}$/.test(value);
}

/**
 * Estado y datos del dashboard de Disponibilidad.
 * - Lee overview/ranking/trend de los endpoints /disponibilidad/*.
 * - Permite filtrar por flota (selectedCustomerId) -> recarga ranking + trend.
 * - recalculate() dispara el job existente de rendimientos con
 *   compute_availability=true y hace polling hasta que termina, luego refresca.
 */
export function useAvailabilityDashboard() {
  const [month, setMonth] = useState(currentMonth);
  const [selectedCustomerId, setSelectedCustomerId] = useState(null);
  const [rankingOrder, setRankingOrder] = useState("worst");
  const [includeNoOrders, setIncludeNoOrders] = useState(false);

  const [customers, setCustomers] = useState([]);
  const [overview, setOverview] = useState(null);
  const [ranking, setRanking] = useState([]);
  const [trend, setTrend] = useState(null);

  const [loadingOverview, setLoadingOverview] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");

  const [job, setJob] = useState(null);
  const [recalcError, setRecalcError] = useState("");
  const pollRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((data) => {
        if (!cancelled) {
          setCustomers(
            Array.isArray(data)
              ? data.filter((c) => c.name !== SYSTEM_CUSTOMER)
              : []
          );
        }
      })
      .catch(() => {
        if (!cancelled) setCustomers([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadOverview = useCallback(async () => {
    if (!isValidMonth(month)) return;
    setLoadingOverview(true);
    setError("");
    try {
      const data = await fetchAvailabilityOverview({ month });
      setOverview(data);
    } catch (err) {
      setError(err.message || "No fue posible cargar el resumen de disponibilidad");
      setOverview(null);
    } finally {
      setLoadingOverview(false);
    }
  }, [month]);

  const loadDetail = useCallback(async () => {
    if (!isValidMonth(month)) return;
    setLoadingDetail(true);
    try {
      const [rankingData, trendData] = await Promise.all([
        fetchAvailabilityRanking({
          month,
          customer_id: selectedCustomerId,
          limit: RANKING_LIMIT,
          order: rankingOrder,
          include_no_orders: includeNoOrders,
        }),
        fetchAvailabilityTrend({
          month_to: month,
          months: TREND_MONTHS,
          customer_id: selectedCustomerId,
        }),
      ]);
      setRanking(rankingData);
      setTrend(trendData);
    } catch (err) {
      setError(err.message || "No fue posible cargar el detalle de disponibilidad");
    } finally {
      setLoadingDetail(false);
    }
  }, [month, selectedCustomerId, rankingOrder, includeNoOrders]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const refreshAll = useCallback(() => {
    loadOverview();
    loadDetail();
  }, [loadOverview, loadDetail]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const recalculate = useCallback(async () => {
    if (!isValidMonth(month)) {
      setRecalcError("Selecciona un mes válido");
      return;
    }
    setRecalcError("");
    const payload = {
      month,
      customer_ids: selectedCustomerId ? [selectedCustomerId] : [],
      compute_availability: true,
      availability_only: true,
      force_recalculate: false,
      adhoc_only: false,
    };
    try {
      const { job: newJob } = await calculateMonthlyPerformance(payload);
      setJob(newJob);
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const updated = await fetchPerformanceJob(newJob.id);
          setJob(updated);
          if (!ACTIVE_JOB_STATUSES.has(updated.status)) {
            stopPolling();
            refreshAll();
          }
        } catch (err) {
          stopPolling();
          setRecalcError(err.message || "Error consultando el job");
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setRecalcError(err.message || "No fue posible iniciar el recalculo");
    }
  }, [month, selectedCustomerId, stopPolling, refreshAll]);

  const isRecalculating = Boolean(job && ACTIVE_JOB_STATUSES.has(job.status));

  return {
    // filtros
    month,
    setMonth,
    selectedCustomerId,
    setSelectedCustomerId,
    rankingOrder,
    setRankingOrder,
    includeNoOrders,
    setIncludeNoOrders,
    // datos
    customers,
    overview,
    ranking,
    trend,
    // estado
    loadingOverview,
    loadingDetail,
    error,
    // recalculo
    recalculate,
    isRecalculating,
    job,
    recalcError,
    refreshAll,
  };
}
