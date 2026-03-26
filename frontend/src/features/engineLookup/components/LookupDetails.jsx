import { useState } from "react";

function DataItem({ label, value }) {
  return (
    <div className="data-item">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function SourceField({ label, value }) {
  return (
    <div className="source-field">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function GeotabBadge({ label, status }) {
  const statusText =
    status === "found"
      ? "OK"
      : status === "not_found"
        ? "NO"
        : status === "not_applicable"
          ? "N/A"
          : "?";

  const badgeClass =
    status === "not_applicable" ? "geotab-na" : `geotab-${status}`;

  return (
    <span className={`status geotab-badge ${badgeClass}`}>
      {label}: {statusText}
    </span>
  );
}

export default function LookupDetails({
  result,
  loading,
  canRegister,
  canConfigure,
  isManualAssignment,
  onAction,
  onForceSearch
}) {
  const [showSources, setShowSources] = useState(false);

  if (!result) {
    return null;
  }

  const fenixEntries = Object.entries(result.source_details?.fenix || {});
  const cumminsEntries = Object.entries(result.source_details?.cummins || {});

  const isOk = result.status === "ok";
  const hasMotor = Boolean(result.registered_motor);
  const canAct = isOk || isManualAssignment;
  const statusClass = isOk
    ? hasMotor
      ? "status-ok"
      : "status-partial"
    : isManualAssignment
      ? "status-partial"
      : `status-${result.status}`;
  const statusLabel = isOk
    ? hasMotor
      ? "Registrado"
      : "Sin catalogar"
    : isManualAssignment
      ? "Asignacion manual"
      : result.status;

  const actionLabel = !canAct
    ? null
    : isManualAssignment
      ? "Asignar motor manualmente"
      : !hasMotor
        ? "Registrar y asignar"
        : result.assigned_database?.client_name
          ? "Editar asignacion"
          : "Asignar cliente";

  return (
    <section className="card lookup-result-card">
      {/* ── Header: badges + status ── */}
      <header className="lookup-result-header">
        <div className="detail-status-group">
          <GeotabBadge label="Navitrans" status={result.geotab_status} />
          <GeotabBadge
            label="Cliente"
            status={result.geotab_customer_status || "not_applicable"}
          />
          <span className={`status ${statusClass}`}>{statusLabel}</span>
        </div>
      </header>

      {/* ── Cache banner ── */}
      {result.cached && onForceSearch ? (
        <div className="notice-banner notice-info">
          Datos cargados desde cache local.
          <button
            type="button"
            className="button-secondary button-sm"
            style={{ marginLeft: 12 }}
            onClick={onForceSearch}
            disabled={loading}
          >
            {loading ? "Buscando..." : "Buscar de nuevo"}
          </button>
        </div>
      ) : null}

      {/* ── Identificacion ── */}
      <section className="lookup-result-section">
        <span className="lookup-section-label">Identificacion</span>
        <div className="data-grid">
          <DataItem label="Placa" value={result.plate} />
          <DataItem label="Marca" value={result.marca} />
          <DataItem label="Linea" value={result.linea} />
          <DataItem label="Año Modelo" value={result.ano_modelo} />
          <DataItem label="Tipo de Combustible" value={result.tipo_combustible} />
          <DataItem label="VIN" value={result.vin} />
          <DataItem label="ESN" value={result.engine_number} />
          <DataItem label="Busqueda" value={`${result.lookup_type === "vin" ? "VIN" : "Placa"}: ${result.lookup_value}`} />
        </div>
      </section>

      {/* ── Motor ── */}
      <section className="lookup-result-section">
        <span className="lookup-section-label">Motor</span>
        <div className="data-grid">
          <DataItem label="TEC#" value={result.technical_engine_configuration} />
          <DataItem label="CPL" value={result.cpl} />
          <DataItem
            label="Motor registrado"
            value={result.registered_motor?.engine_name || "No registrado"}
          />
        </div>
      </section>

      {/* ── Asignacion ── */}
      <section className="lookup-result-section">
        <span className="lookup-section-label">Asignacion</span>
        <div className="data-grid">
          <DataItem
            label="Cliente"
            value={result.assigned_database?.client_name || "Sin cliente"}
          />
          <DataItem
            label="Database"
            value={result.assigned_database?.database_name || "Sin database"}
          />
          <DataItem
            label="Usuario DB"
            value={result.assigned_database?.database_username || "Sin usuario"}
          />
        </div>
      </section>

      {/* ── Action ── */}
      {canAct && actionLabel ? (
        <div className="lookup-result-action">
          {hasMotor ? (
            <div className="motor-chip">
              <strong>{result.registered_motor.engine_name}</strong>
              <span>{result.registered_motor.technical_number}</span>
            </div>
          ) : isManualAssignment ? (
            <p className="support-copy">
              Motor no encontrado en Cummins. Selecciona o crea un motor para asignar a este vehiculo.
            </p>
          ) : (
            <p className="support-copy">
              No existe motor registrado para este TEC#.
            </p>
          )}
          <button
            type="button"
            disabled={(!canRegister && !canConfigure && !isManualAssignment) || loading}
            onClick={onAction}
          >
            {actionLabel}
          </button>
        </div>
      ) : !canAct ? (
        <p className="support-copy">
          No se encontraron datos suficientes para clasificar este vehiculo.
        </p>
      ) : null}

      {/* ── Message + warnings ── */}
      <p className="support-copy">{result.message}</p>

      {result.warnings?.length ? (
        <div className="warning-stack">
          {result.warnings.map((warning) => (
            <p className="notice-banner notice-soft" key={warning}>
              {warning}
            </p>
          ))}
        </div>
      ) : null}

      {/* ── Expandable sources ── */}
      <button
        type="button"
        className="button-secondary button-sm expand-button"
        onClick={() => setShowSources((current) => !current)}
      >
        {showSources ? "Ocultar fuentes" : "Ver fuentes"}
      </button>

      {showSources ? (
        <section className="source-panels-grid">
          <article className="source-panel">
            <header className="source-panel-header">
              <span className="eyebrow">Fenix</span>
              <h4>Datos desde SQL</h4>
            </header>

            <div className="source-grid">
              {fenixEntries.length ? (
                fenixEntries.map(([key, value]) => (
                  <SourceField key={key} label={key} value={value} />
                ))
              ) : (
                <p className="support-copy">Sin informacion adicional.</p>
              )}
            </div>
          </article>

          <article className="source-panel">
            <header className="source-panel-header">
              <span className="eyebrow">Cummins</span>
              <h4>Dataplate completo</h4>
            </header>

            <div className="source-grid">
              {cumminsEntries.length ? (
                cumminsEntries.map(([key, value]) => (
                  <SourceField key={key} label={key} value={value} />
                ))
              ) : (
                <p className="support-copy">Sin informacion adicional.</p>
              )}
            </div>
          </article>
        </section>
      ) : null}
    </section>
  );
}
