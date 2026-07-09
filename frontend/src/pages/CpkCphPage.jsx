import { useCallback, useEffect, useMemo, useState } from "react";

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
  patchCpkCphReportRow,
  previewCpkCphReport,
  saveCpkCphReport
} from "../api/vehicleApi";
import { formatMonthLabel, getPreviousMonth, sanitizeFileName } from "../utils/rendimientosExport";

const SYSTEM_CUSTOMER = "__navitrans_system__";

function normalizeHeader(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
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

function computeRowDiff(row) {
  const vocacional = Boolean(row.vocacional);
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
    km_difference: kmDiff,
    km_difference_pct: kmDiffPct,
    hour_difference: hourDiff,
    hour_difference_pct: hourDiffPct,
    display_diff_pct: vocacional ? hourDiffPct : kmDiffPct
  };
}

function rowsForApi(rows) {
  return rows.map(computeRowDiff);
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
    vocacional: Boolean(row.vocacional),
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
        vocacional: existingRow.vocacional,
        km_adjustment: kmAdjustment,
        hour_adjustment: hourAdjustment,
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
  const [month, setMonth] = useState(getPreviousMonth());
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState("");
  const [reports, setReports] = useState([]);
  const [activeReport, setActiveReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [confirmCalc, setConfirmCalc] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [calcModalOpen, setCalcModalOpen] = useState(false);
  const [calcClients, setCalcClients] = useState([]);
  const [monthRows, setMonthRows] = useState([]);

  useEffect(() => {
    let cancelled = false;
    listCustomers()
      .then((rows) => {
        if (cancelled) return;
        const sorted = [...rows]
          .filter((customer) => String(customer.name) !== SYSTEM_CUSTOMER)
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
    return rows;
  }, [month]);

  useEffect(() => {
    loadReports().catch((err) => pushToast("error", err instanceof Error ? err.message : "No fue posible cargar CPK/CPH"));
  }, [loadReports, pushToast]);

  const visibleRows = activeReport?.rows || [];

  const openReport = useCallback(async (reportId) => {
    setLoading(true);
    try {
      const detail = await fetchCpkCphReport(reportId);
      setActiveReport(detail);
      setCustomerId(String(detail.customer_id));
      setMonth(detail.period_month);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible abrir el reporte");
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  const openCalcModal = useCallback(async (currentReports) => {
    setLoading(true);
    try {
      const response = await fetchMonthlyPerformance({ month_from: month, month_to: month });
      const allRows = Array.isArray(response?.rows) ? response.rows : [];
      setMonthRows(allRows);
      const reportedIds = new Set((currentReports || reports).map((report) => report.customer_id));
      const byCustomer = new Map();
      for (const row of allRows) {
        if (!row || row.customer_id == null) continue;
        if (String(row.client_name) === SYSTEM_CUSTOMER) continue;
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
  }, [month, reports, pushToast]);

  const handleSearch = async () => {
    setLoading(true);
    try {
      const rows = await loadReports();
      const existing = rows.find((report) => String(report.customer_id) === String(customerId));
      if (existing) {
        await openReport(existing.id);
      } else {
        setActiveReport(null);
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
    setCalculating(true);
    const cutoffSet = new Set(cutoffCustomerIds);
    const parsedCutoffs = cutoffText ? parseClipboard(cutoffText).filter((row) => row.plate) : [];
    let calculatedForSelected = null;
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
        const detail = await saveCpkCphReport({ month, customer_id: Number(id), rows: rowsForApi(rows) });
        if (String(id) === String(customerId)) calculatedForSelected = detail;
      }
      const refreshed = await loadReports();
      setCalcModalOpen(false);
      pushToast("success", `CPK/CPH calculado para ${selectedCustomerIds.length} cliente(s).`);
      if (calculatedForSelected) {
        setActiveReport(calculatedForSelected);
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

  const handleSaveAll = async () => {
    if (!activeReport || !visibleRows.length) {
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
        month: activeReport.period_month,
        customer_id: activeReport.customer_id,
        rows: rowsForApi(visibleRows)
      });
      setActiveReport(detail);
      await loadReports();
      pushToast("success", "CPK/CPH guardado.");
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
      await loadReports();
      pushToast("success", "Reporte CPK/CPH borrado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible borrar el reporte");
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
          { Campo: "Filas", Valor: visibleRows.length },
          { Campo: "Generado", Valor: new Date().toLocaleString("es-CO") }
        ];
        const sheet = {};
        sheet["A1"] = { v: "Reporte CPK / CPH", t: "s", s: titleStyle };
        sheet["!ref"] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: 6, c: 1 } });
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
            Ajuste: row.vocacional ? row.hour_adjustment : row.km_adjustment,
            "Diferencia %": row.display_diff_pct,
            "Tanqueo anterior": row.cutoff_start_at || "",
            "Tanqueo actual": row.cutoff_end_at || "",
            Estado: statusLabel(row.calculation_status),
            Nota: row.correction_note || "",
            Warnings: Array.isArray(row.warnings) ? row.warnings.join(" ") : ""
          };
        });

        const aoa = [headers, ...data.map((r) => headers.map((h) => r[h] ?? ""))];
        const sheet = XLSX.utils.aoa_to_sheet(aoa);

        const colWidths = [12, 14, 10, 14, 14, 12, 16, 12, 12, 12, 16, 10, 14, 20, 20, 16, 28, 32];
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
          const isAlert = Math.abs(Number(row["Diferencia %"] || 0)) > 5;
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
      XLSX.writeFile(wb, `cpk_cph_${sanitizeFileName(customerName)}_${month}.xlsx`);
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
            {activeReport ? (
              <>
                <button type="button" className="button-secondary" onClick={() => setConfirmDelete(true)}>
                  Borrar este reporte
                </button>
                <button type="button" onClick={handleSaveAll} disabled={saving || !visibleRows.length}>
                  {saving ? "Guardando..." : "Guardar"}
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

          <div className="cpk-cph-report-list">
            {reports.length === 0 ? (
              <p className="support-copy">Sin reportes para el filtro actual.</p>
            ) : reports.map((report) => (
              <button
                key={report.id}
                type="button"
                className={`cpk-cph-report-item${activeReport?.id === report.id ? " is-active" : ""}`}
                onClick={() => openReport(report.id)}
              >
                <strong>{report.customer_name}</strong>
                <span>{report.period_month} · {statusLabel(report.status)}</span>
                <small>{report.row_count} fila(s)</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="cpk-cph-main">
          <section className="card cpk-cph-grid-card">
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  {activeReport ? `${statusLabel(activeReport.status)} · ${formatMonthLabel(activeReport.period_month)}` : "Sin reporte abierto"}
                </span>
                <h3>{activeReport?.customer_name || selectedCustomer?.name || "Cliente"}</h3>
              </div>
              <span className="cpk-cph-count">{visibleRows.length} fila(s)</span>
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
                      const needsReview = Math.abs(Number(row.display_diff_pct || 0)) > 5;
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
                            <span className={`cpk-cph-origin ${row.vocacional ? "cpk-cph-origin--cutoff" : ""}`}>
                              {row.vocacional ? "Vocacional" : "Comercial"}
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
                                className={`cpk-cph-status cpk-cph-status--${row.calculation_status}${Array.isArray(row.warnings) && row.warnings.length ? " has-warning" : ""}`}
                                title={Array.isArray(row.warnings) && row.warnings.length ? row.warnings.join(" ") : undefined}
                              >
                                {statusLabel(row.calculation_status)}
                                {Array.isArray(row.warnings) && row.warnings.length ? (
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
                          </td>
                          <td>
                            {row.id ? (
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
