export function getCurrentMonth() {
  return new Date().toISOString().slice(0, 7);
}

export function getPreviousMonth() {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return d.toISOString().slice(0, 7);
}

export function sanitizeSheetName(name) {
  const cleaned = String(name || "").replace(/[\[\]:*?/\\]/g, "_").trim();
  return (cleaned || "Cliente").slice(0, 31);
}

export function sanitizeFileName(name) {
  return String(name || "").replace(/[\\/:*?"<>|]/g, "_").trim();
}

export function isValidMonth(value) {
  return typeof value === "string" && /^\d{4}-\d{2}$/.test(value);
}

export function buildCpkFileName(month, clientName) {
  const safeMonth = (month || "").replace(/[^0-9-]/g, "");
  if (clientName) {
    return `cpk_cph_${sanitizeFileName(clientName)}_${safeMonth}.xlsx`;
  }
  return `cpk_cph_${safeMonth}.xlsx`;
}

export function formatMonthLabel(value) {
  if (!value || typeof value !== "string") return "-";
  const [year, month] = value.split("-");
  if (!year || !month) return value;
  const date = new Date(Number(year), Number(month) - 1, 1);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("es-CO", { month: "long", year: "numeric" });
}
