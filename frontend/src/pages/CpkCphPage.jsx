import { useCallback, useEffect, useMemo, useState } from "react";

import Can from "../components/Can";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import {
  approveCpkCphReport,
  fetchMonthlyPerformance,
  fetchCpkCphReport,
  listCpkCphReports,
  listCustomers,
  patchCpkCphReportRow,
  previewCpkCphReport,
  reopenCpkCphReport,
  saveCpkCphReport
} from "../api/vehicleApi";
import { getPreviousMonth, sanitizeFileName } from "../utils/rendimientosExport";

function normalizeHeader(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
}

function normalizePlate(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
}

function parseNumber(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const normalized = raw.includes(",")
    ? raw.replace(/\./g, "").replace(",", ".")
    : raw.replace(/,/g, "");
  const n = Number(normalized);
  return Number.isFinite(n) ? n : null;
}

function splitLine(line) {
  if (line.includes("\t")) return line.split("\t");
  return line.split(",").map((cell) => cell.trim());
}

function parseClipboard(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return [];

  const matrix = lines.map(splitLine);
  const headers = matrix[0].map(normalizeHeader);
  const indexOf = (aliases) => headers.findIndex((header) => aliases.includes(header));
  let plateIdx = indexOf(["placa", "dispositivo", "vehiculo", "vehiculo placa"]);
  let startIdx = indexOf(["tanqueo anterior", "fecha anterior", "inicio", "fecha inicio"]);
  let endIdx = indexOf(["tanqueo actual", "fecha actual", "fin", "fecha fin"]);
  let kmIdx = indexOf(["km cliente", "kms cliente", "kilometraje cliente", "kilometraje reportado", "km reportado"]);
  const hasHeader = plateIdx >= 0 && startIdx >= 0 && endIdx >= 0;
  const dataRows = hasHeader ? matrix.slice(1) : matrix;
  if (!hasHeader) {
    plateIdx = 0;
    startIdx = 1;
    endIdx = 2;
    kmIdx = 3;
  }

  return dataRows.map((cells) => ({
    plate: normalizePlate(cells[plateIdx]),
    cutoff_start_at: String(cells[startIdx] || "").trim(),
    cutoff_end_at: String(cells[endIdx] || "").trim(),
    km_client: parseNumber(cells[kmIdx])
  }));
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return n.toLocaleString("es-CO", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits
  });
}

function statusLabel(status) {
  if (status === "draft") return "Borrador";
  if (status === "approved") return "Aprobado";
  if (status === "reopened") return "Reabierto";
  if (status === "valid") return "Valida";
  if (status === "duplicate") return "Duplicada";
  if (status === "invalid_date") return "Fecha invalida";
  if (status === "invalid_range") return "Rango invalido";
  if (status === "not_found") return "No encontrada";
  if (status === "client_not_selected") return "Otro cliente";
  if (status === "not_geotab") return "No Geotab";
  if (status === "error") return "Error";
  return status || "Pendiente";
}

function rowsForApi(rows) {
  return rows.map(computeRowDiff);
}

function computeRowDiff(row) {
  const kmClient = parseNumber(row.km_client);
  const kmAdjustment = parseNumber(row.km_adjustment) ?? 0;
  const hourAdjustment = parseNumber(row.hour_adjustment) ?? 0;
  const kmsRaw = parseNumber(row.kms_ecm_geotab);
  const hoursRaw = parseNumber(row.hours_ecm);
  const explicitKmsApproved = parseNumber(row.kms_ecm_approved);
  const explicitHoursApproved = parseNumber(row.hours_ecm_approved);
  const kmsApproved = explicitKmsApproved ?? (kmsRaw !== null ? kmsRaw + kmAdjustment : null);
  const hoursApproved = explicitHoursApproved ?? (hoursRaw !== null ? hoursRaw + hourAdjustment : null);
  const kmsGps = parseNumber(row.kms_gps);
  const comparisonKm = kmClient !== null ? kmClient : kmsGps;
  const diff = kmsApproved !== null && comparisonKm !== null ? kmsApproved - comparisonKm : null;
  return {
    ...row,
    km_client: kmClient,
    km_adjustment: kmAdjustment,
    hour_adjustment: hourAdjustment,
    kms_ecm_approved: kmsApproved,
    hours_ecm_approved: hoursApproved,
    kms_ecm_geotab: kmsRaw,
    kms_gps: kmsGps,
    hours_ecm: hoursRaw,
    hours_gps: parseNumber(row.hours_gps),
    fuel_gallons: parseNumber(row.fuel_gallons),
    km_difference: diff,
    km_difference_pct: diff !== null && comparisonKm ? diff / comparisonKm * 100 : null,
  };
}

function mapMonthlyRowToCpkRow(row, index) {
  const kmsApproved = parseNumber(row.kms_ecm);
  return computeRowDiff({
    row_number: index + 1,
    plate: normalizePlate(row.plate),
    cutoff_start_at: "",
    cutoff_end_at: "",
    cutoff_start_utc: null,
    cutoff_end_utc: null,
    client_name: row.client_name,
    database_name: row.database_name,
    source_provider: row.source_provider,
    provider_vehicle_id: row.provider_vehicle_id,
    km_client: null,
    odo_start: parseNumber(row.odo_start),
    odo_end: parseNumber(row.odo_end),
    horo_start: parseNumber(row.horo_start),
    horo_end: parseNumber(row.horo_end),
    kms_ecm_geotab: kmsApproved,
    kms_gps: parseNumber(row.kms_gps),
    hours_ecm: parseNumber(row.hours_ecm),
    hours_gps: parseNumber(row.hours_gps),
    fuel_gallons: parseNumber(row.fuel_gallons),
    km_adjustment: 0,
    hour_adjustment: 0,
    kms_ecm_approved: kmsApproved,
    hours_ecm_approved: parseNumber(row.hours_ecm),
    calculation_status: ["calculated", "partial"].includes(row.calculation_status) ? "valid" : (row.calculation_status || "pending"),
    warnings: Array.isArray(row.warnings) ? row.warnings : [],
    correction_note: ""
  });
}

function mergeRowsByPlate(baseRows, overrideRows) {
  const byPlate = new Map(baseRows.map((row) => [normalizePlate(row.plate), row]));
  const merged = [...baseRows];
  for (const overrideRow of overrideRows) {
    const plate = normalizePlate(overrideRow.plate);
    const existingIndex = merged.findIndex((row) => normalizePlate(row.plate) === plate);
    if (existingIndex >= 0) {
      const existingRow = merged[existingIndex];
      const kmAdjustment = parseNumber(existingRow.km_adjustment) ?? 0;
      const hourAdjustment = parseNumber(existingRow.hour_adjustment) ?? 0;
      const overrideKms = parseNumber(overrideRow.kms_ecm_geotab);
      const overrideHours = parseNumber(overrideRow.hours_ecm);
      merged[existingIndex] = computeRowDiff({
        ...existingRow,
        ...overrideRow,
        id: existingRow.id,
        km_adjustment: kmAdjustment,
        hour_adjustment: hourAdjustment,
        kms_ecm_approved: overrideKms !== null ? overrideKms + kmAdjustment : overrideRow.kms_ecm_approved,
        hours_ecm_approved: overrideHours !== null ? overrideHours + hourAdjustment : overrideRow.hours_ecm_approved,
      });
    } else if (!byPlate.has(plate)) {
      merged.push(computeRowDiff(overrideRow));
    }
  }
  return merged;
}

function EditableCell({ value, disabled, type = "text", onChange }) {
  return (
    <input
      className="cpk-cph-cell-input"
      type={type}
      value={value ?? ""}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export default function CpkCphPage() {
  const { toasts, pushToast } = useToasts();
  const [month, setMonth] = useState(getPreviousMonth());
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState("");
  const [reports, setReports] = useState([]);
  const [activeReport, setActiveReport] = useState(null);
  const [pasteText, setPasteText] = useState("");
  const [previewRows, setPreviewRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [calculatingBase, setCalculatingBase] = useState(false);
  const [useCutoffs, setUseCutoffs] = useState(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(null);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows].sort((a, b) => String(a.name).localeCompare(String(b.name), "es"));
        setCustomers(sorted);
        if (!customerId && sorted.length) setCustomerId(String(sorted[0].id));
      })
      .catch((err) => pushToast("error", err instanceof Error ? err.message : "No fue posible cargar clientes"));
    return () => { cancelled = true; };
  }, [customerId, pushToast]);

  const selectedCustomer = useMemo(
    () => customers.find((customer) => String(customer.id) === String(customerId)) || null,
    [customers, customerId]
  );

  const reloadReports = useCallback(async () => {
    if (!month) return;
    setLoading(true);
    try {
      const rows = await listCpkCphReports({ month, customer_id: customerId || null });
      setReports(rows);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar CPK/CPH");
    } finally {
      setLoading(false);
    }
  }, [customerId, month, pushToast]);

  useEffect(() => {
    reloadReports().catch(() => {});
  }, [reloadReports]);

  const visibleRows = activeReport?.rows || previewRows;
  const isPersistedDraft = activeReport && activeReport.status !== "approved";
  const canApprove = Boolean(
    activeReport &&
    activeReport.status !== "approved" &&
    visibleRows.length > 0 &&
    visibleRows.every((row) => row.calculation_status === "valid")
  );

  const handleReportSelect = async (reportId) => {
    setLoading(true);
    try {
      const detail = await fetchCpkCphReport(reportId);
      setActiveReport(detail);
      setPreviewRows([]);
      setPasteText("");
      setCustomerId(String(detail.customer_id));
      setMonth(detail.period_month);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible abrir el reporte");
    } finally {
      setLoading(false);
    }
  };

  const loadMonthlyBaseRows = useCallback(async () => {
    if (!customerId || !month) {
      pushToast("error", "Selecciona mes y cliente.");
      return [];
    }
    const response = await fetchMonthlyPerformance({
      month_from: month,
      month_to: month,
      customer_id: Number(customerId)
    });
    const allRows = Array.isArray(response?.rows) ? response.rows : [];
    const flotaRows = allRows.filter((row) => (row.category || "").trim() === "Flota Administrada");
    const rows = flotaRows.length ? flotaRows : allRows;
    return rows.map(mapMonthlyRowToCpkRow);
  }, [customerId, month, pushToast]);

  const buildCpkRows = async () => {
    const baseRows = await loadMonthlyBaseRows();
    if (!useCutoffs || !pasteText.trim()) return baseRows;
    const parsed = parseClipboard(pasteText).filter((row) => row.plate);
    if (!parsed.length) return baseRows;
    const response = await previewCpkCphReport({
      month,
      customer_id: Number(customerId),
      rows: parsed
    });
    return mergeRowsByPlate(baseRows, response.rows || []);
  };

  const runCpkCalculation = async () => {
    setCalculatingBase(true);
    try {
      const rows = await buildCpkRows();
      setActiveReport(null);
      setPreviewRows(rows);
      if (rows.length) {
        pushToast("success", `CPK/CPH calculado: ${rows.length} fila(s).`);
      } else {
        pushToast("error", "No hay rendimientos mensuales para ese cliente y mes.");
      }
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible calcular desde rendimientos");
    } finally {
      setCalculatingBase(false);
    }
  };

  const handleCalculateCpkCph = () => {
    const existing = reports.find((report) => String(report.customer_id) === String(customerId) && report.period_month === month);
    if (existing) {
      setConfirmOverwrite(existing);
      return;
    }
    runCpkCalculation();
  };

  const handlePreview = async () => {
    const parsed = parseClipboard(pasteText).filter((row) => row.plate);
    if (!customerId) {
      pushToast("error", "Selecciona un cliente.");
      return;
    }
    if (!parsed.length) {
      pushToast("error", "Pega al menos una fila con placa, fechas y km cliente.");
      return;
    }
    setPreviewing(true);
    try {
      const response = await previewCpkCphReport({
        month,
        customer_id: Number(customerId),
        rows: parsed
      });
      const overrideRows = response.rows || [];
      const baseRows = visibleRows.length ? visibleRows : await loadMonthlyBaseRows();
      const mergedRows = mergeRowsByPlate(baseRows, overrideRows);
      setPreviewRows(mergedRows);
      setActiveReport(null);
      pushToast("success", `Tanqueos aplicados: ${overrideRows.length} fila(s). Total reporte: ${mergedRows.length}.`);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible previsualizar");
    } finally {
      setPreviewing(false);
    }
  };

  const updateLocalRow = (index, patch) => {
    if (activeReport) {
      setActiveReport((current) => ({
        ...current,
        rows: current.rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row)
      }));
    } else {
      setPreviewRows((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
    }
  };

  const handleSaveDraft = async () => {
    if (!customerId || !month) {
      pushToast("error", "Selecciona mes y cliente.");
      return;
    }
    if (!visibleRows.length) {
      pushToast("error", "No hay filas para guardar.");
      return;
    }
    const missingNotes = visibleRows
      .map(computeRowDiff)
      .filter((row) => (Number(row.km_adjustment || 0) !== 0 || Number(row.hour_adjustment || 0) !== 0) && !String(row.correction_note || "").trim());
    if (missingNotes.length) {
      pushToast("error", "Cada ajuste de km u horas debe tener una nota.");
      return;
    }
    setSaving(true);
    try {
      const detail = await saveCpkCphReport({
        month,
        customer_id: Number(customerId),
        rows: rowsForApi(visibleRows)
      });
      setActiveReport(detail);
      setPreviewRows([]);
      await reloadReports();
      pushToast("success", "Borrador CPK/CPH guardado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible guardar");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveRow = async (row) => {
    if (!activeReport || !row.id) return;
    try {
      const hasCutoff = Boolean(row.cutoff_start_at && row.cutoff_end_at);
      const payload = {
        km_client: parseNumber(row.km_client),
        km_adjustment: parseNumber(row.km_adjustment),
        hour_adjustment: parseNumber(row.hour_adjustment),
        kms_ecm_approved: parseNumber(row.kms_ecm_approved),
        hours_ecm_approved: parseNumber(row.hours_ecm_approved),
        correction_note: row.correction_note || null
      };
      if (hasCutoff) {
        payload.cutoff_start_at = row.cutoff_start_at;
        payload.cutoff_end_at = row.cutoff_end_at;
      }
      const detail = await patchCpkCphReportRow(activeReport.id, row.id, payload);
      setActiveReport(detail);
      await reloadReports();
      pushToast("success", "Fila actualizada.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar la fila");
    }
  };

  const handleApprove = async () => {
    if (!activeReport) return;
    try {
      const detail = await approveCpkCphReport(activeReport.id);
      setActiveReport(detail);
      await reloadReports();
      pushToast("success", `CPK/CPH aprobado como version ${detail.current_version}.`);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible aprobar");
    }
  };

  const handleReopen = async () => {
    if (!activeReport) return;
    try {
      const detail = await reopenCpkCphReport(activeReport.id);
      setActiveReport(detail);
      await reloadReports();
      pushToast("success", "Reporte reabierto para correccion.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible reabrir");
    }
  };

  const handleExport = async () => {
    if (!visibleRows.length) {
      pushToast("error", "No hay filas para exportar.");
      return;
    }
    try {
      const XLSX = await import("xlsx");
      const wb = XLSX.utils.book_new();
      const reportStatus = activeReport?.status || "borrador";
      const summary = [
        { Campo: "Mes", Valor: month },
        { Campo: "Cliente", Valor: selectedCustomer?.name || activeReport?.customer_name || "" },
        { Campo: "Estado", Valor: statusLabel(reportStatus) },
        { Campo: "Version", Valor: activeReport?.current_version || 0 },
        { Campo: "Aprobado por", Valor: activeReport?.approved_by_username || "" },
        { Campo: "Aprobado en", Valor: activeReport?.approved_at || "" }
      ];
      const rows = visibleRows.map((rawRow) => {
        const row = computeRowDiff(rawRow);
        return {
          Placa: row.plate,
          "Origen calculo": row.cutoff_start_at && row.cutoff_end_at ? "Tanqueo" : "Mensual",
          "Km cliente": row.km_client,
          "Kms ECM Geotab": row.kms_ecm_geotab,
          "Ajuste km": row.km_adjustment,
          "Kms ECM aprobado": row.kms_ecm_approved,
          "Diferencia km": row.km_difference,
          "Diferencia %": row.km_difference_pct,
          "Kms GPS": row.kms_gps,
          "Horas ECM Geotab": row.hours_ecm,
          "Ajuste horas": row.hour_adjustment,
          "Horas ECM aprobadas": row.hours_ecm_approved,
          "Horas GPS": row.hours_gps,
          Galones: row.fuel_gallons,
          "Tanqueo anterior": row.cutoff_start_at,
          "Tanqueo actual": row.cutoff_end_at,
          Estado: statusLabel(row.calculation_status),
          Notas: row.correction_note || "",
          Warnings: Array.isArray(row.warnings) ? row.warnings.join(" ") : ""
        };
      });
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(summary), "Resumen");
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows), "CPK CPH");
      const customerName = selectedCustomer?.name || activeReport?.customer_name || "cliente";
      const suffix = reportStatus === "approved" ? `v${activeReport?.current_version || 1}` : "BORRADOR";
      XLSX.writeFile(wb, `cpk_cph_${sanitizeFileName(customerName)}_${month}_${suffix}.xlsx`);
      pushToast("success", "Excel CPK/CPH exportado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible exportar");
    }
  };

  return (
    <section className="panel cpk-cph-page">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Cierre operativo</span>
          <h2>CPK/CPH</h2>
        </div>
        <div className="actions-row">
          <button type="button" className="button-secondary" onClick={handleExport} disabled={!visibleRows.length}>
            Exportar Excel
          </button>
          <Can permission={["cpk_cph.manage", "rendimientos.refresh"]}>
            {activeReport?.status === "approved" ? (
              <button type="button" onClick={handleReopen}>Reabrir</button>
            ) : (
              <button type="button" onClick={handleApprove} disabled={!canApprove}>Aprobar cierre</button>
            )}
          </Can>
        </div>
      </header>

      <ToastStack toasts={toasts} />

      <section className="cpk-cph-layout">
        <aside className="card cpk-cph-sidebar">
          <div className="form-field">
            <label htmlFor="cpk-month">Mes</label>
            <input id="cpk-month" type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
          </div>
          <div className="form-field">
            <label htmlFor="cpk-customer">Cliente</label>
            <select id="cpk-customer" value={customerId} onChange={(event) => setCustomerId(event.target.value)}>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>{customer.name}</option>
              ))}
            </select>
          </div>
          <button type="button" className="button-secondary" onClick={reloadReports} disabled={loading}>
            {loading ? "Cargando..." : "Buscar reportes"}
          </button>
          <button type="button" onClick={handleCalculateCpkCph} disabled={calculatingBase || previewing || !customerId || !month}>
            {calculatingBase ? "Calculando..." : "Calcular CPK/CPH"}
          </button>

          <div className="cpk-cph-report-list">
            {reports.length === 0 ? (
              <p className="support-copy">Sin reportes para el filtro actual.</p>
            ) : reports.map((report) => (
              <button
                key={report.id}
                type="button"
                className={`cpk-cph-report-item${activeReport?.id === report.id ? " is-active" : ""}`}
                onClick={() => handleReportSelect(report.id)}
              >
                <strong>{report.customer_name}</strong>
                <span>{report.period_month} · {statusLabel(report.status)} · v{report.current_version}</span>
                <small>{report.row_count} fila(s)</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="cpk-cph-main">
          <section className="card cpk-cph-paste">
            <div className="section-heading">
              <div>
                <span className="eyebrow">Entrada masiva</span>
                <h3>Tanqueos opcionales</h3>
              </div>
              <div className="actions-row">
                <button type="button" className="button-secondary" onClick={handlePreview} disabled={previewing || !useCutoffs}>
                  {previewing ? "Consultando..." : "Aplicar ajustes"}
                </button>
                <Can permission={["cpk_cph.manage", "rendimientos.refresh"]}>
                  <button type="button" onClick={handleSaveDraft} disabled={saving || !visibleRows.length || activeReport?.status === "approved"}>
                    {saving ? "Guardando..." : "Guardar borrador"}
                  </button>
                </Can>
              </div>
            </div>
            <label className="cpk-cutoff-toggle">
              <input
                type="checkbox"
                checked={useCutoffs}
                onChange={(event) => setUseCutoffs(event.target.checked)}
              />
              <span>Este cliente entrego datos de tanqueo</span>
            </label>
            <textarea
              className="cpk-cph-paste-area"
              value={pasteText}
              onChange={(event) => setPasteText(event.target.value)}
              disabled={!useCutoffs}
              placeholder={"placa\ttanqueo_anterior\ttanqueo_actual\tkm_cliente\nLQK264\t30/05/2026 10:43:00 a m\t30/06/2026 9:02:00 a m\t4120"}
            />
          </section>

          <section className="card cpk-cph-grid-card">
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  {activeReport ? `${statusLabel(activeReport.status)} · version ${activeReport.current_version}` : "Previsualizacion"}
                </span>
                <h3>{selectedCustomer?.name || activeReport?.customer_name || "Cliente"}</h3>
              </div>
              <span className="cpk-cph-count">{visibleRows.length} fila(s)</span>
            </div>

            <div className="cpk-cph-table-shell">
              <table className="cpk-cph-table">
                <thead>
                  <tr>
                    <th>Placa</th>
                    <th>Origen</th>
                    <th>Km referencia</th>
                    <th>Km ECM</th>
                    <th>Ajuste km</th>
                    <th>Km aprobado</th>
                    <th>Dif km</th>
                    <th>Dif %</th>
                    <th>Horas ECM</th>
                    <th>Ajuste horas</th>
                    <th>Horas aprobadas</th>
                    <th>Horas GPS</th>
                    <th>Galones</th>
                    <th>Estado</th>
                    <th>Nota</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.length === 0 ? (
                    <tr>
                      <td colSpan={16} className="cpk-cph-empty">Calcula CPK/CPH, pega tanqueos opcionales o abre un reporte existente.</td>
                    </tr>
                  ) : visibleRows.map((rawRow, index) => {
                    const row = computeRowDiff(rawRow);
                    const locked = activeReport?.status === "approved";
                    const hasCutoff = Boolean(row.cutoff_start_at && row.cutoff_end_at);
                    const needsReview = Math.abs(Number(row.km_difference_pct || 0)) > 5;
                    return (
                      <tr
                        key={row.id || `${row.plate}-${index}`}
                        className={[
                          row.calculation_status === "valid" ? "" : "is-warning",
                          needsReview ? "is-diff-alert" : ""
                        ].filter(Boolean).join(" ")}
                      >
                        <td><strong>{row.plate}</strong></td>
                        <td>
                          <span className={`cpk-cph-origin ${hasCutoff ? "cpk-cph-origin--cutoff" : ""}`}>
                            {hasCutoff ? "Tanqueo" : "Mensual"}
                          </span>
                        </td>
                        <td>
                          {hasCutoff && row.km_client !== null ? (
                            <EditableCell
                              type="number"
                              value={row.km_client ?? ""}
                              disabled={locked}
                              onChange={(value) => updateLocalRow(index, { km_client: value })}
                            />
                          ) : formatNumber(row.kms_gps, 0)}
                        </td>
                        <td>{formatNumber(row.kms_ecm_geotab, 0)}</td>
                        <td>
                          <EditableCell
                            type="number"
                            value={row.km_adjustment ?? 0}
                            disabled={locked}
                            onChange={(value) => {
                              const adjustment = parseNumber(value) ?? 0;
                              const raw = parseNumber(row.kms_ecm_geotab);
                              updateLocalRow(index, {
                                km_adjustment: value,
                                kms_ecm_approved: raw !== null ? raw + adjustment : row.kms_ecm_approved
                              });
                            }}
                          />
                        </td>
                        <td>{formatNumber(row.kms_ecm_approved, 0)}</td>
                        <td>{formatNumber(row.km_difference, 0)}</td>
                        <td>{formatNumber(row.km_difference_pct, 2)}</td>
                        <td>{formatNumber(row.hours_ecm, 1)}</td>
                        <td>
                          <EditableCell
                            type="number"
                            value={row.hour_adjustment ?? 0}
                            disabled={locked}
                            onChange={(value) => {
                              const adjustment = parseNumber(value) ?? 0;
                              const raw = parseNumber(row.hours_ecm);
                              updateLocalRow(index, {
                                hour_adjustment: value,
                                hours_ecm_approved: raw !== null ? raw + adjustment : row.hours_ecm_approved
                              });
                            }}
                          />
                        </td>
                        <td>{formatNumber(row.hours_ecm_approved, 1)}</td>
                        <td>{formatNumber(row.hours_gps, 1)}</td>
                        <td>{formatNumber(row.fuel_gallons, 1)}</td>
                        <td>
                          <span className={`cpk-cph-status cpk-cph-status--${row.calculation_status}`}>
                            {statusLabel(row.calculation_status)}
                          </span>
                          {Array.isArray(row.warnings) && row.warnings.length ? (
                            <small>{row.warnings.join(" ")}</small>
                          ) : null}
                        </td>
                        <td>
                          <EditableCell
                            value={row.correction_note || ""}
                            disabled={locked}
                            onChange={(value) => updateLocalRow(index, { correction_note: value })}
                          />
                        </td>
                        <td>
                          {isPersistedDraft && row.id ? (
                            <Can permission={["cpk_cph.manage", "rendimientos.refresh"]}>
                              <button type="button" className="button-secondary button-sm" onClick={() => handleSaveRow(row)}>
                                Guardar
                              </button>
                            </Can>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </main>
      </section>

      {confirmOverwrite ? (
        <div className="modal-overlay" role="presentation" onClick={(event) => {
          if (event.target === event.currentTarget) setConfirmOverwrite(null);
        }}>
          <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Recalcular CPK/CPH">
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Datos existentes</span>
                <h3>Recalcular CPK/CPH</h3>
              </div>
              <button type="button" className="icon-button modal-close-button" onClick={() => setConfirmOverwrite(null)}>
                Cerrar
              </button>
            </header>
            <p className="support-copy">
              Ya existe un CPK/CPH para {confirmOverwrite.customer_name} en {confirmOverwrite.period_month}. Si recalculas, la tabla activa de ese mes y cliente sera reemplazada por el nuevo calculo.
            </p>
            <div className="actions-row modal-actions">
              <button
                type="button"
                onClick={() => {
                  setConfirmOverwrite(null);
                  runCpkCalculation();
                }}
              >
                Recalcular y reemplazar
              </button>
              <button type="button" className="button-secondary" onClick={() => setConfirmOverwrite(null)}>
                Cancelar
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
