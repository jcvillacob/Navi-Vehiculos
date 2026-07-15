import { formatMonthLabel, sanitizeFileName } from "./rendimientosExport";

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

function fmtPct(value) {
  const n = safeNum(value);
  return n === null ? "—" : `${n.toFixed(1)}%`;
}

function fmtMttr(value) {
  const n = safeNum(value);
  return n === null ? "—" : `${n.toFixed(1)} h`;
}

function fmtHours(value) {
  const n = safeNum(value);
  return n === null ? "—" : `${Math.round(n).toLocaleString("es-CO")} h`;
}

function fmtInt(value) {
  const n = safeNum(value);
  return n === null ? "—" : Math.round(n).toLocaleString("es-CO");
}

function fleetStatus(availabilityPct) {
  const n = safeNum(availabilityPct);
  if (n === null) return "Sin datos";
  if (n < 96) return "Crítica";
  if (n < 97) return "Advertencia";
  return "Óptima";
}

function buildSummarySheet(XLSX, month, overview, coverage) {
  const overall = overview?.overall || {};
  const fleets = Array.isArray(overview?.fleets) ? overview.fleets : [];
  const summary = coverage?.summary || {};

  const kpiRows = [
    ["Disponibilidad global", fmtPct(overall.availability_pct)],
    ["MTTR del mes", fmtMttr(overall.mttr_hours)],
    ["Horas no disponibles", fmtHours(overall.h_no_disp)],
    ["Vehículos cubiertos", fmtInt(overall.vehicle_count)],
    ["Flotas críticas", fmtInt(overall.critical_fleets)],
    ["Sin datos", fmtInt((overall.status_breakdown?.not_in_cloudfleet || 0) + (overall.status_breakdown?.error || 0))],
    ["Cobertura CloudFleet", summary.coverage_pct !== null && summary.coverage_pct !== undefined ? `${Number(summary.coverage_pct).toFixed(1)}%` : "—"],
  ];

  const sheet = {};
  sheet["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 5 } }];
  sheet["A1"] = { v: "Disponibilidad", t: "s", s: titleStyle };
  sheet["A2"] = { v: formatMonthLabel(month), t: "s", s: { ...valueStyle, font: { ...valueStyle.font, bold: true } } };

  let row = 4;
  sheet[`A${row}`] = { v: "KPI", t: "s", s: headerStyle };
  sheet[`B${row}`] = { v: "Valor", t: "s", s: headerStyle };
  row += 1;
  kpiRows.forEach(([label, value], idx) => {
    const r = row + idx;
    sheet[`A${r}`] = { v: label, t: "s", s: labelStyle };
    sheet[`B${r}`] = { v: value, t: "s", s: valueStyle };
  });

  row = row + kpiRows.length + 2;
  const fleetStart = row;
  const fleetHeaders = ["Flota", "Vehículos", "Disponibilidad %", "MTTR", "Estado"];
  fleetHeaders.forEach((h, c) => {
    sheet[XLSX.utils.encode_cell({ r: row - 1, c })] = { v: h, t: "s", s: headerStyle };
  });
  fleets.forEach((f, idx) => {
    const r = row + idx;
    const band = idx % 2 === 1;
    const style = band ? cellBand : cellBase;
    const status = fleetStatus(f.availability_pct);
    const values = [
      f.customer_name || "—",
      safeNum(f.vehicle_count) ?? 0,
      safeNum(f.availability_pct) === null ? "—" : safeNum(f.availability_pct) / 100,
      fmtMttr(f.mttr_hours),
      status,
    ];
    values.forEach((v, c) => {
      const cellStyle = status === "Crítica" && c === 4 ? alertStyle : style;
      sheet[XLSX.utils.encode_cell({ r: r - 1, c })] = {
        v,
        t: typeof v === "number" ? "n" : "s",
        s: cellStyle,
        z: c === 2 && typeof v === "number" ? "0.0%" : undefined,
      };
    });
  });

  sheet["!ref"] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: row + Math.max(fleets.length, 1), c: 4 } });
  sheet["!cols"] = [
    { wch: 36 },
    { wch: 12 },
    { wch: 16 },
    { wch: 12 },
    { wch: 14 },
    { wch: 14 },
  ];
  sheet["!rows"] = [{ hpt: 28 }, { hpt: 18 }, { hpt: 18 }, { hpt: 18 }, { hpt: 22 }];

  // Number formatting for availability column via cell objects is limited with this lib,
  // so we apply a percent format string where applicable.
  for (let i = 0; i < fleets.length; i++) {
    const addr = XLSX.utils.encode_cell({ r: fleetStart - 1 + i, c: 2 });
    if (sheet[addr] && typeof sheet[addr].v === "number") {
      sheet[addr].z = "0.0%";
    }
  }

  return sheet;
}

function buildRankingSheet(XLSX, ranking) {
  const headers = ["#", "Placa", "Flota", "Disponibilidad %", "Horas no disp.", "MTTR", "Órdenes"];
  const rows = Array.isArray(ranking) ? ranking : [];
  const data = rows.map((v, idx) => [
    idx + 1,
    v.plate || "—",
    v.customer_name || "—",
    safeNum(v.availability_pct) === null ? "—" : safeNum(v.availability_pct) / 100,
    safeNum(v.h_no_disp) === null ? "—" : safeNum(v.h_no_disp),
    fmtMttr(v.mttr_hours),
    safeNum(v.orders_considered) ?? 0,
  ]);

  const aoa = [headers, ...data];
  const sheet = XLSX.utils.aoa_to_sheet(aoa);

  headers.forEach((_, c) => {
    const addr = XLSX.utils.encode_cell({ r: 0, c });
    if (sheet[addr]) sheet[addr].s = headerStyle;
  });

  data.forEach((_, rIdx) => {
    const excelRow = rIdx + 1;
    const band = excelRow % 2 === 0;
    headers.forEach((_, c) => {
      const addr = XLSX.utils.encode_cell({ r: excelRow, c });
      const cell = sheet[addr];
      if (!cell) return;
      cell.s = band ? cellBand : cellBase;
      if (c === 3 && typeof cell.v === "number") cell.z = "0.0%";
      if (c === 4 && typeof cell.v === "number") cell.z = "0.0";
    });
  });

  sheet["!cols"] = [{ wch: 6 }, { wch: 16 }, { wch: 32 }, { wch: 16 }, { wch: 14 }, { wch: 12 }, { wch: 10 }];
  sheet["!freeze"] = { xSplit: 0, ySplit: 1 };
  sheet["!rows"] = [{ hpt: 28 }];

  return sheet;
}

function buildCoverageSheet(XLSX, coverage) {
  const fleets = Array.isArray(coverage?.fleets) ? coverage.fleets : [];
  const plates = Array.isArray(coverage?.uncovered_plates) ? coverage.uncovered_plates : [];
  const cloudfleetUnmatched = Array.isArray(coverage?.cloudfleet_unmatched) ? coverage.cloudfleet_unmatched : [];
  const summary = coverage?.summary || {};

  const sheet = {};
  let row = 1;

  sheet["A1"] = { v: "Resumen de cobertura CloudFleet", t: "s", s: titleStyle };
  sheet["!merges"] = [{ s: { r: 0, c: 0 }, e: { r: 0, c: 3 } }];

  const summaryRows = [
    ["Total placas", fmtInt(summary.total)],
    ["Con cobertura", fmtInt(summary.covered)],
    ["Sin cobertura", fmtInt(summary.uncovered)],
    ["Con error", fmtInt(summary.error)],
    ["Cobertura %", fmtPct(summary.coverage_pct)],
  ];

  row = 3;
  summaryRows.forEach(([label, value], idx) => {
    const r = row + idx;
    sheet[`A${r}`] = { v: label, t: "s", s: labelStyle };
    sheet[`B${r}`] = { v: value, t: "s", s: valueStyle };
  });

  row = row + summaryRows.length + 2;
  const fleetHeaders = ["Flota", "Placas", "Sin cobertura", "Cobertura %"];
  fleetHeaders.forEach((h, c) => {
    sheet[XLSX.utils.encode_cell({ r: row - 1, c })] = { v: h, t: "s", s: headerStyle };
  });
  fleets.forEach((f, idx) => {
    const r = row + idx;
    const band = idx % 2 === 1;
    const values = [
      f.customer_name || "—",
      safeNum(f.total) ?? 0,
      safeNum(f.uncovered) ?? 0,
      safeNum(f.coverage_pct) === null ? "—" : safeNum(f.coverage_pct) / 100,
    ];
    values.forEach((v, c) => {
      sheet[XLSX.utils.encode_cell({ r: r - 1, c })] = {
        v,
        t: typeof v === "number" ? "n" : "s",
        s: band ? cellBand : cellBase,
      };
      if (c === 3 && typeof v === "number") {
        sheet[XLSX.utils.encode_cell({ r: r - 1, c })].z = "0.0%";
      }
    });
  });

  row = row + fleets.length + 2;
  sheet[XLSX.utils.encode_cell({ r: row - 1, c: 0 })] = { v: "Placas sin cobertura", t: "s", s: headerStyle };
  sheet[XLSX.utils.encode_cell({ r: row - 1, c: 1 })] = { v: "Flota", t: "s", s: headerStyle };
  row += 1;

  plates.forEach((p, idx) => {
    const r = row + idx;
    const band = idx % 2 === 1;
    sheet[XLSX.utils.encode_cell({ r: r - 1, c: 0 })] = { v: p.plate || "—", t: "s", s: band ? cellBand : cellBase };
    sheet[XLSX.utils.encode_cell({ r: r - 1, c: 1 })] = { v: p.customer_name || "—", t: "s", s: band ? cellBand : cellBase };
  });

  if (cloudfleetUnmatched.length > 0) {
    row = row + plates.length + 2;
    sheet[XLSX.utils.encode_cell({ r: row - 1, c: 0 })] = {
      v: `En CloudFleet sin registrar localmente (${cloudfleetUnmatched.length})`,
      t: "s",
      s: headerStyle,
    };
    sheet[XLSX.utils.encode_cell({ r: row - 1, c: 1 })] = { v: "Cost center", t: "s", s: headerStyle };
    row += 1;

    cloudfleetUnmatched.forEach((item, idx) => {
      const r = row + idx;
      const band = idx % 2 === 1;
      sheet[XLSX.utils.encode_cell({ r: r - 1, c: 0 })] = {
        v: item.code || "—",
        t: "s",
        s: band ? cellBand : cellBase,
      };
      sheet[XLSX.utils.encode_cell({ r: r - 1, c: 1 })] = {
        v: item.cost_center || "—",
        t: "s",
        s: band ? cellBand : cellBase,
      };
    });
  }

  const finalRow = row + Math.max(plates.length, cloudfleetUnmatched.length, 1);
  sheet["!ref"] = XLSX.utils.encode_range({
    s: { r: 0, c: 0 },
    e: { r: finalRow, c: 3 },
  });
  sheet["!cols"] = [{ wch: 36 }, { wch: 36 }, { wch: 16 }, { wch: 14 }];
  sheet["!rows"] = [{ hpt: 28 }];

  return sheet;
}

export async function exportDisponibilidadExcel({ month, overview, ranking, coverage }) {
  const XLSXmod = await import("xlsx-js-style");
  const XLSX = XLSXmod.default || XLSXmod;
  const wb = XLSX.utils.book_new();

  const summarySheet = buildSummarySheet(XLSX, month, overview, coverage);
  XLSX.utils.book_append_sheet(wb, summarySheet, "Resumen");

  const rankingSheet = buildRankingSheet(XLSX, ranking);
  XLSX.utils.book_append_sheet(wb, rankingSheet, "Ranking");

  const summary = coverage?.summary || {};
  const hasCoverage = summary.total > 0 || Array.isArray(coverage?.fleets) && coverage.fleets.length > 0;
  if (hasCoverage) {
    const coverageSheet = buildCoverageSheet(XLSX, coverage);
    XLSX.utils.book_append_sheet(wb, coverageSheet, "Sin cobertura");
  }

  const today = new Date().toISOString().slice(0, 10);
  const fileName = `Disponibilidad_${sanitizeFileName(formatMonthLabel(month))}_${today}.xlsx`;
  XLSX.writeFile(wb, fileName, { bookType: "xlsx", cellStyles: true });
}
