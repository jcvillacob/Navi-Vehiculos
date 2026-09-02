import { EXPORT_RAW_METRIC_KEYS, getAvailabilityPct, getConnPct, getGph, getKpg, getStatusLabel } from "./columns";
import { getRowAlerts } from "./filters";

export function buildExportFileName(monthFrom, monthTo) {
  const range = monthFrom === monthTo ? monthFrom : `${monthFrom}_a_${monthTo}`;
  return `rendimientos_${range}.xlsx`;
}

function exportCellValue(col, row, ctx) {
  if (col.key === "status") return getStatusLabel(row.calculation_status);
  if (col.key === "alerts") return getRowAlerts(row).join(" | ") || null;
  if (col.key === "conn_pct") return getConnPct(row, ctx);
  if (col.key === "availability_pct") return getAvailabilityPct(row, ctx);
  if (EXPORT_RAW_METRIC_KEYS.has(col.key)) return row[col.key] ?? null;
  if (col.key === "kpg") {
    const v = getKpg(row);
    return v === null ? null : +v.toFixed(2);
  }
  if (col.key === "gph") {
    const v = getGph(row);
    return v === null ? null : +v.toFixed(2);
  }
  return col.getValue(row, ctx);
}

function joinOrAll(values, all) {
  return values.length ? values.join(", ") : all;
}

/**
 * Genera y descarga el Excel (hoja Rendimientos + hoja Filtros).
 * Carga xlsx-js-style de forma diferida.
 */
export async function exportRendimientosExcel({ rows, columns, ctx, monthFrom, monthTo, filters }) {
  const XLSXmod = await import("xlsx-js-style");
  const XLSX = XLSXmod.default || XLSXmod;

  const headers = columns.map((col) => col.label);
  const body = rows.map((row) => columns.map((col) => exportCellValue(col, row, ctx)));

  const filtersSheet = [
    { Filtro: "Desde", Valor: monthFrom || "Todos" },
    { Filtro: "Hasta", Valor: monthTo || "Todos" },
    { Filtro: "Estado", Valor: joinOrAll(filters.statuses.map(getStatusLabel), "Todos") },
    { Filtro: "Cliente", Valor: joinOrAll(filters.clients, "Todos") },
    { Filtro: "Categoria", Valor: joinOrAll(filters.categories, "Todas") },
    { Filtro: "Grupo de motor", Valor: joinOrAll(filters.motorGroups, "Todos") },
    { Filtro: "Placa", Valor: filters.plateSearch || "Todas" },
  ];

  const dataSheet = XLSX.utils.aoa_to_sheet([headers, ...body]);
  const filtersDataSheet = XLSX.utils.json_to_sheet(filtersSheet);

  if (dataSheet["!ref"]) {
    const range = XLSX.utils.decode_range(dataSheet["!ref"]);
    for (let R = range.s.r + 1; R <= range.e.r; R += 1) {
      for (let C = range.s.c; C <= range.e.c; C += 1) {
        const cell = dataSheet[XLSX.utils.encode_cell({ r: R, c: C })];
        if (cell && cell.t === "n") cell.z = "0.00";
      }
    }
  }

  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, dataSheet, "Rendimientos");
  XLSX.utils.book_append_sheet(workbook, filtersDataSheet, "Filtros");
  XLSX.writeFile(workbook, buildExportFileName(monthFrom, monthTo), { bookType: "xlsx", cellStyles: true });
}
