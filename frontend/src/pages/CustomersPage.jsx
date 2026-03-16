import { useMemo, useState } from "react";

import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";

function EditCustomerModal({ customer, loading, onClose, onSubmit }) {
  const [name, setName] = useState(customer.name);

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({ name: name.trim() });
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Editar cliente">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Editar</span>
            <h3>Cliente: {customer.name}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="edit-customer-name">Nombre del cliente</label>
            <input
              id="edit-customer-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !name.trim()}>
              {loading ? "Guardando..." : "Guardar cambios"}
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

function EditDatabaseModal({ database, loading, onClose, onSubmit }) {
  const [databaseName, setDatabaseName] = useState(database.database_name);
  const [username, setUsername] = useState(database.username);
  const [password, setPassword] = useState("");
  const [connectionType, setConnectionType] = useState(database.connection_type || "database");
  const [accessUrl, setAccessUrl] = useState(database.access_url || "");

  const showAccessUrl = connectionType !== "geotab";

  const handleSubmit = async (event) => {
    event.preventDefault();
    const payload = {
      database_name: databaseName.trim(),
      username: username.trim(),
      connection_type: connectionType,
      access_url: showAccessUrl ? accessUrl.trim() || null : null
    };
    if (password.trim()) {
      payload.password = password.trim();
    }
    await onSubmit(payload);
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Editar database">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Editar</span>
            <h3>Database: {database.database_name}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="edit-db-name">Database</label>
            <input
              id="edit-db-name"
              value={databaseName}
              onChange={(event) => setDatabaseName(event.target.value)}
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-db-username">Usuario</label>
            <input
              id="edit-db-username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-db-password">
              Contrasena <span className="form-optional">(dejar vacio para no cambiar)</span>
            </label>
            <input
              id="edit-db-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="********"
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-db-connection-type">Tipo de conexion</label>
            <select
              id="edit-db-connection-type"
              value={connectionType}
              onChange={(event) => setConnectionType(event.target.value)}
            >
              <option value="database">Database</option>
              <option value="geotab">Geotab</option>
            </select>
          </div>

          {showAccessUrl ? (
            <div className="form-field">
              <label htmlFor="edit-db-access-url">
                Enlace de acceso <span className="form-optional">(opcional)</span>
              </label>
              <input
                id="edit-db-access-url"
                type="url"
                value={accessUrl}
                onChange={(event) => setAccessUrl(event.target.value)}
                placeholder="https://..."
              />
            </div>
          ) : null}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !databaseName.trim() || !username.trim()}>
              {loading ? "Guardando..." : "Guardar cambios"}
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

export default function CustomersPage() {
  const [customerName, setCustomerName] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [databaseName, setDatabaseName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [connectionType, setConnectionType] = useState("database");
  const [createAccessUrl, setCreateAccessUrl] = useState("");
  const [message, setMessage] = useState("");

  const [editingCustomer, setEditingCustomer] = useState(null);
  const [editingDatabase, setEditingDatabase] = useState(null);

  const {
    loading,
    customers,
    error,
    registerCustomer,
    editCustomer,
    registerCustomerDatabase,
    editCustomerDatabase
  } = useCustomersCatalog();

  const totals = useMemo(
    () => ({
      customers: customers.length,
      databases: customers.reduce((acc, customer) => acc + (customer.database_count || 0), 0)
    }),
    [customers]
  );

  const handleCreateCustomer = async (event) => {
    event.preventDefault();
    try {
      const created = await registerCustomer({ name: customerName.trim() });
      setCustomerName("");
      setSelectedCustomerId(String(created.id));
      setMessage("Cliente creado.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible crear el cliente");
    }
  };

  const handleCreateDatabase = async (event) => {
    event.preventDefault();
    try {
      await registerCustomerDatabase(Number(selectedCustomerId), {
        database_name: databaseName.trim(),
        username: username.trim(),
        password: password.trim(),
        connection_type: connectionType,
        access_url: connectionType !== "geotab" ? createAccessUrl.trim() || null : null
      });
      setDatabaseName("");
      setUsername("");
      setPassword("");
      setConnectionType("database");
      setCreateAccessUrl("");
      setMessage("Database creada para el cliente.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible crear la database");
    }
  };

  const handleEditCustomer = async (payload) => {
    try {
      await editCustomer(editingCustomer.id, payload);
      setEditingCustomer(null);
      setMessage("Cliente actualizado.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible actualizar el cliente");
    }
  };

  const handleEditDatabase = async (payload) => {
    try {
      await editCustomerDatabase(editingDatabase.id, editingDatabase.customer_id, payload);
      setEditingDatabase(null);
      setMessage("Database actualizada.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible actualizar la database");
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Administracion</span>
          <h2>Clientes y databases</h2>
          <p>
            Crea clientes, registra sus databases y deja listas las opciones que luego podras
            seleccionar al editar un vehiculo.
          </p>
        </div>
      </header>

      <section className="vehicles-summary-grid">
        <article className="card metric-card">
          <span className="eyebrow">Clientes</span>
          <strong>{totals.customers}</strong>
          <p>Clientes creados en el catalogo</p>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">Databases</span>
          <strong>{totals.databases}</strong>
          <p>Databases disponibles para asignar a vehiculos</p>
        </article>
      </section>

      {error ? <p className="notice-banner notice-error">{error}</p> : null}
      {message ? <p className="notice-banner notice-info">{message}</p> : null}

      <section className="source-panels-grid">
        <article className="card source-panel">
          <header className="source-panel-header">
            <span className="eyebrow">Paso 1</span>
            <h4>Crear cliente</h4>
          </header>

          <div className="source-panel-body">
            <form className="register-form" onSubmit={handleCreateCustomer}>
              <div className="form-field">
                <label htmlFor="customer-name">Nombre del cliente</label>
                <input
                  id="customer-name"
                  value={customerName}
                  onChange={(event) => setCustomerName(event.target.value)}
                  placeholder="Ej: Cliente Norte"
                  required
                />
              </div>

              <div className="actions-row">
                <button type="submit" disabled={loading}>
                  {loading ? "Guardando..." : "Crear cliente"}
                </button>
              </div>
            </form>
          </div>
        </article>

        <article className="card source-panel">
          <header className="source-panel-header">
            <span className="eyebrow">Paso 2</span>
            <h4>Anadir database</h4>
          </header>

          <div className="source-panel-body">
            <form className="register-form" onSubmit={handleCreateDatabase}>
              <div className="form-field">
                <label htmlFor="database-customer">Cliente</label>
                <select
                  id="database-customer"
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
                <label htmlFor="database-name">Database</label>
                <input
                  id="database-name"
                  value={databaseName}
                  onChange={(event) => setDatabaseName(event.target.value)}
                  placeholder="Ej: fenix_prod"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="database-username">Usuario</label>
                <input
                  id="database-username"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="Ej: navifleet"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="database-password">Contrasena</label>
                <input
                  id="database-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Ej: ********"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="database-connection-type">Tipo de conexion</label>
                <select
                  id="database-connection-type"
                  value={connectionType}
                  onChange={(event) => setConnectionType(event.target.value)}
                >
                  <option value="database">Database</option>
                  <option value="geotab">Geotab</option>
                </select>
              </div>

              {connectionType !== "geotab" ? (
                <div className="form-field">
                  <label htmlFor="database-access-url">
                    Enlace de acceso <span className="form-optional">(opcional)</span>
                  </label>
                  <input
                    id="database-access-url"
                    type="url"
                    value={createAccessUrl}
                    onChange={(event) => setCreateAccessUrl(event.target.value)}
                    placeholder="https://..."
                  />
                </div>
              ) : null}

              <div className="actions-row">
                <button type="submit" disabled={loading || customers.length === 0}>
                  {loading ? "Guardando..." : "Crear database"}
                </button>
              </div>
            </form>
          </div>
        </article>
      </section>

      <section className="motor-cards-grid">
        {customers.map((customer) => (
          <article className="card motor-card" key={customer.id}>
            <div className="motor-card-top">
              <span className="motor-count">{customer.database_count} databases</span>
              <span className="status status-ok">activo</span>
            </div>

            <div className="motor-card-heading">
              <h3>{customer.name}</h3>
              <div className="motor-card-heading-row">
                <p className="motor-technical-number">Cliente #{customer.id}</p>
                <button
                  type="button"
                  className="button-secondary button-sm"
                  onClick={() => setEditingCustomer(customer)}
                >
                  Editar
                </button>
              </div>
            </div>

            <div className="source-grid">
              {customer.databases.length === 0 ? (
                <p className="support-copy">Sin databases registradas.</p>
              ) : (
                customer.databases.map((database) => (
                  <div className="source-field" key={database.id}>
                    <span>
                      {database.database_name}
                      {database.connection_type === "geotab" ? (
                        <span className="status geotab-badge geotab-type-label">Geotab</span>
                      ) : null}
                    </span>
                    <div className="source-field-row">
                      <strong>{database.username}</strong>
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() => setEditingDatabase(database)}
                      >
                        Editar
                      </button>
                    </div>
                    {database.access_url ? (
                      <a
                        href={database.access_url}
                        target="_blank"
                        rel="noreferrer"
                        className="access-url-link"
                      >
                        {database.access_url}
                      </a>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </article>
        ))}
      </section>

      {editingCustomer ? (
        <EditCustomerModal
          customer={editingCustomer}
          loading={loading}
          onClose={() => setEditingCustomer(null)}
          onSubmit={handleEditCustomer}
        />
      ) : null}

      {editingDatabase ? (
        <EditDatabaseModal
          database={editingDatabase}
          loading={loading}
          onClose={() => setEditingDatabase(null)}
          onSubmit={handleEditDatabase}
        />
      ) : null}
    </section>
  );
}
