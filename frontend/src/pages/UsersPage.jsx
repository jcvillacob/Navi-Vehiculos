import { useCallback, useEffect, useState } from "react";
import { listUsers, createUser, updateUser } from "../api/vehicleApi";

const ROLE_LABELS = { admin: "Admin", editor: "Editor", viewer: "Viewer" };
const ROLES = ["admin", "editor", "viewer"];

/* ── Create User Modal ──────────────────────────────────────────────── */
function CreateUserModal({ loading, onClose, onSubmit }) {
  const [form, setForm] = useState({ username: "", email: "", password: "", role: "viewer" });
  const set = (key) => (e) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit(form);
  };

  const valid = form.username.trim().length >= 3 && form.email.includes("@") && form.password.length >= 8;

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Crear usuario">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Nuevo</span>
            <h3>Crear usuario</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>Cerrar</button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="cu-username">Usuario</label>
            <input id="cu-username" value={form.username} onChange={set("username")} placeholder="min 3 caracteres" required autoFocus />
          </div>
          <div className="form-field">
            <label htmlFor="cu-email">Email</label>
            <input id="cu-email" type="email" value={form.email} onChange={set("email")} placeholder="correo@ejemplo.com" required />
          </div>
          <div className="form-field">
            <label htmlFor="cu-password">Contrasena</label>
            <input id="cu-password" type="password" value={form.password} onChange={set("password")} placeholder="min 8 caracteres" required />
          </div>
          <div className="form-field">
            <label htmlFor="cu-role">Rol</label>
            <select id="cu-role" value={form.role} onChange={set("role")}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select>
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !valid}>
              {loading ? "Guardando..." : "Crear usuario"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>Cancelar</button>
          </div>
        </form>
      </section>
    </div>
  );
}

/* ── Edit User Modal ────────────────────────────────────────────────── */
function EditUserModal({ user, loading, onClose, onSubmit }) {
  const [form, setForm] = useState({
    email: user.email,
    role: user.role,
    is_active: user.is_active,
  });
  const set = (key) => (e) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({
      email: form.email,
      role: form.role,
      is_active: form.is_active,
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Editar usuario">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Editar</span>
            <h3>Usuario: {user.username}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>Cerrar</button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="eu-email">Email</label>
            <input id="eu-email" type="email" value={form.email} onChange={set("email")} required />
          </div>
          <div className="form-field">
            <label htmlFor="eu-role">Rol</label>
            <select id="eu-role" value={form.role} onChange={set("role")}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="eu-active">Estado</label>
            <select
              id="eu-active"
              value={form.is_active ? "true" : "false"}
              onChange={(e) => setForm((prev) => ({ ...prev, is_active: e.target.value === "true" }))}
            >
              <option value="true">Activo</option>
              <option value="false">Inactivo</option>
            </select>
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : "Guardar cambios"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>Cancelar</button>
          </div>
        </form>
      </section>
    </div>
  );
}

/* ── Users Page ──────────────────────────────────────────────────────── */
export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [modal, setModal] = useState(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await listUsers();
      setUsers(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleCreate = async (form) => {
    setSaving(true);
    try {
      await createUser(form);
      setModal(null);
      await fetchUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleEdit = async (form) => {
    setSaving(true);
    try {
      await updateUser(modal.user.id, form);
      setModal(null);
      await fetchUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      setSaving(false);
    }
  };

  const formatDate = (iso) => {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("es-CO", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Administracion</span>
          <h2>Usuarios</h2>
          <p>Gestiona los accesos y roles de los usuarios del sistema.</p>
        </div>

        <button type="button" onClick={() => setModal("create")}>
          + Usuario
        </button>
      </header>

      {error && (
        <div className="notice-banner notice-error">
          <span className="notice-icon">✕</span>
          {error}
        </div>
      )}

      {loading ? (
        <article className="card empty-state-card">
          <p>Cargando...</p>
        </article>
      ) : users.length === 0 ? (
        <article className="card empty-state-card">
          <span className="eyebrow">Sin resultados</span>
          <h3>No hay usuarios registrados.</h3>
          <p>Crea el primer usuario con el boton de arriba.</p>
        </article>
      ) : (
        <div className="vehicles-table-shell">
          <table className="vehicles-table">
            <thead>
              <tr>
                <th>Usuario</th>
                <th>Email</th>
                <th>Rol</th>
                <th>Estado</th>
                <th>Creado</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td data-label="Usuario">
                    <strong>{u.username}</strong>
                  </td>
                  <td data-label="Email">{u.email}</td>
                  <td data-label="Rol">
                    <span className={`status ${u.role === "admin" ? "status-error" : u.role === "editor" ? "status-partial" : "status-ok"}`}>
                      {ROLE_LABELS[u.role] ?? u.role}
                    </span>
                  </td>
                  <td data-label="Estado">
                    <span className={`status ${u.is_active ? "status-ok" : "status-error"}`}>
                      {u.is_active ? "Activo" : "Inactivo"}
                    </span>
                  </td>
                  <td data-label="Creado">{formatDate(u.created_at)}</td>
                  <td data-label="Acciones">
                    <button
                      className="button-secondary button-sm"
                      onClick={() => setModal({ mode: "edit", user: u })}
                    >
                      Editar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {modal === "create" && (
        <CreateUserModal loading={saving} onClose={() => setModal(null)} onSubmit={handleCreate} />
      )}
      {modal?.mode === "edit" && (
        <EditUserModal user={modal.user} loading={saving} onClose={() => setModal(null)} onSubmit={handleEdit} />
      )}
    </section>
  );
}
