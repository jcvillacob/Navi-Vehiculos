import { useMemo, useState } from "react";

import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";
import { assignVehicleDatabase } from "../api/vehicleApi";

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
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [message, setMessage] = useState("");
  const { loading, vehicles, error, search, setSearch, loadVehicles } = useVehicleAssignments();
  const { customers, loading: customersLoading } = useCustomersCatalog();

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

  const handleUpdateVehicle = async (payload) => {
    if (!selectedVehicle) {
      return;
    }

    await assignVehicleDatabase(selectedVehicle.plate, {
      customer_database_id: payload.customer_database_id
    });
    setMessage(`Vehiculo ${selectedVehicle.plate} actualizado.`);
    setSelectedVehicle(null);
    await loadVehicles(search);
  };

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Relacion vehiculo-motor</span>
        <h2>Vehiculos asociados</h2>
        <p>
          Aqui puedes ver las placas guardadas, su VIN, el TEC# detectado, el motor registrado y el
          cliente/database asociados a cada vehiculo.
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
              placeholder="Placa, VIN, TEC#, motor, cliente o database"
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
        {message ? <p className="notice-banner notice-info">{message}</p> : null}
        {!customersLoading && customers.length === 0 ? (
          <p className="notice-banner notice-soft">
            No hay clientes ni databases creados. Usa la vista de Clientes para poblar los selectores.
          </p>
        ) : null}

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
                  <th>Cliente</th>
                  <th>Database</th>
                  <th>Usuario DB</th>
                  <th>Acciones</th>
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
                    <td data-label="Cliente">{vehicle.client_name || "Sin cliente"}</td>
                    <td data-label="Database">{vehicle.database_name || "Sin database"}</td>
                    <td data-label="Usuario DB">{vehicle.database_username || "Sin usuario"}</td>
                    <td data-label="Acciones">
                      <button type="button" className="button-secondary" onClick={() => setSelectedVehicle(vehicle)}>
                        Editar
                      </button>
                    </td>
                    <td data-label="Ultima deteccion">{formatDateTime(vehicle.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <VehicleAssignmentModal
        open={Boolean(selectedVehicle)}
        loading={loading || customersLoading}
        title={selectedVehicle ? `Editar ${selectedVehicle.plate}` : "Editar vehiculo"}
        vehicle={selectedVehicle}
        customers={customers}
        registeredMotor={
          selectedVehicle?.engine_name
            ? {
                engine_name: selectedVehicle.engine_name,
                technical_number: selectedVehicle.technical_number
              }
            : null
        }
        onClose={() => setSelectedVehicle(null)}
        onSubmit={handleUpdateVehicle}
      />
    </section>
  );
}
