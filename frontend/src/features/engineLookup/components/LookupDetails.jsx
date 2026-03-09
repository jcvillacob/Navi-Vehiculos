function DataItem({ label, value }) {
  return (
    <div className="data-item">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

export default function LookupDetails({ result }) {
  if (!result) {
    return null;
  }

  return (
    <section className="card detail-card">
      <header className="section-heading">
        <div>
          <span className="eyebrow">Resultado</span>
          <h3>Datos de la consulta</h3>
        </div>
        <span className={`status status-${result.status}`}>{result.status}</span>
      </header>

      <div className="data-grid">
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
      </div>

      <p className="support-copy">{result.message}</p>
    </section>
  );
}
