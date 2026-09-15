import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import Can from "../components/Can";
import CpkCalcModal from "../components/CpkCalcModal";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import {
  deleteCpkCphReport,
  fetchMonthlyPerformance,
  fetchCpkCphReport,
  listCpkCphReports,
  listCustomers,
  markCpkCphReportSent,
  patchCpkCphReportRow,
  previewCpkCphReport,
  saveCpkCphReport
} from "../api/vehicleApi";
import { formatMonthLabel, getPreviousMonth, sanitizeFileName } from "../utils/rendimientosExport";
import { normalizePlate, parseCutoffRows, parseNumber } from "../utils/cpkCutoffs";

const SYSTEM_CUSTOMER = "__navitrans_system__";
const ACTIVE_REPORT_STORAGE_KEY = "navi.cpk-cph.active-report-id";

function readStoredActiveReport() {
  try {
    const stored = window.localStorage.getItem(ACTIVE_REPORT_STORAGE_KEY);
    if (!stored) return null;
    try {
      const parsed = JSON.parse(stored);
      if (parsed && parsed.id != null) return parsed;
    } catch {
      // Compatibilidad con la versión que guardaba solo el id.
    }
    return { id: stored, month: null };
  } catch {
    return null;
  }
}

function storeActiveReport(report) {
  try {
    window.localStorage.setItem(
      ACTIVE_REPORT_STORAGE_KEY,
      JSON.stringify({ id: String(report.id), month: report.period_month || null })
    );
  } catch {
    // El reporte sigue seleccionado en memoria si el navegador bloquea storage.
  }
}

function clearStoredActiveReportId() {
  try {
    window.localStorage.removeItem(ACTIVE_REPORT_STORAGE_KEY);
  } catch {
    // El reporte sigue funcionando en memoria si el navegador bloquea storage.
  }
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
  if (status === "saved") return "Guardado";
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

function ActionIcon({ name }) {
  const common = {
    className: "cpk-cph-action-icon",
    viewBox: "0 0 24 24",
    width: "18",
    height: "18",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
  };
  if (name === "excel") {
    return (
      <svg {...common}>
        <path d="M4 3h10l5 5v13H4z" />
        <path d="M14 3v5h5M8 12l4 5m0-5-4 5" />
      </svg>
    );
  }
  if (name === "delete") {
    return (
      <svg {...common}>
        <path d="M4 7h16M10 11v6m4-6v6M9 7V4h6v3m-9 0 1 13h8l1-13" />
      </svg>
    );
  }
  if (name === "send") {
    return (
      <svg {...common}>
        <path d="m22 2-7 20-4-9-9-4Z" />
        <path d="M22 2 11 13" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M5 4h11l3 3v13H5zM8 4v6h8V4M8 20v-6h8v6" />
    </svg>
  );
}

function computeRowDiff(row) {
  const vocacional = Boolean(row.vocacional);
  const kmClient = parseNumber(row.km_client);
  const kmAdjustment = parseNumber(row.km_adjustment) ?? 0;
  const hourAdjustment = parseNumber(row.hour_adjustment) ?? 0;
  const kmsRaw = parseNumber(row.kms_ecm_geotab);
  const hoursRaw = parseNumber(row.hours_ecm);
  const regressionCount = Math.max(0, parseNumber(row.geotab_regression_count) ?? 0);
  const regressionTotalKm = Math.max(0, parseNumber(row.geotab_regression_total_km) ?? 0);
  const regressionTotalHours = Math.max(0, parseNumber(row.geotab_regression_total_hours) ?? 0);
  const suggestedAdjustment = Math.max(
    0,
    parseNumber(row.suggested_adjustment) ?? (vocacional ? regressionTotalHours : regressionTotalKm)
  );
  const explicitKmsApproved = parseNumber(row.kms_ecm_approved);
  const explicitHoursApproved = parseNumber(row.hours_ecm_approved);
  const kmsApproved = explicitKmsApproved ?? (kmsRaw !== null ? kmsRaw + kmAdjustment : null);
  const hoursApproved = explicitHoursApproved ?? (hoursRaw !== null ? hoursRaw + hourAdjustment : null);
  const kmsGps = parseNumber(row.kms_gps);
  const hoursGps = parseNumber(row.hours_gps);
  const kmReference = kmClient !== null ? kmClient : kmsGps;
  const kmDiff = kmsApproved !== null && kmReference !== null ? kmsApproved - kmReference : null;
  const hourDiff = hoursApproved !== null && hoursGps !== null ? hoursApproved - hoursGps : null;
  const kmDiffPct = kmDiff !== null && kmReference ? (kmDiff / kmReference) * 100 : null;
  const hourDiffPct = hourDiff !== null && hoursGps ? (hourDiff / hoursGps) * 100 : null;
  return {
    ...row,
    vocacional,
    km_client: kmClient,
    km_adjustment: kmAdjustment,
    hour_adjustment: hourAdjustment,
    kms_ecm_approved: kmsApproved,
    hours_ecm_approved: hoursApproved,
    kms_ecm_geotab: kmsRaw,
    kms_gps: kmsGps,
    hours_ecm: hoursRaw,
    hours_gps: hoursGps,
    fuel_gallons: parseNumber(row.fuel_gallons),
    geotab_regression_count: regressionCount,
    geotab_regression_total_km: regressionTotalKm,
    geotab_regression_total_hours: regressionTotalHours,
    suggested_adjustment: suggestedAdjustment,
    km_difference: kmDiff,
    km_difference_pct: kmDiffPct,
    hour_difference: hourDiff,
    hour_difference_pct: hourDiffPct,
    display_diff_pct: vocacional ? hourDiffPct : kmDiffPct
  };
}

const GEOTAB_OVERRIDE_MARKER = "Retroceso Geotab aceptado manualmente";

function hasOverrideMarker(row) {
  return (Array.isArray(row.warnings) ? row.warnings : [])
    .some((warning) => String(warning).startsWith(GEOTAB_OVERRIDE_MARKER));
}

function isRegressionOverridden(row) {
  return Boolean(row.regression_override) || hasOverrideMarker(row);
}

// Espeja app/services/cpk_cph.py: solo bloquea el medidor que rige el ajuste
// (km en comercial, horas en vocacional); el otro queda como advertencia.
function getGeotabValidation(row) {
  const isGeotab = String(row.source_provider || "").trim().toLowerCase() === "geotab";
  const relevantDifferencePct = row.vocacional ? row.hour_difference_pct : row.km_difference_pct;
  const needsReview = isGeotab && Math.abs(Number(relevantDifferencePct || 0)) > 5;
  if (!needsReview) return { needsReview: false, blocksSave: false, messages: [], reasons: [] };

  const ownLabel = row.vocacional ? "horómetro" : "odómetro";
  const unit = row.vocacional ? "h" : "km";
  const kmRegression = row.odo_start !== null
    && row.odo_start !== undefined
    && row.odo_end !== null
    && row.odo_end !== undefined
    && Number(row.odo_end) < Number(row.odo_start);
  const hourRegression = row.horo_start !== null
    && row.horo_start !== undefined
    && row.horo_end !== null
    && row.horo_end !== undefined
    && Number(row.horo_end) < Number(row.horo_start);
  const ownTotal = Number(row.vocacional ? row.geotab_regression_total_hours : row.geotab_regression_total_km) || 0;
  const sequenceRegressions = [];
  const otherSequence = [];
  for (const warning of Array.isArray(row.warnings) ? row.warnings : []) {
    const text = String(warning);
    if (!(text.startsWith("Retroceso Geotab detectado") && text.includes("[acumulado]"))) continue;
    if (text.includes(`(${ownLabel})`)) sequenceRegressions.push(text);
    else if (text.includes("(odómetro)") || text.includes("(horómetro)")) otherSequence.push(text);
    else if (ownTotal > 0) sequenceRegressions.push(text);
    else otherSequence.push(text);
  }
  const messages = ["Revisión Geotab: la diferencia relevante supera 5%; se validaron los acumulados de km y horas."];
  const endpointRegressions = [];
  const otherEndpoint = [];
  if (kmRegression) {
    const message = `odómetro retrocede de ${formatNumber(row.odo_start)} a ${formatNumber(row.odo_end)} km`;
    (row.vocacional ? otherEndpoint : endpointRegressions).push(message);
  }
  if (hourRegression) {
    const message = `horómetro retrocede de ${formatNumber(row.horo_start, 1)} a ${formatNumber(row.horo_end, 1)} h`;
    (row.vocacional ? endpointRegressions : otherEndpoint).push(message);
  }
  if (otherEndpoint.length || otherSequence.length) {
    messages.push(`Aviso Geotab: hay retrocesos en el otro medidor (${otherEndpoint.length + otherSequence.length}); no afectan el ajuste de ${row.vocacional ? "horas" : "kilómetros"} de esta fila.`);
  }
  const appliedAdjustment = Math.abs(Number(row.vocacional ? row.hour_adjustment : row.km_adjustment) || 0);
  const sequenceAdjustmentMissing = sequenceRegressions.length > 0
    && (!(row.suggested_adjustment > 0) || appliedAdjustment + 0.0001 < row.suggested_adjustment);
  const regressions = sequenceRegressions.length && !sequenceAdjustmentMissing ? [] : [...endpointRegressions];
  if (sequenceAdjustmentMissing) {
    regressions.push(`${sequenceRegressions.length} retroceso(s) de ${ownLabel} sin cubrir: sugerido ${formatNumber(row.suggested_adjustment, row.vocacional ? 1 : 0)} ${unit}, aplicado ${formatNumber(appliedAdjustment, row.vocacional ? 1 : 0)} ${unit}`);
  }
  if (sequenceRegressions.length && !sequenceAdjustmentMissing) {
    messages.push("El ajuste sugerido cubre los retrocesos Geotab detectados.");
  }
  if (!regressions.length) return { needsReview: true, blocksSave: false, messages, reasons: [] };

  const note = String(row.correction_note || "").trim();
  if (isRegressionOverridden(row)) {
    if (note) {
      messages.push(`${GEOTAB_OVERRIDE_MARKER}: ${regressions.join("; ")}. Nota: ${note}`);
      return { needsReview: true, blocksSave: false, messages, reasons: [] };
    }
    regressions.push("forzar el guardado exige nota de corrección");
  }
  messages.push(`Inconsistencia Geotab: ${regressions.join("; ")}. No se permite guardar el ajuste.`);
  return { needsReview: true, blocksSave: true, messages, reasons: regressions };
}

function rowsForApi(rows) {
  return rows.map(computeRowDiff);
}

function mapMonthlyRowToCpkRow(row, index) {
  const vocacional = Boolean(row.vocacional);
  const kmsRaw = parseNumber(row.kms_ecm);
  const hoursRaw = parseNumber(row.hours_ecm);
  const regressionCount = Math.max(0, parseNumber(row.geotab_regression_count) ?? 0);
  const regressionTotalKm = Math.max(0, parseNumber(row.geotab_regression_total_km) ?? 0);
  const regressionTotalHours = Math.max(0, parseNumber(row.geotab_regression_total_hours) ?? 0);
  const suggestedAdjustment = vocacional ? regressionTotalHours : regressionTotalKm;
  const kmAdjustment = vocacional ? 0 : suggestedAdjustment;
  const hourAdjustment = vocacional ? suggestedAdjustment : 0;
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
    vocacional,
    km_client: null,
    odo_start: parseNumber(row.odo_start),
    odo_end: parseNumber(row.odo_end),
    horo_start: parseNumber(row.horo_start),
    horo_end: parseNumber(row.horo_end),
    kms_ecm_geotab: kmsRaw,
    kms_gps: parseNumber(row.kms_gps),
    hours_ecm: hoursRaw,
    hours_gps: parseNumber(row.hours_gps),
    fuel_gallons: parseNumber(row.fuel_gallons),
    geotab_regression_count: regressionCount,
    geotab_regression_total_km: regressionTotalKm,
    geotab_regression_total_hours: regressionTotalHours,
    suggested_adjustment: suggestedAdjustment,
    km_adjustment: kmAdjustment,
    hour_adjustment: hourAdjustment,
    kms_ecm_approved: kmsRaw !== null ? kmsRaw + kmAdjustment : null,
    hours_ecm_approved: hoursRaw !== null ? hoursRaw + hourAdjustment : null,
    calculation_status: ["calculated", "partial"].includes(row.calculation_status) ? "valid" : (row.calculation_status || "pending"),
    warnings: Array.isArray(row.warnings) ? row.warnings : [],
    correction_note: suggestedAdjustment > 0
      ? `Ajuste sugerido automáticamente por ${regressionCount} retroceso(s) Geotab detectado(s).`
      : ""
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
      const overrideSuggested = parseNumber(overrideRow.suggested_adjustment) ?? 0;
      const existingKmAdjustment = parseNumber(existingRow.km_adjustment) ?? 0;
      const existingHourAdjustment = parseNumber(existingRow.hour_adjustment) ?? 0;
      const kmAdjustment = existingKmAdjustment || (!existingRow.vocacional ? overrideSuggested : 0);
      const hourAdjustment = existingHourAdjustment || (existingRow.vocacional ? overrideSuggested : 0);
      const overrideKms = parseNumber(overrideRow.kms_ecm_geotab);
      const overrideHours = parseNumber(overrideRow.hours_ecm);
      merged[existingIndex] = computeRowDiff({
        ...existingRow,
        ...overrideRow,
        vocacional: existingRow.vocacional,
        km_adjustment: kmAdjustment,
        hour_adjustment: hourAdjustment,
        correction_note: existingRow.correction_note || overrideRow.correction_note || "",
        kms_ecm_approved: overrideKms !== null ? overrideKms + kmAdjustment : overrideRow.kms_ecm_approved,
        hours_ecm_approved: overrideHours !== null ? overrideHours + hourAdjustment : overrideRow.hours_ecm_approved
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
  const [month, setMonth] = useState(
    () => readStoredActiveReport()?.month || getPreviousMonth()
  );
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState("");
  const [reports, setReports] = useState([]);
  const [sentFilter, setSentFilter] = useState("all");
  const [activeReport, setActiveReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [updatingSent, setUpdatingSent] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [confirmCalc, setConfirmCalc] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [calcModalOpen, setCalcModalOpen] = useState(false);
  const [calcClients, setCalcClients] = useState([]);
  const [monthRows, setMonthRows] = useState([]);
  const [reportsLoaded, setReportsLoaded] = useState(false);
  const restoreAttemptedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows]
          .filter((customer) => String(customer.name) !== SYSTEM_CUSTOMER)
          .filter((customer) => customer.category === "Flota Administrada")
          .sort((a, b) => String(a.name).localeCompare(String(b.name), "es"));
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

  const loadReports = useCallback(async () => {
    if (!month) return [];
    const rows = await listCpkCphReports({ month, customer_id: null });
    setReports(rows);
    setReportsLoaded(true);
    return rows;
  }, [month]);

  useEffect(() => {
    loadReports().catch((err) => pushToast("error", err instanceof Error ? err.message : "No fue posible cargar CPK/CPH"));
  }, [loadReports, pushToast]);

  const visibleRows = activeReport?.rows || [];

  const visibleReports = useMemo(() => {
    if (sentFilter === "sent") return reports.filter((report) => report.sent_to_commercial);
    if (sentFilter === "pending") return reports.filter((report) => !report.sent_to_commercial);
    return reports;
  }, [reports, sentFilter]);

  const cutoffRowCount = useMemo(
    () => visibleRows.filter((row) => row.cutoff_start_at && row.cutoff_end_at).length,
    [visibleRows]
  );

  const openReport = useCallback(async (reportId) => {
    setLoading(true);
    try {
      const detail = await fetchCpkCphReport(reportId);
      setActiveReport(detail);
      storeActiveReport(detail);
      setCustomerId(String(detail.customer_id));
      setMonth(detail.period_month);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible abrir el reporte");
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  useEffect(() => {
    if (!reportsLoaded || restoreAttemptedRef.current) return;
    restoreAttemptedRef.current = true;

    const storedReport = readStoredActiveReport();
    if (!storedReport?.id) return;

    const report = reports.find(
      (candidate) => String(candidate.id) === String(storedReport.id)
    );
    if (!report) {
      clearStoredActiveReportId();
      return;
    }

    openReport(report.id);
  }, [openReport, reports, reportsLoaded]);

  const openCalcModal = useCallback(async (currentReports) => {
    setLoading(true);
    try {
      const response = await fetchMonthlyPerformance({ month_from: month, month_to: month });
      const allRows = Array.isArray(response?.rows) ? response.rows : [];
      setMonthRows(allRows);
      const reportedIds = new Set((currentReports || reports).map((report) => report.customer_id));
      const managedFleetIds = new Set(customers.map((customer) => customer.id));
      const byCustomer = new Map();
      for (const row of allRows) {
        if (!row || row.customer_id == null) continue;
        if (String(row.client_name) === SYSTEM_CUSTOMER) continue;
        if (!managedFleetIds.has(row.customer_id)) continue;
        if (reportedIds.has(row.customer_id)) continue;
        const entry = byCustomer.get(row.customer_id) || {
          customer_id: row.customer_id,
          name: row.client_name || "Sin cliente",
          vehicles: 0
        };
        entry.vehicles += 1;
        byCustomer.set(row.customer_id, entry);
      }
      const clients = [...byCustomer.values()];
      if (!clients.length) {
        pushToast("error", "No hay rendimientos pendientes de CPK/CPH para este mes.");
        return;
      }
      setCalcClients(clients);
      setCalcModalOpen(true);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar los rendimientos del mes");
    } finally {
      setLoading(false);
    }
  }, [month, reports, customers, pushToast]);

  const handleSearch = async () => {
    setLoading(true);
    try {
      const rows = await loadReports();
      const existing = rows.find((report) => String(report.customer_id) === String(customerId));
      if (existing) {
        await openReport(existing.id);
      } else {
        setActiveReport(null);
        clearStoredActiveReportId();
        setConfirmCalc(true);
      }
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible buscar reportes");
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmCalc = async () => {
    setConfirmCalc(false);
    await openCalcModal(reports);
  };

  const handleCalculate = async ({ selectedCustomerIds, cutoffText, cutoffCustomerIds }) => {
    if (!selectedCustomerIds.length) return;
    const cutoffSet = new Set(cutoffCustomerIds);
    const parsedCutoffs = cutoffText ? parseCutoffRows(cutoffText) : [];
    // Guarda dura: si se pidieron cortes pero no hay con que hacerlos, se aborta
    // en vez de guardar el mes completo sin avisar.
    if (cutoffSet.size && !parsedCutoffs.length) {
      pushToast("error", "No se pudo leer ninguna fila de tanqueo (se necesita placa + ambas fechas). No se calculo nada.");
      return;
    }
    setCalculating(true);
    let calculatedForSelected = null;
    let cutoffRowsApplied = 0;
    let totalRows = 0;
    try {
      for (const id of selectedCustomerIds) {
        const baseRows = monthRows
          .filter((row) => row.customer_id === id)
          .map(mapMonthlyRowToCpkRow);
        let rows = baseRows;
        if (cutoffSet.has(id) && parsedCutoffs.length) {
          const response = await previewCpkCphReport({ month, customer_id: Number(id), rows: parsedCutoffs });
          rows = mergeRowsByPlate(baseRows, response.rows || []);
        }
        if (!rows.length) continue;
        totalRows += rows.length;
        cutoffRowsApplied += rows.filter((row) => row.cutoff_start_at && row.cutoff_end_at).length;
        const detail = await saveCpkCphReport({ month, customer_id: Number(id), rows: rowsForApi(rows) });
        if (String(id) === String(customerId)) calculatedForSelected = detail;
      }
      const refreshed = await loadReports();
      setCalcModalOpen(false);
      const cutoffSummary = cutoffSet.size
        ? ` ${cutoffRowsApplied} de ${totalRows} fila(s) con corte por tanqueo.`
        : ` ${totalRows} fila(s) calculadas con el mes completo.`;
      pushToast(
        cutoffSet.size && cutoffRowsApplied === 0 ? "error" : "success",
        `CPK/CPH calculado para ${selectedCustomerIds.length} cliente(s).${cutoffSummary}`
      );
      if (calculatedForSelected) {
        setActiveReport(calculatedForSelected);
        storeActiveReport(calculatedForSelected);
      } else {
        const first = refreshed.find((report) => selectedCustomerIds.includes(report.customer_id));
        if (first) await openReport(first.id);
      }
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible calcular CPK/CPH");
    } finally {
      setCalculating(false);
    }
  };

  const updateLocalRow = (index, patch) => {
    setActiveReport((current) => ({
      ...current,
      rows: current.rows.map((row, rowIndex) => rowIndex === index ? computeRowDiff({ ...row, ...patch }) : row)
    }));
  };

  const handleSaveRow = async (row) => {
    if (!activeReport || !row.id) return;
    const validation = getGeotabValidation(computeRowDiff(row));
    if (validation.blocksSave) {
      pushToast("error", `Retroceso Geotab en ${row.plate}: ${validation.reasons.join("; ")}. Corrige las lecturas o marca "Guardar de todas formas" con nota.`);
      return;
    }
    try {
      const hasCutoff = Boolean(row.cutoff_start_at && row.cutoff_end_at);
      const payload = {
        km_client: parseNumber(row.km_client),
        km_adjustment: parseNumber(row.km_adjustment),
        hour_adjustment: parseNumber(row.hour_adjustment),
        kms_ecm_approved: parseNumber(row.kms_ecm_approved),
        hours_ecm_approved: parseNumber(row.hours_ecm_approved),
        correction_note: row.correction_note || null,
        regression_override: Boolean(row.regression_override)
      };
      if (hasCutoff) {
        payload.cutoff_start_at = row.cutoff_start_at;
        payload.cutoff_end_at = row.cutoff_end_at;
      }
      const detail = await patchCpkCphReportRow(activeReport.id, row.id, payload);
      setActiveReport(detail);
      storeActiveReport(detail);
      await loadReports();
      pushToast("success", "Fila actualizada.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar la fila");
    }
  };

  const handleDelete = async () => {
    if (!activeReport) return;
    setConfirmDelete(false);
    try {
      await deleteCpkCphReport(activeReport.id);
      setActiveReport(null);
      clearStoredActiveReportId();
      await loadReports();
      pushToast("success", "Reporte CPK/CPH borrado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible borrar el reporte");
    }
  };

  const handleToggleSent = async () => {
    if (!activeReport) return;
    const nextSent = !activeReport.sent_to_commercial;
    setUpdatingSent(true);
    try {
      const detail = await markCpkCphReportSent(activeReport.id, nextSent);
      setActiveReport(detail);
      storeActiveReport(detail);
      await loadReports();
      pushToast("success", nextSent ? "Reporte marcado como enviado al comercial." : "Reporte marcado como pendiente de envío.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar el estado de envío");
    } finally {
      setUpdatingSent(false);
    }
  };

  const handleExport = async () => {
    if (!visibleRows.length) {
      pushToast("error", "No hay filas para exportar.");
      return;
    }
    try {
      const XLSXmod = await import("xlsx-js-style");
      const XLSX = XLSXmod.default || XLSXmod;
      const wb = XLSX.utils.book_new();
      const customerName = selectedCustomer?.name || activeReport?.customer_name || "cliente";

      const BRAND_RED = "EE2E2F";
      const BRAND_BLACK = "363534";
      const BRAND_GRAY = "5A6275";
      const BAND_GRAY = "F4F5F7";
      const BORDER_GRAY = "C3CAC8";

      const titleStyle = {
        font: { bold: true, color: { rgb: "FFFFFF" }, sz: 14, name: "Calibri" },
        fill: { fgColor: { rgb: BRAND_BLACK } },
        alignment: { horizontal: "left", vertical: "center" }
      };
      const sectionHeaderStyle = {
        font: { bold: true, color: { rgb: "FFFFFF" }, sz: 12, name: "Calibri" },
        fill: { fgColor: { rgb: BRAND_RED } },
        alignment: { horizontal: "left", vertical: "center" },
        border: {
          top: { style: "thin", color: { rgb: BORDER_GRAY } },
          bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
          left: { style: "thin", color: { rgb: BORDER_GRAY } },
          right: { style: "thin", color: { rgb: BORDER_GRAY } }
        }
      };
      const headerStyle = {
        font: { bold: true, color: { rgb: "FFFFFF" }, sz: 11, name: "Calibri" },
        fill: { fgColor: { rgb: BRAND_RED } },
        alignment: { horizontal: "center", vertical: "center", wrapText: true },
        border: {
          top: { style: "thin", color: { rgb: BORDER_GRAY } },
          bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
          left: { style: "thin", color: { rgb: BORDER_GRAY } },
          right: { style: "thin", color: { rgb: BORDER_GRAY } }
        }
      };
      const labelStyle = {
        font: { bold: true, color: { rgb: BRAND_BLACK }, sz: 11, name: "Calibri" },
        fill: { fgColor: { rgb: BAND_GRAY } },
        alignment: { horizontal: "left", vertical: "center" },
        border: {
          top: { style: "thin", color: { rgb: BORDER_GRAY } },
          bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
          left: { style: "thin", color: { rgb: BORDER_GRAY } },
          right: { style: "thin", color: { rgb: BORDER_GRAY } }
        }
      };
      const valueStyle = {
        font: { color: { rgb: BRAND_GRAY }, sz: 11, name: "Calibri" },
        alignment: { horizontal: "left", vertical: "center" },
        border: {
          top: { style: "thin", color: { rgb: BORDER_GRAY } },
          bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
          left: { style: "thin", color: { rgb: BORDER_GRAY } },
          right: { style: "thin", color: { rgb: BORDER_GRAY } }
        }
      };
      const cellBase = {
        font: { color: { rgb: BRAND_BLACK }, sz: 11, name: "Calibri" },
        alignment: { horizontal: "center", vertical: "center" },
        border: {
          top: { style: "thin", color: { rgb: BORDER_GRAY } },
          bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
          left: { style: "thin", color: { rgb: BORDER_GRAY } },
          right: { style: "thin", color: { rgb: BORDER_GRAY } }
        }
      };
      const cellStyleBand = {
        ...cellBase,
        fill: { fgColor: { rgb: BAND_GRAY } }
      };
      const diffAlertStyle = {
        ...cellBase,
        font: { bold: true, color: { rgb: BRAND_RED }, sz: 11, name: "Calibri" },
        fill: { fgColor: { rgb: "FCEBEC" } }
      };
      const diffAlertBandStyle = {
        ...cellStyleBand,
        font: { bold: true, color: { rgb: BRAND_RED }, sz: 11, name: "Calibri" }
      };

      const buildSummarySheet = () => {
        const titleRow = [{ A: "Reporte CPK / CPH" }];
        const summary = [
          { Campo: "Mes", Valor: month },
          { Campo: "Cliente", Valor: customerName },
          { Campo: "Estado", Valor: statusLabel(activeReport?.status || "saved") },
          { Campo: "Envío al comercial", Valor: activeReport?.sent_to_commercial ? "Enviado" : "Pendiente de envío" },
          { Campo: "Filas", Valor: visibleRows.length },
          { Campo: "Generado", Valor: new Date().toLocaleString("es-CO") }
        ];
        const sheet = {};
        sheet["A1"] = { v: "Reporte CPK / CPH", t: "s", s: titleStyle };
        sheet["!ref"] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: summary.length + 2, c: 1 } });
        sheet["!cols"] = [{ wch: 22 }, { wch: 38 }];
        sheet["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 1 } }];
        sheet["A3"] = { v: "Campo", t: "s", s: sectionHeaderStyle };
        sheet["B3"] = { v: "Valor", t: "s", s: sectionHeaderStyle };
        summary.forEach((item, idx) => {
          const row = 4 + idx;
          sheet[`A${row}`] = { v: item.Campo, t: "s", s: labelStyle };
          sheet[`B${row}`] = { v: item.Valor, t: typeof item.Valor === "number" ? "n" : "s", s: valueStyle };
        });
        sheet["!rows"] = [{ hpt: 26 }, { hpt: 18 }, { hpt: 22 }];
        return sheet;
      };

      const buildDataSheet = () => {
        const headers = [
          "Placa",
          "Tipo",
          "Mes",
          "Odometro Inicio",
          "Odometro Fin",
          "Kms ECM",
          "Kms Referencia",
          "Horas Inicio",
          "Horas Final",
          "Horas ECM",
          "Horas Referencia",
          "Retrocesos detectados",
          "Total retroceso",
          "Ajuste sugerido",
          "Ajuste",
          "Diferencia %",
          "Tanqueo anterior",
          "Tanqueo actual",
          "Estado",
          "Nota",
          "Warnings"
        ];
        const data = visibleRows.map((rawRow) => {
          const row = computeRowDiff(rawRow);
          const geotabValidation = getGeotabValidation(row);
          return {
            Placa: row.plate,
            Tipo: row.vocacional ? "Vocacional" : "Comercial",
            Mes: month,
            "Odometro Inicio": row.odo_start,
            "Odometro Fin": row.odo_end,
            "Kms ECM": row.kms_ecm_geotab,
            "Kms Referencia": row.km_client !== null ? row.km_client : row.kms_gps,
            "Horas Inicio": row.horo_start,
            "Horas Final": row.horo_end,
            "Horas ECM": row.hours_ecm,
            "Horas Referencia": row.hours_gps,
            "Retrocesos detectados": row.geotab_regression_count,
            "Total retroceso": row.vocacional ? row.geotab_regression_total_hours : row.geotab_regression_total_km,
            "Ajuste sugerido": row.suggested_adjustment,
            Ajuste: row.vocacional ? row.hour_adjustment : row.km_adjustment,
            "Diferencia %": row.display_diff_pct,
            "Tanqueo anterior": row.cutoff_start_at || "",
            "Tanqueo actual": row.cutoff_end_at || "",
            Estado: statusLabel(row.calculation_status),
            Nota: row.correction_note || "",
            Warnings: [...new Set([...(Array.isArray(row.warnings) ? row.warnings : []), ...geotabValidation.messages])].join(" ")
          };
        });

        const aoa = [headers, ...data.map((r) => headers.map((h) => r[h] ?? ""))];
        const sheet = XLSX.utils.aoa_to_sheet(aoa);

        const colWidths = [12, 14, 10, 14, 14, 12, 16, 12, 12, 12, 16, 14, 16, 16, 10, 14, 20, 20, 16, 28, 32];
        sheet["!cols"] = colWidths.map((w) => ({ wch: w }));
        sheet["!freeze"] = { xSplit: 0, ySplit: 1 };
        sheet["!rows"] = [{ hpt: 28 }];

        headers.forEach((_, c) => {
          const addr = XLSX.utils.encode_cell({ r: 0, c });
          if (sheet[addr]) sheet[addr].s = headerStyle;
        });

        data.forEach((row, rIdx) => {
          const excelRow = rIdx + 1;
          const band = excelRow % 2 === 0;
          const isAlert = getGeotabValidation(computeRowDiff(visibleRows[rIdx])).needsReview;
          headers.forEach((_, c) => {
            const addr = XLSX.utils.encode_cell({ r: excelRow, c });
            const cell = sheet[addr];
            if (!cell) return;
            if (isAlert) {
              cell.s = band ? diffAlertBandStyle : diffAlertStyle;
            } else {
              cell.s = band ? cellStyleBand : cellBase;
            }
          });
        });

        return sheet;
      };

      const summarySheet = buildSummarySheet();
      const dataSheet = buildDataSheet();
      XLSX.utils.book_append_sheet(wb, summarySheet, "Resumen");
      XLSX.utils.book_append_sheet(wb, dataSheet, "CPK CPH");
      XLSX.writeFile(wb, `cpk_cph_${sanitizeFileName(customerName)}_${month}.xlsx`, { bookType: "xlsx", cellStyles: true });
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
          <button
            type="button"
            className="button-secondary cpk-cph-icon-button cpk-cph-excel-button"
            onClick={handleExport}
            disabled={!visibleRows.length}
            aria-label="Exportar Excel"
            title="Exportar Excel"
          >
            <ActionIcon name="excel" />
          </button>
          <Can permission={["cpk_cph.manage", "rendimientos.refresh"]}>
            {activeReport ? (
              <>
                <button
                  type="button"
                  className="button-secondary cpk-cph-icon-button cpk-cph-delete-button"
                  onClick={() => setConfirmDelete(true)}
                  aria-label="Borrar este reporte"
                  title="Borrar este reporte"
                >
                  <ActionIcon name="delete" />
                </button>
                <button
                  type="button"
                  className={activeReport.sent_to_commercial
                    ? "button-secondary cpk-cph-icon-button cpk-cph-sent-button is-sent"
                    : "cpk-cph-icon-button cpk-cph-sent-button"}
                  onClick={handleToggleSent}
                  disabled={updatingSent}
                  title={activeReport.sent_to_commercial ? "Desmarcar como enviado" : "Marcar como enviado al comercial"}
                  aria-label={activeReport.sent_to_commercial ? "Desmarcar como enviado" : "Marcar como enviado al comercial"}
                >
                  <ActionIcon name="send" />
                </button>
              </>
            ) : null}
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
          <button type="button" onClick={handleSearch} disabled={loading || !customerId || !month}>
            {loading ? "Buscando..." : "Buscar reportes"}
          </button>

          <div className="form-field">
            <label htmlFor="cpk-sent-filter">Filtro de envío</label>
            <select id="cpk-sent-filter" value={sentFilter} onChange={(event) => setSentFilter(event.target.value)}>
              <option value="all">Todos ({reports.length})</option>
              <option value="pending">Pendientes de envío ({reports.filter((report) => !report.sent_to_commercial).length})</option>
              <option value="sent">Enviados ({reports.filter((report) => report.sent_to_commercial).length})</option>
            </select>
          </div>

          <div className="cpk-cph-report-list">
            {visibleReports.length === 0 ? (
              <p className="support-copy">Sin reportes para el filtro actual.</p>
            ) : visibleReports.map((report) => (
              <button
                key={report.id}
                type="button"
                className={`cpk-cph-report-item${activeReport?.id === report.id ? " is-active" : ""}`}
                onClick={() => openReport(report.id)}
                title={report.sent_to_commercial ? "Enviado al comercial" : "Pendiente de envío"}
              >
                <strong>{report.customer_name}</strong>
                <span>{report.period_month} · {statusLabel(report.status)}</span>
                <small>{report.row_count} fila(s)</small>
                {report.sent_to_commercial ? (
                  <span className="cpk-cph-sent-dot" aria-label="Enviado al comercial" />
                ) : null}
              </button>
            ))}
          </div>
        </aside>

        <main className="cpk-cph-main">
          <section className="card cpk-cph-grid-card">
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  {activeReport
                    ? `${statusLabel(activeReport.status)} · ${formatMonthLabel(activeReport.period_month)}`
                    : "Sin reporte abierto"}
                </span>
                <h3>{activeReport?.customer_name || selectedCustomer?.name || "Cliente"}</h3>
              </div>
              <span className="cpk-cph-count">
                {visibleRows.length} fila(s)
                {visibleRows.length ? ` · ${cutoffRowCount} con corte de tanqueo · ${visibleRows.length - cutoffRowCount} mes completo` : ""}
              </span>
            </div>

            {!activeReport ? (
              <p className="support-copy cpk-cph-empty">
                Elige mes y cliente y presiona "Buscar reportes". Si no existe, se te ofrecera calcularlo.
              </p>
            ) : (
              <div className="cpk-cph-table-shell">
                <table className="cpk-cph-table">
                  <thead>
                    <tr>
                      <th>Placa</th>
                      <th>Origen</th>
                      <th>Odo. inicio</th>
                      <th>Odo. fin</th>
                      <th>Kms ECM</th>
                      <th>Kms referencia</th>
                      <th>Horas inicio</th>
                      <th>Horas final</th>
                      <th>Horas ECM</th>
                      <th>Horas referencia</th>
                      <th title="Cantidad de caídas entre lecturas consecutivas de Geotab">Retrocesos</th>
                      <th title="Suma de todos los retrocesos Geotab">Total retroceso</th>
                      <th title="Valor propuesto automáticamente para corregir todos los retrocesos">Ajuste sugerido</th>
                      <th>Ajuste</th>
                      <th>Dif %</th>
                      <th>Estado</th>
                      <th>Nota</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((rawRow, index) => {
                      const row = computeRowDiff(rawRow);
                      const hasCutoff = Boolean(row.cutoff_start_at && row.cutoff_end_at);
                      const geotabValidation = getGeotabValidation(row);
                      const rowWarnings = [...new Set([...(Array.isArray(row.warnings) ? row.warnings : []), ...geotabValidation.messages])];
                      return (
                        <tr
                          key={row.id || `${row.plate}-${index}`}
                          className={[
                            row.calculation_status === "valid" ? "" : "is-warning",
                            geotabValidation.needsReview ? "is-diff-alert" : "",
                            geotabValidation.blocksSave ? "is-geotab-regression" : ""
                          ].filter(Boolean).join(" ")}
                        >
                          <td><strong>{row.plate}</strong></td>
                          <td>
                            <span className={`cpk-cph-origin ${row.vocacional ? "cpk-cph-origin--cutoff" : ""}`}>
                              {row.vocacional ? "Vocacional" : "Comercial"}
                            </span>
                            <span
                              className={`cpk-cph-origin cpk-cph-origin--window${hasCutoff ? " cpk-cph-origin--window-cut" : ""}`}
                              title={
                                hasCutoff
                                  ? `Ventana de tanqueo ${row.cutoff_start_at} → ${row.cutoff_end_at}`
                                  : "Sin corte por tanqueo: se uso el mes calendario completo"
                              }
                            >
                              {hasCutoff ? "Tanqueo" : "Mes completo"}
                            </span>
                          </td>
                          <td>{formatNumber(row.odo_start, 0)}</td>
                          <td>{formatNumber(row.odo_end, 0)}</td>
                          <td>{formatNumber(row.kms_ecm_geotab, 0)}</td>
                          <td>
                            {hasCutoff ? (
                              <>
                                <EditableCell
                                  type="number"
                                  value={row.km_client ?? ""}
                                  onChange={(value) => updateLocalRow(index, { km_client: value })}
                                />
                                <small className="cpk-cph-cutoff-dates" title="Ventana de tanqueo usada">
                                  {row.cutoff_start_at} → {row.cutoff_end_at}
                                </small>
                              </>
                            ) : formatNumber(row.kms_gps, 0)}
                          </td>
                          <td>{formatNumber(row.horo_start, 1)}</td>
                          <td>{formatNumber(row.horo_end, 1)}</td>
                          <td>{formatNumber(row.hours_ecm, 1)}</td>
                          <td>{formatNumber(row.hours_gps, 1)}</td>
                          <td>{formatNumber(row.geotab_regression_count, 0)}</td>
                          <td>
                            {formatNumber(row.vocacional ? row.geotab_regression_total_hours : row.geotab_regression_total_km, row.vocacional ? 1 : 0)}
                            <small>{row.vocacional ? "h" : "km"}</small>
                          </td>
                          <td>
                            {formatNumber(row.suggested_adjustment, row.vocacional ? 1 : 0)}
                            <small>{row.vocacional ? "h" : "km"}</small>
                          </td>
                          <td>
                            <EditableCell
                              type="number"
                              value={row.vocacional ? (row.hour_adjustment ?? 0) : (row.km_adjustment ?? 0)}
                              onChange={(value) => {
                                const adjustment = parseNumber(value) ?? 0;
                                if (row.vocacional) {
                                  const raw = parseNumber(row.hours_ecm);
                                  updateLocalRow(index, {
                                    hour_adjustment: value,
                                    hours_ecm_approved: raw !== null ? raw + adjustment : row.hours_ecm_approved
                                  });
                                } else {
                                  const raw = parseNumber(row.kms_ecm_geotab);
                                  updateLocalRow(index, {
                                    km_adjustment: value,
                                    kms_ecm_approved: raw !== null ? raw + adjustment : row.kms_ecm_approved
                                  });
                                }
                              }}
                            />
                          </td>
                          <td>{formatNumber(row.display_diff_pct, 2)}</td>
                          <td>
                            <div className="cpk-cph-status-cell">
                              <span
                                className={`cpk-cph-status cpk-cph-status--${row.calculation_status}${rowWarnings.length ? " has-warning" : ""}`}
                                title={rowWarnings.length ? rowWarnings.join(" ") : undefined}
                              >
                                {statusLabel(row.calculation_status)}
                                {rowWarnings.length ? (
                                  <span className="cpk-cph-status-alert" aria-label="Advertencia">!</span>
                                ) : null}
                              </span>
                            </div>
                          </td>
                          <td>
                            <EditableCell
                              value={row.correction_note || ""}
                              onChange={(value) => updateLocalRow(index, { correction_note: value })}
                            />
                            {geotabValidation.blocksSave || isRegressionOverridden(row) ? (
                              <label className="cpk-cph-override">
                                <input
                                  type="checkbox"
                                  checked={isRegressionOverridden(row)}
                                  disabled={hasOverrideMarker(row)}
                                  onChange={(event) => updateLocalRow(index, { regression_override: event.target.checked })}
                                />
                                Guardar de todas formas
                              </label>
                            ) : null}
                          </td>
                          <td>
                            {row.id ? (
                              <Can permission={["cpk_cph.manage", "rendimientos.refresh"]}>
                                <button
                                  type="button"
                                  className="button-secondary cpk-cph-icon-button cpk-cph-row-save-button"
                                  onClick={() => handleSaveRow(row)}
                                  aria-label={`Guardar cambios de ${row.plate}`}
                                  title={`Guardar cambios de ${row.plate}`}
                                >
                                  <ActionIcon name="save" />
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
            )}
          </section>
        </main>
      </section>

      <CpkCalcModal
        open={calcModalOpen}
        month={month}
        clients={calcClients}
        calculating={calculating}
        onClose={() => setCalcModalOpen(false)}
        onCalculate={handleCalculate}
      />

      {confirmCalc ? (
        <div className="modal-overlay" role="presentation" onClick={(event) => {
          if (event.target === event.currentTarget) setConfirmCalc(false);
        }}>
          <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Calcular CPK/CPH">
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Sin datos</span>
                <h3>Calcular CPK/CPH</h3>
              </div>
              <button type="button" className="icon-button modal-close-button" onClick={() => setConfirmCalc(false)}>
                Cerrar
              </button>
            </header>
            <p className="support-copy">
              Aun no hay datos para el mes de {formatMonthLabel(month)}, ¿deseas calcular el reporte de CPK CPH?
            </p>
            <div className="actions-row modal-actions">
              <button type="button" onClick={handleConfirmCalc}>Si, calcular</button>
              <button type="button" className="button-secondary" onClick={() => setConfirmCalc(false)}>Cancelar</button>
            </div>
          </section>
        </div>
      ) : null}

      {confirmDelete && activeReport ? (
        <div className="modal-overlay" role="presentation" onClick={(event) => {
          if (event.target === event.currentTarget) setConfirmDelete(false);
        }}>
          <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Borrar CPK/CPH">
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Accion irreversible</span>
                <h3>Borrar reporte</h3>
              </div>
              <button type="button" className="icon-button modal-close-button" onClick={() => setConfirmDelete(false)}>
                Cerrar
              </button>
            </header>
            <p className="support-copy">
              Se borrara el CPK/CPH de {activeReport.customer_name} para {formatMonthLabel(activeReport.period_month)}. Esta accion no se puede deshacer.
            </p>
            <div className="actions-row modal-actions">
              <button type="button" onClick={handleDelete}>Borrar reporte</button>
              <button type="button" className="button-secondary" onClick={() => setConfirmDelete(false)}>Cancelar</button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
