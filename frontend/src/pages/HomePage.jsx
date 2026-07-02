import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { fetchDashboardSummaryV2 } from "../api/vehicleApi";

const HISTORY_KEY = "navi:lookup-history";

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

function MetricCard({ label, value, sub, to, tone }) {
  const content = (
    <>
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      {sub ? <p>{sub}</p> : null}
    </>
  );

  const classes = ["card", "metric-card", "metric-card-compact"];
  if (tone === "accent") classes.push("feature-card-accent");
  if (tone === "warn") classes.push("feature-card-warn");
  if (to) classes.push("dash-metric-link");

  if (to) {
    return (
      <Link to={to} className={classes.join(" ")}>
        {content}
      </Link>
    );
  }

  return <article className={classes.join(" ")}>{content}</article>;
}

function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(1)}%`;
}

function fmtMinutes(value) {
  if (value === null || value === undefined) return "—";
  const m = Math.max(0, Math.floor(value));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem ? `${h}h ${rem} min` : `${h}h`;
}

const MONTH_NAMES = [
  "Ene", "Feb", "Mar", "Abr", "May", "Jun",
  "Jul", "Ago", "Sep", "Oct", "Nov", "Dic",
];

function monthLabel(month) {
  if (!month || typeof month !== "string") return "";
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return `${MONTH_NAMES[m - 1] || m} ${String(y).slice(2)}`;
}

const JOB_STATUS_LABEL = {
  queued: "En cola",
  running: "En curso",
  done: "Completado",
  error: "Con error",
};

function jobStatusLabel(status) {
  return JOB_STATUS_LABEL[status] || status || "—";
}

function buildTrendData(trend) {
  if (!trend || !Array.isArray(trend.labels)) return [];
  return trend.labels.map((label, idx) => ({
    label,
    name: monthLabel(label),
    pct: trend.availability_pct?.[idx] ?? null,
  }));
}

function TrendChart({ data }) {
  if (!data.length) {
    return <p className="support-copy">Sin datos de tendencia.</p>;
  }
  return (
    <div className="dash-trend-chart">
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
          />
          <YAxis
            domain={[80, 100]}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
            width={32}
            tickFormatter={(v) => `${v}%`}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              fontSize: 12,
            }}
            formatter={(value) => [value == null ? "—" : `${Number(value).toFixed(1)}%`, "Disponibilidad"]}
            labelFormatter={(label) => label}
          />
          <Line
            type="monotone"
            dataKey="pct"
            stroke="var(--red)"
            strokeWidth={2}
            dot={{ r: 3, fill: "var(--red)" }}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function HomePage() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchValue, setSearchValue] = useState("");
  const { toasts, pushToast } = useToasts();
  const [history] = useState(loadHistory);
  const navigate = useNavigate();

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await fetchDashboardSummaryV2();
      setSummary(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error cargando datos");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const handleSearch = (event) => {
    event.preventDefault();
    const normalized = searchValue.trim().toUpperCase();
    if (normalized.length < 3) return;
    navigate(`/consulta-motor?q=${encodeURIComponent(normalized)}`);
  };

  const trendData = useMemo(
    () => buildTrendData(summary?.availability_trend),
    [summary?.availability_trend]
  );

  const availability = summary?.availability;
  const taller = summary?.taller;
  const lastJob = summary?.last_rendimientos_job;

  const tallerSub = useMemo(() => {
    if (!taller || taller.vehicles_in_taller === 0) {
      return "Sin vehiculos en geocerca";
    }
    const top = taller.top_plates?.length
      ? `Mas antiguo: ${taller.top_plates[0]} (${fmtMinutes(taller.oldest_minutes)})`
      : `Mas antiguo ${fmtMinutes(taller.oldest_minutes)}`;
    return top;
  }, [taller]);

  const tallerTone = taller && taller.vehicles_in_taller > 0 ? "warn" : null;

  const jobSub = useMemo(() => {
    if (!lastJob?.job_id) return "Sin jobs registrados";
    const ts = lastJob.finished_at || lastJob.created_at;
    if (!ts) return jobStatusLabel(lastJob.status);
    const hora = new Date(ts).toLocaleTimeString("es-CO", {
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${jobStatusLabel(lastJob.status)} · ${hora}`;
  }, [lastJob]);

  const lastJobDay = useMemo(() => {
    if (!lastJob?.job_id) return "—";
    const ts = lastJob.finished_at || lastJob.created_at;
    if (!ts) return "—";
    return new Date(ts).toLocaleDateString("es-CO", {
      day: "2-digit",
      month: "short",
    });
  }, [lastJob]);

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Dashboard</span>
        <h2>Centro de control</h2>
      </header>

      <ToastStack toasts={toasts} />

      {/* ── Operational KPIs (nuevo) ── */}
      <section className="dash-metrics-grid">
        <MetricCard
          label="Disponibilidad del mes"
          value={
            loading || !availability?.has_data
              ? "—"
              : fmtPct(availability.availability_pct)
          }
          sub={
            !loading && availability
              ? availability.has_data
                ? `${availability.fleet_count} flotas · ${availability.critical_fleets} criticas`
                : "Sin datos para este mes"
              : "Cargando..."
          }
          to="/disponibilidad"
          tone={availability?.critical_fleets > 0 ? "warn" : "accent"}
        />
        <MetricCard
          label="En taller ahora"
          value={loading ? "-" : taller?.vehicles_in_taller ?? 0}
          sub={loading ? "Cargando..." : tallerSub}
          to="/mapa"
          tone={tallerTone}
        />
        <MetricCard
          label="Ultimo job rendimientos"
          value={loading ? "-" : lastJobDay}
          sub={loading ? "Cargando..." : jobSub}
          to="/rendimientos"
        />
        <MetricCard
          label="Vehiculos sin catalogar"
          value={loading ? "-" : summary?.vehicles_without_motor ?? 0}
          sub={
            !loading && summary?.vehicles_without_motor
              ? "Pendientes de asignar motor"
              : "Todos catalogados"
          }
          to="/vehiculos"
        />
      </section>

      {/* ── Catalogo / clientes ── */}
      <section className="dash-metrics-grid dash-metrics-grid-secondary">
        <MetricCard
          label="Motores"
          value={loading ? "-" : summary?.motors_count ?? 0}
          sub="Registrados en catalogo"
          to="/motores"
        />
        <MetricCard
          label="Vehiculos"
          value={loading ? "-" : summary?.vehicles_count ?? 0}
          sub={
            !loading && summary?.databases_count
              ? `${summary.databases_count} databases configuradas`
              : "Sin databases aun"
          }
          to="/vehiculos"
        />
        <MetricCard
          label="Clientes"
          value={loading ? "-" : summary?.customers_count ?? 0}
          sub="Cuentas activas"
          to="/clientes"
        />
        <MetricCard
          label="Tendencia 6 meses"
          value={
            loading
              ? "—"
              : availability?.has_data
              ? fmtPct(availability.availability_pct)
              : "—"
          }
          sub={
            summary?.availability_trend?.month_from
              ? `${monthLabel(summary.availability_trend.month_from)} → ${monthLabel(summary.availability_trend.month_to)}`
              : "Sin serie"
          }
        />
      </section>

      {/* ── Tendencia chart ── */}
      <section className="card dash-trend-card">
        <header className="dash-recent-header">
          <div>
            <span className="eyebrow">Disponibilidad mensual</span>
            <h3>Ultimos 6 meses</h3>
          </div>
          <Link to="/disponibilidad" className="button-secondary button-sm">Ver detalle</Link>
        </header>
        {loading ? (
          <p className="support-copy">Cargando...</p>
        ) : (
          <TrendChart data={trendData} />
        )}
      </section>

      {/* ── Quick search ── */}
      <section className="card dash-search-card">
        <div className="dash-search-heading">
          <span className="eyebrow">Consulta rapida</span>
          <p className="support-copy">Busca por placa o VIN para identificar motor y configuracion.</p>
        </div>
        <form className="lookup-bar" onSubmit={handleSearch}>
          <input
            className="lookup-bar-input"
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value.toUpperCase())}
            placeholder="Placa o VIN — Ej: TLK240, 3HSDJAPR6GN123456"
            minLength={3}
            maxLength={32}
          />
          <button type="submit" disabled={searchValue.trim().length < 3}>
            Consultar
          </button>
        </form>
        {history.length > 0 ? (
          <div className="lookup-history">
            <span className="lookup-history-label">Recientes</span>
            {history.slice(0, 5).map((item) => (
              <Link
                key={item}
                to={`/consulta-motor?q=${encodeURIComponent(item)}`}
                className="lookup-history-chip"
              >
                {item}
              </Link>
            ))}
          </div>
        ) : null}
      </section>

      {/* ── Recent activity ── */}
      <section className="dash-recent-grid">
        <article className="card dash-recent-card">
          <header className="dash-recent-header">
            <span className="eyebrow">Ultimos motores</span>
            <Link to="/motores" className="button-secondary button-sm">Ver todos</Link>
          </header>
          {loading ? (
            <p className="support-copy">Cargando...</p>
          ) : summary?.recent_motors?.length ? (
            <table className="dash-recent-table">
              <thead>
                <tr>
                  <th>Motor</th>
                  <th>TEC#</th>
                  <th>Registrado</th>
                </tr>
              </thead>
              <tbody>
                {summary.recent_motors.map((motor) => (
                  <tr key={motor.id}>
                    <td><strong>{motor.engine_name}</strong></td>
                    <td>{motor.technical_number}</td>
                    <td>{formatDate(motor.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="support-copy">Sin motores registrados aun.</p>
          )}
        </article>

        <article className="card dash-recent-card">
          <header className="dash-recent-header">
            <span className="eyebrow">Ultimos vehiculos</span>
            <Link to="/vehiculos" className="button-secondary button-sm">Ver todos</Link>
          </header>
          {loading ? (
            <p className="support-copy">Cargando...</p>
          ) : summary?.recent_vehicles?.length ? (
            <table className="dash-recent-table">
              <thead>
                <tr>
                  <th>Placa</th>
                  <th>Cliente</th>
                  <th>Motor</th>
                  <th>Actualizado</th>
                </tr>
              </thead>
              <tbody>
                {summary.recent_vehicles.map((vehicle) => (
                  <tr key={vehicle.plate}>
                    <td><strong>{vehicle.plate}</strong></td>
                    <td>{vehicle.client_name || "Sin cliente"}</td>
                    <td>{vehicle.engine_name || "Sin motor"}</td>
                    <td>{formatDate(vehicle.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="support-copy">Sin vehiculos registrados aun.</p>
          )}
        </article>
      </section>
    </section>
  );
}
