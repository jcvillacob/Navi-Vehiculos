import { useCallback, useEffect, useMemo, useState } from "react";

import Can from "../components/Can";
import {
  createRole,
  deleteRole,
  fetchMe,
  fetchModules,
  fetchRolePermissions,
  listRoles,
  updateRole,
  updateRolePermissions
} from "../api/vehicleApi";
import { useAuth } from "../context/AuthContext";

const LEVEL_OPTIONS = [
  { value: "ninguno", label: "Ninguno" },
  { value: "lectura", label: "Lectura" },
  { value: "escritura", label: "Escritura" },
];

/* ── Create / Edit role modal ────────────────────────────────────────── */
function RoleFormModal({ role, modules, onClose, onSubmit }) {
  const isEdit = Boolean(role);
  const [form, setForm] = useState({
    key: role?.key ?? "",
    label: role?.label ?? "",
    description: role?.description ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const slug = useMemo(() => {
    const base = (form.key || form.label).toString().trim().toLowerCase();
    return base.replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
  }, [form.key, form.label]);

  const valid = form.label.trim().length > 0 && (isEdit || slug.length > 0);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const payload = isEdit
        ? { label: form.label, description: form.description }
        : { key: slug, label: form.label, description: form.description };
      await onSubmit(payload);
    } catch (err) {
      setError(err.message || "No fue posible guardar el rol");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label={isEdit ? "Editar rol" : "Crear rol"}>
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">{isEdit ? "Editar" : "Nuevo"}</span>
            <h3>{isEdit ? `Rol: ${role.label}` : "Crear rol"}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>Cerrar</button>
        </header>

        {error ? <div className="notice-banner notice-error">{error}</div> : null}

        <form className="register-form" onSubmit={handleSubmit}>
          {!isEdit && (
            <div className="form-field">
              <label htmlFor="role-key">Key (slug)</label>
              <input
                id="role-key"
                value={form.key}
                onChange={(e) => setForm((prev) => ({ ...prev, key: e.target.value }))}
                placeholder={slug || "ej: supervisor"}
              />
              <small className="form-hint">
                Se genera automaticamente del nombre si lo dejas vacio. Solo letras, numeros, guion y guion bajo.
              </small>
            </div>
          )}
          <div className="form-field">
            <label htmlFor="role-label">Nombre visible</label>
            <input
              id="role-label"
              value={form.label}
              onChange={(e) => setForm((prev) => ({ ...prev, label: e.target.value }))}
              placeholder="Supervisor de operaciones"
              required
              autoFocus
            />
          </div>
          <div className="form-field">
            <label htmlFor="role-description">Descripcion</label>
            <textarea
              id="role-description"
              value={form.description}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              rows={2}
              placeholder="Descripcion opcional del proposito del rol"
            />
          </div>
          <div className="actions-row modal-actions">
            <button type="submit" disabled={saving || !valid}>
              {saving ? "Guardando..." : isEdit ? "Guardar cambios" : "Crear rol"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>Cancelar</button>
          </div>
        </form>
      </section>
    </div>
  );
}

/* ── Permission matrix ──────────────────────────────────────────────── */
function PermissionMatrix({ modules, current, disabled, onChange }) {
  const [pending, setPending] = useState(current);

  useEffect(() => {
    setPending(current);
  }, [current]);

  const setLevel = (moduleKey, level) => {
    if (disabled) return;
    setPending((prev) => ({ ...prev, [moduleKey]: level }));
  };

  const dirty = useMemo(() => {
    return modules.some((m) => (pending[m.key] ?? "ninguno") !== (current[m.key] ?? "ninguno"));
  }, [pending, current, modules]);

  const applyChanges = () => {
    onChange(pending);
  };

  const cancelChanges = () => {
    setPending(current);
  };

  return (
    <div className="roles-matrix-wrapper">
      <div className="roles-matrix-header">
        <div>
          <span className="eyebrow">Permisos</span>
          <h3>Matriz de acceso por modulo</h3>
          <p className="roles-matrix-help">
            Marca Ninguno, Lectura o Escritura por modulo. Escritura incluye
            automaticamente los permisos de lectura del mismo modulo.
          </p>
        </div>
        {disabled ? (
          <span className="status status-soft">Edicion deshabilitada para este rol</span>
        ) : dirty ? (
          <div className="actions-row">
            <button type="button" className="button-secondary button-sm" onClick={cancelChanges}>
              Descartar
            </button>
            <button type="button" className="button-sm" onClick={applyChanges}>
              Guardar matriz
            </button>
          </div>
        ) : null}
      </div>
      <div className="vehicles-table-shell">
        <table className="vehicles-table roles-matrix-table">
          <thead>
            <tr>
              <th>Modulo</th>
              <th>Ninguno</th>
              <th>Lectura</th>
              <th>Escritura</th>
            </tr>
          </thead>
          <tbody>
            {modules.map((module) => {
              const value = pending[module.key] ?? "ninguno";
              return (
                <tr key={module.key}>
                  <td data-label="Modulo">
                    <strong>{module.label}</strong>
                    <span className="roles-matrix-module-desc">{module.description}</span>
                  </td>
                  {LEVEL_OPTIONS.map((opt) => {
                    const supported = opt.value === "ninguno" || module.levels.includes(opt.value);
                    const isDisabled = disabled || !supported;
                    const reason = !supported
                      ? `El modulo "${module.label}" no admite nivel "${opt.label}".`
                      : undefined;
                    return (
                      <td key={opt.value} data-label={opt.label}>
                        <label
                          className={`roles-matrix-radio ${isDisabled ? "is-disabled" : ""}`}
                          title={reason}
                        >
                          <input
                            type="radio"
                            name={`module-${module.key}`}
                            value={opt.value}
                            checked={value === opt.value}
                            onChange={() => setLevel(module.key, opt.value)}
                            disabled={isDisabled}
                          />
                          <span className={`roles-matrix-radio-dot level-${opt.value}`} aria-hidden />
                          <span className="roles-matrix-radio-label">{opt.label}</span>
                        </label>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Roles Page ─────────────────────────────────────────────────────── */
export default function RolesPage() {
  const { user, setUser } = useAuth();
  const [roles, setRoles] = useState([]);
  const [modules, setModules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedKey, setSelectedKey] = useState(null);
  const [matrix, setMatrix] = useState({});
  const [matrixLoading, setMatrixLoading] = useState(false);
  const [matrixError, setMatrixError] = useState("");
  const [modal, setModal] = useState(null);
  const [actionError, setActionError] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [rolesData, modulesData] = await Promise.all([listRoles(), fetchModules()]);
      setRoles(rolesData);
      setModules(modulesData.modules || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const loadMatrix = useCallback(async (key) => {
    setMatrixLoading(true);
    setMatrixError("");
    try {
      const data = await fetchRolePermissions(key);
      setMatrix(data.modules || {});
    } catch (err) {
      setMatrixError(err.message);
    } finally {
      setMatrixLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedKey) {
      loadMatrix(selectedKey);
    } else {
      setMatrix({});
      setMatrixError("");
    }
  }, [selectedKey, loadMatrix]);

  const selectedRole = useMemo(
    () => roles.find((r) => r.key === selectedKey) || null,
    [roles, selectedKey]
  );

  const handleMatrixChange = async (newMatrix) => {
    if (!selectedKey) return;
    setMatrixError("");
    try {
      await updateRolePermissions(selectedKey, newMatrix);
      setMatrix(newMatrix);
      // Si el usuario actual pertenece al rol editado, refrescar /auth/me
      // para que los checks de <Can> reflejen la nueva matriz.
      if (user?.role === selectedKey && setUser) {
        try {
          const updated = await fetchMe();
          setUser(updated);
        } catch (refreshErr) {
          console.warn("No se pudo refrescar /auth/me:", refreshErr);
        }
      }
    } catch (err) {
      setMatrixError(err.message);
    }
  };

  const handleCreateRole = async (payload) => {
    setActionError("");
    try {
      await createRole(payload);
      await fetchAll();
      setModal(null);
    } catch (err) {
      throw err;
    }
  };

  const handleUpdateRole = async (payload) => {
    if (!selectedRole) return;
    setActionError("");
    try {
      await updateRole(selectedRole.key, payload);
      await fetchAll();
      setModal(null);
    } catch (err) {
      throw err;
    }
  };

  const handleDeleteRole = async (role) => {
    const confirmMsg = `¿Eliminar el rol "${role.label}"? Esta accion no se puede deshacer.`;
    if (!window.confirm(confirmMsg)) return;
    setActionError("");
    try {
      await deleteRole(role.key);
      if (selectedKey === role.key) {
        setSelectedKey(null);
      }
      await fetchAll();
    } catch (err) {
      setActionError(err.message);
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Administracion</span>
          <h2>Roles y permisos</h2>
          <p>
            Crea roles, asignales permisos por modulo (Lectura/Escritura) y
            controla quien tiene acceso a que parte del sistema.
          </p>
        </div>
        <Can permission="roles.manage">
          <button type="button" onClick={() => setModal({ mode: "create" })}>
            + Rol
          </button>
        </Can>
      </header>

      {error && (
        <div className="notice-banner notice-error">
          <span className="notice-icon">✕</span>
          {error}
        </div>
      )}
      {actionError && (
        <div className="notice-banner notice-error">
          <span className="notice-icon">✕</span>
          {actionError}
        </div>
      )}

      {loading ? (
        <article className="card empty-state-card">
          <p>Cargando...</p>
        </article>
      ) : (
        <div className="roles-layout">
          <article className="card roles-list-card">
            <header className="section-heading">
              <div>
                <span className="eyebrow">Roles</span>
                <h3>{roles.length} definidos</h3>
              </div>
            </header>
            <ul className="roles-list">
              {roles.map((role) => (
                <li
                  key={role.key}
                  className={`roles-list-item ${selectedKey === role.key ? "is-selected" : ""}`}
                >
                  <button
                    type="button"
                    className="roles-list-button"
                    onClick={() => setSelectedKey(role.key)}
                  >
                    <div className="roles-list-row">
                      <strong>{role.label}</strong>
                      {role.is_system ? (
                        <span className="status status-soft">Sistema</span>
                      ) : (
                        <span className="status status-ok">Custom</span>
                      )}
                    </div>
                    <div className="roles-list-row roles-list-row-meta">
                      <span className="roles-list-key">{role.key}</span>
                      <span className="roles-list-count">
                        {role.user_count || 0} usuario{(role.user_count || 0) === 1 ? "" : "s"}
                      </span>
                    </div>
                    {role.description ? (
                      <p className="roles-list-desc">{role.description}</p>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          </article>

          <article className="card roles-detail-card">
            {!selectedRole ? (
              <div className="empty-state-card roles-empty">
                <span className="eyebrow">Selecciona un rol</span>
                <h3>Edita los permisos por modulo</h3>
                <p>Escoge un rol de la lista para ver y editar su matriz de permisos.</p>
              </div>
            ) : (
              <>
                <header className="section-heading">
                  <div>
                    <span className="eyebrow">Matriz del rol</span>
                    <h3>{selectedRole.label}</h3>
                    {selectedRole.description ? (
                      <p className="roles-detail-description">{selectedRole.description}</p>
                    ) : null}
                  </div>
                  <div className="actions-row section-heading-actions">
                    <Can permission="roles.manage">
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() => setModal({ mode: "edit", role: selectedRole })}
                      >
                        Editar nombre
                      </button>
                      {!selectedRole.is_system && (
                        <button
                          type="button"
                          className="button-secondary button-sm"
                          onClick={() => handleDeleteRole(selectedRole)}
                        >
                          Eliminar
                        </button>
                      )}
                    </Can>
                  </div>
                </header>

                {selectedRole.is_system ? (
                  <p className="notice-banner notice-soft">
                    Los roles de sistema no se pueden eliminar. La matriz es
                    editable salvo en los permisos criticos del rol admin.
                  </p>
                ) : null}

                {matrixError ? (
                  <div className="notice-banner notice-error">{matrixError}</div>
                ) : null}

                {matrixLoading ? (
                  <p>Cargando matriz...</p>
                ) : (
                  <PermissionMatrix
                    modules={modules}
                    current={matrix}
                    disabled={!user?.permissions?.includes("roles.manage")}
                    onChange={handleMatrixChange}
                  />
                )}
              </>
            )}
          </article>
        </div>
      )}

      {modal?.mode === "create" && (
        <RoleFormModal
          modules={modules}
          onClose={() => setModal(null)}
          onSubmit={handleCreateRole}
        />
      )}
      {modal?.mode === "edit" && (
        <RoleFormModal
          role={modal.role}
          modules={modules}
          onClose={() => setModal(null)}
          onSubmit={handleUpdateRole}
        />
      )}
    </section>
  );
}
