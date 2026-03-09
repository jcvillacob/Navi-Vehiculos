import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import HomePage from "./pages/HomePage";
import EngineLookupPage from "./pages/EngineLookupPage";
import MotorsPage from "./pages/MotorsPage";

export default function App() {
  return (
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
              <NavLink to="/motores">
                <span>Motores</span>
                <small>Catalogo tecnico</small>
              </NavLink>
            </nav>

            <section className="sidebar-note">
              <span className="eyebrow">Operativa</span>
              <p>
                Registra motores una sola vez y deja que la consulta los clasifique automaticamente.
              </p>
            </section>
          </div>
        </aside>

        <main className="content-area">
          <div className="content-shell">
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/consulta-motor" element={<EngineLookupPage />} />
              <Route path="/motores" element={<MotorsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
