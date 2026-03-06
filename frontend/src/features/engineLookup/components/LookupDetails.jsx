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
    <section className="card">
      <header className="result-header">
        <h3>Resultado de consulta</h3>
        <span className={`status status-${result.status}`}>{result.status}</span>
      </header>

      <div className="data-grid">
        <DataItem label="Placa" value={result.plate} />
        <DataItem label="Tipo combustible" value={result.fuel_type} />
        <DataItem label="VIN" value={result.vin} />
        <DataItem label="Numero de motor" value={result.engine_number} />
        <DataItem
          label="Technical Engine Configuration #"
          value={result.technical_engine_configuration}
        />
      </div>

      <p className="helper-text">{result.message}</p>
    </section>
  );
}