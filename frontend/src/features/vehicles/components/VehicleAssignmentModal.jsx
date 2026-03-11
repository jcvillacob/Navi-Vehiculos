import { useEffect, useMemo, useState } from "react";

export default function VehicleAssignmentModal({
  open,
  loading,
  title = "Asignar cliente y database",
  vehicle = null,
  customers = [],
  registeredMotor = null,
  requiresMotorRegistration = false,
  initialTechnicalNumber = "",
  lockTechnicalNumber = false,
  onClose,
  onSubmit
}) {
  const [technicalNumber, setTechnicalNumber] = useState(initialTechnicalNumber);
  const [engineName, setEngineName] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }

    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");

    const matchingCustomer = customers.find(
      (customer) => customer.name === vehicle?.client_name
    );
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

  if (!open) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({
      technical_number: technicalNumber.trim(),
      engine_name: engineName.trim(),
      customer_database_id: Number(selectedDatabaseId)
    });
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Asignacion</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          El vehiculo solo puede tener un cliente y una database. Las opciones disponibles se cargan
          desde la administracion de clientes.
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
            <label htmlFor="assign-customer">Cliente</label>
            <select
              id="assign-customer"
              value={selectedCustomerId}
              onChange={(event) => setSelectedCustomerId(event.target.value)}
              required
            >
              <option value="">Selecciona un cliente</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="assign-database">Database</label>
            <select
              id="assign-database"
              value={selectedDatabaseId}
              onChange={(event) => setSelectedDatabaseId(event.target.value)}
              disabled={!selectedCustomerId}
              required
            >
              <option value="">Selecciona una database</option>
              {availableDatabases.map((database) => (
                <option key={database.id} value={database.id}>
                  {database.database_name} | {database.username}
                </option>
              ))}
            </select>
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || customers.length === 0}>
              {loading ? "Guardando..." : "Guardar asignacion"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
