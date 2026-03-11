import { useMemo, useState } from "react";

import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";

export default function CustomersPage() {
  const [customerName, setCustomerName] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [databaseName, setDatabaseName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  const { loading, customers, error, registerCustomer, registerCustomerDatabase } =
    useCustomersCatalog();

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
        password: password.trim()
      });
      setDatabaseName("");
      setUsername("");
      setPassword("");
      setMessage("Database creada para el cliente.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible crear la database");
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

          <form className="register-form" onSubmit={handleCreateCustomer}>
            <div className="form-field">
              <label htmlFor="customer-name">Nombre</label>
              <input
                id="customer-name"
                value={customerName}
                onChange={(event) => setCustomerName(event.target.value)}
                placeholder="Ej: Cliente Norte"
                required
              />
            </div>

            <div className="actions-row modal-actions">
              <button type="submit" disabled={loading}>
                {loading ? "Guardando..." : "Crear cliente"}
              </button>
            </div>
          </form>
        </article>

        <article className="card source-panel">
          <header className="source-panel-header">
            <span className="eyebrow">Paso 2</span>
            <h4>Anadir database</h4>
          </header>

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

            <div className="actions-row modal-actions">
              <button type="submit" disabled={loading || customers.length === 0}>
                {loading ? "Guardando..." : "Crear database"}
              </button>
            </div>
          </form>
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
              <p className="motor-technical-number">Cliente #{customer.id}</p>
            </div>

            <div className="source-grid">
              {customer.databases.length === 0 ? (
                <p className="support-copy">Sin databases registradas.</p>
              ) : (
                customer.databases.map((database) => (
                  <div className="source-field" key={database.id}>
                    <span>{database.database_name}</span>
                    <strong>{database.username}</strong>
                  </div>
                ))
              )}
            </div>
          </article>
        ))}
      </section>
    </section>
  );
}
