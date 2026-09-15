// Utilidades del pegado de tanqueos para CPK/CPH. Viven fuera de la pagina
// porque el modal de calculo tambien necesita parsear el texto para validar
// antes de enviarlo (si no se parsea nada, el calculo caeria en silencio al
// mes completo).

export function normalizeHeader(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ");
}

export function normalizePlate(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function parseNumber(value) {
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

export function parseClipboard(text) {
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

// Fila util = la que al menos trae placa y las dos fechas. Sin fechas el
// backend no puede recortar la ventana y el CPK saldria del mes completo.
export function parseCutoffRows(text) {
  return parseClipboard(text).filter(
    (row) => row.plate && row.cutoff_start_at && row.cutoff_end_at
  );
}
