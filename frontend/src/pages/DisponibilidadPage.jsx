import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  PolarAngleAxis,
  RadialBar,
  RadialBarChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import Can from "../components/Can";
import { useAvailabilityDashboard } from "../features/availability/hooks/useAvailabilityDashboard";

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

const BREAKDOWN_LABEL = {
  calculated: "Calculados",
  no_orders: "Sin órdenes",
  not_in_cloudfleet: "No en CloudFleet",
  error: "Error",
};

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

function GaugeTooltip() {
  return null;
}

export default function DisponibilidadPage() {
  const {
    month,
    setMonth,
    selectedCustomerId,
    setSelectedCustomerId,
    rankingOrder,
    setRankingOrder,
    includeNoOrders,
    setIncludeNoOrders,
    customers,
    overview,
    ranking,
    trend,
    loadingOverview,
    loadingDetail,
    error,
    recalculate,
    isRecalculating,
    job,
    recalcError,
  } = useAvailabilityDashboard();

  const overall = overview?.overall ?? null;
  const fleets = overview?.fleets ?? [];

  const selectedFleet = useMemo(
    () => fleets.find((f) => f.customer_id === selectedCustomerId) || null,
    [fleets, selectedCustomerId],
  );

  const noDataCount = useMemo(() => {
    const b = overall?.status_breakdown || {};
    return (b.not_in_cloudfleet || 0) + (b.error || 0);
  }, [overall]);

  const prevPct = useMemo(() => {
    if (!trend?.labels?.length || !Array.isArray(trend.availability_pct)) return null;
    const idx = trend.labels.length - 2;
    if (idx < 0) return null;
    return trend.availability_pct[idx] ?? null;
  }, [trend]);

  const availabilityDelta = useMemo(() => {
    const current = overall?.availability_pct;
    if (current === null || current === undefined || prevPct === null || prevPct === undefined) return null;
    return Number(current) - Number(prevPct);
  }, [overall?.availability_pct, prevPct]);

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
      label: monthLabel(label),
      pct: trend.availability_pct[idx],
    }));
  }, [trend]);

  const gaugeData = useMemo(() => {
    const pct = overall?.availability_pct;
    return [{ name: "disp", value: pct ?? 0, fill: STATUS_COLOR[overall?.status || "no_data"] }];
  }, [overall]);

  const handleFleetClick = (data) => {
    const cid = data?.customer_id ?? data?.payload?.customer_id;
    if (!cid) return;
    setSelectedCustomerId((prev) => (prev === cid ? null : cid));
  };

  const jobProgress = job && typeof job.progress_pct === "number" ? Math.round(job.progress_pct) : null;

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Operación de flotas</span>
          <h2>Disponibilidad</h2>
        </div>

        <div className="disp-toolbar">
          <label className="disp-field">
            <span>Mes</span>
            <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
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
          <Can permission="rendimientos.refresh">
            <button type="button" onClick={recalculate} disabled={isRecalculating}>
              {isRecalculating
                ? `Recalculando${jobProgress !== null ? ` ${jobProgress}%` : "..."}`
                : "Recalcular"}
            </button>
          </Can>
        </div>
      </header>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}
      {recalcError ? <div className="notice-banner notice-error">{recalcError}</div> : null}
      {isRecalculating ? (
        <div className="notice-banner notice-soft">
          Recalculando disponibilidad{selectedFleet ? ` de ${selectedFleet.customer_name}` : ""} para{" "}
          {monthLabel(month)}. El panel se actualizará al terminar.
        </div>
      ) : null}

      {/* KPIs */}
      <div className="disp-kpi-grid">
        <article className="card metric-card">
          <span className="eyebrow">Disponibilidad global</span>
          <strong style={{ color: STATUS_COLOR[overall?.status || "no_data"] }}>
            {loadingOverview ? "…" : fmtPct(overall?.availability_pct)}
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
                {`${availabilityDelta > 0 ? "+" : ""}${availabilityDelta.toFixed(1)} pts vs ${shortMonthLabel(trend?.labels?.[trend.labels.length - 2])}`}
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
      <div className="disp-charts-grid">
        <article className="card disp-chart-card disp-gauge-card">
          <span className="eyebrow">Disponibilidad del mes</span>
          <div className="disp-gauge-wrap">
            <ResponsiveContainer width="100%" height={200}>
              <RadialBarChart
                innerRadius="78%"
                outerRadius="100%"
                data={gaugeData}
                startAngle={210}
                endAngle={-30}
              >
                <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
                <RadialBar background dataKey="value" cornerRadius={12} angleAxisId={0} />
                <Tooltip content={<GaugeTooltip />} />
              </RadialBarChart>
            </ResponsiveContainer>
            <div className="disp-gauge-center">
              <span className="disp-gauge-value" style={{ color: STATUS_COLOR[overall?.status || "no_data"] }}>
                {fmtPct(overall?.availability_pct)}
              </span>
              <span className={`availability-badge ${STATUS_BADGE_CLASS[overall?.status || "no_data"]}`}>
                {STATUS_LABEL[overall?.status || "no_data"]}
              </span>
              <span className="disp-gauge-target">Meta: 96%</span>
            </div>
          </div>
          <div className="disp-breakdown">
            {Object.entries(overall?.status_breakdown || {}).map(([key, count]) => (
              <span key={key} className="disp-breakdown-chip">
                <strong>{count}</strong> {BREAKDOWN_LABEL[key] || key}
              </span>
            ))}
          </div>
        </article>

        <article className="card disp-chart-card">
          <div className="disp-chart-head">
            <span className="eyebrow">Disponibilidad por flota</span>
            <small className="disp-hint">Clic en una barra para filtrar</small>
          </div>
          {fleetChartData.length === 0 ? (
            <p className="disp-empty">Sin datos de flotas para {monthLabel(month)}.</p>
          ) : (
            <ResponsiveContainer width="100%" height={Math.max(200, fleetChartData.length * 34)}>
              <BarChart data={fleetChartData} layout="vertical" margin={{ left: 8, right: 16 }}>
                <CartesianGrid horizontal={false} stroke="rgba(53,69,80,0.08)" />
                <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={110}
                  fontSize={11}
                  tickFormatter={(v) => (v.length > 16 ? `${v.slice(0, 15)}…` : v)}
                />
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
                    <Cell
                      key={entry.customer_id ?? entry.name}
                      fill={STATUS_COLOR[entry.status] || STATUS_COLOR.no_data}
                      fillOpacity={selectedCustomerId && selectedCustomerId !== entry.customer_id ? 0.35 : 1}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </article>

        <article className="card disp-chart-card">
          <div className="disp-chart-head">
            <span className="eyebrow">Tendencia</span>
            <small className="disp-hint">{selectedFleet ? selectedFleet.customer_name : "Global"}</small>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={trendData} margin={{ left: 0, right: 12, top: 8 }}>
              <CartesianGrid stroke="rgba(53,69,80,0.08)" />
              <XAxis dataKey="label" fontSize={11} />
              <YAxis domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} width={38} />
              <Tooltip formatter={(value) => (value === null ? "Sin datos" : `${Number(value).toFixed(1)}%`)} />
              <ReferenceLine y={96} stroke="#354550" strokeDasharray="4 4" label={{ value: "Meta 96%", position: "right", fontSize: 10, fill: "#354550" }} />
              <Line
                type="monotone"
                dataKey="pct"
                stroke="#ee2e2f"
                strokeWidth={2.5}
                dot={{ r: 3 }}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </article>
      </div>

      {/* Ranking */}
      <article className="card disp-ranking-card">
        <div className="disp-chart-head">
          <div>
            <span className="eyebrow">Ranking de vehículos</span>
            <h3 className="disp-ranking-title">
              {rankingOrder === "worst" ? "Peor estado" : "Mejor estado"}
              {selectedFleet ? ` · ${selectedFleet.customer_name}` : ""}
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
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => setRankingOrder((o) => (o === "worst" ? "best" : "worst"))}
            >
              {rankingOrder === "worst" ? "Ver mejores" : "Ver peores"}
            </button>
          </div>
        </div>

        <div className="vehicles-table-shell">
          <table className="vehicles-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Placa</th>
                <th>Flota</th>
                <th>Disponibilidad</th>
                <th>Horas no disp.</th>
                <th>Órdenes</th>
              </tr>
            </thead>
            <tbody>
              {loadingDetail ? (
                <tr>
                  <td className="table-empty-row" colSpan={6}>Cargando…</td>
                </tr>
              ) : ranking.length === 0 ? (
                <tr>
                  <td className="table-empty-row" colSpan={6}>
                    Sin vehículos calculados para {monthLabel(month)}.
                  </td>
                </tr>
              ) : (
                ranking.map((v, idx) => (
                  <tr key={v.plate}>
                    <td>{idx + 1}</td>
                    <td><strong>{v.plate}</strong></td>
                    <td>{v.customer_name}</td>
                    <td>
                      <span className={`availability-badge ${STATUS_BADGE_CLASS[v.status]}`}>
                        {fmtPct(v.availability_pct)}
                      </span>
                    </td>
                    <td>{Number(v.h_no_disp).toFixed(1)} h</td>
                    <td>{v.orders_considered}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </article>
    </section>
  );
}
