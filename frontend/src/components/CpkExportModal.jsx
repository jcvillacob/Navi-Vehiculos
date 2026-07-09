import { useEffect, useMemo, useState } from "react";

import { formatMonthLabel, getPreviousMonth, isValidMonth } from "../utils/rendimientosExport";

const CATEGORY_LABELS = {
  "Flota Administrada": "Flota Administrada",
  "Experiencia Superior": "Experiencia Superior",
  "Ninguna": "Ninguna"
};

export default function CpkExportModal({
  open,
  exporting,
  rows,
  loading,
  loadError,
  defaultMonth,
  lockedClient,
  onClose,
  onSubmit,
  onMonthChange,
  onPreviewCutoffs
}) {
  const [month, setMonth] = useState(defaultMonth || getPreviousMonth());
  const [selectedClients, setSelectedClients] = useState(new Set());
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [useCutoffs, setUseCutoffs] = useState(false);
  const [cutoffClients, setCutoffClients] = useState(new Set());
  const [clipboardText, setClipboardText] = useState("");
  const [parsedCutoffRows, setParsedCutoffRows] = useState([]);
  const [previewRows, setPreviewRows] = useState([]);
  const [approvedCutoffs, setApprovedCutoffs] = useState([]);
  const [cutoffError, setCutoffError] = useState("");
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    if (open) {
      setMonth(defaultMonth || getPreviousMonth());
      setSelectedClients(lockedClient ? new Set([lockedClient]) : new Set());
      setDropdownOpen(false);
      setUseCutoffs(false);
      setCutoffClients(new Set());
      setClipboardText("");
      setParsedCutoffRows([]);
      setPreviewRows([]);
      setApprovedCutoffs([]);
      setCutoffError("");
    }
  }, [open, defaultMonth, lockedClient]);

  useEffect(() => {
    if (!open) return;
    if (typeof onMonthChange === "function") {
      onMonthChange(month);
    }
  }, [month, open, onMonthChange]);

  const normalizeCategory = (value) => {
    if (value === null || value === undefined) return "Ninguna";
    return String(value).trim();
  };

  const isFlotaAdministrada = (value) => {
    const normalized = normalizeCategory(value).toLowerCase();
    return normalized === "flota administrada";
  };

  const { allClientsInMonth, faClients, categoryBreakdown } = useMemo(() => {
    if (!Array.isArray(rows) || !isValidMonth(month)) {
      return { allClientsInMonth: [], faClients: [], categoryBreakdown: {} };
    }
    const faSet = new Set();
    const allMap = new Map();
    const catCounts = {};
    for (const row of rows) {
      if (!row || row.period_month !== month) continue;
      const name = row.client_name || "Sin cliente";
      const cat = normalizeCategory(row.category) || "Ninguna";
      catCounts[cat] = (catCounts[cat] || 0) + 1;
      const isFa = isFlotaAdministrada(cat);
      if (isFa) faSet.add(name);
      const entry = allMap.get(name) || {
        name,
        vehicles: 0,
        faVehicles: 0,
        categories: new Set()
      };
      entry.vehicles += 1;
      entry.categories.add(cat);
      if (isFa) entry.faVehicles += 1;
      allMap.set(name, entry);
    }
    const list = [...allMap.values()]
      .map((entry) => ({
        name: entry.name,
        vehicles: entry.vehicles,
        faVehicles: entry.faVehicles,
        category: entry.faVehicles > 0
          ? "Flota Administrada"
          : [...entry.categories][0] || "Ninguna"
      }))
      .sort((a, b) => a.name.localeCompare(b.name, "es"));
    return { allClientsInMonth: list, faClients: [...faSet], categoryBreakdown: catCounts };
  }, [rows, month]);

  useEffect(() => {
    if (!open) return;
    if (lockedClient) {
      setSelectedClients(new Set([lockedClient]));
      return;
    }
    setSelectedClients(new Set(faClients));
  }, [faClients, lockedClient, open]);

  useEffect(() => {
    if (!open) return;
    setCutoffClients((current) => {
      const selected = [...selectedClients];
      if (!useCutoffs) return new Set();
      if (current.size === 0) return new Set(selected);
      return new Set(selected.filter((name) => current.has(name)));
    });
  }, [open, selectedClients, useCutoffs]);

  if (!open) return null;

  const totalSelected = selectedClients.size;
  const totalFa = faClients.length;
  const allChecked = totalFa > 0 && totalSelected === totalFa;

  const toggleClient = (name) => {
    setSelectedClients((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    resetCutoffApproval();
  };

  const toggleAll = () => {
    if (allChecked) {
      setSelectedClients(new Set());
    } else {
      setSelectedClients(new Set(faClients));
    }
    resetCutoffApproval();
  };

  const normalizeHeader = (value) => {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim()
      .toLowerCase()
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ");
  };

  const normalizePlate = (value) => {
    return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  };

  const normalizeDateText = (value) => {
    return String(value || "")
      .replace(/[\u00a0\u202f]/g, " ")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim()
      .replace(/\s+/g, " ")
      .replace(/\b([ap])\s*\.?\s*m\.?\b/gi, (_, marker) => marker.toUpperCase() === "P" ? "PM" : "AM");
  };

  const toTwentyFourHour = (hour, meridiem) => {
    let value = Number(hour);
    const normalized = String(meridiem || "").toUpperCase();
    if (normalized === "PM" && value < 12) value += 12;
    if (normalized === "AM" && value === 12) value = 0;
    return value;
  };

  const parseDateForCompare = (value) => {
    const raw = normalizeDateText(value);
    if (!raw) return null;
    const normalized = raw.replace(/\//g, "-").replace("T", " ");
    const isoLike = normalized.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(AM|PM))?)?$/i);
    if (isoLike) {
      const [, y, m, d, hh = "0", mm = "0", ss = "0", meridiem = ""] = isoLike;
      const date = new Date(Number(y), Number(m) - 1, Number(d), toTwentyFourHour(hh, meridiem), Number(mm), Number(ss));
      return Number.isNaN(date.getTime()) ? null : date;
    }
    const localLike = normalized.match(/^(\d{1,2})-(\d{1,2})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(AM|PM))?)?$/i);
    if (localLike) {
      const [, d, m, y, hh = "0", mm = "0", ss = "0", meridiem = ""] = localLike;
      const date = new Date(Number(y), Number(m) - 1, Number(d), toTwentyFourHour(hh, meridiem), Number(mm), Number(ss));
      return Number.isNaN(date.getTime()) ? null : date;
    }
    const fallback = new Date(raw);
    return Number.isNaN(fallback.getTime()) ? null : fallback;
  };

  const parseCutoffClipboard = (text) => {
    const lines = String(text || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    if (lines.length === 0) return [];

    const splitLine = (line) => line.includes("\t")
      ? line.split("\t")
      : line.split(",").map((cell) => cell.trim());

    const matrix = lines.map(splitLine);
    const first = matrix[0].map(normalizeHeader);
    const findIndex = (candidates) => first.findIndex((header) => candidates.includes(header));
    let plateIndex = findIndex(["placa", "dispositivo", "vehiculo", "vehiculo placa"]);
    let startIndex = findIndex(["tanqueo anterior", "fecha anterior", "inicio", "tanqueo inicio", "fecha inicio"]);
    let endIndex = findIndex(["tanqueo actual", "fecha actual", "fin", "tanqueo fin", "fecha fin"]);
    const hasHeader = plateIndex >= 0 && startIndex >= 0 && endIndex >= 0;
    const dataRows = hasHeader ? matrix.slice(1) : matrix;
    if (!hasHeader) {
      plateIndex = 0;
      startIndex = 1;
      endIndex = 2;
    }

    const seen = new Set();
    return dataRows.map((cells, index) => {
      const plate = normalizePlate(cells[plateIndex]);
      const cutoffStart = String(cells[startIndex] || "").trim();
      const cutoffEnd = String(cells[endIndex] || "").trim();
      const warnings = [];
      let status = "ready";
      const startDate = parseDateForCompare(cutoffStart);
      const endDate = parseDateForCompare(cutoffEnd);
      if (!plate) warnings.push("Placa vacia.");
      if (!startDate || !endDate) warnings.push("Fecha invalida.");
      if (startDate && endDate && endDate <= startDate) warnings.push("Rango invalido.");
      if (plate && seen.has(plate)) warnings.push("Placa duplicada.");
      if (plate) seen.add(plate);
      if (warnings.length > 0) status = "invalid";
      return {
        rowNumber: index + 1,
        plate,
        cutoff_start_at: cutoffStart,
        cutoff_end_at: cutoffEnd,
        status,
        warnings
      };
    });
  };

  const resetCutoffApproval = () => {
    setPreviewRows([]);
    setApprovedCutoffs([]);
    setCutoffError("");
  };

  const handleClipboardChange = (value) => {
    setClipboardText(value);
    setParsedCutoffRows(parseCutoffClipboard(value));
    resetCutoffApproval();
  };

  const toggleCutoffClient = (name) => {
    setCutoffClients((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    resetCutoffApproval();
  };

  const handleToggleCutoffs = (checked) => {
    setUseCutoffs(checked);
    setCutoffClients(checked ? new Set([...selectedClients]) : new Set());
    resetCutoffApproval();
  };

  const getPreviewStatusLabel = (status) => {
    if (status === "valid") return "Valida";
    if (status === "ready") return "Lista";
    if (status === "invalid") return "Revisar";
    if (status === "invalid_date") return "Fecha invalida";
    if (status === "invalid_range") return "Rango invalido";
    if (status === "not_found") return "Placa no encontrada";
    if (status === "client_not_selected") return "Cliente no seleccionado";
    if (status === "not_geotab") return "No Geotab";
    if (status === "error") return "Error Geotab";
    return status || "Pendiente";
  };

  const handleApproveCutoffs = async () => {
    setCutoffError("");
    setApprovedCutoffs([]);
    setPreviewRows([]);
    if (!useCutoffs) return;
    if (!parsedCutoffRows.length) {
      setCutoffError("Pega al menos una fila con placa, tanqueo anterior y tanqueo actual.");
      return;
    }
    const localInvalid = parsedCutoffRows.filter((row) => row.status !== "ready");
    if (localInvalid.length > 0) {
      setPreviewRows(parsedCutoffRows);
      setCutoffError("Corrige las filas marcadas antes de aprobar.");
      return;
    }
    const selectedCutoffClients = [...cutoffClients];
    if (selectedCutoffClients.length === 0) {
      setCutoffError("Selecciona al menos un cliente para aplicar cortes por tanqueo.");
      return;
    }
    setPreviewing(true);
    try {
      const response = await onPreviewCutoffs({
        month,
        client_names: selectedCutoffClients,
        rows: parsedCutoffRows.map((row) => ({
          plate: row.plate,
          cutoff_start_at: row.cutoff_start_at,
          cutoff_end_at: row.cutoff_end_at
        }))
      });
      const serverRows = Array.isArray(response?.rows) ? response.rows : [];
      setPreviewRows(serverRows);
      const invalidRows = serverRows.filter((row) => row.status !== "valid");
      if (invalidRows.length > 0) {
        setCutoffError("Hay filas que Geotab no pudo aprobar. Revisa la tabla.");
        return;
      }
      setApprovedCutoffs(serverRows);
    } catch (err) {
      setCutoffError(err instanceof Error ? err.message : "No fue posible aprobar los cortes.");
    } finally {
      setPreviewing(false);
    }
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!isValidMonth(month)) return;
    if (totalSelected === 0) return;
    if (useCutoffs && parsedCutoffRows.length > 0 && approvedCutoffs.length === 0) {
      setCutoffError("Aprueba los cortes antes de generar el Excel.");
      return;
    }
    onSubmit({ month, selectedClients: [...selectedClients], approvedCutoffs });
  };

  const summaryLabel = lockedClient
    ? lockedClient
    : allChecked
      ? `Todos (${totalFa})`
      : totalSelected === 0
        ? "Ninguno seleccionado"
        : totalSelected === 1
          ? [...selectedClients][0]
          : `${totalSelected} cliente(s)`;

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="card modal-card"
        role="dialog"
        aria-modal="true"
        aria-label="Exportar CPK/CPH"
      >
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Reporte operativo</span>
            <h3>Exportar CPK/CPH</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="cpk-export-month">Mes del reporte</label>
            <input
              id="cpk-export-month"
              type="month"
              value={month}
              onChange={(event) => {
                setMonth(event.target.value);
                resetCutoffApproval();
              }}
              required
            />
            {loading ? (
              <small className="form-hint">Cargando rendimientos de {formatMonthLabel(month)}...</small>
            ) : loadError ? (
              <small className="form-hint cpk-error">{loadError}</small>
            ) : allClientsInMonth.length === 0 ? (
              <small className="form-hint">
                No hay rendimientos cargados para {formatMonthLabel(month)}. Si el mes no se ha calculado, ejecuta "Consultar" desde el explorador.
              </small>
            ) : (
              <small className="form-hint">
                {totalFa > 0
                  ? `${totalFa} cliente(s) con Flota Administrada en ${formatMonthLabel(month)} (de ${allClientsInMonth.length} totales).`
                  : `Hay ${allClientsInMonth.length} cliente(s) en ${formatMonthLabel(month)} pero ninguno con categoria "Flota Administrada".`}
                {Object.keys(categoryBreakdown).length > 0 ? (
                  <>
                    {" "}Categorias presentes:{" "}
                    {Object.entries(categoryBreakdown)
                      .map(([cat, n]) => `${cat} (${n})`)
                      .join(", ")}
                    .
                  </>
                ) : null}
              </small>
            )}
          </div>

          {lockedClient ? (
            <div className="notice-banner notice-soft">
              Cliente fijo por filtro actual: <strong>{lockedClient}</strong>
            </div>
          ) : (
            <div className="form-field">
              <label>Clientes a incluir</label>
              <details
                className="cpk-dropdown"
                open={dropdownOpen}
                onToggle={(event) => setDropdownOpen(event.currentTarget.open)}
                disabled={loading}
              >
                <summary className="cpk-dropdown-summary">
                  <span className="cpk-dropdown-label">
                    {loading ? "Cargando..." : summaryLabel}
                  </span>
                  <span className="cpk-dropdown-caret" aria-hidden="true">▾</span>
                </summary>
                <div className="cpk-dropdown-panel">
                  <div className="cpk-clients-toolbar">
                    <button
                      type="button"
                      className="button-secondary button-sm"
                      onClick={toggleAll}
                      disabled={totalFa === 0}
                    >
                      {allChecked ? "Desmarcar todos" : "Marcar todos"}
                    </button>
                    <span className="cpk-clients-count">
                      {totalSelected} de {totalFa} (Flota Administrada)
                    </span>
                  </div>
                  {allClientsInMonth.length === 0 ? (
                    <p className="cpk-empty">
                      No hay clientes en este mes dentro del explorador actual.
                    </p>
                  ) : (
                    <ul className="cpk-clients-list">
                      {allClientsInMonth.map((client) => {
                        const isFa = client.category === "Flota Administrada";
                        const checked = isFa && selectedClients.has(client.name);
                        return (
                          <li key={client.name}>
                            <label
                              className={`cpk-client-row${isFa ? "" : " cpk-client-row--muted"}`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={!isFa}
                                onChange={() => toggleClient(client.name)}
                              />
                              <span className="cpk-client-name">{client.name}</span>
                              <span className={`cpk-client-badge cpk-client-badge--${client.category.toLowerCase().replace(/ /g, "-")}`}>
                                {CATEGORY_LABELS[client.category] || client.category}
                              </span>
                              <span className="cpk-client-meta">
                                {isFa
                                  ? `${client.faVehicles} vehiculo(s)`
                                  : `${client.vehicles} vehiculo(s) (no Flota Administrada)`}
                              </span>
                            </label>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              </details>
            </div>
          )}

          <div className="cpk-cutoff-panel">
            <label className="cpk-cutoff-toggle">
              <input
                type="checkbox"
                checked={useCutoffs}
                onChange={(event) => handleToggleCutoffs(event.target.checked)}
                disabled={loading || totalSelected === 0}
              />
              <span>Usar corte por tanqueo para Geotab</span>
            </label>

            {useCutoffs ? (
              <>
                <div className="form-field">
                  <label>Clientes con corte por tanqueo</label>
                  <div className="cpk-cutoff-client-list">
                    {[...selectedClients].sort((a, b) => a.localeCompare(b, "es")).map((name) => (
                      <label key={name} className="cpk-cutoff-client-option">
                        <input
                          type="checkbox"
                          checked={cutoffClients.has(name)}
                          onChange={() => toggleCutoffClient(name)}
                        />
                        <span>{name}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <div className="form-field">
                  <label htmlFor="cpk-cutoff-paste">Tabla de tanqueos</label>
                  <textarea
                    id="cpk-cutoff-paste"
                    className="cpk-cutoff-textarea"
                    value={clipboardText}
                    onChange={(event) => handleClipboardChange(event.target.value)}
                    placeholder={"placa\ttanqueo_anterior\ttanqueo_actual\nABC123\t2026-05-30 08:00\t2026-06-28 17:30"}
                    rows={5}
                  />
                  <small className="form-hint">
                    Pega desde Excel o Sheets con columnas placa, tanqueo anterior y tanqueo actual.
                  </small>
                </div>

                <div className="cpk-cutoff-actions">
                  <button
                    type="button"
                    className="button-secondary button-sm"
                    onClick={handleApproveCutoffs}
                    disabled={previewing || !parsedCutoffRows.length || cutoffClients.size === 0}
                  >
                    {previewing ? "Consultando Geotab..." : "Aprobar cortes"}
                  </button>
                  <span className="cpk-clients-count">
                    {approvedCutoffs.length > 0
                      ? `${approvedCutoffs.length} corte(s) aprobado(s)`
                      : `${parsedCutoffRows.length} fila(s) pegada(s)`}
                  </span>
                </div>

                {cutoffError ? <small className="form-hint cpk-error">{cutoffError}</small> : null}

                {(previewRows.length > 0 || parsedCutoffRows.length > 0) ? (
                  <div className="cpk-cutoff-preview">
                    <table>
                      <thead>
                        <tr>
                          <th>Placa</th>
                          <th>Cliente</th>
                          <th>Tanqueo anterior</th>
                          <th>Tanqueo actual</th>
                          <th>Estado</th>
                          <th>Kms ECM</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(previewRows.length > 0 ? previewRows : parsedCutoffRows).map((row, index) => (
                          <tr key={`${row.plate || "fila"}-${index}`} className={row.status === "valid" || row.status === "ready" ? "" : "is-error"}>
                            <td>{row.plate || "-"}</td>
                            <td>{row.client_name || (row.status === "ready" ? "Se resuelve al aprobar" : "-")}</td>
                            <td>{row.cutoff_start_at || "-"}</td>
                            <td>{row.cutoff_end_at || "-"}</td>
                            <td>
                              <span className={`cpk-cutoff-status cpk-cutoff-status--${row.status || "pending"}`}>
                                {getPreviewStatusLabel(row.status)}
                              </span>
                              {Array.isArray(row.warnings) && row.warnings.length > 0 ? (
                                <small>{row.warnings.join(" ")}</small>
                              ) : null}
                            </td>
                            <td>{row.kms_ecm != null ? Number(row.kms_ecm).toLocaleString("es-CO", { maximumFractionDigits: 0 }) : "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </>
            ) : null}
          </div>

          <div className="actions-row modal-actions">
            <button
              type="submit"
              className="button"
              disabled={exporting || totalSelected === 0 || loading || previewing}
            >
              {exporting ? "Exportando..." : "Generar Excel"}
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={onClose}
              disabled={exporting}
            >
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
