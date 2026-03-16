import { useEffect, useMemo, useState } from "react";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";
import { assignVehicleDatabase, refreshVehicle, revalidateCustomerGeotab } from "../api/vehicleApi";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

function AttachmentIcon({ contentType }) {
  const isPdf = contentType === "application/pdf";

  return (
    <span className="attachment-icon" aria-hidden="true">
      {isPdf ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M8 13h8" />
          <path d="M8 17h5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.2" />
          <path d="m21 15-4.5-4.5L8 19" />
        </svg>
      )}
    </span>
  );
}

export default function VehiclesPage() {
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [refreshingPlates, setRefreshingPlates] = useState(new Set());
  const [filterClient, setFilterClient] = useState("");
  const [filterMotor, setFilterMotor] = useState("");
  const [filterDatabase, setFilterDatabase] = useState("");
  const { loading, vehicles, error, search, setSearch, loadVehicles } = useVehicleAssignments();
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { toasts, pushToast } = useToasts();

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const clientOptions = useMemo(() => {
    let subset = vehicles;
    if (filterMotor) subset = subset.filter((v) => v.engine_name === filterMotor);
    if (filterDatabase) subset = subset.filter((v) => v.database_name === filterDatabase);
    const names = new Set();
    for (const v of subset) {
      if (v.client_name) names.add(v.client_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterMotor, filterDatabase]);

  const motorOptions = useMemo(() => {
    let subset = vehicles;
    if (filterClient) subset = subset.filter((v) => v.client_name === filterClient);
    if (filterDatabase) subset = subset.filter((v) => v.database_name === filterDatabase);
    const names = new Set();
    for (const v of subset) {
      if (v.engine_name) names.add(v.engine_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterClient, filterDatabase]);

  const databaseOptions = useMemo(() => {
    let subset = vehicles;
    if (filterClient) subset = subset.filter((v) => v.client_name === filterClient);
    if (filterMotor) subset = subset.filter((v) => v.engine_name === filterMotor);
    const names = new Set();
    for (const v of subset) {
      if (v.database_name) names.add(v.database_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterClient, filterMotor]);

  useEffect(() => {
    if (filterClient && !clientOptions.includes(filterClient)) setFilterClient("");
  }, [clientOptions, filterClient]);

  useEffect(() => {
    if (filterMotor && !motorOptions.includes(filterMotor)) setFilterMotor("");
  }, [motorOptions, filterMotor]);

  useEffect(() => {
    if (filterDatabase && !databaseOptions.includes(filterDatabase)) setFilterDatabase("");
  }, [databaseOptions, filterDatabase]);

  const filteredVehicles = useMemo(() => {
    let result = vehicles;
    if (filterClient) {
      result = result.filter((v) => v.client_name === filterClient);
    }
    if (filterMotor) {
      result = result.filter((v) => v.engine_name === filterMotor);
    }
    if (filterDatabase) {
      result = result.filter((v) => v.database_name === filterDatabase);
    }
    return result;
  }, [vehicles, filterClient, filterMotor, filterDatabase]);

  const summary = useMemo(() => {
    const registered = filteredVehicles.filter((vehicle) => vehicle.engine_name).length;
    const withRules = filteredVehicles.filter((vehicle) => vehicle.has_motor_rules).length;
    return {
      total: filteredVehicles.length,
      registered,
      withRules
    };
  }, [filteredVehicles]);

  const handleClear = () => {
    setSearch("");
    setFilterClient("");
    setFilterMotor("");
    setFilterDatabase("");
  };

  const handleUpdateVehicle = async (payload) => {
    if (!selectedVehicle) {
      return;
    }

    await assignVehicleDatabase(selectedVehicle.plate, {
      customer_database_id: payload.customer_database_id
    });
    pushToast("success", `Vehiculo ${selectedVehicle.plate} actualizado.`);
    setSelectedVehicle(null);
    await loadVehicles(search);
  };

  const handleRevalidateCustomerGeotab = async (plate) => {
    try {
      const result = await revalidateCustomerGeotab(plate);
      await loadVehicles(search);
      pushToast("success", result.message || `Geotab cliente revalidado para ${plate}.`);
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible revalidar Geotab del cliente"
      );
    }
  };

  const handleRefreshVehicle = async (plate) => {
    setRefreshingPlates((prev) => new Set(prev).add(plate));

    try {
      await refreshVehicle(plate);
      await loadVehicles(search);
      pushToast("success", `Vehiculo ${plate} actualizado.`);
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible actualizar el vehiculo"
      );
    } finally {
      setRefreshingPlates((prev) => {
        const next = new Set(prev);
        next.delete(plate);
        return next;
      });
    }
  };

  return (
    <section className="panel">
      <ToastStack toasts={toasts} />

      <header className="page-header">
        <span className="eyebrow">Relacion vehiculo-motor</span>
        <h2>Vehiculos asociados</h2>
        <p>
          Aqui puedes ver las placas guardadas, su VIN, el TEC# detectado, el motor registrado, sus
          adjuntos tecnicos por CPL y el cliente/database asociados a cada vehiculo.
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

        <article className="card metric-card">
          <span className="eyebrow">Con reglas</span>
          <strong>{summary.withRules}</strong>
          <p>Vehiculos con reglas Geotab configuradas</p>
        </article>
      </section>

      <section className="card vehicles-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Explorar</span>
            <h3>Base de vehiculos asociados</h3>
          </div>
        </header>

        <div className="vehicles-filter-bar">
          <div className="form-field vehicles-search-field">
            <label htmlFor="vehicles-search">Buscar</label>
            <input
              id="vehicles-search"
              value={search}
              onChange={(event) => setSearch(event.target.value.toUpperCase())}
              placeholder="Placa, VIN, TEC#, CPL, motor, cliente o database"
            />
          </div>

          <div className="form-field">
            <label htmlFor="vehicles-filter-client">Cliente</label>
            <select
              id="vehicles-filter-client"
              value={filterClient}
              onChange={(event) => setFilterClient(event.target.value)}
            >
              <option value="">Todos</option>
              {clientOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="vehicles-filter-motor">Motor</label>
            <select
              id="vehicles-filter-motor"
              value={filterMotor}
              onChange={(event) => setFilterMotor(event.target.value)}
            >
              <option value="">Todos</option>
              {motorOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="vehicles-filter-database">Database</label>
            <select
              id="vehicles-filter-database"
              value={filterDatabase}
              onChange={(event) => setFilterDatabase(event.target.value)}
            >
              <option value="">Todas</option>
              {databaseOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          </div>

          <div className="actions-row vehicles-filter-actions">
            <button type="button" className="button-secondary button-sm" onClick={handleClear} disabled={loading}>
              Limpiar
            </button>
          </div>
        </div>

        {!customersLoading && customers.length === 0 ? (
          <p className="notice-banner notice-soft">
            No hay clientes ni databases creados. Usa la vista de Clientes para poblar los selectores.
          </p>
        ) : null}

        {!loading && filteredVehicles.length === 0 ? (
          <article className="card empty-state-card vehicles-empty-state">
            <span className="eyebrow">Sin resultados</span>
            <h3>No hay vehiculos asociados para mostrar.</h3>
            <p>
              Ejecuta una consulta exitosa por placa o VIN para que la asociacion quede persistida y
              aparezca aqui.
            </p>
          </article>
        ) : null}

        {filteredVehicles.length > 0 ? (
          <div className="vehicles-table-shell">
            <table className="vehicles-table">
              <thead>
                <tr>
                  <th>Placa</th>
                  <th>VIN</th>
                  <th>CPL</th>
                  <th>Navitrans</th>
                  <th>Cliente</th>
                  <th>Motor</th>
                  <th>TEC#</th>
                  <th>Cliente</th>
                  <th>Database</th>
                  <th>Reglas</th>
                  <th>Acciones</th>
                  <th>Adjuntos</th>
                </tr>
              </thead>
              <tbody>
                {filteredVehicles.map((vehicle) => (
                  <tr key={vehicle.plate}>
                    <td data-label="Placa">
                      <strong>{vehicle.plate}</strong>
                    </td>
                    <td data-label="VIN">{vehicle.vin || "Sin VIN"}</td>
                    <td data-label="CPL">{vehicle.cpl || "Sin CPL"}</td>
                    <td data-label="Navitrans">
                      <span className={`status geotab-badge geotab-${vehicle.geotab_status}`}>
                        {vehicle.geotab_status === "found"
                          ? "OK"
                          : vehicle.geotab_status === "not_found"
                            ? "NO"
                            : "?"}
                      </span>
                    </td>
                    <td data-label="Cliente">
                      {vehicle.geotab_customer_status === "not_applicable" ? (
                        <span className="status geotab-badge geotab-na">N/A</span>
                      ) : (
                        <span className={`status geotab-badge geotab-${vehicle.geotab_customer_status}`}>
                          {vehicle.geotab_customer_status === "found"
                            ? "OK"
                            : vehicle.geotab_customer_status === "not_found"
                              ? "NO"
                              : "?"}
                        </span>
                      )}
                    </td>
                    <td data-label="Motor">{vehicle.engine_name || "Sin catalogar"}</td>
                    <td data-label="TEC#">{vehicle.technical_number}</td>
                    <td data-label="Cliente">{vehicle.client_name || "Sin cliente"}</td>
                    <td data-label="Database">{vehicle.database_name || "Sin database"}</td>
                    <td data-label="Reglas">
                      <span
                        className={`rules-dot ${vehicle.has_motor_rules ? "rules-dot-active" : "rules-dot-inactive"}`}
                        title={vehicle.has_motor_rules ? "Motor con reglas configuradas" : "Sin reglas"}
                      />
                    </td>
                    <td data-label="Acciones">
                      <div className="actions-row vehicles-row-actions">
                        <button
                          type="button"
                          className="button-secondary button-sm"
                          onClick={() => setSelectedVehicle(vehicle)}
                        >
                          Detalles
                        </button>
                        <button
                          type="button"
                          className="icon-button"
                          title="Actualizar datos del vehiculo"
                          onClick={() => handleRefreshVehicle(vehicle.plate)}
                          disabled={refreshingPlates.has(vehicle.plate)}
                        >
                          <svg
                            className={refreshingPlates.has(vehicle.plate) ? "spin" : ""}
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M21 2v6h-6" />
                            <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                            <path d="M3 22v-6h6" />
                            <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                          </svg>
                        </button>
                      </div>
                    </td>
                    <td data-label="Adjuntos">
                      {vehicle.attachments?.length ? (
                        <div className="attachment-list attachment-list-compact">
                          {vehicle.attachments.map((attachment) => (
                            <a
                              key={attachment.id}
                              className="attachment-chip"
                              href={`${API_BASE}${attachment.download_url}`}
                              target="_blank"
                              rel="noreferrer"
                              title={`${attachment.original_filename} | CPL ${attachment.cpl || "Sin CPL"}`}
                              aria-label={`Abrir adjunto ${attachment.original_filename} del CPL ${attachment.cpl || "sin cpl"} en otra pestana`}
                            >
                              <AttachmentIcon contentType={attachment.content_type} />
                            </a>
                          ))}
                        </div>
                      ) : (
                        "Sin adjuntos"
                      )}
                    </td>
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
        title={selectedVehicle ? `Detalles ${selectedVehicle.plate}` : "Detalles del vehiculo"}
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
        onRevalidateCustomerGeotab={handleRevalidateCustomerGeotab}
      />
    </section>
  );
}
