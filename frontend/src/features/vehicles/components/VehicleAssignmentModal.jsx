import { useEffect, useMemo, useState } from "react";

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
  registeredMotor = null,
  requiresMotorRegistration = false,
  initialTechnicalNumber = "",
  lockTechnicalNumber = false,
  onClose,
  onSubmit,
  onRevalidateCustomerGeotab = null
}) {
  const [technicalNumber, setTechnicalNumber] = useState(initialTechnicalNumber);
  const [engineName, setEngineName] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [attachmentCpl, setAttachmentCpl] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }

    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");
    setAttachmentFile(null);
    setAttachmentCpl(vehicle?.cpl || "");

    const matchingCustomer = customers.find((customer) => customer.name === vehicle?.client_name);
    const customerId = matchingCustomer ? String(matchingCustomer.id) : "";
    setSelectedCustomerId(customerId);

    const matchingDatabase = matchingCustomer?.databases.find(
      (database) =>
        database.database_name === vehicle?.database_name &&
        database.username === vehicle?.database_username
    );
    setSelectedDatabaseId(matchingDatabase ? String(matchingDatabase.id) : "");
  }, [
    customers,
    initialTechnicalNumber,
    open,
    vehicle?.client_name,
    vehicle?.cpl,
    vehicle?.database_name,
    vehicle?.database_username
  ]);

  const selectedCustomer = useMemo(
    () => customers.find((customer) => String(customer.id) === selectedCustomerId) || null,
    [customers, selectedCustomerId]
  );

  const availableDatabases = selectedCustomer?.databases || [];

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

  if (!open || !vehicle) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({
      technical_number: technicalNumber.trim(),
      engine_name: engineName.trim(),
      attachmentFile,
      attachmentCpl: attachmentCpl.trim(),
      customer_database_id: selectedDatabaseId ? Number(selectedDatabaseId) : null
    });
  };

  return (
    <div className="modal-overlay" role="presentation">
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
          <DataItem label="VIN" value={vehicle.vin} />
          <DataItem label="ESN" value={vehicle.engine_number} />
          <DataItem label="CPL" value={vehicle.cpl} />
          <DataItem label="TEC#" value={vehicle.technical_number} />
          <DataItem label="Motor" value={vehicle.engine_name || "Sin catalogar"} />
          <DataItem label="Cliente" value={vehicle.client_name || "Sin cliente"} />
          <DataItem label="Database" value={vehicle.database_name || "Sin database"} />
          <DataItem label="Usuario DB" value={vehicle.database_username || "Sin usuario"} />
        </div>

        {vehicle.access_url ? (
          <div className="detail-access-url">
            <span className="form-label-subtle">Enlace de acceso</span>
            <a
              href={vehicle.access_url}
              target="_blank"
              rel="noreferrer"
              className="access-url-link"
            >
              {vehicle.access_url}
            </a>
          </div>
        ) : null}

        <p className="support-copy modal-support-copy">
          Desde aqui puedes revisar el detalle y ajustar la asignacion del cliente/database.
        </p>

        <form className="register-form" onSubmit={handleSubmit}>
          {requiresMotorRegistration ? (
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

              <label className="attachment-dropzone" htmlFor="assign-motor-attachment">
                <span className="attachment-dropzone-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 16V4" />
                    <path d="m7 9 5-5 5 5" />
                    <path d="M4 20h16" />
                  </svg>
                </span>
                <strong>{attachmentFile ? attachmentFile.name : "Selecciona una imagen o PDF"}</strong>
                <span>Curvas de torque, potencia o respaldo tecnico. Opcional.</span>
                <input
                  id="assign-motor-attachment"
                  type="file"
                  accept="application/pdf,image/png,image/jpeg,image/webp"
                  onChange={(event) => {
                    const nextFile = event.target.files?.[0] || null;
                    setAttachmentFile(nextFile);
                    if (!nextFile) {
                      setAttachmentCpl(vehicle?.cpl || "");
                    }
                  }}
                />
              </label>

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
                  {database.connection_type === "geotab" ? " [Geotab]" : ""}
                </option>
              ))}
            </select>
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : "Guardar cambios"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cerrar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
