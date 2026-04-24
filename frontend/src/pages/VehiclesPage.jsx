import { useEffect, useMemo, useRef, useState } from "react";

import Can from "../components/Can";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { usePermission } from "../context/AuthContext";
import { useBulkRefresh } from "../context/BulkRefreshContext";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";
import BulkVehicleAssignmentModal from "../features/vehicles/components/BulkVehicleAssignmentModal";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";
import { assignVehicleDatabase, manualAssignVehicle, refreshVehicle, revalidateCustomerGeotab } from "../api/vehicleApi";

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
  const [bulkAssignOpen, setBulkAssignOpen] = useState(false);
  const [bulkAssigning, setBulkAssigning] = useState(false);
  const [selectedPlates, setSelectedPlates] = useState(() => new Set());
  const [refreshingPlates, setRefreshingPlates] = useState(new Set());
  const [filterClient, setFilterClient] = useState("");
  const [filterMotor, setFilterMotor] = useState("");
  const [filterDatabase, setFilterDatabase] = useState("");
  const selectAllRef = useRef(null);
  const { loading, vehicles, error, search, setSearch, loadVehicles } = useVehicleAssignments();
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { motors, loading: motorsLoading } = useMotorsCatalog();
  const { toasts, pushToast } = useToasts();
  const { bulkRefresh, startBulkRefresh, cancelBulkRefresh, acknowledgeBulkRefresh } = useBulkRefresh();
  const canEditVehicles = usePermission("vehicles.edit");
  const canRefreshVehicles = usePermission("vehicles.refresh");

  // React to bulk refresh finishing (works even if user navigated away and came back)
  useEffect(() => {
    if (bulkRefresh?.status !== "finished") return;
    const { wasCancelled, errors, total } = bulkRefresh;

    loadVehicles(search).then(() => {
      if (wasCancelled) {
        pushToast("error", "Reprocesamiento cancelado.");
      } else if (errors.length) {
        pushToast("error", `Completado con ${errors.length} error(es): ${errors.join(", ")}`);
      } else {
        pushToast("success", `${total} vehiculos reprocesados correctamente.`);
      }
      acknowledgeBulkRefresh();
    });
  }, [bulkRefresh?.status]); // eslint-disable-line react-hooks/exhaustive-deps

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

  useEffect(() => {
    const visiblePlates = new Set(filteredVehicles.map((vehicle) => vehicle.plate));
    setSelectedPlates((current) => {
      const next = new Set([...current].filter((plate) => visiblePlates.has(plate)));
      if (
        next.size === current.size &&
        [...next].every((plate) => current.has(plate))
      ) {
        return current;
      }
      return next;
    });
  }, [filteredVehicles]);

  const selectedVehicles = useMemo(
    () => filteredVehicles.filter((vehicle) => selectedPlates.has(vehicle.plate)),
    [filteredVehicles, selectedPlates]
  );

  const allVisibleSelected =
    filteredVehicles.length > 0 && filteredVehicles.every((vehicle) => selectedPlates.has(vehicle.plate));
  const someVisibleSelected =
    !allVisibleSelected && filteredVehicles.some((vehicle) => selectedPlates.has(vehicle.plate));

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  useEffect(() => {
    if (bulkAssignOpen && selectedVehicles.length === 0) {
      setBulkAssignOpen(false);
    }
  }, [bulkAssignOpen, selectedVehicles.length]);

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
    setSelectedPlates(new Set());
    setBulkAssignOpen(false);
  };

  const handleToggleVehicleSelection = (plate) => {
    setSelectedPlates((current) => {
      const next = new Set(current);
      if (next.has(plate)) {
        next.delete(plate);
      } else {
        next.add(plate);
      }
      return next;
    });
  };

  const handleToggleVisibleSelection = () => {
    setSelectedPlates((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        filteredVehicles.forEach((vehicle) => next.delete(vehicle.plate));
      } else {
        filteredVehicles.forEach((vehicle) => next.add(vehicle.plate));
      }
      return next;
    });
  };

  const handleUpdateVehicle = async (payload) => {
    if (!selectedVehicle) {
      return;
    }

    try {
      const motorChanged =
        payload.technical_number &&
        payload.technical_number !== selectedVehicle.technical_number;

      if (motorChanged) {
        await manualAssignVehicle(selectedVehicle.plate, {
          technical_number: payload.technical_number,
          cpl: selectedVehicle.cpl || null,
          vin: selectedVehicle.vin || null,
          engine_number: selectedVehicle.engine_number || null,
          marca: selectedVehicle.marca || null,
          linea: selectedVehicle.linea || null,
          ano_modelo: selectedVehicle.ano_modelo || null,
          tipo_combustible: selectedVehicle.tipo_combustible || null,
          geotab_status: selectedVehicle.geotab_status || "unknown",
        });
      }

      await assignVehicleDatabase(selectedVehicle.plate, {
        customer_database_id: payload.customer_database_id,
        ...(Object.prototype.hasOwnProperty.call(payload, "provider_vehicle_id")
          ? { provider_vehicle_id: payload.provider_vehicle_id }
          : {}),
      });
      pushToast("success", `Vehiculo ${selectedVehicle.plate} actualizado.`);
      setSelectedVehicle(null);
      await loadVehicles(search);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "Error actualizando vehiculo");
    }
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

  const handleBulkAssignVehicles = async (payload) => {
    const plates = selectedVehicles.map((vehicle) => vehicle.plate);
    if (!plates.length) return;

    setBulkAssigning(true);
    try {
      const failedPlates = [];
      for (const plate of plates) {
        try {
          await assignVehicleDatabase(plate, payload);
        } catch {
          failedPlates.push(plate);
        }
      }

      await loadVehicles(search);

      if (failedPlates.length) {
        setSelectedPlates(new Set(failedPlates));
        const sample = failedPlates.slice(0, 5).join(", ");
        pushToast(
          "error",
          `Se actualizaron ${plates.length - failedPlates.length} vehiculos y fallaron ${failedPlates.length}: ${sample}${failedPlates.length > 5 ? "..." : ""}`
        );
      } else {
        setSelectedPlates(new Set());
        pushToast("success", `${plates.length} vehiculos actualizados correctamente.`);
      }

      setBulkAssignOpen(false);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible aplicar la asignacion masiva");
    } finally {
      setBulkAssigning(false);
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
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Placas unicas</span>
          <strong>{summary.total}</strong>
        </article>

        <article className="card metric-card metric-card-compact feature-card-accent">
          <span className="eyebrow">Catalogadas</span>
          <strong>{summary.registered}</strong>
        </article>

        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Con reglas</span>
          <strong>{summary.withRules}</strong>
        </article>
      </section>

      <section className="card vehicles-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Explorar</span>
            <h3>Base de vehiculos asociados</h3>
          </div>

          <div className="actions-row section-heading-actions">
            <button type="button" className="button-secondary button-sm" onClick={handleClear} disabled={loading}>
              Limpiar
            </button>
            <Can permission="vehicles.edit">
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={() => setBulkAssignOpen(true)}
                disabled={loading || bulkAssigning || selectedVehicles.length === 0}
              >
                Asignar seleccionados ({selectedVehicles.length})
              </button>
            </Can>
            <Can permission="vehicles.refresh">
              <button
                type="button"
                className="button button-sm"
                onClick={() => startBulkRefresh(filteredVehicles.map((v) => v.plate))}
                disabled={loading || Boolean(bulkRefresh) || filteredVehicles.length === 0}
              >
                Reprocesar todos ({filteredVehicles.length})
              </button>
            </Can>
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

        </div>

        <div className="bulk-selection-toolbar">
          <span className="bulk-selection-meta">
            Seleccionados: <strong>{selectedVehicles.length}</strong> de {filteredVehicles.length} visibles
          </span>
          <div className="actions-row">
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={handleToggleVisibleSelection}
              disabled={filteredVehicles.length === 0}
            >
              {allVisibleSelected ? "Quitar visibles" : `Seleccionar visibles (${filteredVehicles.length})`}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => setSelectedPlates(new Set())}
              disabled={selectedVehicles.length === 0}
            >
              Limpiar seleccion
            </button>
          </div>
        </div>

        {/* ── Bulk refresh progress ── */}
        {bulkRefresh?.status === "running" ? (
          <div className="bulk-progress-bar-container">
            <div className="bulk-progress-header">
              <span className="bulk-progress-label">
                Reprocesando {bulkRefresh.done}/{bulkRefresh.total}
                {bulkRefresh.currentPlate ? ` — ${bulkRefresh.currentPlate}` : ""}
              </span>
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={cancelBulkRefresh}
              >
                Cancelar
              </button>
            </div>
            <div className="bulk-progress-track">
              <div
                className="bulk-progress-fill"
                style={{ width: `${(bulkRefresh.done / bulkRefresh.total) * 100}%` }}
              />
            </div>
            <span className="bulk-progress-percent">
              {Math.round((bulkRefresh.done / bulkRefresh.total) * 100)}%
            </span>
          </div>
        ) : null}

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
                  <th>
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={handleToggleVisibleSelection}
                      aria-label="Seleccionar vehiculos visibles"
                    />
                  </th>
                  <th>Placa</th>
                  <th>Marca</th>
                  <th>Linea</th>
                  <th>Año</th>
                  <th>Combustible</th>
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
                  <tr key={vehicle.plate} className={selectedPlates.has(vehicle.plate) ? "is-selected" : ""}>
                    <td data-label="Seleccion">
                      <input
                        type="checkbox"
                        checked={selectedPlates.has(vehicle.plate)}
                        onChange={() => handleToggleVehicleSelection(vehicle.plate)}
                        aria-label={`Seleccionar ${vehicle.plate}`}
                      />
                    </td>
                    <td data-label="Placa">
                      <strong>{vehicle.plate}</strong>
                    </td>
                    <td data-label="Marca">{vehicle.marca || "-"}</td>
                    <td data-label="Linea">{vehicle.linea || "-"}</td>
                    <td data-label="Año">{vehicle.ano_modelo || "-"}</td>
                    <td data-label="Combustible">{vehicle.tipo_combustible || "-"}</td>
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
                        <Can permission="vehicles.refresh">
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
                        </Can>
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
        loading={loading || customersLoading || motorsLoading}
        title={selectedVehicle ? `Detalles ${selectedVehicle.plate}` : "Detalles del vehiculo"}
        vehicle={selectedVehicle}
        customers={customers}
        motors={motors}
        requiresMotorRegistration
        initialTechnicalNumber={selectedVehicle?.technical_number || ""}
        onClose={() => setSelectedVehicle(null)}
        onSubmit={handleUpdateVehicle}
        onRevalidateCustomerGeotab={handleRevalidateCustomerGeotab}
        canEditVehicle={canEditVehicles}
        canRevalidateCustomerGeotab={canRefreshVehicles}
      />

      <BulkVehicleAssignmentModal
        open={bulkAssignOpen}
        loading={bulkAssigning || loading || customersLoading}
        customers={customers}
        vehicles={selectedVehicles}
        onClose={() => setBulkAssignOpen(false)}
        onSubmit={handleBulkAssignVehicles}
      />
    </section>
  );
}
