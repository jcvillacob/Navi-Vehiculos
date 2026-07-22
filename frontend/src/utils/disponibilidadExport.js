import { formatMonthLabel, sanitizeFileName } from "./rendimientosExport";

const COLORS = {
  dark: "354550",
  red: "EE2E2F",
  border: "C3CAC8",
  band: "F4F5F7",
  text: "363534",
  muted: "667781",
  good: "2F8C2F",
  goodBg: "EAF5EA",
  warning: "B87900",
  warningBg: "FFF4D6",
  critical: "C52B2B",
  criticalBg: "FCEBEC",
  noData: "6F808A",
  noDataBg: "EDF1F3",
};

const border = {
  top: { style: "thin", color: { rgb: COLORS.border } },
  bottom: { style: "thin", color: { rgb: COLORS.border } },
  left: { style: "thin", color: { rgb: COLORS.border } },
  right: { style: "thin", color: { rgb: COLORS.border } },
};

const styles = {
  title: {
    font: { bold: true, color: { rgb: "FFFFFF" }, sz: 16, name: "Calibri" },
    fill: { fgColor: { rgb: COLORS.dark } },
    alignment: { horizontal: "left", vertical: "center" },
  },
  subtitle: {
    font: { color: { rgb: COLORS.muted }, sz: 10, italic: true, name: "Calibri" },
    alignment: { horizontal: "left", vertical: "center" },
  },
  section: {
    font: { bold: true, color: { rgb: "FFFFFF" }, sz: 11, name: "Calibri" },
    fill: { fgColor: { rgb: COLORS.red } },
    alignment: { horizontal: "left", vertical: "center" },
    border,
  },
  header: {
    font: { bold: true, color: { rgb: "FFFFFF" }, sz: 10, name: "Calibri" },
    fill: { fgColor: { rgb: COLORS.dark } },
    alignment: { horizontal: "center", vertical: "center", wrapText: true },
    border,
  },
  label: {
    font: { bold: true, color: { rgb: COLORS.dark }, sz: 10, name: "Calibri" },
    fill: { fgColor: { rgb: COLORS.band } },
    alignment: { horizontal: "left", vertical: "center", wrapText: true },
    border,
  },
  cell: {
    font: { color: { rgb: COLORS.text }, sz: 10, name: "Calibri" },
    alignment: { horizontal: "center", vertical: "center" },
    border,
  },
  cellLeft: {
    font: { color: { rgb: COLORS.text }, sz: 10, name: "Calibri" },
    alignment: { horizontal: "left", vertical: "center" },
    border,
  },
};

function banded(style, band) {
  return band ? { ...style, fill: { fgColor: { rgb: COLORS.band } } } : style;
}

function statusName(value) {
  if (value === "good") return "Óptima";
  if (value === "warning") return "Advertencia";
  if (value === "critical") return "Crítica";
  if (value === "no_orders") return "Sin órdenes";
  return "Sin datos";
}

function calculationStatusName(value) {
  if (value === "no_orders") return "Sin órdenes";
  if (value === "not_in_cloudfleet") return "No en CloudFleet";
  if (value === "error") return "Error de cálculo";
  return "Con órdenes";
}

function statusStyle(status, base = styles.cell) {
  const map = {
    good: [COLORS.good, COLORS.goodBg],
    warning: [COLORS.warning, COLORS.warningBg],
    critical: [COLORS.critical, COLORS.criticalBg],
    no_data: [COLORS.noData, COLORS.noDataBg],
  };
  const [color, background] = map[status] || map.no_data;
  return {
    ...base,
    font: { ...base.font, bold: true, color: { rgb: color } },
    fill: { fgColor: { rgb: background } },
  };
}

function safeNum(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function percentValue(value) {
  const number = safeNum(value);
  return number === null ? null : number / 100;
}

function monthDate(value) {
  if (!/^\d{4}-\d{2}$/.test(value || "")) return null;
  const [year, month] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, 1));
}

function setCell(XLSX, sheet, row, column, value, options = {}) {
  const address = XLSX.utils.encode_cell({ r: row, c: column });
  const isDate = value instanceof Date;
  const isNumber = typeof value === "number" && Number.isFinite(value);
  const cell = {
    v: value ?? "",
    t: isDate ? "d" : isNumber ? "n" : "s",
    s: options.style || styles.cell,
  };
  if (options.format) cell.z = options.format;
  sheet[address] = cell;
  return address;
}

function mergeTitle(XLSX, sheet, title, subtitle, lastColumn) {
  setCell(XLSX, sheet, 0, 0, title, { style: styles.title });
  setCell(XLSX, sheet, 1, 0, subtitle, { style: styles.subtitle });
  sheet["!merges"] = [
    { s: { r: 0, c: 0 }, e: { r: 0, c: lastColumn } },
    { s: { r: 1, c: 0 }, e: { r: 1, c: lastColumn } },
  ];
  sheet["!rows"] = [{ hpt: 28 }, { hpt: 20 }];
}

function setSection(XLSX, sheet, row, label, lastColumn) {
  for (let column = 0; column <= lastColumn; column += 1) {
    setCell(XLSX, sheet, row, column, column === 0 ? label : "", { style: styles.section });
  }
  sheet["!merges"].push({ s: { r: row, c: 0 }, e: { r: row, c: lastColumn } });
}

function setHeaders(XLSX, sheet, row, headers) {
  headers.forEach((header, column) => setCell(XLSX, sheet, row, column, header, { style: styles.header }));
  sheet["!rows"][row] = { hpt: 26 };
}

function finalizeSheet(XLSX, sheet, lastRow, lastColumn, widths, freezeRow = null) {
  sheet["!ref"] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: Math.max(lastRow, 1), c: lastColumn } });
  sheet["!cols"] = widths.map((wch) => ({ wch }));
  if (freezeRow !== null) sheet["!freeze"] = { xSplit: 0, ySplit: freezeRow };
  sheet["!pageSetup"] = { orientation: "landscape", fitToWidth: 1, fitToHeight: 0 };
  sheet["!margins"] = { left: 0.3, right: 0.3, top: 0.5, bottom: 0.5, header: 0.2, footer: 0.2 };
}

function buildSummarySheet(XLSX, { month, monthFrom, monthTo, overview, coverage, filters, exportedAt }) {
  const sheet = {};
  const overall = overview?.overall || {};
  const fleets = Array.isArray(overview?.fleets) ? overview.fleets : [];
  const coverageSummary = coverage?.summary || {};
  const subtitle = `Generado ${exportedAt.toLocaleString("es-CO")} · Valores numéricos listos para fórmulas y tablas dinámicas`;
  mergeTitle(XLSX, sheet, "Reporte de disponibilidad", subtitle, 7);

  setSection(XLSX, sheet, 3, "Filtros aplicados", 7);
  const filterRows = [
    ["Rango consultado", `${formatMonthLabel(monthFrom)} a ${formatMonthLabel(monthTo)}`],
    ["Mes activo", formatMonthLabel(month)],
    ["Flota", filters.fleetName || "Todas las flotas"],
    ["Búsqueda de placa", filters.plateSearch ? `${filters.plateSearch} (aplica al ranking)` : "Todas"],
    ["Orden del ranking", filters.sortLabel || "Orden predeterminado"],
    ["Vehículos sin órdenes", filters.includeNoOrders ? "Incluidos" : "Excluidos del ranking"],
    ["Estado de disponibilidad", filters.availabilityStatusLabel || "Todos"],
    ["Categorías", "Flota Administrada y Experiencia Superior"],
  ];
  filterRows.forEach(([label, value], index) => {
    const row = 4 + index;
    setCell(XLSX, sheet, row, 0, label, { style: styles.label });
    setCell(XLSX, sheet, row, 1, value, { style: styles.cellLeft });
  });

  const kpiStart = 13;
  setSection(XLSX, sheet, kpiStart, "Indicadores del mes activo", 7);
  const kpis = [
    ["Disponibilidad", percentValue(overall.availability_pct), "0.0%"],
    ["MTTR", safeNum(overall.mttr_hours), '0.0 "h"'],
    ["Horas no disponibles", safeNum(overall.h_no_disp), '#,##0.0 "h"'],
    ["Vehículos", safeNum(overall.vehicle_count) ?? 0, "#,##0"],
    ["Flotas", safeNum(overall.fleet_count) ?? 0, "#,##0"],
    ["Flotas críticas", safeNum(overall.critical_fleets) ?? 0, "#,##0"],
    ["Sin datos", (overall.status_breakdown?.not_in_cloudfleet || 0) + (overall.status_breakdown?.error || 0), "#,##0"],
    ["Cobertura CloudFleet", percentValue(coverageSummary.coverage_pct), "0.0%"],
  ];
  kpis.forEach(([label, value, format], index) => {
    const row = kpiStart + 1 + index;
    const columnOffset = index >= 4 ? 4 : 0;
    const localRow = index >= 4 ? row - 4 : row;
    setCell(XLSX, sheet, localRow, columnOffset, label, { style: styles.label });
    setCell(XLSX, sheet, localRow, columnOffset + 1, value, { style: styles.cell, format });
  });

  const fleetHeaderRow = 24;
  setSection(XLSX, sheet, fleetHeaderRow - 1, "Disponibilidad por flota", 7);
  const fleetHeaders = ["Flota", "Vehículos", "Disponibilidad", "Estado", "Horas totales", "Horas no disp.", "MTTR", "Órdenes cerradas"];
  setHeaders(XLSX, sheet, fleetHeaderRow, fleetHeaders);
  fleets.forEach((fleet, index) => {
    const row = fleetHeaderRow + 1 + index;
    const base = banded(styles.cell, index % 2 === 1);
    const left = banded(styles.cellLeft, index % 2 === 1);
    const values = [
      [fleet.customer_name || "", left],
      [safeNum(fleet.vehicle_count) ?? 0, base, "#,##0"],
      [percentValue(fleet.availability_pct), base, "0.0%"],
      [statusName(fleet.status), statusStyle(fleet.status, base)],
      [safeNum(fleet.h_total), base, '#,##0.0 "h"'],
      [safeNum(fleet.h_no_disp), base, '#,##0.0 "h"'],
      [safeNum(fleet.mttr_hours), base, '0.0 "h"'],
      [safeNum(fleet.orders_closed) ?? 0, base, "#,##0"],
    ];
    values.forEach(([value, style, format], column) => setCell(XLSX, sheet, row, column, value, { style, format }));
  });
  if (fleets.length) {
    sheet["!autofilter"] = { ref: `A${fleetHeaderRow + 1}:H${fleetHeaderRow + fleets.length + 1}` };
  }
  finalizeSheet(XLSX, sheet, fleetHeaderRow + Math.max(fleets.length, 1), 7, [30, 16, 17, 16, 17, 17, 14, 18], 2);
  return sheet;
}

function buildRankingSheet(XLSX, { month, ranking, filters, exportedAt }) {
  const rows = Array.isArray(ranking) ? ranking : [];
  const sheet = {};
  mergeTitle(
    XLSX,
    sheet,
    `Ranking de vehículos · ${formatMonthLabel(month)}`,
    `${rows.length} resultados · ${filters.fleetName || "Todas las flotas"} · Generado ${exportedAt.toLocaleString("es-CO")}`,
    10,
  );
  const headerRow = 3;
  const headers = ["#", "Placa", "Flota", "Disponibilidad", "Escala", "Condición", "Horas no disp.", "Horas totales", "MTTR", "Órdenes", "Órdenes cerradas"];
  setHeaders(XLSX, sheet, headerRow, headers);
  rows.forEach((vehicle, index) => {
    const row = headerRow + 1 + index;
    const base = banded(styles.cell, index % 2 === 1);
    const left = banded(styles.cellLeft, index % 2 === 1);
    const values = [
      [index + 1, base, "#,##0"],
      [vehicle.plate || "", left],
      [vehicle.customer_name || "", left],
      [percentValue(vehicle.availability_pct), base, "0.0%"],
      [statusName(vehicle.status), statusStyle(vehicle.status, base)],
      [calculationStatusName(vehicle.calculation_status), base],
      [safeNum(vehicle.h_no_disp), base, '#,##0.0 "h"'],
      [safeNum(vehicle.h_total), base, '#,##0.0 "h"'],
      [safeNum(vehicle.mttr_hours), base, '0.0 "h"'],
      [safeNum(vehicle.orders_considered) ?? 0, base, "#,##0"],
      [safeNum(vehicle.orders_closed) ?? 0, base, "#,##0"],
    ];
    values.forEach(([value, style, format], column) => setCell(XLSX, sheet, row, column, value, { style, format }));
  });
  sheet["!autofilter"] = { ref: `A${headerRow + 1}:K${headerRow + rows.length + 1}` };
  finalizeSheet(XLSX, sheet, headerRow + Math.max(rows.length, 1), 10, [7, 15, 28, 17, 16, 16, 17, 17, 14, 12, 18], headerRow + 1);
  return sheet;
}

function buildTrendSheet(XLSX, { month, trend, filters, exportedAt }) {
  const sheet = {};
  const labels = Array.isArray(trend?.labels) ? trend.labels : [];
  const percentages = Array.isArray(trend?.availability_pct) ? trend.availability_pct : [];
  mergeTitle(
    XLSX,
    sheet,
    "Tendencia de disponibilidad",
    `${filters.fleetName || "Todas las flotas"} · Generado ${exportedAt.toLocaleString("es-CO")}`,
    3,
  );
  const headerRow = 3;
  setHeaders(XLSX, sheet, headerRow, ["Mes", "Disponibilidad", "Estado", "Mes activo"]);
  labels.forEach((label, index) => {
    const row = headerRow + 1 + index;
    const pct = safeNum(percentages[index]);
    const status = pct === null ? "no_data" : pct >= 97 ? "good" : pct >= 96 ? "warning" : "critical";
    const base = banded(styles.cell, index % 2 === 1);
    setCell(XLSX, sheet, row, 0, monthDate(label), { style: base, format: "mmm yyyy" });
    setCell(XLSX, sheet, row, 1, pct === null ? null : pct / 100, { style: base, format: "0.0%" });
    setCell(XLSX, sheet, row, 2, statusName(status), { style: statusStyle(status, base) });
    setCell(XLSX, sheet, row, 3, label === month ? "Sí" : "", {
      style: label === month ? styles.section : base,
    });
  });
  sheet["!autofilter"] = { ref: `A${headerRow + 1}:D${headerRow + labels.length + 1}` };
  finalizeSheet(XLSX, sheet, headerRow + Math.max(labels.length, 1), 3, [18, 20, 18, 14], headerRow + 1);
  return sheet;
}

function buildCoverageSheet(XLSX, { month, coverage, filters, exportedAt }) {
  const sheet = {};
  const summary = coverage?.summary || {};
  const fleets = Array.isArray(coverage?.fleets) ? coverage.fleets : [];
  const plates = Array.isArray(coverage?.uncovered_plates) ? coverage.uncovered_plates : [];
  mergeTitle(
    XLSX,
    sheet,
    `Cobertura CloudFleet · ${formatMonthLabel(month)}`,
    `${filters.fleetName || "Todas las flotas"} · Generado ${exportedAt.toLocaleString("es-CO")}`,
    6,
  );
  setSection(XLSX, sheet, 3, "Resumen", 6);
  const summaryRows = [
    ["Total de placas", safeNum(summary.total) ?? 0, "#,##0"],
    ["Con cobertura", safeNum(summary.covered) ?? 0, "#,##0"],
    ["Sin cobertura", safeNum(summary.uncovered) ?? 0, "#,##0"],
    ["Con error", safeNum(summary.error) ?? 0, "#,##0"],
    ["Cobertura", percentValue(summary.coverage_pct), "0.0%"],
  ];
  summaryRows.forEach(([label, value, format], index) => {
    const row = 4 + index;
    setCell(XLSX, sheet, row, 0, label, { style: styles.label });
    setCell(XLSX, sheet, row, 1, value, { style: styles.cell, format });
  });

  const fleetHeaderRow = 11;
  setSection(XLSX, sheet, fleetHeaderRow - 1, "Cobertura por flota", 6);
  setHeaders(XLSX, sheet, fleetHeaderRow, ["Flota", "Placas", "Con cobertura", "No en CloudFleet", "Errores", "Cobertura", "Estado"]);
  fleets.forEach((fleet, index) => {
    const row = fleetHeaderRow + 1 + index;
    const base = banded(styles.cell, index % 2 === 1);
    const status = safeNum(fleet.coverage_pct) === null ? "no_data" : fleet.coverage_pct >= 97 ? "good" : fleet.coverage_pct >= 96 ? "warning" : "critical";
    const values = [
      [fleet.customer_name || "", banded(styles.cellLeft, index % 2 === 1)],
      [safeNum(fleet.total) ?? 0, base, "#,##0"],
      [safeNum(fleet.covered) ?? 0, base, "#,##0"],
      [safeNum(fleet.uncovered) ?? 0, base, "#,##0"],
      [safeNum(fleet.error) ?? 0, base, "#,##0"],
      [percentValue(fleet.coverage_pct), base, "0.0%"],
      [statusName(status), statusStyle(status, base)],
    ];
    values.forEach(([value, style, format], column) => setCell(XLSX, sheet, row, column, value, { style, format }));
  });

  const plateHeaderRow = fleetHeaderRow + Math.max(fleets.length, 1) + 3;
  setSection(XLSX, sheet, plateHeaderRow - 1, "Placas sin cobertura", 6);
  setHeaders(XLSX, sheet, plateHeaderRow, ["Placa", "Flota"]);
  plates.forEach((plate, index) => {
    const row = plateHeaderRow + 1 + index;
    setCell(XLSX, sheet, row, 0, plate.plate || "", { style: banded(styles.cellLeft, index % 2 === 1) });
    setCell(XLSX, sheet, row, 1, plate.customer_name || "", { style: banded(styles.cellLeft, index % 2 === 1) });
  });
  sheet["!autofilter"] = { ref: `A${plateHeaderRow + 1}:B${plateHeaderRow + plates.length + 1}` };
  finalizeSheet(XLSX, sheet, plateHeaderRow + Math.max(plates.length, 1), 6, [28, 18, 18, 20, 14, 18, 16], 2);
  return sheet;
}

export function buildDisponibilidadWorkbook(XLSX, {
  month,
  monthFrom,
  monthTo,
  overview,
  ranking,
  trend,
  coverage,
  filters = {},
}) {
  const workbook = XLSX.utils.book_new();
  const exportedAt = new Date();
  const context = { month, monthFrom, monthTo, overview, ranking, trend, coverage, filters, exportedAt };

  XLSX.utils.book_append_sheet(workbook, buildSummarySheet(XLSX, context), "Resumen");
  XLSX.utils.book_append_sheet(workbook, buildRankingSheet(XLSX, context), "Ranking");
  XLSX.utils.book_append_sheet(workbook, buildTrendSheet(XLSX, context), "Tendencia");
  XLSX.utils.book_append_sheet(workbook, buildCoverageSheet(XLSX, context), "Cobertura");

  workbook.Props = {
    Title: `Disponibilidad ${formatMonthLabel(month)}`,
    Subject: "Disponibilidad de flotas administradas y experiencia superior",
    Author: "Navi Vehículos",
    CreatedDate: exportedAt,
  };

  const fleetSuffix = filters.fleetName && filters.fleetName !== "Todas las flotas"
    ? `_${sanitizeFileName(filters.fleetName)}`
    : "";
  const fileName = `Disponibilidad_${monthFrom}_a_${monthTo}_activo_${month}${fleetSuffix}.xlsx`;
  return { workbook, fileName, rowCount: Array.isArray(ranking) ? ranking.length : 0 };
}

export async function exportDisponibilidadExcel(options) {
  const XLSXmod = await import("xlsx-js-style");
  const XLSX = XLSXmod.default || XLSXmod;
  const result = buildDisponibilidadWorkbook(XLSX, options);
  const { workbook, fileName } = result;
  XLSX.writeFile(workbook, fileName, { bookType: "xlsx", cellStyles: true, cellDates: true });
  return result;
}
