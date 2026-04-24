import { useEffect, useMemo, useState } from "react";

import { getDatabaseTypeLabel } from "../../customers/providerCatalog";

function commonValue(items, getValue) {
  if (!items.length) return "";
  const first = getValue(items[0]) || "";
  return items.every((item) => (getValue(item) || "") === first) ? first : "";
}

export default function BulkVehicleAssignmentModal({
  open,
  loading = false,
  customers = [],
  vehicles = [],
  onClose,
  onSubmit,
}) {
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");

  useEffect(() => {
    if (!open) return;

    const commonClientName = commonValue(vehicles, (vehicle) => vehicle.client_name);
    const matchingCustomer = customers.find((customer) => customer.name === commonClientName);
    const customerId = matchingCustomer ? String(matchingCustomer.id) : "";
    setSelectedCustomerId(customerId);

    const commonDatabaseName = commonValue(vehicles, (vehicle) => vehicle.database_name);
    const commonDatabaseUsername = commonValue(vehicles, (vehicle) => vehicle.database_username);
    const matchingDatabase = matchingCustomer?.databases.find(
      (database) =>
        database.database_name === commonDatabaseName &&
        database.username === commonDatabaseUsername
    );
    setSelectedDatabaseId(matchingDatabase ? String(matchingDatabase.id) : "");
  }, [customers, open, vehicles]);

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
    const exists = availableDatabases.some((database) => String(database.id) === selectedDatabaseId);
    if (!exists) {
      setSelectedDatabaseId("");
    }
  }, [availableDatabases, selectedCustomerId, selectedDatabaseId]);

  if (!open) return null;

  const previewVehicles = vehicles.slice(0, 8);
  const remainingCount = Math.max(vehicles.length - previewVehicles.length, 0);

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({
      customer_database_id: selectedDatabaseId ? Number(selectedDatabaseId) : null,
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Asignacion masiva de vehiculos">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Masivo</span>
            <h3>Asignar cliente y database</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          Se actualizaran {vehicles.length} vehiculos al mismo tiempo.
        </p>

        <div className="bulk-selection-preview">
          {previewVehicles.map((vehicle) => (
            <span key={vehicle.plate} className="warning-chip">
              {vehicle.plate}
            </span>
          ))}
          {remainingCount > 0 ? (
            <span className="warning-chip">+{remainingCount} mas</span>
          ) : null}
        </div>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="bulk-assign-customer">Cliente <span className="form-optional">(opcional)</span></label>
            <select
              id="bulk-assign-customer"
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
            <label htmlFor="bulk-assign-database">Database <span className="form-optional">(opcional)</span></label>
            <select
              id="bulk-assign-database"
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

          <small className="support-copy">
            Si dejas la database vacia, se quitara la asignacion actual de los vehiculos seleccionados.
          </small>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Asignando..." : `Aplicar a ${vehicles.length} vehiculos`}
            </button>
            <button type="button" className="button-secondary" onClick={onClose} disabled={loading}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
