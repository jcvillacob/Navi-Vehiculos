import { useCallback, useMemo, useState } from "react";

import MapView from "../features/mapa/components/MapView";
import { useMapaData } from "../features/mapa/hooks/useMapaData";
import { usePermission } from "../context/AuthContext";
import { postManualTallerAction } from "../api/mapaApi";

const ALL = "all";

function formatDuration(minutes) {
  if (minutes == null) return "—";
  const m = Math.max(0, Math.floor(minutes));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem ? `${h}h ${rem} min` : `${h}h`;
}

function formatLocalTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" });
}

export default function MapaPage() {
  const { loading, refreshing, error, zones, vehicles, vehiclesByZoneId, refresh } =
    useMapaData();
  const canManage = usePermission("mapa.taller.manage");
  const [selectedPlate, setSelectedPlate] = useState(null);
  const [filterZoneId, setFilterZoneId] = useState(ALL);
  const [manualPlate, setManualPlate] = useState("");
  const [manualError, setManualError] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const handleSelectPlate = useCallback(
    (plate) => setSelectedPlate(plate),
    []
  );

  const visibleVehicles = useMemo(
    () =>
      filterZoneId === ALL
        ? vehicles
        : vehicles.filter((v) => v.zone_id === filterZoneId),
    [vehicles, filterZoneId]
  );

  const sortedVehicles = useMemo(
    () => [...visibleVehicles].sort((a, b) => b.minutes_inside - a.minutes_inside),
    [visibleVehicles]
  );

  const totals = useMemo(() => {
    const count = vehicles.length;
    const avg = count
      ? vehicles.reduce((acc, v) => acc + (v.minutes_inside || 0), 0) / count
      : 0;
    const max = count
      ? Math.max(...vehicles.map((v) => v.minutes_inside || 0))
      : 0;
    return { count, avg, max };
  }, [vehicles]);

  const handleManualAction = useCallback(
    async (plate, action) => {
      setActionLoading(true);
      setManualError("");
      try {
        await postManualTallerAction(plate, action);
        await refresh();
        setManualPlate("");
      } catch (err) {
        setManualError(err?.message || "Error en la acción manual");
      } finally {
        setActionLoading(false);
      }
    },
    [refresh]
  );

  const handleAddManual = useCallback(
    (e) => {
      e.preventDefault();
      const plate = manualPlate.trim().toUpperCase();
      if (!plate) return;
      handleManualAction(plate, "add");
    },
    [manualPlate, handleManualAction]
  );

  return (
    <section className="panel mapa-panel">
      <header className="page-header">
        <span className="eyebrow">Geocercas</span>
        <h2>Mapa</h2>
        <p>
          Vehiculos en taller con mas de 30 minutos dentro de la geocerca.
          Datos en vivo desde Geotab; se actualiza automaticamente cada 10
          minutos.
        </p>
      </header>

      <section className="vehicles-summary-grid">
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Vehiculos en taller</span>
          <strong>{totals.count}</strong>
        </article>
        <article className="card metric-card metric-card-compact feature-card-accent">
          <span className="eyebrow">Promedio dentro</span>
          <strong>{formatDuration(totals.avg)}</strong>
        </article>
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Maximo dentro</span>
          <strong>{formatDuration(totals.max)}</strong>
        </article>
      </section>

      {error ? (
        <div className="notice-banner notice-error">
          {error}
          <button
            type="button"
            className="button-secondary"
            onClick={refresh}
            style={{ marginLeft: 12 }}
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {manualError ? (
        <div className="notice-banner notice-error">{manualError}</div>
      ) : null}

      <section className="mapa-layout">
        <aside className="card mapa-sidebar">
          <header className="section-heading">
            <div>
              <span className="eyebrow">Vehiculos</span>
              <h3>Dentro de taller</h3>
            </div>
            <div className="actions-row section-heading-actions">
              <select
                className="mapa-city-filter"
                value={filterZoneId}
                onChange={(event) => setFilterZoneId(event.target.value)}
                aria-label="Filtrar por taller"
              >
                <option value={ALL}>Todos los talleres</option>
                {zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name} ({vehiclesByZoneId[z.id]?.length || 0})
                  </option>
                ))}
              </select>
            </div>
          </header>

          {canManage ? (
            <form
              className="mapa-manual-form"
              onSubmit={handleAddManual}
              style={{ display: "flex", gap: 8, marginBottom: 12 }}
            >
              <input
                type="text"
                placeholder="Placa para agregar manual"
                value={manualPlate}
                onChange={(e) => setManualPlate(e.target.value)}
                maxLength={10}
                style={{ flex: 1 }}
                disabled={actionLoading}
              />
              <button
                type="submit"
                className="button-sm"
                disabled={actionLoading || !manualPlate.trim()}
              >
                Agregar
              </button>
            </form>
          ) : null}

          <div className="mapa-table-shell">
            <table className="mapa-table">
              <thead>
                <tr>
                  <th>Placa</th>
                  <th>Taller</th>
                  <th>Tiempo dentro</th>
                  {canManage ? <th>Acciones</th> : null}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={canManage ? 4 : 3} className="table-empty-row">
                      Cargando…
                    </td>
                  </tr>
                ) : sortedVehicles.length === 0 ? (
                  <tr>
                    <td colSpan={canManage ? 4 : 3} className="table-empty-row">
                      No hay vehiculos en este taller.
                    </td>
                  </tr>
                ) : (
                  sortedVehicles.map((v) => {
                    const isSel = v.plate === selectedPlate;
                    return (
                      <tr
                        key={v.plate}
                        className={isSel ? "is-selected" : ""}
                        onClick={() => setSelectedPlate(v.plate)}
                      >
                        <td>
                          <strong>{v.plate}</strong>
                          {v.manual ? (
                            <span
                              className="status-pill status-soft"
                              style={{ marginLeft: 6, fontSize: 9 }}
                            >
                              MANUAL
                            </span>
                          ) : null}
                          {v.motor ? (
                            <span className="mapa-row-sub">{v.motor}</span>
                          ) : null}
                          {v.client_name ? (
                            <span className="mapa-row-sub">{v.client_name}</span>
                          ) : null}
                        </td>
                        <td>
                          <span className="mapa-geofence-chip">
                            {v.zone_name || "—"}
                          </span>
                          {v.category ? (
                            <span className="mapa-row-sub">{v.category}</span>
                          ) : null}
                        </td>
                        <td className="mapa-hours">
                          {formatDuration(v.minutes_inside)}
                          <span className="mapa-row-sub">
                            desde {formatLocalTime(v.enter_ts_local)}
                          </span>
                        </td>
                        {canManage ? (
                          <td>
                            <div
                              style={{
                                display: "flex",
                                gap: 4,
                                flexWrap: "wrap",
                              }}
                            >
                              <button
                                type="button"
                                className="button-sm button-secondary"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleManualAction(v.plate, "hide");
                                }}
                                disabled={actionLoading}
                                title="Ocultar del mapa"
                              >
                                Ocultar
                              </button>
                              <button
                                type="button"
                                className="button-sm button-danger-outline"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleManualAction(v.plate, "close");
                                }}
                                disabled={actionLoading}
                                title="Cerrar y eliminar del mapa"
                              >
                                Cerrar
                              </button>
                            </div>
                          </td>
                        ) : null}
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
            zones={zones}
            vehicles={visibleVehicles}
            selectedPlate={selectedPlate}
            onSelectPlate={handleSelectPlate}
          />
        </div>
      </section>
    </section>
  );
}
