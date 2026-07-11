import { useEffect, useMemo, useState } from "react";

import { fetchActiveTallerOrders } from "../api/vehicleApi";
import { exportTallerOrdenesExcel } from "../utils/tallerOrdenesExport";

const INDICATOR_OPTIONS = [
  { value: "", label: "Todos los indicadores" },
  { value: "on_time", label: "En tiempo" },
  { value: "about_to_expire", label: "Por vencer" },
  { value: "overdue", label: "Excedido" },
  { value: "pending_closure", label: "Pendiente cierre" },
];

const INDICATOR_BADGE_CLASS = {
  on_time: "availability-good",
  about_to_expire: "availability-warn",
  overdue: "availability-bad",
  pending_closure: "availability-empty",
};

const INDICATOR_LABEL = {
  on_time: "En tiempo",
  about_to_expire: "Por vencer",
  overdue: "Excedido",
  pending_closure: "Pendiente cierre",
};

const SUMMARY_KEYS = [
  { key: "total_active", label: "Total" },
  { key: "on_time", label: "En tiempo" },
  { key: "about_to_expire", label: "Por vencer" },
  { key: "overdue", label: "Excedidas" },
  { key: "pending_closure", label: "Pendiente cierre" },
  { key: "con_etiquetas", label: "Con etiquetas" },
];

function formatMinutesAgo(isoTimestamp) {
  if (!isoTimestamp) return "";
  const generated = new Date(isoTimestamp);
  if (Number.isNaN(generated.getTime())) return "";
  const diffMs = Date.now() - generated.getTime();
  const minutes = Math.max(0, Math.floor(diffMs / 60000));
  if (minutes < 1) return "hace un momento";
  if (minutes === 1) return "hace 1 min";
  return `hace ${minutes} min`;
}

function useActiveTallerOrders() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = async ({ forceRefresh = false } = {}) => {
    setLoading(true);
    setError("");
    try {
      const result = await fetchActiveTallerOrders({ forceRefresh });
      setData(result);
    } catch (err) {
      setError(err.message || "No fue posible cargar las ordenes de taller activas");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return { data, loading, error, load };
}

export default function OrdenesTallerPage() {
  const { data, loading, error, load } = useActiveTallerOrders();

  const [indicatorFilter, setIndicatorFilter] = useState("");
  const [fleetFilter, setFleetFilter] = useState("");
  const [plateFilter, setPlateFilter] = useState("");
  const [exporting, setExporting] = useState(false);

  const orders = data?.orders ?? [];
  const summary = data?.summary ?? {};

  const fleets = useMemo(() => {
    const names = new Set();
    for (const order of orders) {
      const fleet = order.fleet || "Sin flota";
      names.add(fleet);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [orders]);

  const filteredOrders = useMemo(() => {
    const normalizedPlate = plateFilter.trim().toUpperCase();
    return orders.filter((order) => {
      if (indicatorFilter && order.status_indicator !== indicatorFilter) {
        return false;
      }
      if (fleetFilter && (order.fleet || "Sin flota") !== fleetFilter) {
        return false;
      }
      if (normalizedPlate && !(order.plate || "").toUpperCase().includes(normalizedPlate)) {
        return false;
      }
      return true;
    });
  }, [orders, indicatorFilter, fleetFilter, plateFilter]);

  const handleRefresh = () => {
    load({ forceRefresh: true });
  };

  const handleExport = async () => {
    if (!data || filteredOrders.length === 0) return;
    setExporting(true);
    try {
      await exportTallerOrdenesExcel({
        generatedAt: data.generated_at,
        summary,
        orders: filteredOrders,
      });
    } finally {
      setExporting(false);
    }
  };

  const isFirstLoad = loading && data == null;

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Operación de flotas</span>
          <h2>Órdenes de taller</h2>
        </div>

        <div className="disp-toolbar">
          {data?.generated_at ? (
            <span className="disp-hint">Datos {formatMinutesAgo(data.generated_at)}</span>
          ) : null}
          <button
            type="button"
            className="button-secondary"
            onClick={handleExport}
            disabled={loading || exporting || !data || filteredOrders.length === 0}
          >
            {exporting ? "Exportando…" : "Exportar"}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={handleRefresh}
            disabled={loading}
          >
            {loading ? "Actualizando…" : "Actualizar"}
          </button>
        </div>
      </header>

      <div className="disp-filters">
        <label className="disp-field">
          <span>Indicador</span>
          <select value={indicatorFilter} onChange={(e) => setIndicatorFilter(e.target.value)}>
            {INDICATOR_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label className="disp-field">
          <span>Flota</span>
          <select value={fleetFilter} onChange={(e) => setFleetFilter(e.target.value)}>
            <option value="">Todas las flotas</option>
            {fleets.map((fleet) => (
              <option key={fleet} value={fleet}>
                {fleet}
              </option>
            ))}
          </select>
        </label>
        <label className="disp-field">
          <span>Placa</span>
          <input
            type="text"
            placeholder="Buscar placa…"
            value={plateFilter}
            onChange={(e) => setPlateFilter(e.target.value)}
          />
        </label>
        {indicatorFilter || fleetFilter || plateFilter ? (
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={() => {
              setIndicatorFilter("");
              setFleetFilter("");
              setPlateFilter("");
            }}
          >
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}

      {isFirstLoad ? (
        <div className="notice-banner notice-soft">
          <span className="taller-orders-spinner" aria-hidden="true" />
          Consultando órdenes en CloudFleet… puede tardar hasta 30 segundos.
        </div>
      ) : null}

      <div className="disp-breakdown" style={{ marginTop: 12 }}>
        {SUMMARY_KEYS.map(({ key, label }) => (
          <span key={key} className="disp-breakdown-chip">
            <strong>{summary[key] ?? 0}</strong> {label}
          </span>
        ))}
        <span
          className={`disp-breakdown-chip ${
            summary.pending_closure_7d > 0 ? "taller-chip-critical" : ""
          }`}
        >
          <strong>{summary.pending_closure_7d ?? 0}</strong> Cierre &gt;7d
        </span>
        <span
          className={`disp-breakdown-chip ${
            summary.pending_closure_30d > 0 ? "taller-chip-critical" : ""
          }`}
        >
          <strong>{summary.pending_closure_30d ?? 0}</strong> Cierre &gt;30d
        </span>
      </div>

      <article className="card disp-ranking-card">
        <div className="disp-chart-head">
          <div>
            <span className="eyebrow">Órdenes activas</span>
            <h3 className="disp-ranking-title">
              {filteredOrders.length} de {orders.length} ordenes
            </h3>
          </div>
        </div>

        <div className="vehicles-table-shell">
          <table className="vehicles-table">
            <thead>
              <tr>
                <th>Orden</th>
                <th>Placa</th>
                <th>Flota</th>
                <th>Tipo</th>
                <th>Estado</th>
                <th>Indicador</th>
                <th>Cierre pendiente</th>
                <th>Días</th>
                <th>Etiquetas</th>
              </tr>
            </thead>
            <tbody>
              {loading && data == null ? (
                <tr>
                  <td className="table-empty-row" colSpan={9}>
                    Cargando…
                  </td>
                </tr>
              ) : filteredOrders.length === 0 ? (
                <tr>
                  <td className="table-empty-row" colSpan={9}>
                    {orders.length === 0
                      ? "No hay ordenes de taller activas."
                      : "Ninguna orden coincide con los filtros seleccionados."}
                  </td>
                </tr>
              ) : (
                filteredOrders.map((order) => (
                  <tr key={order.order_number}>
                    <td>
                      <strong>{order.order_number}</strong>
                    </td>
                    <td>{order.plate}</td>
                    <td>{order.fleet}</td>
                    <td>{order.type || "—"}</td>
                    <td>{order.status || "—"}</td>
                    <td>
                      <span
                        className={`availability-badge ${
                          INDICATOR_BADGE_CLASS[order.status_indicator] || "availability-empty"
                        }`}
                      >
                        {INDICATOR_LABEL[order.status_indicator] || order.status_indicator}
                      </span>
                    </td>
                    <td>
                      {order.pending_closure_days !== null &&
                      order.pending_closure_days !== undefined ? (
                        order.pending_closure_days > 7 ? (
                          <span className="availability-badge availability-bad">
                            {order.pending_closure_days} d
                          </span>
                        ) : (
                          `${order.pending_closure_days} d`
                        )
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {order.days_elapsed !== null && order.days_elapsed !== undefined
                        ? order.days_elapsed
                        : "—"}
                    </td>
                    <td>
                      {order.maintenance_labels?.length
                        ? order.maintenance_labels.join(", ")
                        : "—"}
                    </td>
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
