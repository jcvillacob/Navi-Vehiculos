import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import HomePage from "./pages/HomePage";
import EngineLookupPage from "./pages/EngineLookupPage";

export default function App() {
  return (
    <div className="dashboard-layout">
      <aside className="sidebar">
        <div className="brand-block">
          <h1>Navi Vehiculos</h1>
          <p>Panel de consulta</p>
        </div>

        <nav className="sidebar-nav">
          <NavLink to="/" end>
            Inicio
          </NavLink>
          <NavLink to="/consulta-motor">Consulta motor</NavLink>
        </nav>
      </aside>

      <main className="content-area">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/consulta-motor" element={<EngineLookupPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}