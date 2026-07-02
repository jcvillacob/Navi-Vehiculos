import { useMemo, useState } from "react";

import { useCustomersCatalog } from "../../customers/hooks/useCustomersCatalog";

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function getStatusClass(status) {
  if (status === "ok") return "status-ok";
  if (status === "partial") return "status-partial";
  if (status === "not_found") return "status-not_found";
  return "status-error";
}

function getRowClass(status) {
  if (status === "ok") return "bulk-row-ok";
  if (status === "partial") return "bulk-row-partial";
  if (status === "not_found") return "bulk-row-not-found";
  return "bulk-row-error";
}

export default function BulkLookupRunner({
  results,
  status,
  processed,
  total,
  currentIdentifier,
  delayMs,
  setDelayMs,
  start,
  pause,
  cancel,
  reset,
  elapsedMs,
  estimatedRemainingMs,
  customerDatabaseId,
  setCustomerDatabaseId,
  assignmentSummary,
}) {
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const [selectedCustomerId, setSelectedCustomerId] = useState("");

  const databases = useMemo(() => {
    const customer = customers.find((c) => String(c.id) === String(selectedCustomerId));
    return customer?.databases || [];
  }, [customers, selectedCustomerId]);

  const percent = total ? Math.round((processed / total) * 100) : 0;
  const counts = results.reduce(
    (acc, item) => {
      if (item.status === "ok") acc.ok += 1;
      else if (item.status === "partial") acc.partial += 1;
      else if (item.status === "not_found") acc.notFound += 1;
      else acc.error += 1;
      return acc;
    },
    { ok: 0, partial: 0, notFound: 0, error: 0 }
  );

  const recentItems = [...results].slice(-8).reverse();

  const handlePrimaryAction = async () => {
    if (status === "running") {
      pause();
      return;
    }

    if (status === "done" || status === "cancelled") {
      reset();
      return;
    }

    await start();
  };

  const primaryLabel = status === "idle"
    ? "Iniciar consulta"
    : status === "running"
      ? "Pausar"
      : status === "paused"
        ? "Reanudar"
        : "Reiniciar";

  const showCancel = status === "running" || status === "paused";

  const handleCancel = () => {
    const confirmed = window.confirm("Se cancelara el lote actual. Esta accion no se puede reanudar.");
    if (confirmed) cancel();
  };

  const handleCustomerChange = (event) => {
    const id = event.target.value;
    setSelectedCustomerId(id);
    setCustomerDatabaseId(null);
  };

  const handleDatabaseChange = (event) => {
    const raw = event.target.value;
    setCustomerDatabaseId(raw ? Number(raw) : null);
  };

  const isAssignmentRunning = status === "running" || status === "paused";
  const assignmentLabel = (() => {
    if (!customerDatabaseId) return null;
    if (assignmentSummary.attempted === 0) {
      return "Asignacion pendiente: aplicara a medida que se resuelvan las placas";
    }
    return `Asignadas ${assignmentSummary.success} de ${assignmentSummary.attempted}`
      + (assignmentSummary.failed > 0 ? ` · fallaron ${assignmentSummary.failed}` : "");
  })();

  return (
    <article className="card bulk-upload-card">
      <div className="dash-search-heading">
        <span className="eyebrow">Paso 2</span>
        <h3>Ejecucion</h3>
        <p className="support-copy">
          El lote corre en secuencia. Si una respuesta llega desde cache, el siguiente delay se omite.
        </p>
      </div>

      <div className="bulk-assignment-row">
        <div className="form-field bulk-assignment-field">
          <label htmlFor="bulk-assign-customer">Cliente (opcional)</label>
          <select
            id="bulk-assign-customer"
            className="login-input"
            value={selectedCustomerId}
            onChange={handleCustomerChange}
            disabled={isAssignmentRunning || customersLoading}
          >
            <option value="">{customersLoading ? "Cargando clientes..." : "Sin asignar"}</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="form-field bulk-assignment-field">
          <label htmlFor="bulk-assign-database">Database</label>
          <select
            id="bulk-assign-database"
            className="login-input"
            value={customerDatabaseId ? String(customerDatabaseId) : ""}
            onChange={handleDatabaseChange}
            disabled={isAssignmentRunning || !selectedCustomerId || databases.length === 0}
          >
            <option value="">
              {!selectedCustomerId
                ? "Selecciona cliente primero"
                : databases.length === 0
                  ? "Cliente sin databases"
                  : "Selecciona database"}
            </option>
            {databases.map((db) => (
              <option key={db.id} value={db.id}>
                {db.database_name} ({db.connection_type})
              </option>
            ))}
          </select>
        </div>
      </div>
      {assignmentLabel ? (
        <p className="support-copy bulk-assignment-status">{assignmentLabel}</p>
      ) : null}

      <div className="bulk-runner-toolbar">
        <div className="form-field bulk-delay-field">
          <label htmlFor="bulk-delay-ms">Delay entre consultas (ms)</label>
          <input
            id="bulk-delay-ms"
            className="login-input"
            type="number"
            min={500}
            max={5000}
            step={100}
            value={delayMs}
            onChange={(event) => setDelayMs(event.target.value)}
            disabled={status === "running"}
          />
        </div>

        <div className="actions-row">
          <button type="button" onClick={handlePrimaryAction}>
            {primaryLabel}
          </button>
          {showCancel ? (
            <button type="button" className="button-secondary" onClick={handleCancel}>
              Cancelar
            </button>
          ) : null}
        </div>
      </div>

      <div className="bulk-lookup-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={total || 0} aria-valuenow={processed}>
        <div className="bulk-lookup-progress-fill" style={{ width: `${percent}%` }} />
      </div>

      <div className="bulk-runner-meta">
        <div className="bulk-current-item">
          Procesando {processed} de {total}
          {currentIdentifier ? ` — ${currentIdentifier}` : ""}
        </div>
        <div className="support-copy">
          OK: {counts.ok} · Parcial: {counts.partial} · No encontrado: {counts.notFound} · Error: {counts.error}
        </div>
      </div>

      <div className="bulk-runner-meta bulk-runner-times">
        <div className="support-copy">Tiempo transcurrido: {formatDuration(elapsedMs)}</div>
        <div className="support-copy">Tiempo estimado restante: {formatDuration(estimatedRemainingMs)}</div>
      </div>

      {recentItems.length > 0 ? (
        <div className="bulk-live-log">
          {recentItems.map((item) => (
            <div key={`${item.identifier}-${item.rowNumber}`} className={`bulk-upload-list-item ${getRowClass(item.status)}`}>
              <span>{item.identifier}</span>
              <div className="bulk-log-item-meta">
                <span className={`status ${getStatusClass(item.status)}`}>{item.status}</span>
                <small>Fila {item.rowNumber}</small>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}
