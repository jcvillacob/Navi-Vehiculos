import { useEffect, useState } from "react";

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

function GeotabBadge({ status }) {
  const statusLabel =
    status === "found" ? "Geotab OK" : status === "not_found" ? "Geotab NO" : "Geotab ?";

  return <span className={`status geotab-badge geotab-${status}`}>{statusLabel}</span>;
}

export default function LookupDetails({ result }) {
  const [showFullInfo, setShowFullInfo] = useState(false);

  useEffect(() => {
    setShowFullInfo(false);
  }, [result?.lookup_value]);

  if (!result) {
    return null;
  }

  const fenixEntries = Object.entries(result.source_details?.fenix || {});
  const cumminsEntries = Object.entries(result.source_details?.cummins || {});

  return (
    <section className="card detail-card">
      <header className="section-heading">
        <div>
          <span className="eyebrow">Resultado</span>
          <h3>Datos de la consulta</h3>
        </div>

        <div className="detail-status-group">
          <GeotabBadge status={result.geotab_status} />
          <span className={`status status-${result.status}`}>{result.status}</span>
        </div>
      </header>

      <div className="data-grid">
        <DataItem label="Tipo de busqueda" value={result.lookup_type === "vin" ? "VIN" : "Placa"} />
        <DataItem label="Valor consultado" value={result.lookup_value} />
        <DataItem label="Placa" value={result.plate} />
        <DataItem label="VIN" value={result.vin} />
        <DataItem label="Numero de motor" value={result.engine_number} />
        <DataItem
          label="Technical Engine Configuration #"
          value={result.technical_engine_configuration}
        />
        <DataItem
          label="Motor registrado"
          value={result.registered_motor?.engine_name || "No registrado"}
        />
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

      <button
        type="button"
        className="button-secondary expand-button"
        onClick={() => setShowFullInfo((current) => !current)}
      >
        {showFullInfo ? "Ocultar info completa" : "Ver info completa"}
      </button>

      {showFullInfo ? (
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
