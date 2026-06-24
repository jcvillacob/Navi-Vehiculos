import { useEffect, useMemo, useState } from "react";

import Can from "../components/Can";
import PasswordInput from "../components/PasswordInput";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { usePermission } from "../context/AuthContext";
import {
  createDatabaseCredential,
  deleteDatabaseCredential,
  inspectGeotabRule,
  listDatabaseCredentials,
  listMotors,
  resolveGeotabRule,
  updateDatabaseCredential
} from "../api/vehicleApi";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { CUSTOMER_CATEGORIES, categoryBadgeClass } from "../features/categories";
import {
  DATABASE_PROVIDERS,
  buildProviderConfigPayload,
  getDatabaseTypeLabel,
  getInitialProviderConfig,
  getProviderDefinition,
  getProviderDetailRows,
  providerUsesAccessUrl
} from "../features/customers/providerCatalog";

function formatMatchModeLabel(value) {
  return value === "any" ? "Cualquiera" : "Todas";
}

const RULE_EVENT_TYPES = [
  { value: "", label: "Evento general" },
  { value: "exceso_rpm", label: "Exceso RPM" }
];

function getRuleApplications(rule) {
  if (Array.isArray(rule.applications) && rule.applications.length > 0) {
    return rule.applications;
  }
  return [
    {
      id: `legacy-${rule.id}`,
      category: rule.category || "operacion",
      motor_id: null,
      motor_name: null,
      technical_number: null,
      event_type: null
    }
  ];
}

function formatRuleApplicationLabel(application) {
  if (application.event_type === "exceso_rpm") return "Exceso RPM";
  return application.category === "habito_seguro" ? "Hábito seguro" : "Operación";
}

function sameMotorIdentity(application, group) {
  return (
    String(application.motor_name || "").trim().toLowerCase() ===
      String(group.motor_name || "").trim().toLowerCase() &&
    String(application.technical_number || "").trim() ===
      String(group.technical_number || "").trim()
  );
}

/* ── Create Customer Modal ─────────────────────────────────────────── */
function CreateCustomerModal({ loading, onClose, onSubmit }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("Ninguna");

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({ name: name.trim(), category });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Nuevo cliente">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Nuevo</span>
            <h3>Crear cliente</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="create-customer-name">Nombre del cliente</label>
            <input
              id="create-customer-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Ej: Cliente Norte"
              required
              autoFocus
            />
          </div>

          <div className="form-field">
            <label htmlFor="create-customer-category">Categoria</label>
            <select
              id="create-customer-category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {CUSTOMER_CATEGORIES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !name.trim()}>
              {loading ? "Guardando..." : "Crear cliente"}
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

/* ── Edit Customer Modal ───────────────────────────────────────────── */
function EditCustomerModal({ customer, loading, onClose, onSubmit }) {
  const [name, setName] = useState(customer.name);
  const [category, setCategory] = useState(customer.category || "Ninguna");

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({ name: name.trim(), category });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
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

          <div className="form-field">
            <label htmlFor="edit-customer-category">Categoria</label>
            <select
              id="edit-customer-category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {CUSTOMER_CATEGORIES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
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

/* ── Create Database Modal ─────────────────────────────────────────── */
function CreateDatabaseModal({ customers, loading, preselectedCustomerId, onClose, onSubmit }) {
  const [selectedCustomerId, setSelectedCustomerId] = useState(preselectedCustomerId || "");
  const [databaseName, setDatabaseName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [connectionType, setConnectionType] = useState("database");
  const [accessUrl, setAccessUrl] = useState("");
  const [providerConfigState, setProviderConfigState] = useState(() => getInitialProviderConfig("database"));

  const providerDefinition = getProviderDefinition(connectionType);
  const showAccessUrl = providerUsesAccessUrl(connectionType);
  const showArtimoFields = connectionType === "artimo";
  const showLogitracsFields = connectionType === "logitracs_triton";

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit(Number(selectedCustomerId), {
      database_name: databaseName.trim(),
      username: username.trim(),
      password: password.trim(),
      connection_type: connectionType,
      access_url: showAccessUrl ? accessUrl.trim() || null : null,
      provider_config: buildProviderConfigPayload(connectionType, providerConfigState)
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Nueva database">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Nueva</span>
            <h3>Anadir database</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="create-db-customer">Cliente</label>
            <select
              id="create-db-customer"
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
            <label htmlFor="create-db-name">Database</label>
            <input
              id="create-db-name"
              value={databaseName}
              onChange={(event) => setDatabaseName(event.target.value)}
              placeholder="Ej: fenix_prod"
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="create-db-username">Usuario</label>
            <input
              id="create-db-username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="Ej: navifleet"
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="create-db-password">Contrasena</label>
            <PasswordInput
              id="create-db-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Ej: ********"
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="create-db-connection-type">Tipo de conexion</label>
            <select
              id="create-db-connection-type"
              value={connectionType}
              onChange={(event) => {
                const nextType = event.target.value;
                setConnectionType(nextType);
                setProviderConfigState(getInitialProviderConfig(nextType));
                if (!providerUsesAccessUrl(nextType)) {
                  setAccessUrl("");
                }
              }}
            >
              {DATABASE_PROVIDERS.map((provider) => (
                <option key={provider.key} value={provider.key}>
                  {provider.label}
                </option>
              ))}
            </select>
            <small className="support-copy">{providerDefinition.description}</small>
          </div>

          {showArtimoFields ? (
            <>
              <div className="form-field">
                <label htmlFor="create-db-artimo-customer-id">Artimo customer_id</label>
                <input
                  id="create-db-artimo-customer-id"
                  value={providerConfigState.customerId || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, customerId: event.target.value }))
                  }
                  placeholder="Ej: 939b02d6-074c-4416-87ca-877c443be9f9"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="create-db-artimo-group-name">Artimo group_name</label>
                <input
                  id="create-db-artimo-group-name"
                  value={providerConfigState.groupName || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, groupName: event.target.value }))
                  }
                  placeholder="Ej: NAVITRANS"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="create-db-artimo-api-base">API base URL</label>
                <input
                  id="create-db-artimo-api-base"
                  type="url"
                  value={providerConfigState.apiBaseUrl || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, apiBaseUrl: event.target.value }))
                  }
                  placeholder="https://api.artimo.com.co"
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="create-db-artimo-auth-base">Auth base URL</label>
                <input
                  id="create-db-artimo-auth-base"
                  type="url"
                  value={providerConfigState.authBaseUrl || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, authBaseUrl: event.target.value }))
                  }
                  placeholder="https://apifront.artimo.com.co"
                  required
                />
              </div>

              <div className="rule-assign-form-row">
                <div className="form-field">
                  <label htmlFor="create-db-artimo-start-hour">Hora inicio UTC</label>
                  <input
                    id="create-db-artimo-start-hour"
                    type="number"
                    min="0"
                    max="23"
                    value={providerConfigState.monthStartHourUtc || ""}
                    onChange={(event) =>
                      setProviderConfigState((current) => ({ ...current, monthStartHourUtc: event.target.value }))
                    }
                    required
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="create-db-artimo-end-hour">Hora fin UTC</label>
                  <input
                    id="create-db-artimo-end-hour"
                    type="number"
                    min="0"
                    max="23"
                    value={providerConfigState.monthEndHourUtc || ""}
                    onChange={(event) =>
                      setProviderConfigState((current) => ({ ...current, monthEndHourUtc: event.target.value }))
                    }
                    required
                  />
                </div>
              </div>
            </>
          ) : null}

          {showLogitracsFields ? (
            <>
              <div className="form-field">
                <label htmlFor="create-db-logitracs-codigo">Codigo empresa LogiTracs</label>
                <input
                  id="create-db-logitracs-codigo"
                  value={providerConfigState.codigoEmpresa || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, codigoEmpresa: event.target.value }))
                  }
                  placeholder="Opcional, ej. GRUPOK"
                />
              </div>

              <div className="form-field">
                <label htmlFor="create-db-logitracs-password-web">
                  Password web LogiVIM <span className="form-optional">(opcional)</span>
                </label>
                <PasswordInput
                  id="create-db-logitracs-password-web"
                  value={providerConfigState.passwordWeb || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, passwordWeb: event.target.value }))
                  }
                  placeholder="Si se omite, usa la password principal"
                />
              </div>
            </>
          ) : null}

          {showAccessUrl ? (
            <div className="form-field">
              <label htmlFor="create-db-access-url">
                Enlace de acceso <span className="form-optional">(opcional)</span>
              </label>
              <input
                id="create-db-access-url"
                type="url"
                value={accessUrl}
                onChange={(event) => setAccessUrl(event.target.value)}
                placeholder="https://..."
              />
            </div>
          ) : null}

          <div className="actions-row modal-actions">
            <button
              type="submit"
              disabled={
                loading ||
                !selectedCustomerId ||
                !databaseName.trim() ||
                !username.trim() ||
                !password.trim() ||
                (showArtimoFields &&
                  (!(providerConfigState.customerId || "").trim() || !(providerConfigState.groupName || "").trim()))
              }
            >
              {loading ? "Guardando..." : "Crear database"}
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

/* ── Edit Database Modal ───────────────────────────────────────────── */
function EditDatabaseModal({ database, loading, onClose, onSubmit }) {
  const [databaseName, setDatabaseName] = useState(database.database_name);
  const [username, setUsername] = useState(database.username);
  const [password, setPassword] = useState("");
  const [connectionType, setConnectionType] = useState(database.connection_type || "database");
  const [accessUrl, setAccessUrl] = useState(database.access_url || "");
  const [providerConfigState, setProviderConfigState] = useState(() =>
    getInitialProviderConfig(database.connection_type || "database", database.provider_config)
  );

  const providerDefinition = getProviderDefinition(connectionType);
  const showAccessUrl = providerUsesAccessUrl(connectionType);
  const showArtimoFields = connectionType === "artimo";
  const showLogitracsFields = connectionType === "logitracs_triton";

  const handleSubmit = async (event) => {
    event.preventDefault();
    const payload = {
      database_name: databaseName.trim(),
      username: username.trim(),
      connection_type: connectionType,
      access_url: showAccessUrl ? accessUrl.trim() || null : null,
      provider_config: buildProviderConfigPayload(connectionType, providerConfigState)
    };
    if (password.trim()) {
      payload.password = password.trim();
    }
    await onSubmit(payload);
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
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
            <PasswordInput
              id="edit-db-password"
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
              onChange={(event) => {
                const nextType = event.target.value;
                setConnectionType(nextType);
                setProviderConfigState(getInitialProviderConfig(nextType, database.provider_config));
                if (!providerUsesAccessUrl(nextType)) {
                  setAccessUrl("");
                }
              }}
            >
              {DATABASE_PROVIDERS.map((provider) => (
                <option key={provider.key} value={provider.key}>
                  {provider.label}
                </option>
              ))}
            </select>
            <small className="support-copy">{providerDefinition.description}</small>
          </div>

          {showArtimoFields ? (
            <>
              <div className="form-field">
                <label htmlFor="edit-db-artimo-customer-id">Artimo customer_id</label>
                <input
                  id="edit-db-artimo-customer-id"
                  value={providerConfigState.customerId || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, customerId: event.target.value }))
                  }
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="edit-db-artimo-group-name">Artimo group_name</label>
                <input
                  id="edit-db-artimo-group-name"
                  value={providerConfigState.groupName || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, groupName: event.target.value }))
                  }
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="edit-db-artimo-api-base">API base URL</label>
                <input
                  id="edit-db-artimo-api-base"
                  type="url"
                  value={providerConfigState.apiBaseUrl || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, apiBaseUrl: event.target.value }))
                  }
                  required
                />
              </div>

              <div className="form-field">
                <label htmlFor="edit-db-artimo-auth-base">Auth base URL</label>
                <input
                  id="edit-db-artimo-auth-base"
                  type="url"
                  value={providerConfigState.authBaseUrl || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, authBaseUrl: event.target.value }))
                  }
                  required
                />
              </div>

              <div className="rule-assign-form-row">
                <div className="form-field">
                  <label htmlFor="edit-db-artimo-start-hour">Hora inicio UTC</label>
                  <input
                    id="edit-db-artimo-start-hour"
                    type="number"
                    min="0"
                    max="23"
                    value={providerConfigState.monthStartHourUtc || ""}
                    onChange={(event) =>
                      setProviderConfigState((current) => ({ ...current, monthStartHourUtc: event.target.value }))
                    }
                    required
                  />
                </div>
                <div className="form-field">
                  <label htmlFor="edit-db-artimo-end-hour">Hora fin UTC</label>
                  <input
                    id="edit-db-artimo-end-hour"
                    type="number"
                    min="0"
                    max="23"
                    value={providerConfigState.monthEndHourUtc || ""}
                    onChange={(event) =>
                      setProviderConfigState((current) => ({ ...current, monthEndHourUtc: event.target.value }))
                    }
                    required
                  />
                </div>
              </div>
            </>
          ) : null}

          {showLogitracsFields ? (
            <>
              <div className="form-field">
                <label htmlFor="edit-db-logitracs-codigo">Codigo empresa LogiTracs</label>
                <input
                  id="edit-db-logitracs-codigo"
                  value={providerConfigState.codigoEmpresa || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, codigoEmpresa: event.target.value }))
                  }
                  placeholder="Opcional, ej. GRUPOK"
                />
              </div>

              <div className="form-field">
                <label htmlFor="edit-db-logitracs-password-web">
                  Password web LogiVIM <span className="form-optional">(opcional)</span>
                </label>
                <PasswordInput
                  id="edit-db-logitracs-password-web"
                  value={providerConfigState.passwordWeb || ""}
                  onChange={(event) =>
                    setProviderConfigState((current) => ({ ...current, passwordWeb: event.target.value }))
                  }
                  placeholder="Si se omite, usa la password principal"
                />
              </div>
            </>
          ) : null}

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
            <button
              type="submit"
              disabled={
                loading ||
                !databaseName.trim() ||
                !username.trim() ||
                (showArtimoFields &&
                  (!(providerConfigState.customerId || "").trim() ||
                    !(providerConfigState.groupName || "").trim()))
              }
            >
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

function RuleFactChips({ facts = [] }) {
  if (!facts.length) {
    return <p className="support-copy">Sin condiciones resumidas.</p>;
  }

  return (
    <div className="rule-facts">
      {facts.map((fact) => (
        <span className="rule-fact-chip" key={fact}>
          {fact}
        </span>
      ))}
    </div>
  );
}

function RuleConditionTree({ node }) {
  if (!node) {
    return <p className="support-copy">Sin arbol tecnico disponible.</p>;
  }

  return (
    <ul className="rule-tree">
      <li className={`rule-tree-node rule-tree-node-${node.kind}`}>
        <span>{node.label}</span>
        {node.children?.length ? (
          <div className="rule-tree-children">
            {node.children.map((child, index) => (
              <RuleConditionTree key={`${child.label}-${index}`} node={child} />
            ))}
          </div>
        ) : null}
      </li>
    </ul>
  );
}

function RuleSummaryCard({ inspection, loading = false, error = "", emptyMessage = "Selecciona una regla." }) {
  if (loading) {
    return <p className="support-copy">Consultando regla...</p>;
  }

  if (error) {
    return <p className="notice-banner notice-error">{error}</p>;
  }

  if (!inspection) {
    return <p className="support-copy">{emptyMessage}</p>;
  }

  return (
    <div className="rule-summary-card">
      <div className="rule-summary-header">
        <div>
          <p className="rule-summary-title">{inspection.name || "Regla no disponible"}</p>
          <p className="rule-summary-id">{inspection.rule_id}</p>
        </div>
        <div className="rule-summary-badges">
          <span className={`rule-badge ${inspection.exists ? "is-ok" : "is-muted"}`}>{inspection.status}</span>
          {inspection.type ? <span className="rule-badge">{inspection.type}</span> : null}
        </div>
      </div>

      <p className="rule-headline">{inspection.headline || "Sin resumen disponible."}</p>

      {inspection.comment ? <p className="rule-comment">{inspection.comment}</p> : null}

      <div className="rule-meta-grid">
        <div>
          <span className="db-detail-label">Grupos</span>
          <strong>{inspection.groups_count ?? 0}</strong>
        </div>
        <div>
          <span className="db-detail-label">Existencia</span>
          <strong>{inspection.exists ? "Confirmada" : "No encontrada"}</strong>
        </div>
      </div>

      <RuleFactChips facts={inspection.facts} />

      {inspection.message ? <p className="support-copy">{inspection.message}</p> : null}
    </div>
  );
}

function RuleInspectorPanel({ inspection, loading, error, selectedRule }) {
  if (!selectedRule) {
    return <p className="support-copy">Selecciona una regla para ver su inspector.</p>;
  }

  return (
    <div className="rule-inspector-card">
      <RuleSummaryCard inspection={inspection} loading={loading} error={error} />

      {!loading && !error && inspection ? (
        <>
          <details className="rule-accordion" open>
            <summary>Arbol tecnico</summary>
            <RuleConditionTree node={inspection.tree} />
          </details>

          <details className="rule-accordion">
            <summary>Condicion cruda</summary>
            <pre className="rule-raw-json">{JSON.stringify(inspection.raw_condition, null, 2)}</pre>
          </details>
        </>
      ) : null}
    </div>
  );
}

/* ── Credentials pool panel (multiple credentials per database) ────── */
function CredentialsPanel({ databaseId, canEdit = true }) {
  const [credentials, setCredentials] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const records = await listDatabaseCredentials(databaseId);
      setCredentials(records);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible cargar las credenciales");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databaseId]);

  const handleAdd = async (event) => {
    event.preventDefault();
    if (!newUsername.trim() || !newPassword.trim()) return;
    setSaving(true);
    setError("");
    try {
      await createDatabaseCredential(databaseId, {
        username: newUsername.trim(),
        password: newPassword.trim(),
        label: newLabel.trim() || null
      });
      setNewUsername("");
      setNewPassword("");
      setNewLabel("");
      setShowAddForm(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible crear la credencial");
    } finally {
      setSaving(false);
    }
  };

  const handleToggleActive = async (credential) => {
    setSaving(true);
    setError("");
    try {
      await updateDatabaseCredential(credential.id, { is_active: !credential.is_active });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible actualizar la credencial");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (credentialId) => {
    setSaving(true);
    setError("");
    try {
      await deleteDatabaseCredential(credentialId);
      setConfirmDeleteId(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible eliminar la credencial");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rules-motor-section">
      <div className="rules-motor-section-header">
        <div>
          <span className="rules-label">Credenciales</span>
          <span className="rules-count-subtle">
            {credentials.length} {credentials.length === 1 ? "credencial" : "credenciales"} · rotación automática
          </span>
        </div>
        {canEdit ? (
          <button
            type="button"
            className={`button-sm ${showAddForm ? "button-secondary" : "button"}`}
            onClick={() => setShowAddForm(!showAddForm)}
          >
            {showAddForm ? "Cancelar" : "Agregar credencial"}
          </button>
        ) : null}
      </div>

      {error ? <p className="notice-banner notice-error">{error}</p> : null}

      {canEdit && showAddForm ? (
        <form className="rule-assign-form" onSubmit={handleAdd}>
          <div className="rule-assign-form-row">
            <div className="form-field">
              <label htmlFor={`credential-username-${databaseId}`}>Usuario</label>
              <input
                id={`credential-username-${databaseId}`}
                value={newUsername}
                onChange={(event) => setNewUsername(event.target.value)}
                placeholder="usuario@dominio.com"
                required
                autoComplete="off"
              />
            </div>
            <div className="form-field">
              <label htmlFor={`credential-password-${databaseId}`}>Contraseña</label>
              <input
                id={`credential-password-${databaseId}`}
                type="password"
                value={newPassword}
                onChange={(event) => setNewPassword(event.target.value)}
                required
                autoComplete="new-password"
              />
            </div>
            <div className="form-field">
              <label htmlFor={`credential-label-${databaseId}`}>Etiqueta (opcional)</label>
              <input
                id={`credential-label-${databaseId}`}
                value={newLabel}
                onChange={(event) => setNewLabel(event.target.value)}
                placeholder="cuenta reportes"
                autoComplete="off"
              />
            </div>
          </div>
          <button type="submit" className="button button-sm" disabled={saving}>
            Guardar credencial
          </button>
        </form>
      ) : null}

      {loading ? (
        <p className="support-copy">Cargando credenciales...</p>
      ) : credentials.length > 0 ? (
        <div className="unassigned-rules-list">
          {credentials.map((credential) => (
            <div className="rule-list-item" key={`credential-${credential.id}`}>
              <div className="rule-select-button" style={{ cursor: "default" }}>
                <span className="rule-list-name">
                  {credential.username}
                  {credential.label ? <span className="rules-count-subtle"> · {credential.label}</span> : null}
                </span>
                <span className="rule-id-cell">
                  {credential.is_active ? "Activa" : "Inactiva"}
                  {credential.last_used_at
                    ? ` · usada ${new Date(credential.last_used_at).toLocaleString()}`
                    : " · sin uso"}
                  {credential.last_auth_error_at ? " · ⚠ error de autenticación reciente" : ""}
                </span>
              </div>
              {canEdit ? (
                <>
                  <button
                    type="button"
                    className="button-secondary button-sm"
                    onClick={() => handleToggleActive(credential)}
                    disabled={saving}
                  >
                    {credential.is_active ? "Desactivar" : "Activar"}
                  </button>
                  {confirmDeleteId === credential.id ? (
                    <>
                      <button
                        type="button"
                        className="button-secondary button-sm rule-confirm-delete"
                        onClick={() => handleDelete(credential.id)}
                        disabled={saving}
                      >
                        Confirmar
                      </button>
                      <button
                        type="button"
                        className="icon-button rule-delete-button"
                        onClick={() => setConfirmDeleteId(null)}
                        title="Cancelar"
                      >
                        &#8592;
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="icon-button rule-delete-button"
                      onClick={() => setConfirmDeleteId(credential.id)}
                      disabled={saving}
                      title="Eliminar"
                    >
                      &#10005;
                    </button>
                  )}
                </>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="rules-empty-state">
          <p>Sin credenciales registradas para esta database.</p>
        </div>
      )}
    </div>
  );
}

/* ── Database Detail Modal (info + rules for Geotab) ───────────────── */
function DatabaseDetailModal({
  database,
  loading,
  motors,
  motorsLoading,
  onClose,
  onEdit,
  onAddRule,
  onDeleteRule,
  onAddRuleGroup,
  onDeleteRuleGroup,
  canEdit = true
}) {
  const [ruleId, setRuleId] = useState("");
  const [ruleCategory, setRuleCategory] = useState("operacion");
  const [ruleMotorId, setRuleMotorId] = useState("");
  const [ruleEventType, setRuleEventType] = useState("");
  const [resolveStatus, setResolveStatus] = useState("idle");
  const [resolveError, setResolveError] = useState("");
  const [rulePreview, setRulePreview] = useState(null);
  const [selectedRuleId, setSelectedRuleId] = useState(null);
  const [pendingSelectionRuleId, setPendingSelectionRuleId] = useState(null);
  const [inspection, setInspection] = useState(null);
  const [inspectionLoading, setInspectionLoading] = useState(false);
  const [inspectionError, setInspectionError] = useState("");
  const [confirmDeleteRuleId, setConfirmDeleteRuleId] = useState(null);
  const [selectedMotorId, setSelectedMotorId] = useState("");
  const [matchMode, setMatchMode] = useState("all");
  const [selectedGroupRuleIds, setSelectedGroupRuleIds] = useState([]);
  const [confirmDeleteGroupId, setConfirmDeleteGroupId] = useState(null);
  const [showAssignForm, setShowAssignForm] = useState(false);

  const rules = database.rules || [];
  const ruleGroups = database.rule_groups || [];
  const isGeotab = database.connection_type === "geotab";
  const providerDetailRows = getProviderDetailRows(database.connection_type, database.provider_config);
  const selectedRule = rules.find((rule) => rule.id === selectedRuleId) || null;

  const operationRules = useMemo(
    () => rules.filter((rule) =>
      getRuleApplications(rule).some((application) => application.category === "operacion")
    ),
    [rules]
  );
  const safeHabitRules = useMemo(
    () => rules.flatMap((rule) =>
      getRuleApplications(rule)
        .filter((application) => application.category === "habito_seguro" && !application.motor_id)
        .map((application) => ({ rule, application }))
    ),
    [rules]
  );

  const motorGroupCards = useMemo(() => {
    const groupsByMotor = new Map();

    const ensureGroup = ({ key, baseGroup, application }) => {
      if (!groupsByMotor.has(key)) {
        groupsByMotor.set(key, {
          id: baseGroup?.id ?? `motor-app-${application.motor_id}`,
          database_id: baseGroup?.database_id ?? database.id,
          motor_id: baseGroup?.motor_id ?? application.motor_id,
          motor_name: baseGroup?.motor_name ?? application.motor_name,
          technical_number: baseGroup?.technical_number ?? application.technical_number,
          name: baseGroup?.name ?? application.motor_name,
          match_mode: baseGroup?.match_mode ?? "all",
          rules: [],
          isVirtual: !baseGroup
        });
      }
      return groupsByMotor.get(key);
    };

    for (const group of ruleGroups) {
      const key = `${String(group.motor_name).trim().toLowerCase()}|${String(group.technical_number).trim()}`;
      const merged = ensureGroup({ key, baseGroup: group, application: {} });
      const existingRuleKeys = new Set(merged.rules.map((rule) => rule.application_key));
      for (const rule of group.rules) {
        const fullRule = rules.find((candidate) => candidate.id === rule.rule_record_id);
        const hasMotorRpmApplication = fullRule
          ? getRuleApplications(fullRule).some(
              (application) =>
                application.category === "habito_seguro" &&
                application.event_type === "exceso_rpm" &&
                sameMotorIdentity(application, group)
            )
          : false;
        if (hasMotorRpmApplication) continue;
        const applicationKey = `${rule.rule_record_id}:operacion:`;
        if (existingRuleKeys.has(applicationKey)) continue;
        existingRuleKeys.add(applicationKey);
        merged.rules.push({
          ...rule,
          application_key: applicationKey,
          application_category: "operacion",
          event_type: null
        });
      }
      merged.rules.sort((a, b) => a.name.localeCompare(b.name));
      merged.isVirtual = false;
    }

    for (const rule of rules) {
      for (const application of getRuleApplications(rule)) {
        if (!application.motor_id) continue;
        const motorName = application.motor_name || "Motor";
        const technicalNumber = application.technical_number || "";
        const key = `${String(motorName).trim().toLowerCase()}|${String(technicalNumber).trim()}`;
        const merged = ensureGroup({ key, application });
        const applicationKey = `${rule.id}:${application.category}:${application.event_type || ""}`;
        if (merged.rules.some((currentRule) => currentRule.application_key === applicationKey)) {
          continue;
        }
        if (
          application.category === "operacion" &&
          merged.rules.some(
            (currentRule) =>
              currentRule.rule_record_id === rule.id &&
              currentRule.application_category === "operacion"
          )
        ) {
          continue;
        }
        merged.rules.push({
          rule_record_id: rule.id,
          name: rule.name,
          rule_id: rule.rule_id,
          application_key: applicationKey,
          application_category: application.category,
          event_type: application.event_type
        });
      }
    }

    return Array.from(groupsByMotor.values())
      .map((group) => ({
        ...group,
        rules: [...group.rules].sort((a, b) => a.name.localeCompare(b.name))
      }))
      .sort((a, b) => a.motor_name.localeCompare(b.motor_name) || a.name.localeCompare(b.name));
  }, [database.id, ruleGroups, rules]);

  const assignedRuleIds = useMemo(() => {
    const ids = new Set();
    for (const group of motorGroupCards) {
      for (const rule of group.rules) {
        if (rule.application_category === "operacion") {
          ids.add(rule.rule_record_id);
        }
      }
    }
    return ids;
  }, [motorGroupCards]);

  const unassignedRules = useMemo(
    () => operationRules.filter((rule) => !assignedRuleIds.has(rule.id)),
    [operationRules, assignedRuleIds]
  );

  useEffect(() => {
    if (!rules.length) {
      setPendingSelectionRuleId(null);
      setSelectedRuleId(null);
      return;
    }
    if (pendingSelectionRuleId && rules.some((rule) => rule.id === pendingSelectionRuleId)) {
      setSelectedRuleId(pendingSelectionRuleId);
      setPendingSelectionRuleId(null);
      return;
    }
    if (!rules.some((rule) => rule.id === selectedRuleId) && !pendingSelectionRuleId) {
      setSelectedRuleId(null);
    }
  }, [pendingSelectionRuleId, rules, selectedRuleId]);

  useEffect(() => {
    if (!isGeotab) {
      return undefined;
    }

    const normalizedRuleId = ruleId.trim();
    if (!normalizedRuleId) {
      setResolveStatus("idle");
      setResolveError("");
      setRulePreview(null);
      return undefined;
    }

    let cancelled = false;
    setResolveStatus("loading");
    setResolveError("");

    const timer = window.setTimeout(async () => {
      try {
        const resolved = await resolveGeotabRule(database.id, normalizedRuleId);
        if (cancelled) return;
        setRulePreview(resolved);
        setResolveStatus(resolved.exists ? "resolved" : "missing");
      } catch (err) {
        if (cancelled) return;
        setRulePreview(null);
        setResolveStatus("error");
        setResolveError(err instanceof Error ? err.message : "No fue posible validar la regla");
      }
    }, 450);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [database.id, isGeotab, ruleId]);

  useEffect(() => {
    if (!selectedRule) {
      setInspection(null);
      setInspectionError("");
      setInspectionLoading(false);
      return;
    }

    let cancelled = false;
    setInspectionLoading(true);
    setInspectionError("");

    inspectGeotabRule(selectedRule.id)
      .then((payload) => {
        if (cancelled) return;
        setInspection(payload);
      })
      .catch((err) => {
        if (cancelled) return;
        setInspection(null);
        setInspectionError(err instanceof Error ? err.message : "No fue posible inspeccionar la regla");
      })
      .finally(() => {
        if (!cancelled) {
          setInspectionLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedRule?.id]);

  useEffect(() => {
    const validUnassigned = new Set(unassignedRules.map((rule) => rule.id));
    setSelectedGroupRuleIds((prev) => prev.filter((id) => validUnassigned.has(id)));
  }, [unassignedRules]);

  useEffect(() => {
    if (ruleCategory === "operacion") {
      setRuleMotorId("");
      setRuleEventType("");
    }
  }, [ruleCategory]);

  const handleAddRule = async (event) => {
    event.preventDefault();
    const normalizedRuleId = ruleId.trim();
    if (!normalizedRuleId || resolveStatus !== "resolved" || rulePreview?.rule_id !== normalizedRuleId) {
      return;
    }
    if (ruleEventType === "exceso_rpm" && !ruleMotorId) {
      return;
    }
    const created = await onAddRule({
      rule_id: normalizedRuleId,
      category: ruleCategory,
      motor_id: ruleMotorId ? Number(ruleMotorId) : null,
      event_type: ruleEventType || null
    });
    setRuleId("");
    setRuleCategory("operacion");
    setRuleMotorId("");
    setRuleEventType("");
    setResolveStatus("idle");
    setResolveError("");
    setRulePreview(null);
    if (created?.id) {
      setPendingSelectionRuleId(created.id);
    }
  };

  const toggleRuleInGroup = (ruleRecordId) => {
    setSelectedGroupRuleIds((prev) =>
      prev.includes(ruleRecordId)
        ? prev.filter((currentId) => currentId !== ruleRecordId)
        : [...prev, ruleRecordId]
    );
  };

  const handleAddRuleGroup = async (event) => {
    event.preventDefault();
    if (!selectedMotorId || selectedGroupRuleIds.length === 0) {
      return;
    }

    await onAddRuleGroup({
      motor_id: Number(selectedMotorId),
      match_mode: matchMode,
      rule_record_ids: selectedGroupRuleIds
    });

    setSelectedMotorId("");
    setMatchMode("all");
    setSelectedGroupRuleIds([]);
    setShowAssignForm(false);
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card modal-card-rules" role="dialog" aria-modal="true" aria-label="Detalle de database">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">
              {getDatabaseTypeLabel(database.connection_type)}
            </span>
            <h3>{database.database_name}</h3>
          </div>
          <div className="modal-header-actions">
            {canEdit && onEdit ? (
              <button
                type="button"
                className="icon-button"
                onClick={onEdit}
                title="Editar database"
              >
                &#9998;
              </button>
            ) : null}
            <button type="button" className="icon-button modal-close-button" onClick={onClose}>
              &#10005;
            </button>
          </div>
        </header>

        <div className="db-detail-info">
          <div className="db-detail-row">
            <span className="db-detail-label">Usuario</span>
            <strong>{database.username}</strong>
          </div>
          <div className="db-detail-row">
            <span className="db-detail-label">Tipo</span>
            <span>{getDatabaseTypeLabel(database.connection_type)}</span>
          </div>
          {providerDetailRows.map((row) => (
            <div className="db-detail-row" key={row.label}>
              <span className="db-detail-label">{row.label}</span>
              <strong>{row.value}</strong>
            </div>
          ))}
          {database.access_url ? (
            <div className="db-detail-row">
              <span className="db-detail-label">Enlace</span>
              <a href={database.access_url} target="_blank" rel="noreferrer" className="access-url-link">
                {database.access_url}
              </a>
            </div>
          ) : null}
        </div>

        {isGeotab ? (
          <div className="rules-section">
            {/* ── Add rule input ── */}
            <div className="rules-add-panel">
              <div className="rules-add-panel-header">
                <span className="rules-label">Agregar regla</span>
                <span className={`rule-resolution-dot is-${resolveStatus}`} />
              </div>
              <form className="rule-add-form" onSubmit={handleAddRule}>
                <input
                  value={ruleId}
                  onChange={(event) => setRuleId(event.target.value)}
                  placeholder="Pega el ID de regla Geotab"
                  required
                  autoComplete="off"
                />
                <select
                  value={ruleCategory}
                  onChange={(event) => setRuleCategory(event.target.value)}
                  disabled={loading}
                  aria-label="Categoria de la regla"
                >
                  <option value="operacion">Operación</option>
                  <option value="habito_seguro">Hábito seguro</option>
                </select>
                {ruleCategory === "habito_seguro" ? (
                  <>
                    <select
                      value={ruleEventType}
                      onChange={(event) => setRuleEventType(event.target.value)}
                      disabled={loading}
                      aria-label="Tipo de evento"
                    >
                      {RULE_EVENT_TYPES.map((option) => (
                        <option key={option.value || "general"} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <select
                      value={ruleMotorId}
                      onChange={(event) => setRuleMotorId(event.target.value)}
                      disabled={loading || motorsLoading}
                      required={ruleEventType === "exceso_rpm"}
                      aria-label="Motor de la regla"
                    >
                      <option value="">
                        {motorsLoading ? "Cargando..." : "Global"}
                      </option>
                      {motors.map((motor) => (
                        <option key={`rule-motor-${motor.id}`} value={motor.id}>
                          {motor.engine_name} | {motor.technical_number}
                        </option>
                      ))}
                    </select>
                  </>
                ) : null}
                {canEdit ? (
                  <button
                    type="submit"
                    className="button button-sm"
                    disabled={
                      loading ||
                      (ruleEventType === "exceso_rpm" && !ruleMotorId) ||
                      resolveStatus !== "resolved" ||
                      !rulePreview?.exists ||
                      rulePreview?.rule_id !== ruleId.trim()
                    }
                    title="Agregar regla"
                  >
                    Agregar
                  </button>
                ) : null}
              </form>

              {rulePreview || resolveStatus === "loading" || (resolveStatus === "error" && resolveError) ? (
                <RuleSummaryCard
                  inspection={rulePreview}
                  loading={resolveStatus === "loading"}
                  error={resolveStatus === "error" ? resolveError : ""}
                  emptyMessage=""
                />
              ) : null}
            </div>

            {/* ── Motor groups (assigned rules) ── */}
            <div className="rules-motor-section">
              <div className="rules-motor-section-header">
                <div>
                  <span className="rules-label">Reglas por motor</span>
                  <span className="rules-count-subtle">{motorGroupCards.length} {motorGroupCards.length === 1 ? "motor" : "motores"} · {operationRules.length} {operationRules.length === 1 ? "regla" : "reglas"}</span>
                </div>
                {canEdit && unassignedRules.length > 0 ? (
                  <button
                    type="button"
                    className={`button-sm ${showAssignForm ? "button-secondary" : "button"}`}
                    onClick={() => setShowAssignForm(!showAssignForm)}
                  >
                    {showAssignForm ? "Cancelar" : "Asignar a motor"}
                  </button>
                ) : null}
              </div>

              {/* ── Assign form (only unassigned rules) ── */}
              {canEdit && showAssignForm && unassignedRules.length > 0 ? (
                <form className="rule-assign-form" onSubmit={handleAddRuleGroup}>
                  <div className="rule-assign-form-row">
                    <div className="form-field">
                      <label htmlFor="rule-group-motor">Motor</label>
                      <select
                        id="rule-group-motor"
                        value={selectedMotorId}
                        onChange={(event) => setSelectedMotorId(event.target.value)}
                        disabled={motorsLoading || loading}
                        required
                      >
                        <option value="">{motorsLoading ? "Cargando..." : "Selecciona un motor"}</option>
                        {motors.map((motor) => (
                          <option key={motor.id} value={motor.id}>
                            {motor.engine_name} | {motor.technical_number}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="rule-group-mode">Coincidencia</label>
                      <select
                        id="rule-group-mode"
                        value={matchMode}
                        onChange={(event) => setMatchMode(event.target.value)}
                        disabled={loading}
                      >
                        <option value="all">Todas (AND)</option>
                        <option value="any">Cualquiera (OR)</option>
                      </select>
                    </div>
                  </div>

                  <div className="rule-assign-checklist">
                    {unassignedRules.map((rule) => (
                      <label
                        className={`rule-assign-check ${selectedGroupRuleIds.includes(rule.id) ? "is-checked" : ""}`}
                        key={`assign-rule-${rule.id}`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedGroupRuleIds.includes(rule.id)}
                          onChange={() => toggleRuleInGroup(rule.id)}
                          disabled={loading}
                        />
                        <span className="rule-assign-check-label">
                          <strong>{rule.name}</strong>
                          <code>{rule.rule_id}</code>
                        </span>
                      </label>
                    ))}
                  </div>

                  <button
                    type="submit"
                    className="button button-sm"
                    disabled={loading || motorsLoading || !selectedMotorId || selectedGroupRuleIds.length === 0}
                  >
                    Crear grupo
                  </button>
                </form>
              ) : null}

              {/* ── Motor group cards ── */}
              {motorGroupCards.length > 0 ? (
                <div className="motor-group-list">
                  {motorGroupCards.map((group) => (
                    <details className="motor-group-card" key={group.id}>
                      <summary className="motor-group-card-header">
                        <div className="motor-group-card-identity">
                          <span className="motor-group-card-chevron" />
                          <span className="motor-group-card-motor">{group.motor_name}</span>
                          <span className="motor-group-card-tech">{group.technical_number}</span>
                        </div>
                        <div className="motor-group-card-meta">
                          <span className="rule-badge is-match-mode">{formatMatchModeLabel(group.match_mode)}</span>
                          <span className="rule-badge is-muted">{group.rules.length} {group.rules.length === 1 ? "regla" : "reglas"}</span>
                          {canEdit && !group.isVirtual && confirmDeleteGroupId === group.id ? (
                            <div className="motor-group-card-confirm" onClick={(e) => e.stopPropagation()}>
                              <button
                                type="button"
                                className="button-secondary button-sm rule-confirm-delete"
                                onClick={() => {
                                  setConfirmDeleteGroupId(null);
                                  onDeleteRuleGroup(group);
                                }}
                                disabled={loading}
                              >
                                Eliminar
                              </button>
                              <button
                                type="button"
                                className="icon-button"
                                onClick={() => setConfirmDeleteGroupId(null)}
                                title="Cancelar"
                              >
                                &#8592;
                              </button>
                            </div>
                          ) : canEdit && !group.isVirtual ? (
                            <button
                              type="button"
                              className="icon-button rule-delete-button"
                              onClick={(e) => { e.stopPropagation(); setConfirmDeleteGroupId(group.id); }}
                              disabled={loading}
                              title="Eliminar grupo"
                            >
                              &#10005;
                            </button>
                          ) : null}
                        </div>
                      </summary>
                      <div className="motor-group-card-rules">
                        {group.rules.map((rule) => (
                          <div
                            className={`motor-group-rule-chip ${selectedRuleId === rule.rule_record_id ? "is-active" : ""}`}
                            key={`mg-${group.id}-r-${rule.application_key || rule.rule_record_id}`}
                          >
                            <button
                              type="button"
                              className="motor-group-rule-chip-main"
                              onClick={() => setSelectedRuleId(rule.rule_record_id)}
                            >
                              <span className="motor-group-rule-chip-name">{rule.name}</span>
                              {rule.application_category === "habito_seguro" ? (
                                <span className="rule-app-tag">{formatRuleApplicationLabel(rule)}</span>
                              ) : null}
                              <code className="motor-group-rule-chip-id">{rule.rule_id}</code>
                            </button>
                            {canEdit && confirmDeleteRuleId === rule.rule_record_id ? (
                              <>
                                <button
                                  type="button"
                                  className="button-secondary button-sm rule-confirm-delete"
                                  onClick={() => {
                                    setConfirmDeleteRuleId(null);
                                    onDeleteRule({
                                      id: rule.rule_record_id,
                                      name: rule.name,
                                      rule_id: rule.rule_id
                                    });
                                  }}
                                  disabled={loading}
                                >
                                  Confirmar
                                </button>
                                <button
                                  type="button"
                                  className="icon-button rule-delete-button"
                                  onClick={() => setConfirmDeleteRuleId(null)}
                                  title="Cancelar"
                                >
                                  &#8592;
                                </button>
                              </>
                            ) : canEdit ? (
                              <button
                                type="button"
                                className="icon-button rule-delete-button"
                                onClick={() => setConfirmDeleteRuleId(rule.rule_record_id)}
                                disabled={loading}
                                title="Eliminar regla"
                              >
                                &#10005;
                              </button>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </details>
                  ))}
                </div>
              ) : null}

              {/* ── Unassigned rules ── */}
              {unassignedRules.length > 0 && !showAssignForm ? (
                <div className="unassigned-rules-panel">
                  <span className="unassigned-rules-label">Sin asignar ({unassignedRules.length})</span>
                  <div className="unassigned-rules-list">
                    {unassignedRules.map((rule) => (
                      <div
                        className={`rule-list-item ${selectedRuleId === rule.id ? "is-selected" : ""}`}
                        key={`unassigned-${rule.id}`}
                      >
                        <button
                          type="button"
                          className="rule-select-button"
                          onClick={() => setSelectedRuleId(rule.id)}
                        >
                          <span className="rule-list-name">{rule.name}</span>
                          <span className="rule-id-cell">{rule.rule_id}</span>
                        </button>
                        {canEdit && confirmDeleteRuleId === rule.id ? (
                          <>
                            <button
                              type="button"
                              className="button-secondary button-sm rule-confirm-delete"
                              onClick={() => {
                                setConfirmDeleteRuleId(null);
                                onDeleteRule(rule);
                              }}
                              disabled={loading}
                            >
                              Confirmar
                            </button>
                            <button
                              type="button"
                              className="icon-button rule-delete-button"
                              onClick={() => setConfirmDeleteRuleId(null)}
                              title="Cancelar"
                            >
                              &#8592;
                            </button>
                          </>
                        ) : canEdit ? (
                          <button
                            type="button"
                            className="icon-button rule-delete-button"
                            onClick={() => setConfirmDeleteRuleId(rule.id)}
                            disabled={loading}
                            title="Eliminar"
                          >
                            &#10005;
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {operationRules.length === 0 ? (
                <div className="rules-empty-state">
                  <p>Agrega reglas desde Geotab para asignarlas a motores.</p>
                </div>
              ) : null}

              {operationRules.length > 0 && unassignedRules.length === 0 && motorGroupCards.length === 0 ? (
                <div className="rules-empty-state">
                  <p>Todas las reglas necesitan ser asignadas a un motor.</p>
                </div>
              ) : null}
            </div>

            {/* ── Safe-habit rules ── */}
            <div className="rules-motor-section">
              <div className="rules-motor-section-header">
                <div>
                  <span className="rules-label">Reglas de hábito seguro</span>
                  <span className="rules-count-subtle">{safeHabitRules.length} {safeHabitRules.length === 1 ? "regla" : "reglas"}</span>
                </div>
              </div>
              {safeHabitRules.length > 0 ? (
                <div className="unassigned-rules-list">
                  {safeHabitRules.map(({ rule, application }) => (
                    <div
                      className={`rule-list-item ${selectedRuleId === rule.id ? "is-selected" : ""}`}
                      key={`safe-habit-${rule.id}-${application.id}`}
                    >
                      <button
                        type="button"
                        className="rule-select-button"
                        onClick={() => setSelectedRuleId(rule.id)}
                      >
                        <span className="rule-list-name">{rule.name}</span>
                        {application.event_type ? (
                          <span className="rule-app-tag">{formatRuleApplicationLabel(application)}</span>
                        ) : null}
                        <span className="rule-id-cell">{rule.rule_id}</span>
                      </button>
                      {canEdit && confirmDeleteRuleId === rule.id ? (
                        <>
                          <button
                            type="button"
                            className="button-secondary button-sm rule-confirm-delete"
                            onClick={() => {
                              setConfirmDeleteRuleId(null);
                              onDeleteRule(rule);
                            }}
                            disabled={loading}
                          >
                            Confirmar
                          </button>
                          <button
                            type="button"
                            className="icon-button rule-delete-button"
                            onClick={() => setConfirmDeleteRuleId(null)}
                            title="Cancelar"
                          >
                            &#8592;
                          </button>
                        </>
                      ) : canEdit ? (
                        <button
                          type="button"
                          className="icon-button rule-delete-button"
                          onClick={() => setConfirmDeleteRuleId(rule.id)}
                          disabled={loading}
                          title="Eliminar"
                        >
                          &#10005;
                        </button>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rules-empty-state">
                  <p>Sin reglas de hábito seguro. Agrégalas con la categoría "Hábito seguro".</p>
                </div>
              )}
            </div>

            {/* ── Credentials pool ── */}
            <CredentialsPanel databaseId={database.id} canEdit={canEdit} />

            {/* ── Inspector panel ── */}
            {selectedRule ? (
              <div className="rule-inspector-section">
                <div className="rules-label">Inspector</div>
                <RuleInspectorPanel
                  inspection={inspection}
                  loading={inspectionLoading}
                  error={inspectionError}
                  selectedRule={selectedRule}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────────────── */
export default function CustomersPage() {
  const { toasts, pushToast } = useToasts();
  const canEditCustomer = usePermission("customers.edit");

  const [showCreateCustomer, setShowCreateCustomer] = useState(false);
  const [createDbForCustomerId, setCreateDbForCustomerId] = useState(null);
  const [editingCustomer, setEditingCustomer] = useState(null);
  const [editingDatabase, setEditingDatabase] = useState(null);
  const [viewingDatabase, setViewingDatabase] = useState(null);
  const [motors, setMotors] = useState([]);
  const [motorsLoading, setMotorsLoading] = useState(false);

  const [showInactive, setShowInactive] = useState(false);

  const {
    loading,
    customers,
    error,
    registerCustomer,
    editCustomer,
    toggleCustomerActive,
    registerCustomerDatabase,
    editCustomerDatabase,
    addGeotabRule,
    addGeotabRuleGroup,
    removeGeotabRule,
    removeGeotabRuleGroup
  } = useCustomersCatalog();

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  useEffect(() => {
    let cancelled = false;
    setMotorsLoading(true);
    listMotors()
      .then((records) => {
        if (!cancelled) {
          setMotors(records);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          pushToast("error", err instanceof Error ? err.message : "No fue posible cargar los motores");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setMotorsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pushToast]);

  const inactiveCount = useMemo(
    () => customers.filter((customer) => customer.is_active === false).length,
    [customers]
  );

  const visibleCustomers = useMemo(
    () => (showInactive ? customers : customers.filter((customer) => customer.is_active !== false)),
    [customers, showInactive]
  );

  const totals = useMemo(
    () => ({
      customers: customers.filter((customer) => customer.is_active !== false).length,
      databases: customers.reduce((acc, customer) => acc + (customer.database_count || 0), 0)
    }),
    [customers]
  );

  // Keep viewingDatabase in sync with customer state after rule add/delete
  const resolvedViewingDb = useMemo(() => {
    if (!viewingDatabase) return null;
    for (const customer of customers) {
      const found = customer.databases.find((db) => db.id === viewingDatabase.id);
      if (found) return { ...found, customer_id: customer.id };
    }
    return viewingDatabase;
  }, [viewingDatabase, customers]);

  const handleCreateCustomer = async (payload) => {
    try {
      await registerCustomer(payload);
      setShowCreateCustomer(false);
      pushToast("success", "Cliente creado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible crear el cliente");
    }
  };

  const handleCreateDatabase = async (customerId, payload) => {
    try {
      await registerCustomerDatabase(customerId, payload);
      setCreateDbForCustomerId(null);
      pushToast("success", "Database creada.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible crear la database");
    }
  };

  const handleEditCustomer = async (payload) => {
    try {
      await editCustomer(editingCustomer.id, payload);
      setEditingCustomer(null);
      pushToast("success", "Cliente actualizado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible actualizar el cliente");
    }
  };

  const handleToggleActive = async (customer) => {
    const nextActive = customer.is_active === false;
    try {
      await toggleCustomerActive(customer.id, nextActive);
      pushToast("success", nextActive ? "Cliente reactivado." : "Cliente archivado.");
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible cambiar el estado del cliente"
      );
    }
  };

  const handleEditDatabase = async (payload) => {
    try {
      await editCustomerDatabase(editingDatabase.id, editingDatabase.customer_id, payload);
      setEditingDatabase(null);
      pushToast("success", "Database actualizada.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible actualizar la database");
    }
  };

  const handleAddRule = async (payload) => {
    if (!resolvedViewingDb) return;
    try {
      const created = await addGeotabRule(resolvedViewingDb.id, resolvedViewingDb.customer_id, payload);
      pushToast("success", "Regla agregada.");
      return created;
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible crear la regla");
      throw err;
    }
  };

  const handleDeleteRule = async (rule) => {
    if (!resolvedViewingDb) return;
    try {
      await removeGeotabRule(rule.id, resolvedViewingDb.id, resolvedViewingDb.customer_id);
      pushToast("success", "Regla eliminada.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message :"No fue posible eliminar la regla");
    }
  };

  const handleAddRuleGroup = async (payload) => {
    if (!resolvedViewingDb) return;
    try {
      await addGeotabRuleGroup(resolvedViewingDb.id, resolvedViewingDb.customer_id, payload);
      pushToast("success", "Grupo de reglas creado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible crear el grupo");
      throw err;
    }
  };

  const handleDeleteRuleGroup = async (group) => {
    if (!resolvedViewingDb) return;
    try {
      await removeGeotabRuleGroup(group.id, resolvedViewingDb.id, resolvedViewingDb.customer_id);
      pushToast("success", "Grupo de reglas eliminado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible eliminar el grupo");
    }
  };

  const openEditFromDetail = () => {
    if (!resolvedViewingDb) return;
    setEditingDatabase(resolvedViewingDb);
    setViewingDatabase(null);
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Administracion</span>
          <h2>Clientes y databases</h2>
          <p>
            Gestiona clientes, databases y proveedores como Geotab o Artimo.
          </p>
        </div>
        <div className="page-header-actions">
          {inactiveCount > 0 ? (
            <button
              type="button"
              className={`button-secondary ${showInactive ? "is-active" : ""}`}
              onClick={() => setShowInactive((prev) => !prev)}
              title="Mostrar u ocultar clientes archivados"
            >
              {showInactive ? "Ocultar inactivos" : `Ver inactivos (${inactiveCount})`}
            </button>
          ) : null}
          <Can permission="customers.create">
            <button type="button" onClick={() => setShowCreateCustomer(true)}>
              + Cliente
            </button>
          </Can>
          <Can permission="customers.edit">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setCreateDbForCustomerId("")}
              disabled={customers.length === 0}
            >
              + Database
            </button>
          </Can>
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
          <p>Databases disponibles para asignar</p>
        </article>
      </section>

      <ToastStack toasts={toasts} />

      <section className="motor-cards-grid">
        {customers.length === 0 && !loading ? (
          <p className="support-copy">No hay clientes registrados.</p>
        ) : null}
        {customers.length > 0 && visibleCustomers.length === 0 && !loading ? (
          <p className="support-copy">Todos los clientes estan archivados. Usa "Ver inactivos" para mostrarlos.</p>
        ) : null}
        {visibleCustomers.map((customer) => {
          const isInactive = customer.is_active === false;
          return (
          <article className={`card motor-card ${isInactive ? "is-inactive" : ""}`} key={customer.id}>
            <div className="motor-card-top">
              <span className="motor-count">{customer.database_count} databases</span>
              <span className={`status ${isInactive ? "status-error" : "status-ok"}`}>
                {isInactive ? "inactivo" : "activo"}
              </span>
            </div>

            <div className="motor-card-heading">
              <h3>{customer.name}</h3>
              {customer.category && customer.category !== "Ninguna" ? (
                <span className={categoryBadgeClass(customer.category)}>{customer.category}</span>
              ) : null}
              <div className="motor-card-heading-row">
                <div className="motor-card-heading-actions">
                  <Can permission="customers.edit">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => setCreateDbForCustomerId(String(customer.id))}
                      title="Agregar database"
                    >
                      +
                    </button>
                  </Can>
                  <Can permission="customers.edit">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => setEditingCustomer(customer)}
                      title="Editar cliente"
                    >
                      &#9998;
                    </button>
                  </Can>
                  <Can permission="customers.edit">
                    <button
                      type="button"
                      className="icon-button"
                      onClick={() => handleToggleActive(customer)}
                      title={isInactive ? "Reactivar cliente" : "Archivar cliente (inactivo)"}
                    >
                      {isInactive ? "↺" : "⏻"}
                    </button>
                  </Can>
                </div>
              </div>
            </div>

            <div className="source-grid">
              {customer.databases.length === 0 ? (
                <p className="support-copy">Sin databases.</p>
              ) : (
                customer.databases.map((database) => (
                  <div className="source-field" key={database.id}>
                    <div className="source-field-row">
                      <span>
                        {database.database_name}
                        {database.connection_type !== "database" ? (
                          <span className="status geotab-badge geotab-type-label">
                            {getDatabaseTypeLabel(database.connection_type)}
                          </span>
                        ) : null}
                      </span>
                      <div className="source-field-actions">
                        <button
                          type="button"
                          className="icon-button"
                          onClick={() => setViewingDatabase({ ...database, customer_id: customer.id })}
                          title="Ver detalle"
                        >
                          &#8943;
                        </button>
                      </div>
                    </div>
                    <strong>{database.username}</strong>
                    {database.connection_type === "artimo" ? (
                      <span className="db-rule-count">
                        Grupo: {database.provider_config?.group_name || "Sin group_name"}
                      </span>
                    ) : null}
                    {database.connection_type === "geotab" && (database.rules || []).length > 0 ? (
                      <span className="db-rule-count">{(database.rules || []).length} reglas</span>
                    ) : null}
                    {database.connection_type === "geotab" && (database.rule_groups || []).length > 0 ? (
                      <span className="db-rule-count">{(database.rule_groups || []).length} grupos</span>
                    ) : null}
                  </div>
                ))
              )}
            </div>
          </article>
          );
        })}
      </section>

      {showCreateCustomer ? (
        <CreateCustomerModal
          loading={loading}
          onClose={() => setShowCreateCustomer(false)}
          onSubmit={handleCreateCustomer}
        />
      ) : null}

      {createDbForCustomerId !== null ? (
        <CreateDatabaseModal
          customers={customers}
          loading={loading}
          preselectedCustomerId={createDbForCustomerId}
          onClose={() => setCreateDbForCustomerId(null)}
          onSubmit={handleCreateDatabase}
        />
      ) : null}

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

      {resolvedViewingDb ? (
        <DatabaseDetailModal
          database={resolvedViewingDb}
          loading={loading}
          motors={motors}
          motorsLoading={motorsLoading}
          onClose={() => setViewingDatabase(null)}
          onEdit={canEditCustomer ? openEditFromDetail : null}
          onAddRule={handleAddRule}
          onDeleteRule={handleDeleteRule}
          onAddRuleGroup={handleAddRuleGroup}
          onDeleteRuleGroup={handleDeleteRuleGroup}
          canEdit={canEditCustomer}
        />
      ) : null}
    </section>
  );
}
