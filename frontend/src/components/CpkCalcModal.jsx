import { useEffect, useMemo, useState } from "react";

import { formatMonthLabel } from "../utils/rendimientosExport";

export default function CpkCalcModal({
  open,
  month,
  clients,
  calculating,
  onClose,
  onCalculate
}) {
  const [selected, setSelected] = useState(new Set());
  const [useCutoffs, setUseCutoffs] = useState(false);
  const [cutoffClients, setCutoffClients] = useState(new Set());
  const [cutoffText, setCutoffText] = useState("");

  useEffect(() => {
    if (!open) return;
    setSelected(new Set(clients.map((client) => client.customer_id)));
    setUseCutoffs(false);
    setCutoffClients(new Set());
    setCutoffText("");
  }, [open, clients]);

  const sortedClients = useMemo(
    () => [...clients].sort((a, b) => String(a.name).localeCompare(String(b.name), "es")),
    [clients]
  );

  if (!open) return null;

  const toggleClient = (id) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setCutoffClients((current) => {
      const next = new Set(current);
      next.delete(id);
      return next;
    });
  };

  const toggleCutoffClient = (id) => {
    setCutoffClients((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (selected.size === 0) return;
    onCalculate({
      selectedCustomerIds: [...selected],
      cutoffText: useCutoffs ? cutoffText : "",
      cutoffCustomerIds: useCutoffs ? [...cutoffClients] : []
    });
  };

  const selectedList = sortedClients.filter((client) => selected.has(client.customer_id));

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Calcular CPK/CPH">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Cierre operativo</span>
            <h3>Calcular CPK/CPH · {formatMonthLabel(month)}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label>Clientes sin CPK/CPH este mes</label>
            <p className="support-copy">
              Quita los que no quieras calcular todavia. Se creara un reporte por cada cliente marcado.
            </p>
            {sortedClients.length === 0 ? (
              <p className="cpk-empty">Todos los clientes del mes ya tienen CPK/CPH.</p>
            ) : (
              <ul className="cpk-clients-list">
                {sortedClients.map((client) => (
                  <li key={client.customer_id}>
                    <label className="cpk-client-row">
                      <input
                        type="checkbox"
                        checked={selected.has(client.customer_id)}
                        onChange={() => toggleClient(client.customer_id)}
                      />
                      <span className="cpk-client-name">{client.name}</span>
                      <span className="cpk-client-meta">{client.vehicles} vehiculo(s)</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="cpk-cutoff-panel">
            <label className="cpk-cutoff-toggle">
              <input
                type="checkbox"
                checked={useCutoffs}
                onChange={(event) => {
                  setUseCutoffs(event.target.checked);
                  if (!event.target.checked) setCutoffClients(new Set());
                }}
                disabled={selectedList.length === 0}
              />
              <span>Algun cliente entrego datos de tanqueo</span>
            </label>

            {useCutoffs ? (
              <>
                <div className="form-field">
                  <label>Clientes con corte por tanqueo</label>
                  <div className="cpk-cutoff-client-list">
                    {selectedList.map((client) => (
                      <label key={client.customer_id} className="cpk-cutoff-client-option">
                        <input
                          type="checkbox"
                          checked={cutoffClients.has(client.customer_id)}
                          onChange={() => toggleCutoffClient(client.customer_id)}
                        />
                        <span>{client.name}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="form-field">
                  <label htmlFor="cpk-calc-cutoff">Tabla de tanqueos (placa, tanqueo anterior, tanqueo actual, km cliente)</label>
                  <textarea
                    id="cpk-calc-cutoff"
                    className="cpk-cutoff-textarea"
                    value={cutoffText}
                    onChange={(event) => setCutoffText(event.target.value)}
                    placeholder={"placa\ttanqueo_anterior\ttanqueo_actual\tkm_cliente\nLQK264\t30/05/2026 10:43:00 a m\t30/06/2026 9:02:00 a m\t4120"}
                    rows={5}
                  />
                  <small className="form-hint">
                    Las placas se resuelven al cliente correspondiente. Solo se aplican a los clientes marcados arriba.
                  </small>
                </div>
              </>
            ) : null}
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" className="button" disabled={calculating || selected.size === 0}>
              {calculating ? "Calculando..." : `Calcular ${selected.size} cliente(s)`}
            </button>
            <button type="button" className="button-secondary" onClick={onClose} disabled={calculating}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
