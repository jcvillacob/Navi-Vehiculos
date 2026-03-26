import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./context/AuthContext";
import { BulkRefreshProvider } from "./context/BulkRefreshContext";
import ProtectedRoute from "./components/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import AuditPage from "./pages/AuditPage";
import UsersPage from "./pages/UsersPage";
import HomePage from "./pages/HomePage";
import RendimientosPage from "./pages/RendimientosPage";
import CustomersPage from "./pages/CustomersPage";
import EngineLookupPage from "./pages/EngineLookupPage";
import MotorsPage from "./pages/MotorsPage";
import VehiclesPage from "./pages/VehiclesPage";

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

  return (
    <BulkRefreshProvider>
    <div className="app-shell">
      <div className="app-orb app-orb-one" aria-hidden="true" />
      <div className="app-orb app-orb-two" aria-hidden="true" />
      <div className="app-grid">
        <aside className="sidebar">
          <div className="sidebar-panel">
            <div className="brand-block">
              <span className="brand-kicker">Navi Fleet Intelligence</span>
              <h1>Navi Vehiculos</h1>
              <p>Consulta vehiculos, identifica motores y consolida el catalogo tecnico.</p>
            </div>

            <nav className="sidebar-nav">
              <NavLink to="/" end>
                <span>Inicio</span>
                <small>Resumen del sistema</small>
              </NavLink>
              <NavLink to="/consulta-motor">
                <span>Consulta motor</span>
                <small>Lookup por placa</small>
              </NavLink>
              <NavLink to="/vehiculos">
                <span>Vehiculos</span>
                <small>Placas asociadas</small>
              </NavLink>
              <NavLink to="/rendimientos">
                <span>Rendimientos</span>
                <small>Kms, horas y consumo</small>
              </NavLink>
              <NavLink to="/motores">
                <span>Motores</span>
                <small>Catalogo tecnico</small>
              </NavLink>
              <NavLink to="/clientes">
                <span>Clientes</span>
                <small>Databases y accesos</small>
              </NavLink>
              {user?.role === "admin" && (
                <>
                  <NavLink to="/usuarios">
                    <span>Usuarios</span>
                    <small>Roles y accesos</small>
                  </NavLink>
                  <NavLink to="/auditoria">
                    <span>Auditoria</span>
                    <small>Logs del sistema</small>
                  </NavLink>
                </>
              )}
            </nav>

            <section className="sidebar-note">
              <span className="eyebrow">Operativa</span>
              <p>
                Registra motores una sola vez y deja que la consulta los clasifique automaticamente.
              </p>
            </section>

            <div className="sidebar-user">
              <div className="sidebar-user-info">
                <span className="sidebar-user-name">{user?.username}</span>
                <span className="sidebar-user-role">{user?.role}</span>
              </div>
              <button
                className="button-secondary button-sm sidebar-logout"
                onClick={logout}
              >
                Cerrar sesion
              </button>
            </div>
          </div>
        </aside>

        <main className="content-area">
          <div className="content-shell">
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/consulta-motor" element={<EngineLookupPage />} />
              <Route path="/motores" element={<MotorsPage />} />
              <Route path="/clientes" element={<CustomersPage />} />
              <Route path="/vehiculos" element={<VehiclesPage />} />
              <Route path="/rendimientos" element={<RendimientosPage />} />
              <Route
                path="/usuarios"
                element={
                  <ProtectedRoute roles={["admin"]}>
                    <UsersPage />
                  </ProtectedRoute>
                }
              />
              <Route
                path="/auditoria"
                element={
                  <ProtectedRoute roles={["admin"]}>
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
    </BulkRefreshProvider>
  );
}
