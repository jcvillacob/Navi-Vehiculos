import { useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import Can from "./components/Can";
import { changeOwnPassword } from "./api/vehicleApi";
import { useAuth } from "./context/AuthContext";
import { BulkRefreshProvider } from "./context/BulkRefreshContext";
import PasswordInput from "./components/PasswordInput";
import ProtectedRoute from "./components/ProtectedRoute";
import { validatePasswordStrength } from "./utils/passwordValidation";
import LoginPage from "./pages/LoginPage";
import AuditPage from "./pages/AuditPage";
import UsersPage from "./pages/UsersPage";
import RolesPage from "./pages/RolesPage";
import HomePage from "./pages/HomePage";
import RendimientosPage from "./pages/RendimientosPage";
import DisponibilidadPage from "./pages/DisponibilidadPage";
import MapaPage from "./pages/MapaPage";
import CustomersPage from "./pages/CustomersPage";
import EngineLookupPage from "./pages/EngineLookupPage";
import MotorsPage from "./pages/MotorsPage";
import VehiclesPage from "./pages/VehiclesPage";

function ChangePasswordModal({ user, onClose }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const errors = validatePasswordStrength(newPassword, user?.username);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      await changeOwnPassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      onClose();
    } catch (err) {
      setError(err.message || "No fue posible cambiar la contrasena");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Cambiar contrasena">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Seguridad</span>
            <h3>Cambiar contrasena</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>Cerrar</button>
        </header>
        {error ? <div className="notice-banner notice-error">{error}</div> : null}
        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="current-password">Contrasena actual</label>
            <PasswordInput id="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required />
          </div>
          <div className="form-field">
            <label htmlFor="new-password">Nueva contrasena</label>
            <PasswordInput id="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
          </div>
          {newPassword && errors.length ? (
            <div className="notice-banner notice-soft">{errors.join(" ")}</div>
          ) : null}
          <div className="actions-row modal-actions">
            <button type="submit" disabled={saving || !currentPassword || !newPassword || errors.length > 0}>
              {saving ? "Guardando..." : "Actualizar contrasena"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>Cancelar</button>
          </div>
        </form>
      </section>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

function AppShell() {
  const { user, logout } = useAuth();
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef(null);

  useEffect(() => {
    function closeOnClickOutside(event) {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target)) {
        setUserMenuOpen(false);
      }
    }
    if (userMenuOpen) {
      document.addEventListener("mousedown", closeOnClickOutside);
    }
    return () => document.removeEventListener("mousedown", closeOnClickOutside);
  }, [userMenuOpen]);

  return (
    <BulkRefreshProvider>
    <div className="app-shell">
      <div className="app-orb app-orb-one" aria-hidden="true" />
      <div className="app-orb app-orb-two" aria-hidden="true" />
      <div className={`app-grid${sidebarOpen ? "" : " is-collapsed"}`}>
        <aside className="sidebar" aria-hidden={!sidebarOpen}>
          <div className="sidebar-panel">
            <div className="brand-block">
              <div className="sidebar-logo-wrap">
                <img
                  className="sidebar-logo"
                  src="/logo-navitrans.png"
                  alt="Navitrans"
                />
              </div>
              <button
                type="button"
                className="sidebar-toggle sidebar-toggle-close"
                onClick={() => setSidebarOpen(false)}
                aria-label="Ocultar menu"
                title="Ocultar menu"
              >
                &larr;
              </button>
            </div>

            <nav className="sidebar-nav">
              <NavLink to="/" end>
                <span>Inicio</span>
                <small>Resumen del sistema</small>
              </NavLink>
              <NavLink to="/consulta-motor">
                <span>Consulta motor</span>
                <small>Individual y lote</small>
              </NavLink>
              <NavLink to="/rendimientos">
                <span>Rendimientos</span>
                <small>Kms, horas y consumo</small>
              </NavLink>
              <NavLink to="/disponibilidad">
                <span>Disponibilidad</span>
                <small>Panel de flotas</small>
              </NavLink>
              <NavLink to="/mapa">
                <span>Mapa</span>
                <small>Geocercas y vehiculos</small>
              </NavLink>
              <NavLink to="/vehiculos">
                <span>Vehiculos</span>
                <small>Placas asociadas</small>
              </NavLink>
              <div className="sidebar-group">
                <div className="sidebar-group-label">
                  <span>Gestion</span>
                  <small>Catalogos y control</small>
                </div>
                <div className="sidebar-subnav">
                  <NavLink to="/motores">
                    <span>Motores</span>
                    <small>Catalogo tecnico</small>
                  </NavLink>
                  <NavLink to="/clientes">
                    <span>Clientes</span>
                    <small>Databases y accesos</small>
                  </NavLink>
                  <Can permission="users.list">
                    <NavLink to="/usuarios">
                      <span>Usuarios</span>
                      <small>Cuentas y accesos</small>
                    </NavLink>
                  </Can>
                  <Can permission="roles.manage">
                    <NavLink to="/roles">
                      <span>Roles y permisos</span>
                      <small>Matriz por modulo</small>
                    </NavLink>
                  </Can>
                  <Can permission="audit.view">
                    <NavLink to="/auditoria">
                      <span>Auditoria</span>
                      <small>Logs del sistema</small>
                    </NavLink>
                  </Can>
                </div>
              </div>
            </nav>
            <div className="sidebar-user" ref={userMenuRef}>
              <button
                type="button"
                className="sidebar-user-trigger"
                onClick={() => setUserMenuOpen(!userMenuOpen)}
                aria-expanded={userMenuOpen}
                aria-haspopup="menu"
              >
                <div className="sidebar-user-info">
                  <span className="sidebar-user-name">{user?.username}</span>
                  <span className="sidebar-user-role">{user?.role}</span>
                </div>
                <span className="sidebar-user-arrow">{userMenuOpen ? "▲" : "▼"}</span>
              </button>
              {userMenuOpen ? (
                <div className="sidebar-user-menu" role="menu">
                  <button
                    type="button"
                    className="sidebar-user-menu-item"
                    role="menuitem"
                    onClick={() => { setShowPasswordModal(true); setUserMenuOpen(false); }}
                  >
                    Cambiar contraseña
                  </button>
                  <button
                    type="button"
                    className="sidebar-user-menu-item"
                    role="menuitem"
                    onClick={() => { logout(); setUserMenuOpen(false); }}
                  >
                    Cerrar sesión
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </aside>

        <main className="content-area">
          {!sidebarOpen ? (
            <button
              type="button"
              className="sidebar-toggle sidebar-toggle-open"
              onClick={() => setSidebarOpen(true)}
              aria-label="Mostrar menu"
              title="Mostrar menu"
            >
              &rarr;
            </button>
          ) : null}
          <div className="content-shell">
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/consulta-motor" element={<EngineLookupPage />} />
              <Route path="/consulta-lote" element={<Navigate to="/consulta-motor?modo=lote" replace />} />
              <Route path="/motores" element={<MotorsPage />} />
              <Route path="/clientes" element={<CustomersPage />} />
              <Route path="/vehiculos" element={<VehiclesPage />} />
              <Route path="/rendimientos" element={<RendimientosPage />} />
              <Route path="/disponibilidad" element={<DisponibilidadPage />} />
              <Route path="/mapa" element={<MapaPage />} />
              <Route
                path="/usuarios"
                element={
                  <ProtectedRoute permissions={["users.list"]}>
                    <UsersPage />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/roles"
                element={
                  <ProtectedRoute permissions={["roles.manage"]}>
                    <RolesPage />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/auditoria"
                element={
                  <ProtectedRoute permissions={["audit.view"]}>
                    <AuditPage />
                  </ProtectedRoute>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
    {showPasswordModal ? (
      <ChangePasswordModal user={user} onClose={() => setShowPasswordModal(false)} />
    ) : null}
    </BulkRefreshProvider>
  );
}
