function DataRow({ label, value }) {
  return (
    <div className="data-row">
      <span>{label}</span>
      <strong>{value ?? "-"}</strong>
    </div>
  );
}

export default function LookupResult({ result }) {
  if (!result) {
    return null;
  }

  return (
    <section className="result-card">
      <header>
        <h2>Resultado</h2>
        <span className={`status status-${result.status}`}>{result.status}</span>
      </header>

      <DataRow label="Placa" value={result.plate} />
      <DataRow label="Marca" value={result.marca} />
      <DataRow label="Linea" value={result.linea} />
      <DataRow label="Año Modelo" value={result.ano_modelo} />
      <DataRow label="Tipo de Combustible" value={result.tipo_combustible} />
      <DataRow label="VIN" value={result.vin} />
      <DataRow label="Numero de motor (ESN)" value={result.engine_number} />
      <DataRow
        label="Technical Engine Configuration #"
        value={result.technical_engine_configuration}
      />

      <p className="message">{result.message}</p>
    </section>
  );
}