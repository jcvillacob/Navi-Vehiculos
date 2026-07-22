import { useCallback, useEffect, useRef, useState } from "react";

import {
  calculateMonthlyPerformance,
  fetchAvailabilityCoverage,
  fetchAvailabilityOverview,
  fetchAvailabilityRanking,
  fetchAvailabilityTrend,
  fetchMtbfSummary,
  fetchPerformanceJob,
  listCustomers,
} from "../../../api/vehicleApi";

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function addMonths(month, delta) {
  const [year, mon] = month.split("-").map(Number);
  const index = year * 12 + (mon - 1) + delta;
  const [nextYear, nextMonth] = [Math.floor(index / 12), index % 12];
  return `${nextYear}-${String(nextMonth + 1).padStart(2, "0")}`;
}

function monthsInRange(monthFrom, monthTo) {
  if (!isValidMonth(monthFrom) || !isValidMonth(monthTo)) return TREND_MONTHS;
  const [fromYear, fromMonth] = monthFrom.split("-").map(Number);
  const [toYear, toMonth] = monthTo.split("-").map(Number);
  return Math.max(1, Math.min(24, (toYear - fromYear) * 12 + toMonth - fromMonth + 1));
}

const TREND_MONTHS = 6;
const RANKING_LIMIT = 5000;
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
export function useAvailabilityDashboard(initialFilters = {}) {
  const defaultMonth = currentMonth();
  const initialMonth = isValidMonth(initialFilters.month) ? initialFilters.month : defaultMonth;
  const initialMonthFrom = isValidMonth(initialFilters.monthFrom)
    ? initialFilters.monthFrom
    : addMonths(defaultMonth, -(TREND_MONTHS - 1));
  const initialMonthTo = isValidMonth(initialFilters.monthTo) ? initialFilters.monthTo : defaultMonth;
  const [month, setMonth] = useState(initialMonth);
  const [monthFrom, setMonthFrom] = useState(initialMonthFrom);
  const [monthTo, setMonthTo] = useState(initialMonthTo);
  const [selectedCustomerId, setSelectedCustomerId] = useState(
    Number.isInteger(initialFilters.selectedCustomerId) ? initialFilters.selectedCustomerId : null,
  );
  const [plateSearch, setPlateSearch] = useState(
    typeof initialFilters.plateSearch === "string" ? initialFilters.plateSearch : "",
  );
  const [availabilityStatusFilter, setAvailabilityStatusFilter] = useState(
    ["good", "warning", "critical", "no_data"].includes(initialFilters.availabilityStatusFilter)
      ? initialFilters.availabilityStatusFilter
      : null,
  );
  const [includeNoOrders, setIncludeNoOrders] = useState(initialFilters.includeNoOrders !== false);

  const [customers, setCustomers] = useState([]);
  const [overview, setOverview] = useState(null);
  const [ranking, setRanking] = useState([]);
  const [trend, setTrend] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [mtbf, setMtbf] = useState(null);

  const [loadingOverview, setLoadingOverview] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [mtbfLoading, setMtbfLoading] = useState(false);
  const [error, setError] = useState("");
  const [mtbfError, setMtbfError] = useState("");

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

  const loadCoverage = useCallback(async () => {
    if (!isValidMonth(month)) return;
    try {
      const data = await fetchAvailabilityCoverage({ month });
      setCoverage(data);
    } catch (err) {
      setCoverage(null);
    }
  }, [month]);

  const loadMtbf = useCallback(async (forceRefresh = false) => {
    setMtbfLoading(true);
    setMtbfError("");
    try {
      const data = await fetchMtbfSummary({ forceRefresh });
      setMtbf(data);
    } catch (err) {
      setMtbfError(err.message || "No fue posible cargar el MTBF del año");
      setMtbf(null);
    } finally {
      setMtbfLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async () => {
    if (!isValidMonth(month)) return;
    setLoadingDetail(true);
    try {
      const [rankingData, trendData] = await Promise.all([
        fetchAvailabilityRanking({
          month,
          customer_id: selectedCustomerId,
          limit: RANKING_LIMIT,
          order: "worst",
          include_no_orders: includeNoOrders,
          plate_search: plateSearch,
          availability_status: availabilityStatusFilter,
        }),
        fetchAvailabilityTrend({
          month_to: monthTo,
          months: monthsInRange(monthFrom, monthTo),
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
  }, [month, monthFrom, monthTo, selectedCustomerId, plateSearch, availabilityStatusFilter, includeNoOrders]);

  useEffect(() => {
    loadOverview();
    loadCoverage();
  }, [loadOverview, loadCoverage]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const refreshAll = useCallback(() => {
    loadOverview();
    loadCoverage();
    loadDetail();
  }, [loadOverview, loadCoverage, loadDetail]);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const recalculate = useCallback(async ({ month: requestedMonth = month, customerIds = [] } = {}) => {
    if (!isValidMonth(requestedMonth)) {
      setRecalcError("Selecciona un mes válido");
      return false;
    }
    setRecalcError("");
    const payload = {
      month: requestedMonth,
      customer_ids: customerIds,
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
      return true;
    } catch (err) {
      setRecalcError(err.message || "No fue posible iniciar el recalculo");
      return false;
    }
  }, [month, stopPolling, refreshAll]);

  const isRecalculating = Boolean(job && ACTIVE_JOB_STATUSES.has(job.status));

  return {
    // filtros
    month,
    setMonth,
    monthFrom,
    setMonthFrom,
    monthTo,
    setMonthTo,
    selectedCustomerId,
    setSelectedCustomerId,
    plateSearch,
    setPlateSearch,
    availabilityStatusFilter,
    setAvailabilityStatusFilter,
    includeNoOrders,
    setIncludeNoOrders,
    // datos
    customers,
    overview,
    ranking,
    trend,
    coverage,
    mtbf,
    // estado
    loadingOverview,
    loadingDetail,
    mtbfLoading,
    error,
    mtbfError,
    // mtbf lazy
    loadMtbf,
    // recalculo
    recalculate,
    isRecalculating,
    job,
    recalcError,
    refreshAll,
  };
}
