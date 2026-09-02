import { formatMonthLabel } from "../../../utils/formatters";

/**
 * Barra de progreso del calculo en curso (meses + placas dentro del mes).
 */
export default function JobProgressBar({ progress, onCancel }) {
  if (!progress || progress.total <= 0) return null;

  const monthsPct = (progress.current / progress.total) * 100;
  const withinMonthPct = progress.totalTargets > 0
    ? (progress.processedTargets / progress.totalTargets) * 100
    : 0;
  // Progreso global = meses completos + fraccion del mes en curso
  const completedMonths = Math.max(0, progress.current - 1);
  const currentMonthFraction = progress.totalTargets > 0
    ? progress.processedTargets / progress.totalTargets
    : 0;
  const overallPct = ((completedMonths + currentMonthFraction) / progress.total) * 100;
  const shownPct = overallPct || monthsPct;

  return (
    <div
      className="bulk-progress-bar-container rendimientos-progress"
      style={{ marginTop: 8 }}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(shownPct)}
      aria-label="Progreso del cálculo de rendimientos"
    >
      <div className="bulk-progress-header">
        <span className="bulk-progress-label">
          Calculando {formatMonthLabel(progress.currentMonth)} ({progress.current} de {progress.total})
          {progress.totalTargets > 0 && (
            <> — {progress.processedTargets} / {progress.totalTargets} placas ({Math.round(withinMonthPct)}%)</>
          )}
        </span>
        <span className="bulk-progress-percent">{Math.round(shownPct)}%</span>
      </div>
      <div className="bulk-progress-track">
        <div className="bulk-progress-fill" style={{ width: `${shownPct}%` }} />
      </div>
      <button
        type="button"
        className="button-secondary button-sm"
        style={{ marginTop: 6, alignSelf: "flex-end" }}
        onClick={onCancel}
      >
        Cancelar
      </button>
    </div>
  );
}

/**
 * Resumen compacto del ultimo job completado (header de la pagina).
 */
export function LastRunBadge({ job }) {
  if (!job || job.status !== "done") return null;
  const d = job.finished_at ? new Date(job.finished_at) : null;
  const dateStr = d
    ? d.toLocaleDateString("es-CO", { day: "2-digit", month: "short", year: "numeric" })
    : "";
  const s = job.summary || {};
  const calculated = s.calculated || 0;
  const total = s.total || 0;
  const pct = total > 0 ? Math.round((calculated / total) * 100) : 0;
  return (
    <span className="rendimientos-last-run" title="Ultimo calculo completado">
      <svg
        className="rendimientos-last-run-icon"
        viewBox="0 0 24 24"
        width="14"
        height="14"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <rect x="3" y="4" width="18" height="18" rx="2" />
        <path d="M16 2v4" />
        <path d="M8 2v4" />
        <path d="M3 10h18" />
      </svg>
      <span className="rendimientos-last-run-date">{dateStr}</span>
      <span className="rendimientos-last-run-divider" aria-hidden="true">·</span>
      <strong className="rendimientos-last-run-count">
        {calculated.toLocaleString("es-CO")} / {total.toLocaleString("es-CO")}
      </strong>
      <span className="rendimientos-last-run-pct">{pct}%</span>
    </span>
  );
}
