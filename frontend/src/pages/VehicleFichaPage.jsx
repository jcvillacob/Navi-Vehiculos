import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { fetchVehicleFicha, fetchVehicleTelemetry } from "../api/vehicleApi";

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

function formatRelativeTime(isoTimestamp) {
  if (!isoTimestamp) return { relative: "", absolute: "" };
  const generated = new Date(isoTimestamp);
  if (Number.isNaN(generated.getTime())) return { relative: "", absolute: "" };
  const diffMs = Math.max(0, Date.now() - generated.getTime());
  const minutes = Math.floor(diffMs / 60000);
  const hours = Math.floor(diffMs / 3600000);
  const days = Math.floor(diffMs / 86400000);
  let relative = "hace un momento";
  if (days >= 1) {
    relative = days === 1 ? "hace 1 d" : `hace ${days} d`;
  } else if (hours >= 1) {
    relative = hours === 1 ? "hace 1 h" : `hace ${hours} h`;
  } else if (minutes >= 1) {
    relative = minutes === 1 ? "hace 1 min" : `hace ${minutes} min`;
  }
  return { relative, absolute: generated.toLocaleString("es-CO") };
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

function TelemetryPair({ label, value }) {
  return (
    <div className="telemetry-pair">
      <span className="telemetry-label">{label}</span>
      <span className="telemetry-value">{value ?? "—"}</span>
    </div>
  );
}

function TelemetryCard({ plate, onVerificationChange }) {
  const [telemetry, setTelemetry] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadTelemetry = () => {
    setLoading(true);
    setError("");
    onVerificationChange?.({ state: "checking" });
    fetchVehicleTelemetry(plate)
      .then((data) => {
        setTelemetry(data);
        onVerificationChange?.({
          state: data?.available ? "confirmed" : "unavailable",
          reason: data?.reason || null,
        });
      })
      .catch((err) => {
        setTelemetry(null);
        setError(err instanceof Error ? err.message : "Error consultando Geotab");
        onVerificationChange?.({ state: "error" });
      })
      .finally(() => setLoading(false));
  };

  const reasonMessage = (reason, detail) => {
    if (reason === "sin_database_geotab") {
      return "Telemetría no disponible: este vehículo no tiene una base de datos Geotab asignada.";
    }
    if (reason === "device_no_encontrado") {
      return "No se encontró el dispositivo Geotab para esta placa.";
    }
    if (reason === "geotab_error") {
      return `Error consultando Geotab${detail ? `: ${detail}` : ""}.`;
    }
    return "No se pudo consultar la telemetría.";
  };

  const lastComm = formatRelativeTime(telemetry?.last_communication);

  return (
    <article className="card telemetry-card">
      <div className="disp-chart-head">
        <div>
          <span className="eyebrow">Telemetría en vivo</span>
          <h3 className="disp-ranking-title">Estado desde Geotab</h3>
        </div>
        <button
          type="button"
          className="button-secondary button-sm"
          onClick={loadTelemetry}
          disabled={loading}
        >
          {loading ? (
            <>
              <span className="spin" aria-hidden="true">
                ⟳
              </span>
              Consultando…
            </>
          ) : telemetry ? (
            "Actualizar"
          ) : (
            "Consultar telemetría"
          )}
        </button>
      </div>

      {!telemetry && !loading && !error && (
        <p className="disp-hint">Presiona el botón para consultar la telemetría actual del vehículo.</p>
      )}

      {loading && <p className="disp-hint">Consultando Geotab…</p>}

      {error && <p className="notice-banner notice-error">{error}</p>}

      {telemetry && !telemetry.available && !error && (
        <p className="notice-banner notice-soft">{reasonMessage(telemetry.reason, telemetry.detail)}</p>
      )}

      {telemetry?.available && (
        <>
          <div className="telemetry-grid">
            <TelemetryPair
              label="Última comunicación"
              value={
                lastComm.relative ? (
                  <span title={lastComm.absolute}>
                    {lastComm.relative}
                    <span className="telemetry-absolute"> · {lastComm.absolute}</span>
                  </span>
                ) : null
              }
            />
            <TelemetryPair
              label="En movimiento"
              value={
                telemetry.is_driving === true ? "Sí" : telemetry.is_driving === false ? "No" : null
              }
            />
            <TelemetryPair
              label="Velocidad"
              value={
                telemetry.speed !== null && telemetry.speed !== undefined
                  ? `${fmtNumber(telemetry.speed, 1)} km/h`
                  : null
              }
            />
            <TelemetryPair
              label="Odómetro actual"
              value={
                telemetry.odometer_km !== null && telemetry.odometer_km !== undefined
                  ? `${fmtNumber(telemetry.odometer_km, 1)} km`
                  : null
              }
            />
            <TelemetryPair
              label="Horómetro actual"
              value={
                telemetry.engine_hours !== null && telemetry.engine_hours !== undefined
                  ? `${fmtNumber(telemetry.engine_hours, 1)} h`
                  : null
              }
            />
            {telemetry.latitude !== null &&
              telemetry.latitude !== undefined &&
              telemetry.longitude !== null &&
              telemetry.longitude !== undefined && (
                <TelemetryPair
                  label="Ubicación"
                  value={
                    <a
                      className="access-url-link"
                      href={`https://maps.google.com/?q=${telemetry.latitude},${telemetry.longitude}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Ver en Google Maps
                    </a>
                  }
                />
              )}
          </div>
          <p className="disp-hint" style={{ marginTop: 10 }}>
            Ventana de lecturas: {telemetry.readings_window_days} días
          </p>
        </>
      )}
    </article>
  );
}

export default function VehicleFichaPage() {
  const { placa } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [ficha, setFicha] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [telemetryVerification, setTelemetryVerification] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setFicha(null);
    setTelemetryVerification(null);

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
  const hasGeotabConfiguration =
    String(master.database_connection_type || "").toLowerCase() === "geotab" &&
    Boolean(master.database_name);
  const geotabStatus = telemetryVerification?.state === "confirmed"
    ? {
      label: "Telemetría confirmada",
      className: "db-status-connected",
      dotClass: "db-status-dot-ok",
      title: "El dispositivo respondió desde la base Geotab asignada al cliente",
    }
    : telemetryVerification?.state === "checking"
      ? {
        label: "Verificando telemetría",
        className: "db-status-disconnected",
        dotClass: "db-status-dot-warn",
        title: "Consultando el dispositivo en Geotab",
      }
      : telemetryVerification?.state === "unavailable" || telemetryVerification?.state === "error"
        ? {
          label: "Telemetría no confirmada",
          className: "db-status-not-found",
          dotClass: "db-status-dot-error",
          title: "La última consulta no pudo confirmar telemetría para este vehículo",
        }
        : hasGeotabConfiguration
          ? {
            label: "Geotab configurado",
            className: "ficha-geotab-configured",
            dotClass: "ficha-geotab-dot-configured",
            title: "Tiene una base Geotab asignada; consulta la telemetría para confirmar el dispositivo",
          }
          : null;

  const handleBack = () => {
    const returnTo = location.state?.returnTo || "/disponibilidad";
    navigate(returnTo, { replace: true, state: location.state || null });
  };

  return (
    <section className="panel vehicle-ficha-page">
      <header className="page-header page-header-row">
        <div>
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
            {geotabStatus ? (
              <span className={`status ${geotabStatus.className}`} title={geotabStatus.title}>
                <span className={`db-status-dot ${geotabStatus.dotClass}`} aria-hidden="true" />
                {geotabStatus.label}
              </span>
            ) : null}
          </div>
        </div>
        <button type="button" className="button-secondary ficha-back-button" onClick={handleBack}>
          ← Volver a {location.state?.returnLabel || "Disponibilidad"}
        </button>
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

      <TelemetryCard plate={ficha?.plate} onVerificationChange={setTelemetryVerification} />

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
