import { useMemo } from "react";

import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";

function formatDateTime(value) {
  if (!value) {
    return "Sin fecha";
  }

  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

export default function VehiclesPage() {
  const { loading, vehicles, error, search, setSearch, loadVehicles } = useVehicleAssignments();

  const summary = useMemo(() => {
    const registered = vehicles.filter((vehicle) => vehicle.engine_name).length;
    return {
      total: vehicles.length,
      registered
    };
  }, [vehicles]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    await loadVehicles(search);
  };

  const handleClear = async () => {
    setSearch("");
    await loadVehicles("");
  };

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Relacion vehiculo-motor</span>
        <h2>Vehiculos asociados</h2>
        <p>
          Aqui puedes ver las placas guardadas, su VIN, el TEC# detectado y el motor registrado al
          que quedaron asociadas.
        </p>
      </header>

      <section className="vehicles-summary-grid">
        <article className="card metric-card">
          <span className="eyebrow">Placas unicas</span>
          <strong>{summary.total}</strong>
          <p>Vehiculos persistidos en la relacion placa-motor</p>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">Catalogadas</span>
          <strong>{summary.registered}</strong>
          <p>Placas con un motor visible ya registrado</p>
        </article>
      </section>

      <section className="card vehicles-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Explorar</span>
            <h3>Base de vehiculos asociados</h3>
          </div>
        </header>

        <form className="vehicles-filter-bar" onSubmit={handleSubmit}>
          <div className="form-field vehicles-search-field">
            <label htmlFor="vehicles-search">Buscar</label>
            <input
              id="vehicles-search"
              value={search}
              onChange={(event) => setSearch(event.target.value.toUpperCase())}
              placeholder="Placa, VIN, TEC# o motor"
            />
          </div>

          <div className="actions-row vehicles-filter-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Buscando..." : "Buscar"}
            </button>
            <button type="button" className="button-secondary" onClick={handleClear} disabled={loading}>
              Limpiar
            </button>
          </div>
        </form>

        {error ? <p className="notice-banner notice-error">{error}</p> : null}

        {!loading && vehicles.length === 0 ? (
          <article className="card empty-state-card vehicles-empty-state">
            <span className="eyebrow">Sin resultados</span>
            <h3>No hay vehiculos asociados para mostrar.</h3>
            <p>
              Ejecuta una consulta exitosa por placa o VIN para que la asociacion quede persistida y
              aparezca aqui.
            </p>
          </article>
        ) : null}

        {vehicles.length > 0 ? (
          <div className="vehicles-table-shell">
            <table className="vehicles-table">
              <thead>
                <tr>
                  <th>Placa</th>
                  <th>VIN</th>
                  <th>Motor</th>
                  <th>TEC#</th>
                  <th>ESN</th>
                  <th>Ultima deteccion</th>
                </tr>
              </thead>
              <tbody>
                {vehicles.map((vehicle) => (
                  <tr key={vehicle.plate}>
                    <td data-label="Placa">
                      <strong>{vehicle.plate}</strong>
                    </td>
                    <td data-label="VIN">{vehicle.vin || "Sin VIN"}</td>
                    <td data-label="Motor">{vehicle.engine_name || "Sin catalogar"}</td>
                    <td data-label="TEC#">{vehicle.technical_number}</td>
                    <td data-label="ESN">{vehicle.engine_number || "Sin ESN"}</td>
                    <td data-label="Ultima deteccion">{formatDateTime(vehicle.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </section>
  );
}
