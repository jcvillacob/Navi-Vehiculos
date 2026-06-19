import { useEffect, useMemo, useState } from "react";

import FileDropzone from "../../../components/FileDropzone";
import { getDatabaseTypeLabel, providerSupportsManualVehicleId } from "../../customers/providerCatalog";
import { CUSTOMER_CATEGORIES, categoryBadgeClass } from "../../categories";

function DataItem({ label, value }) {
  return (
    <div className="data-item">
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

export default function VehicleAssignmentModal({
  open,
  loading,
  title = "Detalles del vehiculo",
  vehicle = null,
  customers = [],
  motors = [],
  registeredMotor = null,
  requiresMotorRegistration = false,
  initialTechnicalNumber = "",
  lockTechnicalNumber = false,
  onClose,
  onSubmit,
  onRevalidateCustomerGeotab = null,
  canEditVehicle = true,
  canRevalidateCustomerGeotab = true,
  onChangeCategory = null,
  savingCategory = false,
  onChangeVocacional = null,
  savingVocacional = false
}) {
  const [technicalNumber, setTechnicalNumber] = useState(initialTechnicalNumber);
  const [engineName, setEngineName] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [attachmentCpl, setAttachmentCpl] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");
  const [motorMode, setMotorMode] = useState("new");
  const [selectedMotorId, setSelectedMotorId] = useState("");
  const [providerVehicleId, setProviderVehicleId] = useState("");
  const [providerVehicleIdTouched, setProviderVehicleIdTouched] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }

    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");
    setAttachmentFile(null);
    setAttachmentCpl(vehicle?.cpl || "");
    setMotorMode(motors.length > 0 ? "existing" : "new");

    // Pre-select the motor that matches the vehicle's current technical_number
    const currentMotor = motors.find(
      (m) => m.technical_number === initialTechnicalNumber
    );
    setSelectedMotorId(currentMotor ? String(currentMotor.id) : "");

    const matchingCustomer = customers.find((customer) => customer.name === vehicle?.client_name);
    const customerId = matchingCustomer ? String(matchingCustomer.id) : "";
    setSelectedCustomerId(customerId);

    const matchingDatabase = matchingCustomer?.databases.find(
      (database) =>
        database.database_name === vehicle?.database_name &&
        database.username === vehicle?.database_username
    );
    setSelectedDatabaseId(matchingDatabase ? String(matchingDatabase.id) : "");
    setProviderVehicleId(vehicle?.provider_vehicle_id || "");
    setProviderVehicleIdTouched(false);
  }, [
    customers,
    initialTechnicalNumber,
    open,
    vehicle?.client_name,
    vehicle?.cpl,
    vehicle?.database_name,
    vehicle?.database_username,
    vehicle?.provider_vehicle_id,
  ]);

  const selectedCustomer = useMemo(
    () => customers.find((customer) => String(customer.id) === selectedCustomerId) || null,
    [customers, selectedCustomerId]
  );

  const availableDatabases = selectedCustomer?.databases || [];

  const selectedDatabase = useMemo(
    () => availableDatabases.find((db) => String(db.id) === selectedDatabaseId) || null,
    [availableDatabases, selectedDatabaseId]
  );

  const showProviderVehicleIdField = !!selectedDatabase &&
    providerSupportsManualVehicleId(selectedDatabase.connection_type);

  useEffect(() => {
    if (!selectedCustomerId) {
      setSelectedDatabaseId("");
      return;
    }

    const exists = availableDatabases.some(
      (database) => String(database.id) === selectedDatabaseId
    );
    if (!exists) {
      setSelectedDatabaseId("");
    }
  }, [availableDatabases, selectedCustomerId, selectedDatabaseId]);

  const selectedExistingMotor = useMemo(
    () => motors.find((m) => String(m.id) === selectedMotorId) || null,
    [motors, selectedMotorId]
  );

  if (!open || !vehicle) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    const shouldSendProviderId =
      providerVehicleIdTouched ||
      providerVehicleId.trim() !== (vehicle?.provider_vehicle_id || "");
    const basePayload = {
      customer_database_id: selectedDatabaseId ? Number(selectedDatabaseId) : null,
    };
    if (shouldSendProviderId && showProviderVehicleIdField) {
      basePayload.provider_vehicle_id = providerVehicleId.trim() || null;
    }
    if (motors.length > 0 && motorMode === "existing" && selectedExistingMotor) {
      await onSubmit({
        technical_number: selectedExistingMotor.technical_number,
        engine_name: selectedExistingMotor.engine_name,
        attachmentFile: null,
        attachmentCpl: "",
        ...basePayload,
      });
    } else {
      await onSubmit({
        technical_number: technicalNumber.trim(),
        engine_name: engineName.trim(),
        attachmentFile,
        attachmentCpl: attachmentCpl.trim(),
        ...basePayload,
      });
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Detalles</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <div className="detail-status-group">
          <GeotabBadge label="Navitrans" status={vehicle.geotab_status} />
          <GeotabBadge
            label="Cliente"
            status={vehicle.geotab_customer_status || "not_applicable"}
          />
          {onRevalidateCustomerGeotab &&
            canRevalidateCustomerGeotab &&
            vehicle.database_connection_type === "geotab" &&
            vehicle.plate ? (
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => onRevalidateCustomerGeotab(vehicle.plate)}
              disabled={loading}
            >
              Revalidar Geotab cliente
            </button>
          ) : null}
        </div>

        <div className="data-grid">
          <DataItem label="Placa" value={vehicle.plate} />
          <DataItem label="Nombre" value={vehicle.nombre_vehiculo} />
          <DataItem label="VIN" value={vehicle.vin} />
          <DataItem label="ESN" value={vehicle.engine_number} />
          <DataItem label="CPL" value={vehicle.cpl} />
          <DataItem label="TEC#" value={vehicle.technical_number} />
          <DataItem label="Motor" value={vehicle.engine_name || "Sin catalogar"} />
          <DataItem label="Cliente" value={vehicle.client_name || "Sin cliente"} />
          <DataItem label="Database" value={vehicle.database_name || "Sin database"} />
          <DataItem label="Usuario DB" value={vehicle.database_username || "Sin usuario"} />
          {vehicle.provider_vehicle_id ? (
            <DataItem
              label={`ID externo${vehicle.is_provider_vehicle_id_manual ? " (manual)" : ""}`}
              value={vehicle.provider_vehicle_id}
            />
          ) : null}
        </div>

        <section className="detail-editable-cards">
          <h4 className="detail-section-title">Informacion general</h4>
          <div className="editable-cards-row">
            <div className="editable-card">
              <div className="editable-card-header">
                <span className="editable-card-label">Categoria</span>
                <span
                  className={`${categoryBadgeClass(vehicle.category)}${vehicle.category_is_inherited ? " is-inherited" : ""}`}
                  title={vehicle.category_is_inherited ? "Heredada del cliente" : "Categoria propia del vehiculo"}
                >
                  {vehicle.category || "Ninguna"}
                </span>
              </div>
              {onChangeCategory && canEditVehicle ? (
                <select
                  className="editable-card-select"
                  value={vehicle.category_is_inherited ? "__inherit__" : (vehicle.category || "__inherit__")}
                  onChange={(event) => onChangeCategory(vehicle, event.target.value)}
                  disabled={savingCategory}
                >
                  <option value="__inherit__">
                    Heredar del cliente ({vehicle.customer_category || "Ninguna"})
                  </option>
                  {CUSTOMER_CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
              ) : (
                <span className="editable-card-hint">
                  {vehicle.category_is_inherited ? "Heredada del cliente" : "Categoria propia"}
                </span>
              )}
            </div>

            <div className="editable-card">
              <div className="editable-card-header">
                <span className="editable-card-label">Vocacional</span>
                <span
                  className={`status vocacional-badge ${vehicle.vocacional ? "is-true" : "is-false"}`}
                  title={vehicle.vocacional ? "Vehiculo de uso vocacional" : "Vehiculo de uso comercial"}
                >
                  {vehicle.vocacional ? "Si" : "No"}
                </span>
              </div>
              {onChangeVocacional && canEditVehicle ? (
                <label className="vocacional-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(vehicle.vocacional)}
                    disabled={savingVocacional}
                    onChange={(event) => onChangeVocacional(vehicle, event.target.checked)}
                  />
                  <span>Marcar como vocacional</span>
                </label>
              ) : (
                <span className="editable-card-hint">
                  {vehicle.vocacional ? "Vocacional" : "Comercial"}
                </span>
              )}
            </div>
          </div>
        </section>

        <p className="support-copy modal-support-copy">
          {canEditVehicle
            ? "Desde aqui puedes revisar el detalle y ajustar la asignacion del cliente/database."
            : "Desde aqui puedes revisar el detalle del vehiculo y su asignacion actual."}
        </p>

        {canEditVehicle ? (
        <form className="register-form" onSubmit={handleSubmit}>
          {requiresMotorRegistration ? (
            <>
              {motors.length > 0 ? (
                <div className="form-field">
                  <label>Motor</label>
                  <div className="motor-mode-toggle">
                    <button
                      type="button"
                      className={`button-sm ${motorMode === "existing" ? "" : "button-secondary"}`}
                      onClick={() => setMotorMode("existing")}
                    >
                      Seleccionar existente
                    </button>
                    <button
                      type="button"
                      className={`button-sm ${motorMode === "new" ? "" : "button-secondary"}`}
                      onClick={() => setMotorMode("new")}
                    >
                      Crear nuevo
                    </button>
                  </div>
                </div>
              ) : null}

              {motors.length > 0 && motorMode === "existing" ? (
                <div className="form-field">
                  <label htmlFor="assign-existing-motor">Motor del catalogo</label>
                  <select
                    id="assign-existing-motor"
                    value={selectedMotorId}
                    onChange={(event) => setSelectedMotorId(event.target.value)}
                    required
                  >
                    <option value="">Selecciona un motor...</option>
                    {motors.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.engine_name} ({m.technical_number})
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <>
                  <div className="form-field">
                    <label htmlFor="assign-technical-number">Technical Engine Configuration #</label>
                    <input
                      id="assign-technical-number"
                      value={technicalNumber}
                      onChange={(event) => setTechnicalNumber(event.target.value)}
                      placeholder="Ej: D103042BX03"
                      readOnly={lockTechnicalNumber}
                      required
                    />
                  </div>

                  <div className="form-field">
                    <label htmlFor="assign-engine-name">Nombre del motor</label>
                    <input
                      id="assign-engine-name"
                      value={engineName}
                      onChange={(event) => setEngineName(event.target.value)}
                      placeholder="Ej: ISX15"
                      required
                    />
                  </div>

                  <FileDropzone
                    id="assign-motor-attachment"
                    file={attachmentFile}
                    onChange={(nextFile) => {
                      setAttachmentFile(nextFile);
                      if (!nextFile) {
                        setAttachmentCpl(vehicle?.cpl || "");
                      }
                    }}
                    label="Arrastra o selecciona una imagen o PDF"
                    hint="Curvas de torque, potencia o respaldo tecnico. Opcional."
                  />

                  {attachmentFile ? (
                    <div className="form-field">
                      <label htmlFor="assign-motor-cpl">CPL del adjunto</label>
                      <input
                        id="assign-motor-cpl"
                        value={attachmentCpl}
                        onChange={(event) => setAttachmentCpl(event.target.value)}
                        placeholder="Ej: 5248"
                        required
                      />
                    </div>
                  ) : null}
                </>
              )}
            </>
          ) : registeredMotor ? (
            <div className="form-field">
              <label htmlFor="assign-registered-motor">Motor registrado</label>
              <input
                id="assign-registered-motor"
                value={`${registeredMotor.engine_name} (${registeredMotor.technical_number})`}
                readOnly
              />
            </div>
          ) : null}

          <div className="form-field">
            <label htmlFor="assign-customer">Cliente <span className="form-optional">(opcional)</span></label>
            <select
              id="assign-customer"
              value={selectedCustomerId}
              onChange={(event) => setSelectedCustomerId(event.target.value)}
            >
              <option value="">Sin cliente</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="assign-database">Database <span className="form-optional">(opcional)</span></label>
            <select
              id="assign-database"
              value={selectedDatabaseId}
              onChange={(event) => setSelectedDatabaseId(event.target.value)}
              disabled={!selectedCustomerId}
            >
              <option value="">Sin database</option>
              {availableDatabases.map((database) => (
                <option key={database.id} value={database.id}>
                  {database.database_name} | {database.username}
                  {database.connection_type && database.connection_type !== "database"
                    ? ` [${getDatabaseTypeLabel(database.connection_type)}]`
                    : ""}
                </option>
              ))}
            </select>
          </div>

          {showProviderVehicleIdField ? (
            <div className="form-field">
              <label htmlFor="assign-provider-vehicle-id">
                ID externo del vehiculo <span className="form-optional">(opcional)</span>
              </label>
              <input
                id="assign-provider-vehicle-id"
                value={providerVehicleId}
                onChange={(event) => {
                  setProviderVehicleId(event.target.value);
                  setProviderVehicleIdTouched(true);
                }}
                placeholder={
                  selectedDatabase.connection_type === "geotab"
                    ? "Ej: b1234 (opcional; por defecto se busca por placa)"
                    : "Ej: 386804 (opcional; por defecto se resuelve por placa)"
                }
                autoComplete="off"
              />
              <small className="support-copy">
                Se usara tal cual para consultar {getDatabaseTypeLabel(selectedDatabase.connection_type)}.
                {vehicle?.is_provider_vehicle_id_manual
                  ? " Este vehiculo tiene un ID manual guardado."
                  : " Deja vacio para que se resuelva automaticamente por placa."}
              </small>
            </div>
          ) : null}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : "Guardar cambios"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cerrar
            </button>
          </div>
        </form>
        ) : null}
      </section>
    </div>
  );
}
