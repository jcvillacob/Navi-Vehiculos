import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { fetchDashboardSummary } from "../api/vehicleApi";

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

function MetricCard({ label, value, sub, to }) {
  const content = (
    <>
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      {sub ? <p>{sub}</p> : null}
    </>
  );

  if (to) {
    return (
      <Link to={to} className="card metric-card dash-metric-link">
        {content}
      </Link>
    );
  }

  return <article className="card metric-card">{content}</article>;
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
      const data = await fetchDashboardSummary();
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

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Dashboard</span>
        <h2>Centro de control</h2>
      </header>

      <ToastStack toasts={toasts} />

      {/* ── Metrics ── */}
      <section className="dash-metrics-grid">
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
            !loading && summary?.vehicles_without_motor
              ? `${summary.vehicles_without_motor} sin motor catalogado`
              : "Todos con motor asignado"
          }
          to="/vehiculos"
        />
        <MetricCard
          label="Clientes"
          value={loading ? "-" : summary?.customers_count ?? 0}
          sub={
            !loading && summary?.databases_count
              ? `${summary.databases_count} databases configuradas`
              : "Sin databases aun"
          }
          to="/clientes"
        />
        <MetricCard
          label="Sin catalogar"
          value={loading ? "-" : summary?.vehicles_without_motor ?? 0}
          sub="Vehiculos pendientes de motor"
        />
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
