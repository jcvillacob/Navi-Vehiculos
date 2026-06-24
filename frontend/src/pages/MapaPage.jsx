import { useCallback, useMemo, useState } from "react";

import MapView from "../features/mapa/components/MapView";
import { useMapaData } from "../features/mapa/hooks/useMapaData";

const ALL = "all";

function formatHours(h) {
  if (h == null) return "—";
  const hours = Math.floor(h);
  const mins = Math.round((h - hours) * 60);
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}

export default function MapaPage() {
  const { loading, error, geofences, vehicles, vehiclesByGeofence } = useMapaData();
  const [selectedPlate, setSelectedPlate] = useState(null);
  const [filterCity, setFilterCity] = useState(ALL);

  const handleSelectPlate = useCallback((plate) => setSelectedPlate(plate), []);

  const visibleVehicles = useMemo(
    () => (filterCity === ALL ? vehicles : vehicles.filter((v) => v.geofenceId === filterCity)),
    [vehicles, filterCity]
  );

  const sortedVehicles = useMemo(
    () => [...visibleVehicles].sort((a, b) => b.hoursInside - a.hoursInside),
    [visibleVehicles]
  );

  const totals = useMemo(() => {
    const count = vehicles.length;
    const avg = count ? vehicles.reduce((acc, v) => acc + (v.hoursInside || 0), 0) / count : 0;
    const max = count ? Math.max(...vehicles.map((v) => v.hoursInside || 0)) : 0;
    return { count, avg, max };
  }, [vehicles]);

  return (
    <section className="panel mapa-panel">
      <header className="page-header">
        <span className="eyebrow">Geocercas</span>
        <h2>Mapa</h2>
        <p>
          Vehiculos dentro de geocercas en Bogota, Medellin y Cali, con el tiempo que llevan
          dentro. Datos de demostracion; mas adelante se conectan en vivo.
        </p>
      </header>

      <section className="vehicles-summary-grid">
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Vehiculos en geocercas</span>
          <strong>{totals.count}</strong>
        </article>
        <article className="card metric-card metric-card-compact feature-card-accent">
          <span className="eyebrow">Promedio dentro</span>
          <strong>{formatHours(totals.avg)}</strong>
        </article>
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Maximo dentro</span>
          <strong>{formatHours(totals.max)}</strong>
        </article>
      </section>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}

      <section className="mapa-layout">
        <aside className="card mapa-sidebar">
          <header className="section-heading">
            <div>
              <span className="eyebrow">Vehiculos</span>
              <h3>Dentro de geocercas</h3>
            </div>
            <div className="actions-row section-heading-actions">
              <select
                className="mapa-city-filter"
                value={filterCity}
                onChange={(event) => setFilterCity(event.target.value)}
                aria-label="Filtrar por ciudad"
              >
                <option value={ALL}>Todas las ciudades</option>
                {geofences.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} ({vehiclesByGeofence[g.id]?.length || 0})
                  </option>
                ))}
              </select>
            </div>
          </header>

          <div className="mapa-table-shell">
            <table className="mapa-table">
              <thead>
                <tr>
                  <th>Placa</th>
                  <th>Ciudad</th>
                  <th>Tiempo dentro</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={3} className="table-empty-row">
                      Cargando…
                    </td>
                  </tr>
                ) : sortedVehicles.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="table-empty-row">
                      No hay vehiculos en esta geocerca.
                    </td>
                  </tr>
                ) : (
                  sortedVehicles.map((v) => {
                    const gf = geofences.find((g) => g.id === v.geofenceId);
                    const isSel = v.plate === selectedPlate;
                    return (
                      <tr
                        key={v.plate}
                        className={isSel ? "is-selected" : ""}
                        onClick={() => setSelectedPlate(v.plate)}
                      >
                        <td>
                          <strong>{v.plate}</strong>
                          {v.motor ? <span className="mapa-row-sub">{v.motor}</span> : null}
                        </td>
                        <td>
                          <span className="mapa-geofence-chip" style={gf ? { "--gf-color": gf.color } : undefined}>
                            {v.geofenceName}
                          </span>
                        </td>
                        <td className="mapa-hours">{formatHours(v.hoursInside)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </aside>

        <div className="card mapa-main">
          <MapView
            geofences={geofences}
            vehicles={visibleVehicles}
            selectedPlate={selectedPlate}
            onSelectPlate={handleSelectPlate}
          />
        </div>
      </section>
    </section>
  );
}
