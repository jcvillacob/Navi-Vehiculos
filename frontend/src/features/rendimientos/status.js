// Catalogo de estados de calculo (compartido por columnas, filtros y chips).

export const STATUS_FILTER_OPTIONS = [
  { key: "calculated", label: "Calculadas", className: "is-calculated" },
  { key: "partial", label: "Parciales", className: "is-partial" },
  { key: "unbound", label: "Sin binding", className: "is-unbound" },
  { key: "no_data", label: "Sin datos", className: "is-no-data" },
  { key: "error", label: "Error", className: "is-error" },
];

export const STATUS_KEYS = STATUS_FILTER_OPTIONS.map((option) => option.key);

export function getStatusLabel(status) {
  if (status === "calculated") return "Calculado";
  if (status === "partial") return "Parcial";
  if (status === "unbound") return "Sin binding";
  if (status === "no_data") return "Sin datos";
  if (status === "error") return "Error";
  return status || "Desconocido";
}

export function getStatusClass(status) {
  if (status === "calculated") return "status-ok";
  if (status === "partial") return "status-soft";
  if (status === "unbound" || status === "no_data") return "status-partial";
  return "status-error";
}
