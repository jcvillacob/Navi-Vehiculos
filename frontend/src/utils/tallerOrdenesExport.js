import { sanitizeFileName } from "./rendimientosExport";

const HEADER_FILL = "354550";
const BORDER_GRAY = "C3CAC8";
const BAND_GRAY = "F4F5F7";
const BRAND_RED = "EE2E2F";

const thinBorder = {
  top: { style: "thin", color: { rgb: BORDER_GRAY } },
  bottom: { style: "thin", color: { rgb: BORDER_GRAY } },
  left: { style: "thin", color: { rgb: BORDER_GRAY } },
  right: { style: "thin", color: { rgb: BORDER_GRAY } },
};

const titleStyle = {
  font: { bold: true, color: { rgb: "FFFFFF" }, sz: 14, name: "Calibri" },
  fill: { fgColor: { rgb: HEADER_FILL } },
  alignment: { horizontal: "left", vertical: "center" },
};

const headerStyle = {
  font: { bold: true, color: { rgb: "FFFFFF" }, sz: 11, name: "Calibri" },
  fill: { fgColor: { rgb: HEADER_FILL } },
  alignment: { horizontal: "center", vertical: "center", wrapText: true },
  border: thinBorder,
};

const labelStyle = {
  font: { bold: true, color: { rgb: HEADER_FILL }, sz: 11, name: "Calibri" },
  fill: { fgColor: { rgb: BAND_GRAY } },
  alignment: { horizontal: "left", vertical: "center" },
  border: thinBorder,
};

const valueStyle = {
  font: { color: { rgb: "5A6275" }, sz: 11, name: "Calibri" },
  alignment: { horizontal: "left", vertical: "center" },
  border: thinBorder,
};

const cellBase = {
  font: { color: { rgb: "363534" }, sz: 11, name: "Calibri" },
  alignment: { horizontal: "center", vertical: "center" },
  border: thinBorder,
};

const cellBand = {
  ...cellBase,
  fill: { fgColor: { rgb: BAND_GRAY } },
};

const alertStyle = {
  ...cellBase,
  font: { bold: true, color: { rgb: BRAND_RED }, sz: 11, name: "Calibri" },
  fill: { fgColor: { rgb: "FCEBEC" } },
};

function safeNum(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function fmtInt(value) {
  const n = safeNum(value);
  return n === null ? "—" : Math.round(n).toLocaleString("es-CO");
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString("es-CO", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const INDICATOR_LABEL = {
  on_time: "En tiempo",
  about_to_expire: "Por vencer",
  overdue: "Excedido",
  pending_closure: "Pendiente cierre",
};

function buildSummarySheet(XLSX, generatedAt, summary) {
  const safeSummary = summary || {};
  const rows = [
    ["Generado", fmtDate(generatedAt)],
    ["Total órdenes activas", fmtInt(safeSummary.total_active)],
    ["En tiempo", fmtInt(safeSummary.on_time)],
    ["Por vencer", fmtInt(safeSummary.about_to_expire)],
    ["Excedidas", fmtInt(safeSummary.overdue)],
    ["Pendiente cierre", fmtInt(safeSummary.pending_closure)],
    ["Cierre > 7 días", fmtInt(safeSummary.pending_closure_7d)],
    ["Cierre > 30 días", fmtInt(safeSummary.pending_closure_30d)],
    ["Con etiquetas", fmtInt(safeSummary.con_etiquetas)],
  ];

  const sheet = {};
  sheet["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 1 } }];
  sheet["A1"] = { v: "Órdenes de taller", t: "s", s: titleStyle };

  rows.forEach(([label, value], idx) => {
    const r = idx + 2;
    sheet[`A${r}`] = { v: label, t: "s", s: labelStyle };
    sheet[`B${r}`] = { v: value, t: "s", s: valueStyle };
  });

  sheet["!ref"] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: rows.length + 1, c: 1 } });
  sheet["!cols"] = [{ wch: 30 }, { wch: 22 }];
  sheet["!rows"] = [{ hpt: 28 }];

  return sheet;
}

function buildOrdersSheet(XLSX, orders) {
  const headers = [
    "Orden",
    "Placa",
    "Flota",
    "Tipo",
    "Estado",
    "Indicador",
    "Días transcurridos",
    "Días cierre pendiente",
    "Etiquetas",
  ];

  const rows = (orders || []).map((order) => [
    order.order_number || "—",
    order.plate || "—",
    order.fleet || "Sin flota",
    order.type || "—",
    order.status || "—",
    INDICATOR_LABEL[order.status_indicator] || order.status_indicator || "—",
    order.days_elapsed !== null && order.days_elapsed !== undefined ? order.days_elapsed : "—",
    order.pending_closure_days !== null && order.pending_closure_days !== undefined
      ? order.pending_closure_days
      : "—",
    order.maintenance_labels?.length ? order.maintenance_labels.join(", ") : "—",
  ]);

  const aoa = [headers, ...rows];
  const sheet = XLSX.utils.aoa_to_sheet(aoa);

  headers.forEach((_, c) => {
    const addr = XLSX.utils.encode_cell({ r: 0, c });
    if (sheet[addr]) sheet[addr].s = headerStyle;
  });

  rows.forEach((_, rIdx) => {
    const excelRow = rIdx + 1;
    const band = excelRow % 2 === 0;
    const order = orders[rIdx];
    const isAlert = order?.pending_closure_days !== null && order?.pending_closure_days > 7;
    headers.forEach((_, c) => {
      const addr = XLSX.utils.encode_cell({ r: excelRow, c });
      const cell = sheet[addr];
      if (!cell) return;
      cell.s = isAlert ? alertStyle : band ? cellBand : cellBase;
    });
  });

  sheet["!cols"] = [
    { wch: 16 },
    { wch: 14 },
    { wch: 32 },
    { wch: 18 },
    { wch: 18 },
    { wch: 16 },
    { wch: 18 },
    { wch: 22 },
    { wch: 36 },
  ];
  sheet["!freeze"] = { xSplit: 0, ySplit: 1 };
  sheet["!rows"] = [{ hpt: 28 }];

  return sheet;
}

export async function exportTallerOrdenesExcel({ generatedAt, summary, orders }) {
  const XLSXmod = await import("xlsx-js-style");
  const XLSX = XLSXmod.default || XLSXmod;
  const wb = XLSX.utils.book_new();

  const summarySheet = buildSummarySheet(XLSX, generatedAt, summary);
  XLSX.utils.book_append_sheet(wb, summarySheet, "Resumen");

  const ordersSheet = buildOrdersSheet(XLSX, orders);
  XLSX.utils.book_append_sheet(wb, ordersSheet, "Órdenes activas");

  const today = new Date().toISOString().slice(0, 10);
  const fileName = `Ordenes_Taller_${sanitizeFileName(today)}.xlsx`;
  XLSX.writeFile(wb, fileName);
}
