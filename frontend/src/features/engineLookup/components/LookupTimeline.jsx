import { useEffect, useRef } from "react";

const STEP_ICON = {
  running: "…",
  ok: "✓",
  warning: "!",
  error: "✕",
  info: "i",
};

const STEP_TONE = {
  running: "is-running",
  ok: "is-ok",
  warning: "is-warning",
  error: "is-error",
  info: "is-info",
};

const SOURCE_LABEL = {
  geotab: "Geotab",
  fenix: "Fenix",
  cummins: "Cummins/QuickServe",
  local: "Local",
  cache: "Cache",
};

export default function LookupTimeline({ steps, loading }) {
  const listRef = useRef(null);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = 0;
    }
  }, [steps.length]);

  if (!steps.length && !loading) return null;

  // Mostrar los steps mas recientes arriba; en el array `steps` van en orden
  // de llegada, asi que invertimos al renderizar.
  const orderedSteps = steps.slice().reverse();

  return (
    <section className="card lookup-timeline" aria-live="polite">
      <header className="lookup-timeline-header">
        <span className="eyebrow">Progreso de la consulta</span>
        <span className="lookup-timeline-status">
          {loading ? "En curso..." : "Consulta finalizada"}
        </span>
      </header>
      <ol className="lookup-timeline-list" ref={listRef}>
        {orderedSteps.map((step, idx) => {
          const status = STEP_TONE[step.status] ? step.status : "info";
          // idx=0 es el step mas reciente, idx=1 el anterior, etc.
          // Escalonamos la animacion para que se sienta como una cascada.
          const delay = `${idx * 70}ms`;
          return (
            <li
              key={`${step.step}-${step.message}-${idx}`}
              className={`lookup-timeline-item ${STEP_TONE[status] || ""}`}
              style={{ animationDelay: delay }}
            >
              <span className="lookup-timeline-dot" aria-hidden>
                {STEP_ICON[status] || "·"}
              </span>
              <div className="lookup-timeline-body">
                <div className="lookup-timeline-message">{step.message}</div>
                <div className="lookup-timeline-meta">
                  {SOURCE_LABEL[step.source] || step.source}
                  {step.step && step.step !== "done" ? ` · ${step.step}` : ""}
                </div>
              </div>
            </li>
          );
        })}
        {loading ? (
          <li className="lookup-timeline-item is-pending" style={{ animationDelay: "0ms" }}>
            <span className="lookup-timeline-dot" aria-hidden>
              …
            </span>
            <div className="lookup-timeline-body">
              <div className="lookup-timeline-message">Esperando siguiente paso...</div>
            </div>
          </li>
        ) : null}
      </ol>
    </section>
  );
}
