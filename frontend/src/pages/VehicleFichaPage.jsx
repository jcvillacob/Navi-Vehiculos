import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchVehicleFicha } from "../api/vehicleApi";

const TALLER_INDICATOR_BADGE_CLASS = {
  on_time: "availability-good",
  about_to_expire: "availability-warn",
  overdue: "availability-bad",
  pending_closure: "availability-empty",
};

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

function fmtNumber(value, decimals = 1) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("es-CO", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtHours(value) {
  return fmtNumber(value, 1);
}

function fmtKm(value) {
  return fmtNumber(value, 1);
}

function fmtGallons(value) {
  return fmtNumber(value, 2);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function availabilityStatus(pct) {
  if (pct === null || pct === undefined) return "no_data";
  const n = Number(pct);
  if (!Number.isFinite(n)) return "no_data";
  if (n >= 97) return "good";
  if (n >= 96) return "warning";
  return "critical";
}

function AvailabilityBadge({ value }) {
  const status = availabilityStatus(value);
  const classMap = {
    good: "availability-good",
    warning: "availability-warn",
    critical: "availability-bad",
    no_data: "availability-empty",
  };
  const labelMap = {
    good: "Óptima",
    warning: "Advertencia",
    critical: "Crítica",
    no_data: "Sin datos",
  };
  return (
    <span className={`availability-badge ${classMap[status]}`}>
      {fmtPct(value)} · {labelMap[status]}
    </span>
  );
}

function MasterPair({ label, value }) {
  return (
    <div className="ficha-master-pair">
      <span className="ficha-master-label">{label}</span>
      <span className="ficha-master-value">{value ?? "—"}</span>
    </div>
  );
}

export default function VehicleFichaPage() {
  const { placa } = useParams();
  const [ficha, setFicha] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setFicha(null);

    fetchVehicleFicha(placa)
      .then((data) => {
        if (!cancelled) setFicha(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Error cargando la ficha");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [placa]);

  if (loading) {
    return (
      <section className="panel">
        <p className="notice-banner notice-soft">Cargando ficha del vehículo…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="panel">
        <div className="notice-banner notice-error">{error}</div>
      </section>
    );
  }

  const master = ficha?.master ?? {};

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Vehículo</span>
        <h2>{ficha?.plate}</h2>
        <div className="ficha-header-chips">
          {master.client_name ? (
            <span className="status" title="Cliente">
              {master.client_name}
            </span>
          ) : null}
          {master.category ? (
            <span className="status" title="Categoría">
              {master.category}
            </span>
          ) : null}
          {master.geotab_status ? (
            <span className={`status ${master.geotab_status === "connected" ? "db-status-connected" : "db-status-disconnected"}`} title="Estado Geotab">
              <span className={`db-status-dot ${master.geotab_status === "connected" ? "db-status-dot-ok" : "db-status-dot-warn"}`} aria-hidden="true" />
              {master.geotab_status}
            </span>
          ) : null}
        </div>
      </header>

      <article className="card">
        <div className="disp-chart-head">
          <span className="eyebrow">Datos maestros</span>
        </div>
        <div className="ficha-grid">
          <MasterPair label="VIN" value={master.vin} />
          <MasterPair label="TEC#" value={master.technical_number} />
          <MasterPair label="Motor" value={master.engine_name} />
          <MasterPair label="Marca" value={master.marca} />
          <MasterPair label="Línea" value={master.linea} />
          <MasterPair label="Año" value={master.ano_modelo} />
          <MasterPair label="Combustible" value={master.tipo_combustible} />
          <MasterPair label="Nombre" value={master.nombre_vehiculo} />
          <MasterPair label="Vocacional" value={master.vocacional ? "Sí" : "No"} />
          <MasterPair label="Database" value={master.database_name} />
          <MasterPair label="Provider" value={master.database_connection_type} />
          <MasterPair label="Último visto" value={master.last_seen_at ? new Date(master.last_seen_at).toLocaleString("es-CO") : null} />
        </div>
      </article>

      <article className="card disp-ranking-card">
        <div className="disp-chart-head">
          <div>
            <span className="eyebrow">Taller ahora</span>
            <h3 className="disp-ranking-title">Estado en tiempo real</h3>
          </div>
        </div>

        {!ficha?.taller?.available ? (
          <p className="disp-hint">Sin datos de taller en caché (se actualiza cada 10 min)</p>
        ) : ficha.taller.orders.length === 0 ? (
          <span className="availability-badge availability-good">Sin órdenes activas</span>
        ) : (
          <>
            <div className="vehicles-table-shell">
              <table className="vehicles-table">
                <thead>
                  <tr>
                    <th>Orden</th>
                    <th>Tipo</th>
                    <th>Indicador</th>
                    <th>Días</th>
                    <th>Cierre pendiente</th>
                    <th>Etiquetas</th>
                  </tr>
                </thead>
                <tbody>
                  {ficha.taller.orders.map((order) => (
                    <tr key={order.order_number}>
                      <td>
                        <strong>{order.order_number}</strong>
                      </td>
                      <td>{order.type || "—"}</td>
                      <td>
                        <span
                          className={`availability-badge ${
                            TALLER_INDICATOR_BADGE_CLASS[order.status_indicator] || "availability-empty"
                          }`}
                        >
                          {order.time_status_text || order.status_indicator || "—"}
                        </span>
                      </td>
                      <td>{order.days_elapsed ?? "—"}</td>
                      <td>
                        {order.pending_closure_days !== null && order.pending_closure_days !== undefined
                          ? `${order.pending_closure_days} d`
                          : "—"}
                      </td>
                      <td>{order.maintenance_labels?.length ? order.maintenance_labels.join(", ") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="disp-hint" style={{ marginTop: 10 }}>
              Dato {formatMinutesAgo(ficha.taller.generated_at)}
            </p>
          </>
        )}
      </article>

      <div className="ficha-cards-grid">
        <article className="card">
          <div className="disp-chart-head">
            <span className="eyebrow">Rendimientos (últimos 12 meses)</span>
          </div>
          {ficha?.rendimientos?.length ? (
            <div className="vehicles-table-shell">
              <table className="vehicles-table">
                <thead>
                  <tr>
                    <th>Mes</th>
                    <th>Kms ECM</th>
                    <th>Kms GPS</th>
                    <th>Horas ECM</th>
                    <th>Galones</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {ficha.rendimientos.map((row) => (
                    <tr key={row.period_month}>
                      <td>{row.period_month}</td>
                      <td>{fmtKm(row.kms_ecm)}</td>
                      <td>{fmtKm(row.kms_gps)}</td>
                      <td>{fmtHours(row.hours_ecm)}</td>
                      <td>{fmtGallons(row.fuel_gallons)}</td>
                      <td>{row.calculation_status ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="disp-empty">No hay registros de rendimiento para este vehículo.</p>
          )}
        </article>

        <article className="card">
          <div className="disp-chart-head">
            <span className="eyebrow">Disponibilidad (últimos 12 meses)</span>
          </div>
          {ficha?.disponibilidad?.length ? (
            <div className="vehicles-table-shell">
              <table className="vehicles-table">
                <thead>
                  <tr>
                    <th>Mes</th>
                    <th>Disponibilidad</th>
                    <th>Horas no disp.</th>
                    <th>MTTR</th>
                    <th>Órdenes</th>
                  </tr>
                </thead>
                <tbody>
                  {ficha.disponibilidad.map((row) => (
                    <tr key={row.period_month}>
                      <td>{row.period_month}</td>
                      <td>
                        <AvailabilityBadge value={row.project_availability_pct} />
                      </td>
                      <td>{fmtHours(row.h_no_disp)}</td>
                      <td>{fmtHours(row.mttr_hours)}</td>
                      <td>{row.orders_considered ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="disp-empty">No hay registros de disponibilidad para este vehículo.</p>
          )}
        </article>
      </div>

      <article className="card">
        <div className="disp-chart-head">
          <span className="eyebrow">Bindings de provider</span>
        </div>
        {ficha?.bindings?.length ? (
          <div className="vehicles-table-shell">
            <table className="vehicles-table">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>ID externo</th>
                  <th>Estado</th>
                  <th>Último error</th>
                  <th>Actualizado</th>
                </tr>
              </thead>
              <tbody>
                {ficha.bindings.map((row, idx) => (
                  <tr key={`${row.provider}-${idx}`}>
                    <td>{row.provider}</td>
                    <td>{row.provider_vehicle_id ?? "—"}</td>
                    <td>{row.binding_status ?? "—"}</td>
                    <td>{row.last_error ?? "—"}</td>
                    <td>{row.updated_at ? new Date(row.updated_at).toLocaleString("es-CO") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="disp-empty">No hay bindings de provider registrados para este vehículo.</p>
        )}
      </article>
    </section>
  );
}
