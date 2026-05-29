import { useEffect, useMemo, useRef, useState, useCallback } from "react";

import Can from "../components/Can";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import {
  calculateMonthlyPerformance,
  fetchActivePerformanceJobs,
  fetchConnectionStats,
  fetchMonthlyAvailability,
  fetchMonthlyPerformance,
  fetchPerformanceJob,
  fetchRecentPerformanceJobs,
  listCustomers,
  listVehicleAssignments
} from "../api/vehicleApi";
import { DATABASE_PROVIDERS } from "../features/customers/providerCatalog";

const PERFORMANCE_PROVIDER_KEYS = new Set(
  DATABASE_PROVIDERS.filter((provider) => provider.supportsMonthlyPerformance).map((provider) => provider.key)
);
const RENDIMIENTOS_COLUMNS = [
  { key: "status", label: "Estado", getValue: (row) => getStatusLabel(row.calculation_status), getSortValue: (row) => getStatusLabel(row.calculation_status) },
  { key: "plate", label: "Placa", getValue: (row) => row.plate || "-", getSortValue: (row) => row.plate || "" },
  { key: "client", label: "Cliente", getValue: (row) => row.client_name || "-", getSortValue: (row) => row.client_name || "" },
  { key: "database", label: "Database", getValue: (row) => row.database_name || "-", getSortValue: (row) => row.database_name || "" },
  { key: "motor", label: "Motor", getValue: (row) => row.engine_name || "Sin catalogar", getSortValue: (row) => row.engine_name || "Sin catalogar" },
  { key: "nombre_vehiculo", label: "Nombre", getValue: (row) => row.nombre_vehiculo || "-", getSortValue: (row) => row.nombre_vehiculo || "" },
  { key: "marca", label: "Marca", getValue: (row) => row.marca || "-", getSortValue: (row) => row.marca || "" },
  { key: "linea", label: "Linea", getValue: (row) => row.linea || "-", getSortValue: (row) => row.linea || "" },
  { key: "ano_modelo", label: "Año", getValue: (row) => row.ano_modelo || "-", getSortValue: (row) => row.ano_modelo || "" },
  { key: "tipo_combustible", label: "Combustible", getValue: (row) => row.tipo_combustible || "-", getSortValue: (row) => row.tipo_combustible || "" },
  { key: "vin", label: "VIN", getValue: (row) => row.vin || "Sin VIN", getSortValue: (row) => row.vin || "" },
  { key: "cpl", label: "CPL", getValue: (row) => row.cpl || "Sin CPL", getSortValue: (row) => row.cpl || "" },
  { key: "technical_number", label: "TEC#", getValue: (row) => row.technical_number || "-", getSortValue: (row) => row.technical_number || "" },
  { key: "source_provider", label: "Proveedor", getValue: (row) => row.source_provider || "-", getSortValue: (row) => row.source_provider || "" },
  { key: "period_month", label: "Mes", getValue: (row) => formatMonthLabel(row.period_month), getSortValue: (row) => row.period_month || "" },
  { key: "odo_start", label: "Odo ini", getValue: (row) => formatMetric(row.odo_start, 0), getSortValue: (row) => row.odo_start },
  { key: "odo_end", label: "Odo fin", getValue: (row) => formatMetric(row.odo_end, 0), getSortValue: (row) => row.odo_end },
  { key: "kms_ecm", label: "Kms ECM", getValue: (row) => formatMetric(row.kms_ecm, 0), getSortValue: (row) => row.kms_ecm },
  { key: "kms_gps", label: "Kms GPS", getValue: (row) => formatMetric(row.kms_gps, 0), getSortValue: (row) => row.kms_gps },
  { key: "horo_start", label: "Horo ini", getValue: (row) => formatMetric(row.horo_start, 0), getSortValue: (row) => row.horo_start },
  { key: "horo_end", label: "Horo fin", getValue: (row) => formatMetric(row.horo_end, 0), getSortValue: (row) => row.horo_end },
  { key: "hours_ecm", label: "Hrs ECM", getValue: (row) => formatMetric(row.hours_ecm, 0), getSortValue: (row) => row.hours_ecm },
  { key: "hours_gps", label: "Hrs GPS", getValue: (row) => formatMetric(row.hours_gps, 0), getSortValue: (row) => row.hours_gps },
  { key: "fuel_gallons", label: "Galones", getValue: (row) => formatMetric(row.fuel_gallons, 0), getSortValue: (row) => row.fuel_gallons },
  { key: "kpg", label: "KPG", getValue: (row) => row.fuel_gallons > 0 && row.kms_ecm != null ? formatMetric(row.kms_ecm / row.fuel_gallons, 2) : "-", getSortValue: (row) => row.fuel_gallons > 0 && row.kms_ecm != null ? row.kms_ecm / row.fuel_gallons : null },
  { key: "gph", label: "GPH", getValue: (row) => row.hours_ecm > 0 && row.fuel_gallons != null ? formatMetric(row.fuel_gallons / row.hours_ecm, 2) : "-", getSortValue: (row) => row.hours_ecm > 0 && row.fuel_gallons != null ? row.fuel_gallons / row.hours_ecm : null },
  { key: "conn_pct", label: "Conexion %", getValue: (row, ctx) => ctx?.connStats?.[row.plate] ? `${Math.round(ctx.connStats[row.plate].connection_pct)}%` : "--", getSortValue: (row, ctx) => ctx?.connStats?.[row.plate]?.connection_pct ?? -1 },
  { key: "availability_pct", label: "Disp %", getValue: (row, ctx) => { const a = ctx?.availabilityByPlate?.[row.plate]; if (!a) return "Sin Datos"; if (a.calculation_status === "not_in_cloudfleet") return "No Aplica"; if (a.calculation_status === "error") return "Error"; return `${(a.project_availability_pct ?? 0).toFixed(1)}%`; }, getSortValue: (row, ctx) => { const a = ctx?.availabilityByPlate?.[row.plate]; if (!a) return -1; if (a.calculation_status === "not_in_cloudfleet") return -2; if (a.calculation_status === "error") return -3; return a.project_availability_pct ?? -1; } },
  { key: "calculated_at", label: "Último cálculo", getValue: (row) => row.calculated_at ? new Date(row.calculated_at).toLocaleString("es-CO") : "-", getSortValue: (row) => row.calculated_at ? new Date(row.calculated_at).getTime() : 0 },
];

const STATUS_FILTER_OPTIONS = [
  { key: "calculated", label: "Calculadas", className: "is-calculated" },
  { key: "partial", label: "Parciales", className: "is-partial" },
  { key: "unbound", label: "Sin binding", className: "is-unbound" },
  { key: "no_data", label: "Sin datos", className: "is-no-data" },
  { key: "error", label: "Error", className: "is-error" }
];

function getCurrentMonth() {
  return new Date().toISOString().slice(0, 7);
}

function buildOptions(rows, getValue) {
  return [...new Set(rows.map(getValue).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function filterRows(rows, filters, omit = "") {
  return rows.filter((row) => {
    if (omit !== "status" && filters.status && row.calculation_status !== filters.status) return false;
    if (omit !== "client" && filters.client && row.client_name !== filters.client) return false;
    if (omit !== "database" && filters.database && row.database_name !== filters.database) return false;
    if (omit !== "motorGroup" && filters.motorGroup && (row.engine_name || "Sin catalogar") !== filters.motorGroup) {
      return false;
    }
    if (omit !== "plateSearch" && filters.plateSearch) {
      const normalizedPlate = (row.plate || "").toUpperCase();
      if (!normalizedPlate.includes(filters.plateSearch)) return false;
    }
    return true;
  });
}

function formatNumber(value, options) {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("es-CO", options).format(value);
}

function formatMetric(value, digits = 1) {
  return formatNumber(value, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function getStatusLabel(status) {
  if (status === "calculated") return "Calculado";
  if (status === "partial") return "Parcial";
  if (status === "unbound") return "Sin binding";
  if (status === "no_data") return "Sin datos";
  if (status === "error") return "Error";
  return status || "Desconocido";
}

function getStatusClass(status) {
  if (status === "calculated") return "status-ok";
  if (status === "partial") return "status-soft";
  if (status === "unbound" || status === "no_data") return "status-partial";
  return "status-error";
}

function buildClientSelectionLabel(eligibleClients, selectedCustomerIds) {
  if (!eligibleClients.length) {
    return "Sin clientes";
  }
  if (!selectedCustomerIds.length) {
    return "Todos los clientes";
  }
  const selected = eligibleClients.filter((client) => selectedCustomerIds.includes(client.id));
  if (selected.length === 1) {
    return selected[0].name;
  }
  if (selected.length === 2) {
    return `${selected[0].name} y ${selected[1].name}`;
  }
  return `${selected.length} clientes seleccionados`;
}

function formatMonthLabel(monthStr) {
  if (!monthStr) return "";
  const [year, m] = monthStr.split("-");
  const names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  return `${names[parseInt(m, 10) - 1]} ${year}`;
}

function generateMonthRange(from, to) {
  const months = [];
  const [startYear, startMonth] = from.split("-").map(Number);
  const [endYear, endMonth] = to.split("-").map(Number);
  let y = startYear;
  let m = startMonth;
  while (y < endYear || (y === endYear && m <= endMonth)) {
    months.push(`${y}-${String(m).padStart(2, "0")}`);
    m++;
    if (m > 12) { m = 1; y++; }
  }
  return months;
}

function buildExportFileName(monthFrom, monthTo) {
  const range = monthFrom === monthTo ? monthFrom : `${monthFrom}_a_${monthTo}`;
  return `rendimientos_${range}.xlsx`;
}

function compareValues(left, right, direction = "asc") {
  if (left === right) return 0;
  if (left === null || left === undefined || left === "") return 1;
  if (right === null || right === undefined || right === "") return -1;

  let result = 0;

  if (typeof left === "number" && typeof right === "number") {
    result = left - right;
  } else {
    result = String(left).localeCompare(String(right), "es", { numeric: true, sensitivity: "base" });
  }

  return direction === "desc" ? result * -1 : result;
}

export default function RendimientosPage() {
  const { toasts, pushToast } = useToasts();

  // ── Calculation controls (header) ──
  const [calcMonthFrom, setCalcMonthFrom] = useState(getCurrentMonth);
  const [calcMonthTo, setCalcMonthTo] = useState(getCurrentMonth);
  const [calculating, setCalculating] = useState(false);
  const [calcProgress, setCalcProgress] = useState({
    current: 0,
    total: 0,
    currentMonth: "",
    processedTargets: 0,
    totalTargets: 0,
    jobId: null
  });
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [eligibleClients, setEligibleClients] = useState([]);
  const [selectedCustomerIds, setSelectedCustomerIds] = useState([]);
  const [consultOpen, setConsultOpen] = useState(false);
  const [calcAvailability, setCalcAvailability] = useState(false);
  const pollingCancelledRef = useRef(false);

  // ── Table range controls ──
  const [monthFrom, setMonthFrom] = useState(getCurrentMonth);
  const [monthTo, setMonthTo] = useState(getCurrentMonth);
  const [loading, setLoading] = useState(false);
  const [payload, setPayload] = useState({ month: getCurrentMonth(), summary: null, rows: [] });
  const [filters, setFilters] = useState({
    status: "",
    client: "",
    database: "",
    motorGroup: "",
    plateSearch: ""
  });
  const [sortConfig, setSortConfig] = useState({ key: "", direction: "asc" });
  const [connStats, setConnStats] = useState({});
  const [availabilityByPlate, setAvailabilityByPlate] = useState({});
  const [recentJobs, setRecentJobs] = useState([]);
  const [recentJobsLoading, setRecentJobsLoading] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [visibleColumns, setVisibleColumns] = useState(() => new Set(RENDIMIENTOS_COLUMNS.map((c) => c.key)));
  const [columnSelectorOpen, setColumnSelectorOpen] = useState(false);
  const columnSelectorRef = useRef(null);

  useEffect(() => {
    if (!columnSelectorOpen) return;
    function handleClickOutside(e) {
      if (columnSelectorRef.current && !columnSelectorRef.current.contains(e.target)) setColumnSelectorOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [columnSelectorOpen]);

  const toggleColumn = useCallback((key) => {
    setVisibleColumns((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const activeColumns = useMemo(
    () => RENDIMIENTOS_COLUMNS.filter((col) => visibleColumns.has(col.key)),
    [visibleColumns]
  );

  const isRange = monthFrom !== monthTo;
  const pickerRef = useRef(null);

  // Close client picker on click outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) {
        pickerRef.current.removeAttribute("open");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Cancel any in-flight polling cuando se desmonta la página
  useEffect(() => {
    return () => {
      pollingCancelledRef.current = true;
    };
  }, []);

  const reloadRecentJobs = useCallback(async () => {
    setRecentJobsLoading(true);
    try {
      const jobs = await fetchRecentPerformanceJobs(50);
      setRecentJobs(jobs);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar el historial");
    } finally {
      setRecentJobsLoading(false);
    }
  }, [pushToast]);

  // Carga inicial del historial
  useEffect(() => {
    reloadRecentJobs().catch(() => {});
  }, [reloadRecentJobs]);

  // Refresca el historial cada 5s mientras hay un cálculo en curso
  useEffect(() => {
    if (!calculating) return;
    const id = setInterval(() => { reloadRecentJobs().catch(() => {}); }, 5000);
    return () => clearInterval(id);
  }, [calculating, reloadRecentJobs]);

  // Refresca el historial cuando termina un cálculo
  useEffect(() => {
    if (calculating) return;
    reloadRecentJobs().catch(() => {});
  }, [calculating, reloadRecentJobs]);

  // Resume polling si al entrar a la página hay un job activo
  // (ej.: el usuario disparó un cálculo, navegó a otra página, y volvió)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const activeJobs = await fetchActivePerformanceJobs();
        if (cancelled || activeJobs.length === 0) return;
        // Tomamos el job activo más reciente. Si hay varios para el mismo
        // disparo en cadena, solo retomamos visualmente el que está corriendo.
        const job = activeJobs[0];
        if (calculating) return; // ya hay polling propio en curso
        pollingCancelledRef.current = false;
        setCalculating(true);
        setCalcProgress({
          current: 1,
          total: 1,
          currentMonth: job.month,
          processedTargets: job.processed_targets || 0,
          totalTargets: job.total_targets || 0,
          jobId: job.id
        });
        const finalJob = await pollJobUntilDone(job.id, job.month, 0, 1);
        if (!finalJob || cancelled) return;
        if (finalJob.status === "done") {
          const s = finalJob.summary || {};
          pushToast(
            "success",
            `Rendimiento ${formatMonthLabel(finalJob.month)} listo (${s.calculated || 0} de ${s.total || 0} placas).`
          );
          fireBrowserNotification(
            `Rendimiento ${formatMonthLabel(finalJob.month)} listo`,
            `${s.calculated || 0} calculadas / ${s.total || 0} placas`
          );
          await loadRecords(monthFrom, monthTo);
        } else if (finalJob.status === "error") {
          pushToast("error", `Error en ${formatMonthLabel(finalJob.month)}: ${finalJob.error_message || "Error desconocido"}`);
        }
        setCalculating(false);
        setCalcProgress({ current: 0, total: 0, currentMonth: "", processedTargets: 0, totalTargets: 0, jobId: null });
      } catch {
        // silencioso: si la API no responde el listado de jobs, no hacemos nada
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadRecords = useCallback(async (from, to) => {
    setLoading(true);
    try {
      const response = await fetchMonthlyPerformance({ month_from: from, month_to: to });
      setPayload(response);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar rendimientos");
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  // Load eligible clients on mount
  useEffect(() => {
    let cancelled = false;
    setCatalogLoading(true);

    Promise.all([listCustomers(), listVehicleAssignments()])
      .then(([customers, vehicles]) => {
        if (cancelled) return;

        const readyByCustomerId = new Map();
        for (const vehicle of vehicles) {
          const providerType = vehicle.database_connection_type;
          if (!PERFORMANCE_PROVIDER_KEYS.has(providerType) || !vehicle.customer_id || !vehicle.plate) {
            continue;
          }
          if (!readyByCustomerId.has(vehicle.customer_id)) {
            readyByCustomerId.set(vehicle.customer_id, new Set());
          }
          readyByCustomerId.get(vehicle.customer_id).add(vehicle.plate);
        }

        const eligible = customers
          .map((customer) => ({
            id: customer.id,
            name: customer.name,
            readyVehicles: readyByCustomerId.get(customer.id)?.size || 0,
            hasPerformanceDatabase: (customer.databases || []).some((database) => PERFORMANCE_PROVIDER_KEYS.has(database.connection_type))
          }))
          .filter(Boolean)
          .sort((a, b) => a.name.localeCompare(b.name));

        setEligibleClients(eligible);
      })
      .catch((err) => {
        if (!cancelled) {
          pushToast("error", err instanceof Error ? err.message : "No fue posible cargar clientes");
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });

    return () => { cancelled = true; };
  }, [pushToast]);

  useEffect(() => {
    setSelectedCustomerIds((current) =>
      current.filter((customerId) => eligibleClients.some((client) => client.id === customerId))
    );
  }, [eligibleClients]);

  // Auto-load table when range changes
  useEffect(() => {
    loadRecords(monthFrom, monthTo).catch(() => {});
  }, [monthFrom, monthTo, loadRecords]);

  // Load availability rows for current visible month range
  useEffect(() => {
    const from = monthFrom <= monthTo ? monthFrom : monthTo;
    const to = monthFrom <= monthTo ? monthTo : monthFrom;
    let cancelled = false;
    fetchMonthlyAvailability({ month_from: from, month_to: to })
      .then((rows) => {
        if (cancelled) return;
        // Una placa puede tener varios meses en el rango: nos quedamos con
        // el mas reciente (last_calculated_at) para mostrar un valor "actual".
        const byPlate = {};
        for (const row of rows) {
          const prev = byPlate[row.plate];
          if (!prev) {
            byPlate[row.plate] = row;
            continue;
          }
          const prevTs = new Date(prev.last_calculated_at).getTime();
          const nextTs = new Date(row.last_calculated_at).getTime();
          if (nextTs >= prevTs) byPlate[row.plate] = row;
        }
        setAvailabilityByPlate(byPlate);
      })
      .catch(() => {
        if (!cancelled) setAvailabilityByPlate({});
      });
    return () => { cancelled = true; };
  }, [monthFrom, monthTo]);

  // Load connection stats for current visible month range
  useEffect(() => {
    const months = generateMonthRange(
      monthFrom <= monthTo ? monthFrom : monthTo,
      monthFrom <= monthTo ? monthTo : monthFrom
    );
    Promise.all(months.map((m) => fetchConnectionStats(m).catch(() => [])))
      .then((results) => {
        const merged = {};
        for (const rows of results) {
          for (const row of rows) {
            const prev = merged[row.plate];
            if (!prev) {
              merged[row.plate] = { ...row };
            } else {
              prev.days_checked += row.days_checked;
              prev.days_connected += row.days_connected;
              prev.days_disconnected += row.days_disconnected;
              prev.connection_pct = prev.days_checked > 0
                ? Math.round(prev.days_connected / prev.days_checked * 1000) / 10
                : 0;
              prev.consecutive_disconnected = Math.max(prev.consecutive_disconnected, row.consecutive_disconnected);
            }
          }
        }
        setConnStats(merged);
      });
  }, [monthFrom, monthTo]);

  const clientOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "client");
    return buildOptions(subset, (row) => row.client_name);
  }, [filters, payload.rows]);

  const databaseOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "database");
    return buildOptions(subset, (row) => row.database_name);
  }, [filters, payload.rows]);

  const motorGroupOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "motorGroup");
    return buildOptions(subset, (row) => row.engine_name || "Sin catalogar");
  }, [filters, payload.rows]);

  useEffect(() => {
    if (filters.client && !clientOptions.includes(filters.client)) {
      setFilters((current) => ({ ...current, client: "" }));
    }
  }, [clientOptions, filters.client]);

  useEffect(() => {
    if (filters.database && !databaseOptions.includes(filters.database)) {
      setFilters((current) => ({ ...current, database: "" }));
    }
  }, [databaseOptions, filters.database]);

  useEffect(() => {
    if (filters.motorGroup && !motorGroupOptions.includes(filters.motorGroup)) {
      setFilters((current) => ({ ...current, motorGroup: "" }));
    }
  }, [filters.motorGroup, motorGroupOptions]);

  const filteredRows = useMemo(() => filterRows(payload.rows, filters), [filters, payload.rows]);
  const statusCounts = useMemo(() => {
    const counts = Object.fromEntries(STATUS_FILTER_OPTIONS.map((option) => [option.key, 0]));
    for (const row of filterRows(payload.rows, filters, "status")) {
      if (counts[row.calculation_status] !== undefined) {
        counts[row.calculation_status] += 1;
      }
    }
    return counts;
  }, [filters, payload.rows]);

  const sortedRows = useMemo(() => {
    if (!sortConfig.key) return filteredRows;

    const col = RENDIMIENTOS_COLUMNS.find((c) => c.key === sortConfig.key);
    if (!col) return filteredRows;

    const ctx = { connStats, availabilityByPlate };

    return [...filteredRows].sort((left, right) => {
      const leftVal = col.getSortValue ? col.getSortValue(left, ctx) : col.getValue(left, ctx);
      const rightVal = col.getSortValue ? col.getSortValue(right, ctx) : col.getValue(right, ctx);

      const comparison = compareValues(leftVal, rightVal, sortConfig.direction);
      if (comparison !== 0) return comparison;
      return compareValues(left.plate || "", right.plate || "", "asc");
    });
  }, [filteredRows, sortConfig, connStats, availabilityByPlate]);

  const visibleSummary = useMemo(() => {
    const totals = filteredRows.reduce(
      (accumulator, row) => {
        accumulator.kms += row.kms_ecm || 0;
        accumulator.hours += row.hours_ecm || 0;
        accumulator.gallons += row.fuel_gallons || 0;
        return accumulator;
      },
      { kms: 0, hours: 0, gallons: 0 }
    );

    return {
      ...totals,
      vehicles: filteredRows.length,
      kpg: totals.gallons > 0 ? totals.kms / totals.gallons : 0,
      gph: totals.hours > 0 ? totals.gallons / totals.hours : 0
    };
  }, [filteredRows]);

  const handleChange = (key) => (event) => {
    const value = key === "plateSearch" ? event.target.value.toUpperCase() : event.target.value;
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const handleClear = () => {
    setFilters({ status: "", client: "", database: "", motorGroup: "", plateSearch: "" });
  };

  const handleStatusChipClick = (status) => {
    setFilters((current) => ({
      ...current,
      status: current.status === status ? "" : status
    }));
  };

  const handleSort = (key) => {
    setSortConfig((current) => {
      if (current.key === key) {
        return { key, direction: current.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: "asc" };
    });
  };

  const ensureNotificationPermission = useCallback(async () => {
    if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
    if (Notification.permission === "granted" || Notification.permission === "denied") {
      return Notification.permission;
    }
    try {
      return await Notification.requestPermission();
    } catch {
      return "denied";
    }
  }, []);

  const fireBrowserNotification = useCallback((title, body) => {
    if (typeof window === "undefined" || !("Notification" in window)) return;
    if (Notification.permission !== "granted") return;
    try {
      const n = new Notification(title, { body, tag: "rendimientos-job" });
      n.onclick = () => { window.focus(); n.close(); };
    } catch {
      // ignore — algunos navegadores requieren contexto de usuario fresco
    }
  }, []);

  const pollJobUntilDone = useCallback(async (jobId, monthLabel, monthIndex, monthsTotal) => {
    const intervalMs = 3000;
    while (!pollingCancelledRef.current) {
      let job;
      try {
        job = await fetchPerformanceJob(jobId);
      } catch (err) {
        // Reintentamos suavemente ante errores transitorios
        await new Promise((resolve) => setTimeout(resolve, intervalMs));
        continue;
      }
      setCalcProgress({
        current: monthIndex + 1,
        total: monthsTotal,
        currentMonth: monthLabel,
        processedTargets: job.processed_targets || 0,
        totalTargets: job.total_targets || 0,
        jobId: job.id
      });
      if (job.status === "done" || job.status === "error") {
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
    }
    return null;
  }, []);

  const handleCalculate = async () => {
    const from = calcMonthFrom <= calcMonthTo ? calcMonthFrom : calcMonthTo;
    const to = calcMonthFrom <= calcMonthTo ? calcMonthTo : calcMonthFrom;
    const months = generateMonthRange(from, to);
    pollingCancelledRef.current = false;
    setConsultOpen(false);
    setCalculating(true);
    setCalcProgress({ current: 0, total: months.length, currentMonth: "", processedTargets: 0, totalTargets: 0, jobId: null });

    // Pedimos permiso para notificaciones del navegador la primera vez
    await ensureNotificationPermission();

    let errors = 0;
    for (let i = 0; i < months.length; i++) {
      if (pollingCancelledRef.current) break;
      const m = months[i];
      setCalcProgress({ current: i + 1, total: months.length, currentMonth: m, processedTargets: 0, totalTargets: 0, jobId: null });

      let createResponse;
      try {
        createResponse = await calculateMonthlyPerformance({
          month: m,
          customer_ids: selectedCustomerIds,
          force_recalculate: true,
          compute_availability: calcAvailability
        });
      } catch (err) {
        errors++;
        pushToast("error", `Error en ${formatMonthLabel(m)}: ${err instanceof Error ? err.message : "Error desconocido"}`);
        continue;
      }

      const { job: createdJob, reused } = createResponse;
      if (reused) {
        pushToast("info", `Ya hay un cálculo en curso para ${formatMonthLabel(m)} — siguiendo el job existente.`);
      }

      const finalJob = await pollJobUntilDone(createdJob.id, m, i, months.length);
      if (!finalJob) break; // polling cancelado

      if (finalJob.status === "error") {
        errors++;
        pushToast("error", `Error en ${formatMonthLabel(m)}: ${finalJob.error_message || "Error desconocido"}`);
        fireBrowserNotification(
          `Rendimiento ${formatMonthLabel(m)} falló`,
          finalJob.error_message || "Revisa los logs"
        );
      } else if (finalJob.status === "done") {
        const s = finalJob.summary || {};
        fireBrowserNotification(
          `Rendimiento ${formatMonthLabel(m)} listo`,
          `${s.calculated || 0} calculadas / ${s.total || 0} placas`
        );
      }
    }

    const ok = months.length - errors;
    if (!pollingCancelledRef.current && ok > 0) {
      pushToast("success", `Rendimientos calculados: ${ok} de ${months.length} mes(es).`);
    }
    if (!pollingCancelledRef.current && to >= monthFrom && from <= monthTo) {
      await loadRecords(monthFrom, monthTo);
      if (calcAvailability) {
        try {
          const rows = await fetchMonthlyAvailability({ month_from: monthFrom, month_to: monthTo });
          const byPlate = {};
          for (const row of rows) {
            const prev = byPlate[row.plate];
            if (!prev) { byPlate[row.plate] = row; continue; }
            const prevTs = new Date(prev.last_calculated_at).getTime();
            const nextTs = new Date(row.last_calculated_at).getTime();
            if (nextTs >= prevTs) byPlate[row.plate] = row;
          }
          setAvailabilityByPlate(byPlate);
        } catch (err) {
          pushToast("error", err instanceof Error ? err.message : "No fue posible cargar disponibilidad");
        }
      }
    }
    setCalculating(false);
    setCalcProgress({ current: 0, total: 0, currentMonth: "", processedTargets: 0, totalTargets: 0, jobId: null });
  };

  const handleExport = async () => {
    if (!sortedRows.length) {
      pushToast("error", "No hay filas para exportar con los filtros actuales.");
      return;
    }

    try {
      const XLSX = await import("xlsx");
      const ctx = { connStats, availabilityByPlate };

      const headers = activeColumns.map((col) => col.label);
      const rows = sortedRows.map((row) =>
        activeColumns.map((col) => {
          if (col.key === "status") return getStatusLabel(row.calculation_status);
          if (col.key === "conn_pct") {
            const cs = connStats[row.plate];
            return cs ? cs.connection_pct : null;
          }
          if (col.key === "availability_pct") {
            const a = availabilityByPlate[row.plate];
            if (!a) return "Sin Datos";
            if (a.calculation_status === "not_in_cloudfleet") return "No Aplica";
            if (a.calculation_status === "error") return "Error";
            return a.project_availability_pct;
          }
          const val = col.getValue(row, ctx);
          return val;
        })
      );

      const filtersSheet = [
        { Filtro: "Desde", Valor: monthFrom || "Todos" },
        { Filtro: "Hasta", Valor: monthTo || "Todos" },
        { Filtro: "Estado", Valor: filters.status ? getStatusLabel(filters.status) : "Todos" },
        { Filtro: "Cliente", Valor: filters.client || "Todos" },
        { Filtro: "Database", Valor: filters.database || "Todas" },
        { Filtro: "Grupo de motor", Valor: filters.motorGroup || "Todos" },
        { Filtro: "Placa", Valor: filters.plateSearch || "Todas" }
      ];

      const matrix = [headers, ...rows];
      const dataSheet = XLSX.utils.aoa_to_sheet(matrix);
      const filtersDataSheet = XLSX.utils.json_to_sheet(filtersSheet);

      const workbook = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(workbook, dataSheet, "Rendimientos");
      XLSX.utils.book_append_sheet(workbook, filtersDataSheet, "Filtros");
      XLSX.writeFile(workbook, buildExportFileName(monthFrom, monthTo));

      pushToast("success", `Excel exportado con ${sortedRows.length} filas.`);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible exportar el Excel");
    }
  };

  const rangeLabel = isRange
    ? `${formatMonthLabel(monthFrom)} – ${formatMonthLabel(monthTo)}`
    : formatMonthLabel(monthFrom);

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Analitica operativa</span>
          <h2>Rendimientos</h2>
        </div>

        <div className="rendimientos-month-actions">
          {recentJobs.length > 0 && (() => {
            const last = recentJobs[0];
            if (last.status !== "done") return null;
            const d = last.finished_at ? new Date(last.finished_at) : null;
            const dateStr = d ? `${d.getDate()}/${d.getMonth() + 1}/${d.getFullYear()}` : "";
            const s = last.summary || {};
            return (
              <span className="rendimientos-last-run">
                {dateStr} - {s.calculated || 0} de {s.total || 0}
              </span>
            );
          })()}
          <Can permission="rendimientos.refresh">
            <button
              type="button"
              onClick={() => setConsultOpen(true)}
              disabled={calculating}
            >
              {calculating ? "Calculando..." : "Consultar"}
            </button>
          </Can>
        </div>

        {calculating && calcProgress.total > 0 && (() => {
          const monthsPct = (calcProgress.current / calcProgress.total) * 100;
          const withinMonthPct = calcProgress.totalTargets > 0
            ? (calcProgress.processedTargets / calcProgress.totalTargets) * 100
            : 0;
          // Progreso global = meses completos + fracción del mes en curso
          const completedMonths = Math.max(0, calcProgress.current - 1);
          const currentMonthFraction = calcProgress.totalTargets > 0
            ? calcProgress.processedTargets / calcProgress.totalTargets
            : 0;
          const overallPct = ((completedMonths + currentMonthFraction) / calcProgress.total) * 100;
          return (
            <div className="bulk-progress-bar-container rendimientos-progress" style={{ marginTop: 8 }}>
              <div className="bulk-progress-header">
                <span className="bulk-progress-label">
                  Calculando {formatMonthLabel(calcProgress.currentMonth)} ({calcProgress.current} de {calcProgress.total})
                  {calcProgress.totalTargets > 0 && (
                    <> — {calcProgress.processedTargets} / {calcProgress.totalTargets} placas ({Math.round(withinMonthPct)}%)</>
                  )}
                </span>
                <span className="bulk-progress-percent">
                  {Math.round(overallPct || monthsPct)}%
                </span>
              </div>
              <div className="bulk-progress-track">
                <div
                  className="bulk-progress-fill"
                  style={{ width: `${overallPct || monthsPct}%` }}
                />
              </div>
            </div>
          );
        })()}
      </header>

      <ToastStack toasts={toasts} />

      <section className="rendimientos-summary-grid">
        <article className="card metric-card">
          <span className="eyebrow">Placas visibles</span>
          <strong>{visibleSummary.vehicles}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Kms ECM</span>
          <strong>{formatMetric(visibleSummary.kms, 0)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Horas ECM</span>
          <strong>{formatMetric(visibleSummary.hours, 0)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Galones</span>
          <strong>{formatMetric(visibleSummary.gallons, 0)}</strong>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">KPG</span>
          <strong>{formatMetric(visibleSummary.kpg, 2)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">GPH</span>
          <strong>{formatMetric(visibleSummary.gph, 2)}</strong>
        </article>
      </section>

      <section className="card rendimientos-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">{isRange ? "Acumulado" : "Lote"} {rangeLabel}</span>
            <h3>Explorador {isRange ? "por rango" : "mensual"}</h3>
          </div>

          <div className="actions-row section-heading-actions">
            <button
              type="button"
              className="button button-sm rendimientos-button-reload"
              onClick={() => loadRecords(monthFrom, monthTo)}
            >
              {loading ? "Cargando..." : "Recargar"}
            </button>
            <button type="button" className="button-secondary button-sm" onClick={handleClear}>
              Limpiar
            </button>
            <button
              type="button"
              className="button button-sm rendimientos-button-export"
              onClick={handleExport}
              disabled={!filteredRows.length}
            >
              Exportar Excel
            </button>
            <div className="column-selector-wrapper" ref={columnSelectorRef}>
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={() => setColumnSelectorOpen((prev) => !prev)}
              >
                Columnas ({visibleColumns.size}/{RENDIMIENTOS_COLUMNS.length})
              </button>
              {columnSelectorOpen && (
                <div className="column-selector-dropdown">
                  <div className="column-selector-actions">
                    <button
                      type="button"
                      className="column-selector-link"
                      onClick={() => setVisibleColumns(new Set(RENDIMIENTOS_COLUMNS.map((c) => c.key)))}
                    >
                      Todas
                    </button>
                    <button
                      type="button"
                      className="column-selector-link"
                      onClick={() => setVisibleColumns(new Set())}
                    >
                      Ninguna
                    </button>
                  </div>
                  {RENDIMIENTOS_COLUMNS.map((col) => (
                    <label key={col.key} className="column-selector-option">
                      <input
                        type="checkbox"
                        checked={visibleColumns.has(col.key)}
                        onChange={() => toggleColumn(col.key)}
                      />
                      <span>{col.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>
        </header>

        <div className="rendimientos-status-strip">
          {STATUS_FILTER_OPTIONS.map((option) => {
            const isActive = filters.status === option.key;
            return (
              <button
                key={option.key}
                type="button"
                className={`status-chip ${option.className} ${isActive ? "is-active" : ""}`}
                onClick={() => handleStatusChipClick(option.key)}
                aria-pressed={isActive}
                title={isActive ? `Quitar filtro ${option.label}` : `Filtrar por ${option.label}`}
              >
                {option.label}: {statusCounts[option.key] ?? 0}
              </button>
            );
          })}
        </div>

        <div className="rendimientos-range-bar">
          <div className="form-field">
            <label htmlFor="rendimientos-month-from">Desde</label>
            <input
              id="rendimientos-month-from"
              type="month"
              value={monthFrom}
              onChange={(event) => setMonthFrom(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="rendimientos-month-to">Hasta</label>
            <input
              id="rendimientos-month-to"
              type="month"
              value={monthTo}
              onChange={(event) => setMonthTo(event.target.value)}
            />
          </div>
        </div>

        <div className="rendimientos-filter-bar">
          <div className="form-field rendimientos-search-field">
            <label htmlFor="rendimientos-plate-search">Placas</label>
            <input
              id="rendimientos-plate-search"
              value={filters.plateSearch}
              onChange={handleChange("plateSearch")}
              placeholder="Buscar por placa"
            />
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-client">Clientes</label>
            <select id="rendimientos-client" value={filters.client} onChange={handleChange("client")}>
              <option value="">Todos</option>
              {clientOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-database">Database</label>
            <select id="rendimientos-database" value={filters.database} onChange={handleChange("database")}>
              <option value="">Todas</option>
              {databaseOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-motor-group">Grupo de motor</label>
            <select id="rendimientos-motor-group" value={filters.motorGroup} onChange={handleChange("motorGroup")}>
              <option value="">Todos</option>
              {motorGroupOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

        </div>

        <div className="rendimientos-table-shell">
          <table className="rendimientos-table">
            <thead>
              <tr>
                {activeColumns.map((column) => {
                  const isActive = sortConfig.key === column.key;
                  const directionSymbol = isActive ? (sortConfig.direction === "asc" ? "▲" : "▼") : "↕";

                  return (
                    <th key={column.key}>
                      <button
                        type="button"
                        className={`table-sort-button ${isActive ? "is-active" : ""}`}
                        onClick={() => handleSort(column.key)}
                        aria-label={`Ordenar por ${column.label} ${isActive && sortConfig.direction === "asc" ? "descendente" : "ascendente"}`}
                      >
                        <span>{column.label}</span>
                        <span className="table-sort-indicator" aria-hidden="true">{directionSymbol}</span>
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={activeColumns.length} className="table-empty-row">
                    No hay cortes para los filtros actuales.
                  </td>
                </tr>
              ) : (
                sortedRows.map((row) => {
                  const ctx = { connStats, availabilityByPlate };

                  return (
                    <tr key={`${row.customer_database_id}-${row.plate}-${row.period_month}`}>
                      {activeColumns.map((col) => {
                        if (col.key === "status") {
                          return (
                            <td key={col.key} data-label={col.label}>
                              <span
                                className={`status-dot ${getStatusClass(row.calculation_status)}`}
                                title={getStatusLabel(row.calculation_status)}
                              />
                            </td>
                          );
                        }
                        if (col.key === "plate") {
                          return (
                            <td key={col.key} data-label={col.label}>
                              <strong>{row.plate}</strong>
                            </td>
                          );
                        }
                        if (col.key === "conn_pct") {
                          const cs = connStats[row.plate];
                          return (
                            <td key={col.key} data-label={col.label}>
                              {!cs ? (
                                <span className="conn-pct-badge conn-pct-none">--</span>
                              ) : (
                                (() => {
                                  const level = cs.connection_pct >= 80 ? "good" : cs.connection_pct >= 50 ? "warn" : "bad";
                                  const alert = cs.consecutive_disconnected >= 3;
                                  return (
                                    <span
                                      className={`conn-pct-badge conn-pct-${level}${alert ? " conn-pct-alert" : ""}`}
                                      title={`${cs.days_connected}/${cs.days_checked} dias conectado${alert ? ` | ${cs.consecutive_disconnected} dias seguidos desconectado` : ""}`}
                                    >
                                      <span className="conn-pct-bar">
                                        <span className="conn-pct-fill" style={{ width: `${cs.connection_pct}%` }} />
                                      </span>
                                      <span className="conn-pct-label">{Math.round(cs.connection_pct)}%</span>
                                    </span>
                                  );
                                })()
                              )}
                            </td>
                          );
                        }
                        if (col.key === "availability_pct") {
                          const a = availabilityByPlate[row.plate];
                          return (
                            <td key={col.key} data-label={col.label}>
                              {!a ? (
                                <span className="availability-badge availability-empty">Sin Datos</span>
                              ) : a.calculation_status === "not_in_cloudfleet" ? (
                                <span className="availability-badge availability-na" title="La placa no aparece en CloudFleet">No Aplica</span>
                              ) : a.calculation_status === "error" ? (
                                <span className="availability-badge availability-error" title={a.error_message || "Error en el calculo"}>Error</span>
                              ) : (
                                (() => {
                                  const pct = a.project_availability_pct ?? 0;
                                  const level = pct >= 97 ? "good" : pct >= 96 ? "warn" : "bad";
                                  const title = a.calculation_status === "no_orders"
                                    ? "Sin ordenes en el mes"
                                    : `${a.orders_considered} orden(es) consideradas | h_no_disp=${Number(a.h_no_disp || 0).toFixed(1)} / h_total=${Number(a.h_total || 0).toFixed(1)}`;
                                  return (
                                    <span className={`availability-badge availability-${level}`} title={title}>
                                      {pct.toFixed(1)}%
                                    </span>
                                  );
                                })()
                              )}
                            </td>
                          );
                        }
                        if (col.key === "source_provider") {
                          return <td key={col.key} data-label={col.label}>{row.source_provider || "-"}</td>;
                        }
                        return <td key={col.key} data-label={col.label}>{col.getValue(row, ctx)}</td>;
                      })}
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card rendimientos-history-card">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Historial</span>
            <h3>Últimos cálculos</h3>
          </div>
          <div className="actions-row section-heading-actions">
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => setHistoryOpen((v) => !v)}
            >
              {historyOpen ? "Ocultar" : "Mostrar"}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => reloadRecentJobs()}
              disabled={recentJobsLoading}
            >
              {recentJobsLoading ? "Cargando..." : "Recargar"}
            </button>
          </div>
        </header>

        {historyOpen && (
          recentJobs.length === 0 ? (
            <p className="support-copy">No hay cálculos registrados todavía.</p>
          ) : (
            <div className="rendimientos-table-shell">
              <table className="rendimientos-table">
                <thead>
                  <tr>
                    <th>Estado</th>
                    <th>Mes</th>
                    <th>Disparado por</th>
                    <th>Inicio</th>
                    <th>Fin</th>
                    <th>Duración</th>
                    <th>Placas</th>
                    <th>Resumen / Error</th>
                  </tr>
                </thead>
                <tbody>
                  {recentJobs.map((job) => {
                    const status = job.status;
                    const statusClass = status === "done"
                      ? "status-ok"
                      : status === "error"
                        ? "status-error"
                        : status === "running"
                          ? "status-soft"
                          : "status-partial";
                    const statusLabel = status === "done"
                      ? "Listo"
                      : status === "error"
                        ? "Error"
                        : status === "running"
                          ? "Corriendo"
                          : status === "queued"
                            ? "En cola"
                            : status;
                    const startedAt = job.started_at ? new Date(job.started_at) : null;
                    const finishedAt = job.finished_at ? new Date(job.finished_at) : null;
                    const duration = (startedAt && finishedAt)
                      ? `${Math.max(1, Math.round((finishedAt - startedAt) / 1000))} s`
                      : (startedAt && !finishedAt ? "—" : "—");
                    const s = job.summary || {};
                    const totalLabel = job.total_targets > 0
                      ? `${job.processed_targets}/${job.total_targets}`
                      : (s.total ? `${s.calculated || 0}/${s.total}` : "—");
                    return (
                      <tr key={job.id}>
                        <td data-label="Estado">
                          <span className={`status-dot ${statusClass}`} title={statusLabel} />
                          <span style={{ marginLeft: 8 }}>{statusLabel}</span>
                        </td>
                        <td data-label="Mes">{formatMonthLabel(job.month)}</td>
                        <td data-label="Disparado por">
                          {job.triggered_by === "cron" ? (
                            <span className="trigger-chip trigger-chip-cron" title="Ejecucion automatica del scheduler (05:00 Colombia)">
                              <span aria-hidden="true">⏱</span> Cron
                            </span>
                          ) : (
                            <span className="trigger-chip trigger-chip-ui">Manual</span>
                          )}
                        </td>
                        <td data-label="Inicio">
                          {startedAt ? startedAt.toLocaleString("es-CO") : "—"}
                        </td>
                        <td data-label="Fin">
                          {finishedAt ? finishedAt.toLocaleString("es-CO") : "—"}
                        </td>
                        <td data-label="Duración">{duration}</td>
                        <td data-label="Placas">{totalLabel}</td>
                        <td data-label="Resumen / Error">
                          {status === "error" && job.error_message ? (
                            <span title={job.error_message} style={{ color: "var(--red)" }}>
                              {job.error_message.length > 120
                                ? job.error_message.slice(0, 120) + "…"
                                : job.error_message}
                            </span>
                          ) : status === "done" && job.summary ? (
                            <span>
                              calc {s.calculated || 0} · parcial {s.partial || 0} · sin binding {s.unbound || 0} · sin datos {s.no_data || 0} · err {s.error || 0}
                            </span>
                          ) : (
                            <span className="support-copy">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}
      </section>

      {consultOpen && (
        <div
          className="modal-overlay"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) setConsultOpen(false);
          }}
        >
          <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Consultar rendimientos">
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Calcular rendimientos</span>
                <h3>Consultar</h3>
              </div>
              <button
                type="button"
                className="icon-button modal-close-button"
                onClick={() => setConsultOpen(false)}
              >
                Cerrar
              </button>
            </header>

            <p className="support-copy modal-support-copy">
              Selecciona el rango de meses y los clientes a procesar. El cálculo correrá en segundo plano y verás el progreso en la parte superior.
            </p>

            <form
              className="register-form"
              onSubmit={(event) => {
                event.preventDefault();
                handleCalculate();
              }}
            >
              <div className="rendimientos-month-actions">
                <div className="form-field">
                  <label htmlFor="rendimientos-calc-month-from">Desde</label>
                  <input
                    id="rendimientos-calc-month-from"
                    type="month"
                    value={calcMonthFrom}
                    onChange={(event) => setCalcMonthFrom(event.target.value)}
                    disabled={calculating}
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="rendimientos-calc-month-to">Hasta</label>
                  <input
                    id="rendimientos-calc-month-to"
                    type="month"
                    value={calcMonthTo}
                    onChange={(event) => setCalcMonthTo(event.target.value)}
                    disabled={calculating}
                  />
                </div>

                <details className="client-picker" ref={pickerRef}>
                  <summary className="client-picker-summary">
                    <span className="client-picker-label">Clientes</span>
                    <span className="client-picker-value">{buildClientSelectionLabel(eligibleClients, selectedCustomerIds)}</span>
                  </summary>

                  <div className="client-picker-panel">
                    <label className="client-picker-option" key="all-artimo-clients">
                      <input
                        type="checkbox"
                        checked={selectedCustomerIds.length === 0}
                        onChange={() => setSelectedCustomerIds([])}
                      />
                      <span>
                        Todos los clientes
                        <small>
                          {eligibleClients.reduce((total, client) => total + client.readyVehicles, 0)} placas listas
                        </small>
                      </span>
                    </label>

                    {catalogLoading ? (
                      <p className="support-copy">Cargando clientes...</p>
                    ) : eligibleClients.length === 0 ? (
                      <p className="support-copy">No hay clientes registrados.</p>
                    ) : (
                      eligibleClients.map((client) => {
                        const checked = selectedCustomerIds.includes(client.id);
                        return (
                          <label className="client-picker-option" key={client.id}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                setSelectedCustomerIds((current) =>
                                  checked
                                    ? current.filter((value) => value !== client.id)
                                    : [...current, client.id].sort((a, b) => a - b)
                                );
                              }}
                            />
                            <span>
                              {client.name}
                              <small>
                                {client.readyVehicles > 0
                                  ? `${client.readyVehicles} placas listas`
                                  : client.hasPerformanceDatabase
                                    ? "Sin placas listas"
                                    : "Sin database activa"}
                              </small>
                            </span>
                          </label>
                        );
                      })
                    )}
                  </div>
                </details>
              </div>

              <label className="client-picker-option" style={{ margin: 0 }}>
                <input
                  type="checkbox"
                  checked={calcAvailability}
                  onChange={(event) => setCalcAvailability(event.target.checked)}
                />
                <span>
                  Calcular Disponibilidad
                  <small>Próximamente: incluirá el cálculo de disponibilidad de la flota.</small>
                </span>
              </label>

              <div className="actions-row modal-actions">
                <button type="submit" disabled={calculating || !calcMonthFrom || !calcMonthTo}>
                  {calculating ? "Calculando..." : "Calcular"}
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => setConsultOpen(false)}
                >
                  Cancelar
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
