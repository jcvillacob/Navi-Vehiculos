// Formateadores compartidos (es-CO). Movidos desde RendimientosPage; otros
// archivos conservan sus propias copias a proposito (no se tocan aqui).

const SHORT_MONTH_NAMES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

export function isValidMonth(value) {
  return typeof value === "string" && /^\d{4}-\d{2}$/.test(value);
}

export function getCurrentMonth() {
  return new Date().toISOString().slice(0, 7);
}

export function formatNumber(value, options) {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("es-CO", options).format(value);
}

export function formatMetric(value, digits = 1) {
  return formatNumber(value, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/**
 * "2026-08" -> "Ago 2026" (short, default) | "agosto de 2026" (long).
 */
export function formatMonthLabel(monthStr, { style = "short" } = {}) {
  if (!monthStr || typeof monthStr !== "string") return "";
  const [year, m] = monthStr.split("-");
  const monthIndex = parseInt(m, 10) - 1;
  if (!year || Number.isNaN(monthIndex) || monthIndex < 0 || monthIndex > 11) return monthStr;
  if (style === "long") {
    return new Date(Number(year), monthIndex, 1).toLocaleDateString("es-CO", { month: "long", year: "numeric" });
  }
  return `${SHORT_MONTH_NAMES[monthIndex]} ${year}`;
}

export function formatDateTime(value) {
  if (!value) return "-";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("es-CO");
}

/** Lista de meses "YYYY-MM" inclusiva entre from y to (from <= to). */
export function generateMonthRange(from, to) {
  const months = [];
  if (!isValidMonth(from) || !isValidMonth(to)) return months;
  const [startYear, startMonth] = from.split("-").map(Number);
  const [endYear, endMonth] = to.split("-").map(Number);
  let y = startYear;
  let m = startMonth;
  while (y < endYear || (y === endYear && m <= endMonth)) {
    months.push(`${y}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return months;
}

/** Devuelve [from, to] ordenados (swap si vienen invertidos). */
export function normalizeMonthRange(from, to) {
  return from <= to ? [from, to] : [to, from];
}

/**
 * Comparador para ordenamiento: nulos/vacios al final, numeros por valor,
 * strings con localeCompare numerico.
 */
export function compareValues(left, right, direction = "asc") {
  if (left === right) return 0;
  if (left === null || left === undefined || left === "") return 1;
  if (right === null || right === undefined || right === "") return -1;

  let result = 0;
  if (typeof left === "number" && typeof right === "number") {
    result = left - right;
  } else {
    result = String(left).localeCompare(String(right), "es", { numeric: true, sensitivity: "base" });
  }
  return direction === "desc" ? result * -1 : result;
}
