import { useEffect, useMemo, useState } from "react";

import TablePagination from "../../../components/TablePagination";
import { formatDateTime, formatMonthLabel } from "../../../utils/formatters";

const HISTORY_PAGE_SIZES = [5, 10, 25, 50];

function jobStatusMeta(status) {
  if (status === "done") return { className: "status-ok", label: "Listo" };
  if (status === "error") return { className: "status-error", label: "Error" };
  if (status === "running") return { className: "status-soft", label: "Corriendo" };
  if (status === "queued") return { className: "status-partial", label: "En cola" };
  return { className: "status-partial", label: status };
}

function JobRow({ job }) {
  const { className, label } = jobStatusMeta(job.status);
  const startedAt = job.started_at ? new Date(job.started_at) : null;
  const finishedAt = job.finished_at ? new Date(job.finished_at) : null;
  const duration = startedAt && finishedAt
    ? `${Math.max(1, Math.round((finishedAt - startedAt) / 1000))} s`
    : "—";
  const s = job.summary || {};
  const totalLabel = job.total_targets > 0
    ? `${job.processed_targets}/${job.total_targets}`
    : (s.total ? `${s.calculated || 0}/${s.total}` : "—");

  return (
    <tr>
      <td data-label="Estado">
        <span className={`status-dot ${className}`} title={label} />
        <span style={{ marginLeft: 8 }}>{label}</span>
      </td>
      <td data-label="Mes">{formatMonthLabel(job.month)}</td>
      <td data-label="Disparado por">
        {job.triggered_by === "cron" ? (
          <span className="trigger-chip trigger-chip-cron" title="Ejecucion automatica del scheduler (05:00 Colombia)">
            <span aria-hidden="true">⏱</span> Cron
          </span>
        ) : (
          <span className="trigger-chip trigger-chip-ui">Manual</span>
        )}
      </td>
      <td data-label="Inicio">{startedAt ? formatDateTime(startedAt) : "—"}</td>
      <td data-label="Fin">{finishedAt ? formatDateTime(finishedAt) : "—"}</td>
      <td data-label="Duración">{duration}</td>
      <td data-label="Placas">{totalLabel}</td>
      <td data-label="Resumen / Error">
        {job.status === "error" && job.error_message ? (
          <span title={job.error_message} style={{ color: "var(--red)" }}>
            {job.error_message.length > 120 ? `${job.error_message.slice(0, 120)}…` : job.error_message}
          </span>
        ) : job.status === "done" && job.summary ? (
          <span>
            calc {s.calculated || 0} · parcial {s.partial || 0} · sin binding {s.unbound || 0} · sin datos {s.no_data || 0} · err {s.error || 0}
          </span>
        ) : (
          <span className="support-copy">—</span>
        )}
      </td>
    </tr>
  );
}

/**
 * Historial de jobs recientes (colapsable, paginado en cliente).
 */
export default function JobHistoryCard({ jobs, loading, onReload }) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(jobs.length / pageSize));
    if (page > maxPage) setPage(1);
  }, [jobs.length, pageSize, page]);

  const paginated = useMemo(() => {
    const start = (page - 1) * pageSize;
    return jobs.slice(start, start + pageSize);
  }, [jobs, page, pageSize]);

  return (
    <section className="card rendimientos-history-card">
      <header className="section-heading">
        <div>
          <span className="eyebrow">Historial</span>
          <h3>Últimos cálculos</h3>
        </div>
        <div className="actions-row section-heading-actions">
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? "Ocultar" : "Mostrar"}
          </button>
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={onReload}
            disabled={loading}
          >
            {loading ? "Cargando..." : "Recargar"}
          </button>
        </div>
      </header>

      {open && (jobs.length === 0 ? (
        <p className="support-copy">No hay cálculos registrados todavía.</p>
      ) : (
        <>
          <div className="rendimientos-table-shell">
            <table className="rendimientos-table">
              <thead>
                <tr>
                  <th>Estado</th>
                  <th>Mes</th>
                  <th>Disparado por</th>
                  <th>Inicio</th>
                  <th>Fin</th>
                  <th>Duración</th>
                  <th>Placas</th>
                  <th>Resumen / Error</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((job) => <JobRow key={job.id} job={job} />)}
              </tbody>
            </table>
          </div>
          <TablePagination
            page={page}
            pageSize={pageSize}
            total={jobs.length}
            onPageChange={setPage}
            onPageSizeChange={(size) => { setPageSize(size); setPage(1); }}
            pageSizeOptions={HISTORY_PAGE_SIZES}
            itemLabel="job(s)"
          />
        </>
      ))}
    </section>
  );
}
