import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import Can from "../components/Can";
import { SortButton } from "../components/SortButton";
import {
  fetchAvailabilityCoverage,
  fetchAvailabilityOverview,
  fetchAvailabilityRanking,
  manualAssignVehicle,
} from "../api/vehicleApi";
import { useAvailabilityDashboard } from "../features/availability/hooks/useAvailabilityDashboard";
import { exportDisponibilidadExcel } from "../utils/disponibilidadExport";

// Colores alineados con los badges .availability-* de styles.css
const STATUS_COLOR = {
  good: "#2f8c2f",
  warning: "#d18c00",
  critical: "#c52b2b",
  no_data: "#98aab4",
};

const STATUS_BADGE_CLASS = {
  good: "availability-good",
  warning: "availability-warn",
  critical: "availability-bad",
  no_data: "availability-empty",
};

const STATUS_LABEL = {
  good: "Óptima",
  warning: "Advertencia",
  critical: "Crítica",
  no_data: "Sin datos",
};

const STATUS_FILTER_LABEL = {
  good: "Óptimos",
  warning: "Advertencia",
  critical: "Críticos",
  no_data: "Sin datos",
};

const RANKING_SORT_LABEL = {
  plate: "Placa",
  customer_name: "Flota",
  availability_pct: "Disponibilidad",
  h_no_disp: "Horas no disponibles",
  mttr_hours: "MTTR",
  orders_considered: "Órdenes",
};

function sortRankingRows(rows, sort) {
  if (!sort?.key || !sort?.dir) return rows;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftValue = left.row[sort.key];
      const rightValue = right.row[sort.key];
      const leftEmpty = leftValue === null || leftValue === undefined || leftValue === "";
      const rightEmpty = rightValue === null || rightValue === undefined || rightValue === "";
      if (leftEmpty || rightEmpty) {
        if (leftEmpty && rightEmpty) return left.index - right.index;
        return leftEmpty ? 1 : -1;
      }
      const comparison = typeof leftValue === "number" && typeof rightValue === "number"
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue), "es", { numeric: true, sensitivity: "base" });
      return comparison === 0 ? left.index - right.index : sort.dir === "asc" ? comparison : -comparison;
    })
    .map(({ row }) => row);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(1)}%`;
}

function fmtMttr(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(1)} h`;
}

function fmtHours(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${Math.round(n).toLocaleString("es-CO")} h`;
}

function monthLabel(month) {
  if (!month) return "";
  const [year, mon] = month.split("-").map(Number);
  const names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  return `${names[mon - 1] || mon} ${year}`;
}

function shortMonthLabel(month) {
  if (!month) return "";
  const mon = Number(month.split("-")[1]);
  const names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  return names[mon - 1] || month;
}

function TrendMonthTick({ x, y, payload, selectedLabel }) {
  const selected = payload?.value === selectedLabel;
  return (
    <text
      x={x}
      y={y + 12}
      textAnchor="middle"
      fill={selected ? "#ee2e2f" : "#667781"}
      fontSize={11}
      fontWeight={selected ? 700 : 400}
    >
      {payload?.value}
    </text>
  );
}

export default function DisponibilidadPage() {
  const location = useLocation();
  const restoredFilters = location.state?.availabilityFilters || {};
  const {
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
    customers,
    overview,
    ranking,
    trend,
    coverage,
    mtbf,
    loadingOverview,
    loadingDetail,
    mtbfLoading,
    error,
    mtbfError,
    recalculate,
    isRecalculating,
    job,
    recalcError,
    refreshAll,
    loadMtbf,
  } = useAvailabilityDashboard(restoredFilters);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [rankingSort, setRankingSort] = useState(
    restoredFilters.rankingSort?.key && restoredFilters.rankingSort?.dir
      ? restoredFilters.rankingSort
      : { key: "availability_pct", dir: "asc" },
  );
  const [recalcOpen, setRecalcOpen] = useState(false);
  const [recalcMonth, setRecalcMonth] = useState(month);
  const [recalcCustomerIds, setRecalcCustomerIds] = useState([]);
  const [activeRecalcMonth, setActiveRecalcMonth] = useState(month);
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [mtbfOpen, setMtbfOpen] = useState(false);
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [registerItem, setRegisterItem] = useState(null);
  const [registerLoading, setRegisterLoading] = useState(false);
  const [registerError, setRegisterError] = useState("");
  const [registerSuccess, setRegisterSuccess] = useState("");

  const overall = overview?.overall ?? null;
  const fleets = overview?.fleets ?? [];

  const selectedFleet = useMemo(() => {
    const fleet = fleets.find((f) => f.customer_id === selectedCustomerId);
    if (fleet) return fleet;
    const customer = customers.find((c) => c.id === selectedCustomerId);
    return customer ? { customer_id: customer.id, customer_name: customer.name } : null;
  }, [fleets, customers, selectedCustomerId]);

  const scopedAvailability = selectedCustomerId ? selectedFleet : overall;

  const noDataCount = useMemo(() => {
    const b = overall?.status_breakdown || {};
    return (b.not_in_cloudfleet || 0) + (b.error || 0);
  }, [overall]);

  const prevPct = useMemo(() => {
    if (!trend?.labels?.length || !Array.isArray(trend.availability_pct)) return null;
    const selectedIndex = trend.labels.indexOf(month);
    if (selectedIndex <= 0) return null;
    return trend.availability_pct[selectedIndex - 1] ?? null;
  }, [trend, month]);

  const previousTrendMonth = useMemo(() => {
    const selectedIndex = trend?.labels?.indexOf(month) ?? -1;
    return selectedIndex > 0 ? trend.labels[selectedIndex - 1] : null;
  }, [trend, month]);

  const availabilityDelta = useMemo(() => {
    const current = scopedAvailability?.availability_pct;
    if (current === null || current === undefined || prevPct === null || prevPct === undefined) return null;
    return Number(current) - Number(prevPct);
  }, [scopedAvailability?.availability_pct, prevPct]);

  const fleetChartData = useMemo(
    () =>
      fleets
        .filter((f) => f.availability_pct !== null)
        .map((f) => ({
          name: f.customer_name,
          customer_id: f.customer_id,
          pct: f.availability_pct,
          status: f.status,
          vehicles: f.vehicle_count,
          mttr_hours: f.mttr_hours,
        })),
    [fleets],
  );

  const trendData = useMemo(() => {
    if (!trend) return [];
    return trend.labels.map((label, idx) => ({
      month: label,
      label: monthLabel(label),
      pct: trend.availability_pct[idx],
    }));
  }, [trend]);

  const distributionData = useMemo(() => {
    const source = selectedCustomerId
      ? selectedFleet?.availability_breakdown
      : overall?.availability_breakdown;
    return [
      { key: "good", name: "Óptimos", value: source?.good || 0, fill: STATUS_COLOR.good },
      { key: "warning", name: "Advertencia", value: source?.warning || 0, fill: STATUS_COLOR.warning },
      { key: "critical", name: "Críticos", value: source?.critical || 0, fill: STATUS_COLOR.critical },
      { key: "no_data", name: "Sin datos", value: source?.no_data || 0, fill: STATUS_COLOR.no_data },
    ];
  }, [overall?.availability_breakdown, selectedCustomerId, selectedFleet]);

  const distributionTotal = useMemo(
    () => distributionData.reduce((total, item) => total + item.value, 0),
    [distributionData],
  );

  const sortedRanking = useMemo(
    () => sortRankingRows(ranking, rankingSort),
    [ranking, rankingSort],
  );

  const handleFleetClick = (data) => {
    const cid = data?.customer_id ?? data?.payload?.customer_id;
    if (!cid) return;
    setSelectedCustomerId((prev) => (prev === cid ? null : cid));
  };

  const handleAvailabilityStatusClick = (status) => {
    if (!status) return;
    setAvailabilityStatusFilter((current) => (current === status ? null : status));
  };

  const handleTrendClick = (state) => {
    const selectedMonth =
      state?.activePayload?.[0]?.payload?.month ||
      trendData.find((point) => point.label === state?.activeLabel)?.month;
    if (selectedMonth && selectedMonth !== month) setMonth(selectedMonth);
  };

  const handleMonthFromChange = (value) => {
    if (!value) return;
    setMonthFrom(value);
    if (value > monthTo) setMonthTo(value);
    if (month < value || month > monthTo) setMonth(value);
  };

  const handleMonthToChange = (value) => {
    if (!value) return;
    const nextFrom = value < monthFrom ? value : monthFrom;
    setMonthFrom(nextFrom);
    setMonthTo(value);
    setMonth(value);
  };

  const openRecalcModal = () => {
    setRecalcMonth(month);
    setRecalcCustomerIds([]);
    setRecalcOpen(true);
  };

  const handleRecalculate = async (event) => {
    event.preventDefault();
    setActiveRecalcMonth(recalcMonth);
    const started = await recalculate({ month: recalcMonth, customerIds: recalcCustomerIds });
    if (started) setRecalcOpen(false);
  };

  const toggleRecalcCustomer = (customerId) => {
    setRecalcCustomerIds((current) =>
      current.includes(customerId)
        ? current.filter((id) => id !== customerId)
        : [...current, customerId],
    );
  };

  const jobProgress = job && typeof job.progress_pct === "number" ? Math.round(job.progress_pct) : null;

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setExportError("");
    try {
      const [exportOverview, exportRanking, exportCoverage] = await Promise.all([
        fetchAvailabilityOverview({ month, customer_id: selectedCustomerId }),
        fetchAvailabilityRanking({
          month,
          customer_id: selectedCustomerId,
          limit: 5000,
          order: "worst",
          include_no_orders: includeNoOrders,
          plate_search: plateSearch,
          availability_status: availabilityStatusFilter,
        }),
        fetchAvailabilityCoverage({ month, customer_id: selectedCustomerId }),
      ]);
      await exportDisponibilidadExcel({
        month,
        monthFrom,
        monthTo,
        overview: exportOverview,
        ranking: sortRankingRows(exportRanking, rankingSort),
        trend,
        coverage: exportCoverage,
        filters: {
          fleetName: selectedFleet?.customer_name || "Todas las flotas",
          plateSearch: plateSearch.trim(),
          sortLabel: rankingSort.key
            ? `${RANKING_SORT_LABEL[rankingSort.key]} (${rankingSort.dir === "asc" ? "ascendente" : "descendente"})`
            : "Orden predeterminado",
          includeNoOrders,
          availabilityStatusLabel: availabilityStatusFilter
            ? STATUS_FILTER_LABEL[availabilityStatusFilter]
            : "Todos",
        },
      });
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "No fue posible exportar la disponibilidad");
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    if (!registerSuccess) return;
    const timer = setTimeout(() => setRegisterSuccess(""), 5000);
    return () => clearTimeout(timer);
  }, [registerSuccess]);

  const handleOpenRegister = useCallback((item) => {
    setRegisterItem(item);
    setRegisterError("");
    setRegisterSuccess("");
    setRegisterModalOpen(true);
  }, []);

  const handleCloseRegister = useCallback(() => {
    setRegisterModalOpen(false);
    setRegisterItem(null);
    setRegisterError("");
  }, []);

  const handleRegisterSubmit = useCallback(
    async ({ plate, technical_number, nombre_vehiculo, marca, linea }) => {
      const normalizedPlate = plate.trim().toUpperCase();
      const normalizedTechnical = technical_number.trim();
      if (!normalizedPlate || !normalizedTechnical) return;

      setRegisterLoading(true);
      setRegisterError("");
      try {
        await manualAssignVehicle(normalizedPlate, {
          technical_number: normalizedTechnical,
          nombre_vehiculo: nombre_vehiculo.trim() || null,
          marca: marca.trim() || null,
          linea: linea.trim() || null,
          geotab_status: "unknown",
        });
        setRegisterSuccess(`Vehiculo ${normalizedPlate} registrado correctamente.`);
        setRegisterModalOpen(false);
        setRegisterItem(null);
        refreshAll();
      } catch (err) {
        setRegisterError(err instanceof Error ? err.message : "No fue posible registrar el vehiculo");
      } finally {
        setRegisterLoading(false);
      }
    },
    [refreshAll]
  );

  const coverageSummary = coverage?.summary || {};
  const coverageFleets = Array.isArray(coverage?.fleets) ? coverage.fleets : [];
  const coveragePlates = Array.isArray(coverage?.uncovered_plates) ? coverage.uncovered_plates : [];
  const cloudfleetUnmatched = Array.isArray(coverage?.cloudfleet_unmatched) ? coverage.cloudfleet_unmatched : [];
  const cloudfleetOnlyCount = cloudfleetUnmatched.length;
  const coverageByFleet = useMemo(() => {
    const map = new Map();
    for (const p of coveragePlates) {
      const key = p.customer_id ?? p.customer_name ?? "Sin flota";
      const entry = map.get(key) || { customer_id: p.customer_id, customer_name: p.customer_name || "Sin flota", plates: [] };
      entry.plates.push(p.plate);
      map.set(key, entry);
    }
    return [...map.values()];
  }, [coveragePlates]);

  const hasCoverageData = coverageSummary.total > 0 || coverageFleets.length > 0;

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Operación de flotas</span>
          <h2>Disponibilidad</h2>
        </div>

        <div className="disp-toolbar">
          <button
            type="button"
            className="button-secondary"
            onClick={handleExport}
            disabled={loadingOverview || loadingDetail || exporting}
            title="Exportar disponibilidad a Excel"
          >
            {exporting ? "Exportando…" : "Exportar"}
          </button>
          <Can permission="rendimientos.refresh">
            <button
              type="button"
              onClick={openRecalcModal}
              disabled={isRecalculating}
              title="Configurar un nuevo recálculo"
            >
              {isRecalculating
                ? `Recalculando${jobProgress !== null ? ` ${jobProgress}%` : "..."}`
                : "Recalcular"}
            </button>
          </Can>
        </div>
      </header>

      <div className="disp-filters">
        <label className="disp-field">
          <span>Mes desde</span>
          <input type="month" value={monthFrom} max={monthTo} onChange={(e) => handleMonthFromChange(e.target.value)} />
        </label>
        <label className="disp-field">
          <span>Mes hasta</span>
          <input type="month" value={monthTo} min={monthFrom} onChange={(e) => handleMonthToChange(e.target.value)} />
        </label>
        <label className="disp-field">
          <span>Flota</span>
          <select
            value={selectedCustomerId ?? ""}
            onChange={(e) => setSelectedCustomerId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Todas las flotas</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="disp-field">
          <span>Placa</span>
          <input
            type="text"
            placeholder="Buscar placa…"
            value={plateSearch}
            onChange={(e) => setPlateSearch(e.target.value)}
          />
        </label>
        {availabilityStatusFilter ? (
          <button
            type="button"
            className="button-secondary button-sm disp-active-filter"
            onClick={() => setAvailabilityStatusFilter(null)}
            title="Quitar filtro de estado"
          >
            Estado: {STATUS_FILTER_LABEL[availabilityStatusFilter]} ×
          </button>
        ) : null}
        {selectedCustomerId || plateSearch || availabilityStatusFilter ? (
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={() => {
              setSelectedCustomerId(null);
              setPlateSearch("");
              setAvailabilityStatusFilter(null);
            }}
          >
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}
      {exportError ? <div className="notice-banner notice-error">{exportError}</div> : null}
      {recalcError ? <div className="notice-banner notice-error">{recalcError}</div> : null}
      {registerSuccess ? <div className="notice-banner notice-info">{registerSuccess}</div> : null}
      {isRecalculating ? (
        <div className="notice-banner notice-soft">
          Recalculando disponibilidad para {monthLabel(activeRecalcMonth)}. El panel se actualizará al terminar.
        </div>
      ) : null}

      {/* KPIs */}
      <div className="disp-kpi-grid">
        <article className="card metric-card">
          <span className="eyebrow">
            {selectedFleet ? "Disponibilidad de flota" : "Disponibilidad global"}
          </span>
          <strong style={{ color: STATUS_COLOR[scopedAvailability?.status || "no_data"] }}>
            {loadingOverview ? "…" : fmtPct(scopedAvailability?.availability_pct)}
          </strong>
          <p>
            {monthLabel(month)}
            {availabilityDelta !== null && (
              <span
                className="disp-kpi-delta"
                style={{
                  color:
                    Math.abs(availabilityDelta) < 0.05
                      ? STATUS_COLOR.no_data
                      : availabilityDelta > 0
                        ? STATUS_COLOR.good
                        : STATUS_COLOR.critical,
                }}
              >
                {availabilityDelta > 0 ? "▲" : availabilityDelta < 0 ? "▼" : "●"}{" "}
                {`${availabilityDelta > 0 ? "+" : ""}${availabilityDelta.toFixed(1)} pts vs ${shortMonthLabel(previousTrendMonth)}`}
              </span>
            )}
          </p>
        </article>
        <article className="card metric-card">
          <span className="eyebrow">MTTR del mes</span>
          <strong style={{ color: STATUS_COLOR[overall?.mttr_status || "no_data"] }}>
            {loadingOverview ? "…" : fmtMttr(overall?.mttr_hours)}
          </strong>
          <p>
            {overall?.orders_closed ?? "—"} órdenes cerradas · meta ≤24 h
          </p>
        </article>
        <article className="card metric-card">
          <span className="eyebrow">Horas no disponibles</span>
          <strong>{loadingOverview ? "…" : fmtHours(overall?.h_no_disp)}</strong>
          <p>acumuladas en {monthLabel(month)}</p>
        </article>
        <article className="card metric-card">
          <span className="eyebrow">Vehículos cubiertos</span>
          <strong>{loadingOverview ? "…" : overall?.vehicle_count ?? 0}</strong>
          <p>en {overall?.fleet_count ?? 0} flotas</p>
        </article>
        <article className="card metric-card">
          <span className="eyebrow">Flotas críticas</span>
          <strong style={{ color: overall?.critical_fleets ? STATUS_COLOR.critical : undefined }}>
            {loadingOverview ? "…" : overall?.critical_fleets ?? 0}
          </strong>
          <p>{`< ${96}% disponibilidad`}</p>
        </article>
        <article className="card metric-card">
          <span className="eyebrow">Sin datos</span>
          <strong>{loadingOverview ? "…" : noDataCount}</strong>
          <p>no en CloudFleet o con error</p>
        </article>
      </div>

      {/* Charts */}
      <div className="disp-charts-grid disp-charts-grid--primary">
        <article className="card disp-chart-card disp-gauge-card">
          <div className="disp-chart-head">
            <span className="eyebrow">Distribución del mes</span>
            <small className="disp-hint">
              Clic para filtrar · {selectedFleet ? selectedFleet.customer_name : "Global"}
            </small>
          </div>
          <div className="disp-distribution-wrap">
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={distributionData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={58}
                  outerRadius={82}
                  paddingAngle={distributionTotal ? 2 : 0}
                  stroke="none"
                  onClick={(entry) => handleAvailabilityStatusClick(entry?.key ?? entry?.payload?.key)}
                  style={{ cursor: "pointer" }}
                >
                  {distributionData.map((entry) => (
                    <Cell
                      key={entry.key}
                      fill={entry.fill}
                      fillOpacity={availabilityStatusFilter && availabilityStatusFilter !== entry.key ? 0.25 : 1}
                      stroke={availabilityStatusFilter === entry.key ? "#354550" : "none"}
                      strokeWidth={availabilityStatusFilter === entry.key ? 2 : 0}
                    />
                  ))}
                </Pie>
                <Tooltip formatter={(value, name) => [`${value} vehículos`, name]} />
              </PieChart>
            </ResponsiveContainer>
            <div className="disp-distribution-center">
              <strong>{distributionTotal}</strong>
              <span>vehículos</span>
            </div>
          </div>
          <div className="disp-distribution-legend">
            {distributionData.map((item) => (
              <button
                type="button"
                key={item.key}
                className={`disp-distribution-item${availabilityStatusFilter === item.key ? " is-active" : ""}`}
                onClick={() => handleAvailabilityStatusClick(item.key)}
                aria-pressed={availabilityStatusFilter === item.key}
              >
                <i style={{ background: item.fill }} />
                <span>{item.name}</span>
                <strong>{item.value}</strong>
              </button>
            ))}
          </div>
        </article>

        <article className="card disp-chart-card disp-trend-card">
          <div className="disp-chart-head">
            <span className="eyebrow">Tendencia</span>
            <small className="disp-hint">
              Clic en un mes · Activo: {monthLabel(month)} · {selectedFleet ? selectedFleet.customer_name : "Global"}
            </small>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart
              data={trendData}
              margin={{ left: 16, right: 38, top: 26, bottom: 12 }}
              onClick={handleTrendClick}
              style={{ cursor: "pointer" }}
            >
              <CartesianGrid stroke="rgba(53,69,80,0.08)" />
              <XAxis
                dataKey="label"
                fontSize={11}
                height={34}
                tickMargin={10}
                padding={{ left: 10, right: 10 }}
                tick={(props) => (
                  <TrendMonthTick {...props} selectedLabel={monthLabel(month)} />
                )}
              />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} width={46} tickMargin={6} />
              <Tooltip formatter={(value) => (value === null ? "Sin datos" : `${Number(value).toFixed(1)}%`)} />
              <ReferenceLine
                x={monthLabel(month)}
                stroke="#ee2e2f"
                strokeWidth={10}
                strokeOpacity={0.08}
              />
              <ReferenceLine y={96} stroke="#354550" strokeDasharray="4 4" label={{ value: "Meta 96%", position: "insideTopRight", fontSize: 10, fill: "#354550" }} />
              <Line
                type="monotone"
                dataKey="pct"
                stroke="#ee2e2f"
                strokeWidth={2.5}
                dot={(props) => (
                  <circle
                    cx={props.cx}
                    cy={props.cy}
                    r={props.payload.month === month ? 6 : 3.5}
                    fill={props.payload.month === month ? "#ee2e2f" : "#fff"}
                    stroke="#ee2e2f"
                    strokeWidth={2}
                  />
                )}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </article>
      </div>

      <div className="disp-detail-grid">
        <article className="card disp-chart-card">
          <div className="disp-chart-head">
            <span className="eyebrow">Disponibilidad por flota</span>
            <small className="disp-hint">Clic para combinar este filtro</small>
          </div>
          {fleetChartData.length === 0 ? (
            <p className="disp-empty">Sin datos de flotas para {monthLabel(month)}.</p>
          ) : (
            <ResponsiveContainer width="100%" height={Math.max(240, fleetChartData.length * 34)}>
              <BarChart data={fleetChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid horizontal={false} stroke="rgba(53,69,80,0.08)" />
                <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} />
                <YAxis type="category" dataKey="name" width={110} fontSize={11} tickFormatter={(v) => (v.length > 16 ? `${v.slice(0, 15)}…` : v)} />
                <Tooltip
                  formatter={(value, _n, p) => {
                    const mttr = p.payload.mttr_hours;
                    const mttrText = mttr === null || mttr === undefined ? "" : ` · MTTR ${fmtMttr(mttr)}`;
                    return [`${Number(value).toFixed(1)}% · ${p.payload.vehicles} veh.${mttrText}`, ""];
                  }}
                  cursor={{ fill: "rgba(53,69,80,0.05)" }}
                />
                <ReferenceLine x={97} stroke="#354550" strokeDasharray="4 4" label={{ value: "Meta 97%", position: "top", fontSize: 10, fill: "#354550" }} />
                <Bar dataKey="pct" radius={[0, 6, 6, 0]} onClick={handleFleetClick} cursor="pointer">
                  {fleetChartData.map((entry) => (
                    <Cell key={entry.customer_id ?? entry.name} fill={STATUS_COLOR[entry.status] || STATUS_COLOR.no_data} fillOpacity={selectedCustomerId && selectedCustomerId !== entry.customer_id ? 0.35 : 1} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
          <div className="disp-scale" aria-label="Escala de disponibilidad">
            <span><i style={{ background: STATUS_COLOR.critical }} />Crítica &lt; 96%</span>
            <span><i style={{ background: STATUS_COLOR.warning }} />Advertencia 96–96.9%</span>
            <span><i style={{ background: STATUS_COLOR.good }} />Óptima ≥ 97%</span>
          </div>
        </article>

      {/* Ranking */}
      <article className="card disp-ranking-card">
        <div className="disp-chart-head">
          <div>
            <span className="eyebrow">Ranking de vehículos</span>
            <h3 className="disp-ranking-title">
              Vehículos{selectedFleet ? ` · ${selectedFleet.customer_name}` : ""}
              {availabilityStatusFilter ? ` · ${STATUS_FILTER_LABEL[availabilityStatusFilter]}` : ""}
            </h3>
          </div>
          <div className="disp-ranking-actions">
            <label className="disp-checkbox">
              <input
                type="checkbox"
                checked={includeNoOrders}
                onChange={(e) => setIncludeNoOrders(e.target.checked)}
              />
              <span>Incluir sin órdenes</span>
            </label>
            {selectedCustomerId ? (
              <button type="button" className="button-secondary button-sm" onClick={() => setSelectedCustomerId(null)}>
                Ver todas
              </button>
            ) : null}
          </div>
        </div>

        <div className="vehicles-table-shell disp-ranking-table-shell">
          <table className="vehicles-table">
            <thead>
              <tr>
                <th>#</th>
                {[
                  ["plate", "Placa"],
                  ["customer_name", "Flota"],
                  ["availability_pct", "Disponibilidad"],
                  ["h_no_disp", "Horas no disp."],
                  ["mttr_hours", "MTTR"],
                  ["orders_considered", "Órdenes"],
                ].map(([key, label]) => (
                  <th key={key}>
                    <div className="th-content">
                      <span>{label}</span>
                      <SortButton columnKey={key} currentSort={rankingSort} onSortChange={setRankingSort} />
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loadingDetail ? (
                <tr>
                  <td className="table-empty-row" colSpan={7}>Cargando…</td>
                </tr>
              ) : ranking.length === 0 ? (
                <tr>
                  <td className="table-empty-row" colSpan={7}>
                    Sin vehículos calculados para {monthLabel(month)}.
                  </td>
                </tr>
              ) : (
                sortedRanking.map((v, idx) => (
                  <tr key={v.plate}>
                    <td>{idx + 1}</td>
                    <td>
                      <Link
                        to={`/vehiculo/${v.plate}`}
                        className="ficha-plate-link"
                        state={{
                          returnTo: "/disponibilidad",
                          returnLabel: "Disponibilidad",
                          availabilityFilters: {
                            month,
                            monthFrom,
                            monthTo,
                            selectedCustomerId,
                            plateSearch,
                            availabilityStatusFilter,
                            includeNoOrders,
                            rankingSort,
                          },
                        }}
                      >
                        <strong>{v.plate}</strong>
                      </Link>
                    </td>
                    <td>{v.customer_name}</td>
                    <td>
                      <span className={`availability-badge ${STATUS_BADGE_CLASS[v.status]}`}>
                        {fmtPct(v.availability_pct)}
                      </span>
                    </td>
                    <td>{v.availability_pct === null ? "—" : `${Number(v.h_no_disp).toFixed(1)} h`}</td>
                    <td>{fmtMttr(v.mttr_hours)}</td>
                    <td>{v.orders_considered}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </article>
      </div>

      {/* Cobertura CloudFleet */}
      <article className="card disp-coverage-card">
        <button
          type="button"
          className="button-block disp-coverage-header"
          onClick={() => setCoverageOpen((open) => !open)}
          aria-expanded={coverageOpen}
        >
          <div>
            <span className="eyebrow">Cobertura CloudFleet</span>
            <h3 className="disp-ranking-title">
              {hasCoverageData
                ? `${coverageSummary.covered ?? 0} de ${coverageSummary.total ?? 0} placas con datos (${fmtPct(coverageSummary.coverage_pct)})${cloudfleetOnlyCount > 0 ? ` · ${cloudfleetOnlyCount} solo en CloudFleet` : ""}`
                : "Sin información de cobertura"}
            </h3>
          </div>
          <span className="button-secondary button-sm">
            {coverageOpen ? "Ocultar" : "Ver detalle"}
          </span>
        </button>

        {coverageOpen && (
          <div className="disp-coverage-body">
            {!hasCoverageData ? (
              <p className="disp-empty">No hay datos de cobertura para {monthLabel(month)}.</p>
            ) : (
              <>
                <div className="vehicles-table-shell">
                  <table className="vehicles-table">
                    <thead>
                      <tr>
                        <th>Flota</th>
                        <th>Placas</th>
                        <th>Sin cobertura</th>
                        <th>Cobertura %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {coverageFleets.length === 0 ? (
                        <tr>
                          <td className="table-empty-row" colSpan={4}>Sin datos por flota.</td>
                        </tr>
                      ) : (
                        coverageFleets.map((f) => (
                          <tr key={f.customer_id ?? f.customer_name}>
                            <td>{f.customer_name}</td>
                            <td>{f.total}</td>
                            <td>{f.uncovered}</td>
                            <td>{fmtPct(f.coverage_pct)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>

                {coverageByFleet.length > 0 && (
                  <div className="disp-coverage-plates">
                    <h4 className="disp-coverage-subtitle">Placas sin cobertura</h4>
                    {coverageByFleet.map((group) => (
                      <div key={group.customer_id ?? group.customer_name} className="disp-coverage-group">
                        <span className="disp-coverage-fleet">{group.customer_name}</span>
                        <div className="disp-coverage-chips">
                          {group.plates.map((plate) => (
                            <span key={plate} className="disp-coverage-chip">{plate}</span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {cloudfleetUnmatched.length > 0 && (
                  <div className="disp-coverage-plates">
                    <h4 className="disp-coverage-subtitle">
                      En CloudFleet sin registrar localmente ({cloudfleetUnmatched.length})
                    </h4>
                    <div className="vehicles-table-shell">
                      <table className="vehicles-table">
                        <thead>
                          <tr>
                            <th>Código</th>
                            <th>Cost center</th>
                            <th>Acciones</th>
                          </tr>
                        </thead>
                        <tbody>
                          {cloudfleetUnmatched.map((item) => (
                            <tr key={item.code}>
                              <td>{item.code}</td>
                              <td>{item.cost_center || "—"}</td>
                              <td>
                                <Can permission="vehicles.edit">
                                  <button
                                    type="button"
                                    className="button-secondary button-sm"
                                    onClick={() => handleOpenRegister(item)}
                                  >
                                    Registrar
                                  </button>
                                </Can>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </article>

      {/* MTBF del año */}
      <article className="card disp-coverage-card">
        <button
          type="button"
          className="button-block disp-coverage-header"
          onClick={() => setMtbfOpen((open) => !open)}
          aria-expanded={mtbfOpen}
        >
          <div>
            <span className="eyebrow">MTBF del año</span>
            <h3 className="disp-ranking-title">
              {mtbfLoading
                ? "Calculando MTBF…"
                : mtbf?.mtbf_hours !== null && mtbf?.mtbf_hours !== undefined
                  ? `${fmtHours(mtbf.mtbf_hours)} · ${STATUS_LABEL[mtbf.status || "no_data"].toLowerCase()}`
                  : "MTBF del año en curso"}
            </h3>
          </div>
          <span className="button-secondary button-sm">
            {mtbfOpen ? "Ocultar" : "Ver detalle"}
          </span>
        </button>

        {mtbfOpen && (
          <div className="disp-coverage-body">
            {mtbfError ? <div className="notice-banner notice-error">{mtbfError}</div> : null}

            {!mtbf || mtbfLoading ? (
              <div className="disp-empty">
                <p>Consulta el año completo en CloudFleet para calcular el MTBF.</p>
                <p className="disp-hint">La primera vez puede tardar ~1-2 min descargando todas las órdenes.</p>
                <button
                  type="button"
                  onClick={() => loadMtbf(false)}
                  disabled={mtbfLoading}
                >
                  {mtbfLoading ? "Calculando…" : "Calcular MTBF"}
                </button>
              </div>
            ) : (
              <>
                <div className="disp-gauge-center" style={{ margin: "1rem 0" }}>
                  <span
                    className="disp-gauge-value"
                    style={{ color: STATUS_COLOR[mtbf.status || "no_data"] }}
                  >
                    {fmtHours(mtbf.mtbf_hours)}
                  </span>
                  <span className={`availability-badge ${STATUS_BADGE_CLASS[mtbf.status || "no_data"]}`}>
                    {STATUS_LABEL[mtbf.status || "no_data"]}
                  </span>
                  <span className="disp-gauge-target">Meta: 500 h</span>
                </div>
                <p className="disp-hint" style={{ textAlign: "center" }}>
                  {mtbf.intervals_count} intervalos · {mtbf.vehicles_considered} vehículos con ≥2 fallas
                </p>

                <div className="vehicles-table-shell">
                  <table className="vehicles-table">
                    <thead>
                      <tr>
                        <th>Flota</th>
                        <th>MTBF h</th>
                        <th>Fallas</th>
                        <th>Vehículos</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Array.isArray(mtbf.fleets) && mtbf.fleets.length === 0 ? (
                        <tr>
                          <td className="table-empty-row" colSpan={4}>
                            Sin flotas con intervalos calculables.
                          </td>
                        </tr>
                      ) : (
                        (mtbf.fleets || []).map((f) => (
                          <tr key={f.customer_id ?? f.customer_name}>
                            <td>{f.customer_name}</td>
                            <td>
                              <span style={{ color: STATUS_COLOR[f.status || "no_data"] }}>
                                {fmtHours(f.mtbf_hours)}
                              </span>
                            </td>
                            <td>{f.failures}</td>
                            <td>{f.vehicles_with_failures}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="disp-ranking-actions" style={{ marginTop: "1rem" }}>
                  <button
                    type="button"
                    className="button-secondary button-sm"
                    onClick={() => loadMtbf(true)}
                    disabled={mtbfLoading}
                  >
                    {mtbfLoading ? "Actualizando…" : "Actualizar"}
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </article>

      {recalcOpen && (
        <div
          className="modal-overlay"
          role="presentation"
          onClick={(event) => {
            if (event.target === event.currentTarget) setRecalcOpen(false);
          }}
        >
          <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Recalcular disponibilidad">
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Procesar CloudFleet</span>
                <h3>Recalcular disponibilidad</h3>
              </div>
              <button type="button" className="icon-button modal-close-button" onClick={() => setRecalcOpen(false)}>
                Cerrar
              </button>
            </header>

            <p className="support-copy modal-support-copy">
              Este periodo y estas flotas solo aplican al recálculo; no cambiarán los filtros del tablero.
            </p>

            <form className="register-form" onSubmit={handleRecalculate}>
              {recalcError ? <div className="notice-banner notice-error">{recalcError}</div> : null}
              <div className="form-field">
                <label htmlFor="availability-recalc-month">Mes a recalcular</label>
                <input
                  id="availability-recalc-month"
                  type="month"
                  value={recalcMonth}
                  onChange={(event) => setRecalcMonth(event.target.value)}
                  required
                  autoFocus
                />
              </div>

              <fieldset className="disp-recalc-scope">
                <legend>Flotas</legend>
                <label className="client-picker-option">
                  <input
                    type="checkbox"
                    checked={recalcCustomerIds.length === 0}
                    onChange={() => setRecalcCustomerIds([])}
                  />
                  <span>
                    Todas las flotas
                    <small>Procesa la disponibilidad completa del mes.</small>
                  </span>
                </label>
                <div className="disp-recalc-customers">
                  {customers.map((customer) => (
                    <label className="client-picker-option" key={customer.id}>
                      <input
                        type="checkbox"
                        checked={recalcCustomerIds.includes(customer.id)}
                        onChange={() => toggleRecalcCustomer(customer.id)}
                      />
                      <span>{customer.name}</span>
                    </label>
                  ))}
                </div>
              </fieldset>

              <div className="actions-row modal-actions">
                <button type="submit" disabled={isRecalculating || !recalcMonth}>
                  {isRecalculating ? "Recalculando…" : "Iniciar recálculo"}
                </button>
                <button type="button" className="button-secondary" onClick={() => setRecalcOpen(false)}>
                  Cancelar
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {registerModalOpen && registerItem && (
        <RegisterUnmatchedModal
          item={registerItem}
          loading={registerLoading}
          error={registerError}
          onClose={handleCloseRegister}
          onSubmit={handleRegisterSubmit}
        />
      )}
    </section>
  );
}

function RegisterUnmatchedModal({ item, loading, error, onClose, onSubmit }) {
  const [plate, setPlate] = useState((item?.code || "").toUpperCase());
  const [technicalNumber, setTechnicalNumber] = useState("");
  const [nombreVehiculo, setNombreVehiculo] = useState("");
  const [marca, setMarca] = useState("");
  const [linea, setLinea] = useState("");

  const handleSubmit = (event) => {
    event.preventDefault();
    onSubmit({
      plate,
      technical_number: technicalNumber,
      nombre_vehiculo: nombreVehiculo,
      marca,
      linea,
    });
  };

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="card modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Registrar vehiculo local"
      >
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">CloudFleet</span>
            <h3>Registrar vehiculo local</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          {error ? <div className="notice-banner notice-error">{error}</div> : null}

          <div className="form-field">
            <label htmlFor="register-plate">Placa</label>
            <input
              id="register-plate"
              value={plate}
              onChange={(event) => setPlate(event.target.value.toUpperCase())}
              placeholder="Ej: ABC123"
              required
              autoFocus
            />
          </div>

          <div className="form-field">
            <label htmlFor="register-technical-number">TEC#</label>
            <input
              id="register-technical-number"
              value={technicalNumber}
              onChange={(event) => setTechnicalNumber(event.target.value)}
              placeholder="Technical Engine Configuration #"
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="register-nombre">
              Nombre vehiculo <span className="form-optional">(opcional)</span>
            </label>
            <input
              id="register-nombre"
              value={nombreVehiculo}
              onChange={(event) => setNombreVehiculo(event.target.value)}
              placeholder="Nombre del vehiculo"
            />
          </div>

          <div className="form-field">
            <label htmlFor="register-marca">
              Marca <span className="form-optional">(opcional)</span>
            </label>
            <input
              id="register-marca"
              value={marca}
              onChange={(event) => setMarca(event.target.value)}
              placeholder="Marca"
            />
          </div>

          <div className="form-field">
            <label htmlFor="register-linea">
              Linea <span className="form-optional">(opcional)</span>
            </label>
            <input
              id="register-linea"
              value={linea}
              onChange={(event) => setLinea(event.target.value)}
              placeholder="Linea"
            />
          </div>

          <p className="support-copy">
            El vehiculo quedara en el maestro local sin cliente asignado; asigna cliente y database
            desde Vehiculos.
          </p>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !plate.trim() || !technicalNumber.trim()}>
              {loading ? "Registrando…" : "Registrar"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
