import { useEffect, useMemo, useState } from "react";

import { fetchTallerHistory } from "../../../api/mapaApi";
import { categoryBadgeClass } from "../../categories";

const PAGE_SIZE = 15;
const PAGE_SIZE_OPTIONS = [15, 25, 50, 100];

function formatDuration(minutes) {
  if (minutes == null) return "—";
  const m = Math.max(0, Math.floor(minutes));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m - h * 60;
  return rem ? `${h}h ${rem} min` : `${h}h`;
}

function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const datePart = d.toLocaleDateString("es-CO", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  const timePart = d.toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return (
    <span className="mapa-datetime">
      <span className="mapa-datetime-date">
        {datePart.replace(/^\w/, (c) => c.toUpperCase())}
      </span>
      <span className="mapa-datetime-time">{timePart}</span>
    </span>
  );
}

export default function HistoryModal({ zones = [], onClose }) {
  const [plateFilter, setPlateFilter] = useState("");
  const [zoneFilter, setZoneFilter] = useState("");
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [data, setData] = useState({ days: 0, count: 0, visits: [] });

  // Filtro de placa client-side (sobre lo ya cargado) para respuesta instantanea;
  // el filtro de taller pega al backend porque acota el dataset.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setPage(1);
    fetchTallerHistory({ zoneId: zoneFilter || undefined })
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message || "No fue posible cargar el historico");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [zoneFilter]);

  const visibleVisits = useMemo(() => {
    const q = plateFilter.trim().toUpperCase();
    if (!q) return data.visits;
    return data.visits.filter((v) => v.plate?.toUpperCase().includes(q));
  }, [data.visits, plateFilter]);

  // Reinicia la paginacion cuando cambia el filtro de placa o el tamaño de pagina.
  useEffect(() => {
    setPage(1);
  }, [plateFilter, pageSize]);

  const totalCount = visibleVisits.length;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const safePage = Math.min(page, totalPages);
  const startIdx = (safePage - 1) * pageSize;
  const pageVisits = visibleVisits.slice(startIdx, startIdx + pageSize);

  const fromRow = totalCount === 0 ? 0 : startIdx + 1;
  const toRow = Math.min(startIdx + pageSize, totalCount);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-card mapa-history-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Geocercas</span>
            <h3>Histórico taller</h3>
            <p className="modal-support-copy">
              Visitas entrada→salida de los últimos {data.days || 45} días.
            </p>
          </div>
          <button
            type="button"
            className="icon-button modal-close-button"
            onClick={onClose}
            aria-label="Cerrar"
          >
            ✕
          </button>
        </header>

        <div className="mapa-history-filters">
          <input
            type="text"
            placeholder="Filtrar por placa"
            value={plateFilter}
            onChange={(e) => setPlateFilter(e.target.value)}
            maxLength={10}
          />
          <select
            value={zoneFilter}
            onChange={(e) => setZoneFilter(e.target.value)}
            aria-label="Filtrar por taller"
          >
            <option value="">Todos los talleres</option>
            {zones.map((z) => (
              <option key={z.id} value={z.id}>
                {z.name}
              </option>
            ))}
          </select>
          <span className="mapa-history-count">
            {loading ? "…" : `${totalCount} visitas`}
          </span>
        </div>

        {error ? (
          <div className="notice-banner notice-error">{error}</div>
        ) : null}

        <div className="mapa-table-shell mapa-history-shell">
          <table className="mapa-table">
            <thead>
              <tr>
                <th>Placa</th>
                <th>Taller</th>
                <th>Categoría</th>
                <th>Entrada</th>
                <th>Salida</th>
                <th>Duración</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="table-empty-row">
                    Cargando…
                  </td>
                </tr>
              ) : pageVisits.length === 0 ? (
                <tr>
                  <td colSpan={6} className="table-empty-row">
                    Sin visitas en el periodo.
                  </td>
                </tr>
              ) : (
                pageVisits.map((v, i) => (
                  <tr key={`${v.plate}-${v.enter_ts_local || v.exit_ts_local}-${i}`}>
                    <td>
                      <strong>{v.plate}</strong>
                      {v.motor ? <span className="mapa-row-sub">{v.motor}</span> : null}
                      {v.client_name ? (
                        <span className="mapa-row-sub">{v.client_name}</span>
                      ) : null}
                    </td>
                    <td>
                      <span className="mapa-geofence-chip">{v.zone_name || "—"}</span>
                    </td>
                    <td>
                      <span className={categoryBadgeClass(v.category || "Ninguna")}>
                        {v.category || "Ninguna"}
                      </span>
                    </td>
                    <td>{formatDateTime(v.enter_ts_local)}</td>
                    <td>
                      {v.exit_ts_local ? (
                        formatDateTime(v.exit_ts_local)
                      ) : (
                        <span className="status-pill status-soft">Dentro</span>
                      )}
                    </td>
                    <td className="mapa-hours">{formatDuration(v.minutes_inside)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {!loading && totalCount > 0 ? (
          <div className="mapa-history-pagination">
            <span className="mapa-history-pagination-info">
              {fromRow}–{toRow} de {totalCount}
            </span>
            <div className="mapa-history-pagination-controls">
              <button
                type="button"
                className="icon-button mapa-history-page-btn"
                onClick={() => setPage(Math.max(1, safePage - 1))}
                disabled={safePage <= 1}
                aria-label="Página anterior"
              >
                ‹
              </button>
              <span className="mapa-history-pagination-pages">
                Página {safePage} de {totalPages}
              </span>
              <button
                type="button"
                className="icon-button mapa-history-page-btn"
                onClick={() => setPage(Math.min(totalPages, safePage + 1))}
                disabled={safePage >= totalPages}
                aria-label="Página siguiente"
              >
                ›
              </button>
            </div>
            <label className="mapa-history-pagination-size">
              Filas
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
                aria-label="Filas por página"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
      </div>
    </div>
  );
}