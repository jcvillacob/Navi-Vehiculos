import * as XLSX from "xlsx-js-style";

const FIXED_HEADERS = [
  "#",
  "Dispositivo",
  "Tipo busqueda",
  "Valor busqueda",
  "Placa",
  "VIN",
  "ESN",
  "Marca",
  "Linea",
  "Modelo",
  "Configuracion",
  "Año modelo",
  "Tipo combustible",
  "Marketing Model Name",
  "Service Model Name",
  "TEC#",
  "CPL",
  "Motor registrado ID",
  "Motor registrado nombre",
  "Motor registrado TEC",
  "Cliente",
  "Database",
  "Usuario DB",
  "Tiene password DB",
  "Geotab Navi",
  "Geotab Cliente",
  "Estado",
  "Mensaje",
  "Warnings",
  "Cached",
];

function getTimestampParts() {
  const now = new Date();
  const date = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
  const time = `${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}`;
  return { date, time };
}

function toCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function collectDynamicKeys(results, sourceKey) {
  const keys = new Set();
  for (const result of results) {
    const bag = result?.response?.source_details?.[sourceKey];
    if (bag && typeof bag === "object") {
      for (const key of Object.keys(bag)) {
        if (key) keys.add(key);
      }
    }
  }
  return Array.from(keys).sort((a, b) => a.localeCompare(b));
}

function buildFixedRow(result) {
  const response = result?.response || {};
  const fenix = response.source_details?.fenix || {};
  const motor = response.registered_motor || {};
  const assigned = response.assigned_database || {};

  return [
    result?.rowNumber ?? "",
    result?.identifier ?? "",
    response.lookup_type ?? "",
    response.lookup_value ?? "",
    response.plate ?? "",
    response.vin ?? "",
    response.engine_number ?? "",
    response.marca ?? fenix.marca ?? "",
    response.linea ?? fenix.linea ?? "",
    fenix.modelo ?? "",
    fenix.configuracion ?? "",
    response.ano_modelo ?? fenix.ano_modelo ?? "",
    response.tipo_combustible ?? fenix.tipo_combustible ?? "",
    response.marketing_model_name ?? "",
    response.service_model_name ?? "",
    response.technical_engine_configuration ?? "",
    response.cpl ?? "",
    motor.id ?? "",
    motor.engine_name ?? "",
    motor.technical_number ?? "",
    assigned.client_name ?? "",
    assigned.database_name ?? "",
    assigned.database_username ?? "",
    assigned.has_database_password === true ? "true" : "false",
    response.geotab_status ?? "",
    response.geotab_customer_status ?? "",
    result?.status ?? "",
    result?.error || response.message || "",
    Array.isArray(response.warnings) ? response.warnings.join(" | ") : "",
    response.cached === true ? "true" : "false",
  ].map(toCell);
}

function buildDynamicRow(result, fenixKeys, cumminsKeys) {
  const fenix = result?.response?.source_details?.fenix || {};
  const cummins = result?.response?.source_details?.cummins || {};
  const fenixCells = fenixKeys.map((key) => toCell(fenix[key]));
  const cumminsCells = cumminsKeys.map((key) => toCell(cummins[key]));
  return [...fenixCells, ...cumminsCells];
}

function computeColumnWidths(matrix, headers) {
  return headers.map((_, columnIndex) => {
    const maxLength = matrix.reduce((max, row) => {
      const value = row[columnIndex] == null ? "" : String(row[columnIndex]);
      return Math.max(max, value.length);
    }, String(headers[columnIndex] || "").length);
    return { wch: Math.min(60, Math.max(10, maxLength + 2)) };
  });
}

export function buildBulkResultsWorkbook(results) {
  const safeResults = Array.isArray(results) ? results : [];
  const fenixKeys = collectDynamicKeys(safeResults, "fenix");
  const cumminsKeys = collectDynamicKeys(safeResults, "cummins");

  const headers = [
    ...FIXED_HEADERS,
    ...fenixKeys.map((key) => `Fenix: ${key}`),
    ...cumminsKeys.map((key) => `Cummins: ${key}`),
  ];

  const rows = safeResults.map((result) => [
    ...buildFixedRow(result),
    ...buildDynamicRow(result, fenixKeys, cumminsKeys),
  ]);

  const matrix = [headers, ...rows];
  const sheet = XLSX.utils.aoa_to_sheet(matrix);

  sheet["!cols"] = computeColumnWidths(matrix, headers);
  sheet["!freeze"] = { ySplit: 1 };
  sheet["!views"] = [{ state: "frozen", ySplit: 1 }];

  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, sheet, "Resultados");
  return workbook;
}

export function downloadBulkResultsWorkbook(results) {
  const workbook = buildBulkResultsWorkbook(results);
  const { date, time } = getTimestampParts();
  XLSX.writeFile(workbook, `navi-consulta-lote-${date}-${time}.xlsx`);
}
