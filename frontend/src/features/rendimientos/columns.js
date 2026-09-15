import { formatDateTime, formatMetric, formatMonthLabel } from "../../utils/formatters";
import { getRowAlerts } from "./filters";
import { getStatusLabel } from "./status";

export { STATUS_FILTER_OPTIONS, STATUS_KEYS, getStatusClass, getStatusLabel } from "./status";

// Decimales: km/odometro 0; horas/horometro 1 (alineado con CpkCphPage).
const KM_DIGITS = 0;
const HOUR_DIGITS = 1;

function text(key, label, fallback = "-") {
  return {
    key,
    label,
    getValue: (row) => row[key] || fallback,
    getSortValue: (row) => row[key] || "",
  };
}

function metric(key, label, digits) {
  return {
    key,
    label,
    getValue: (row) => formatMetric(row[key], digits),
    getSortValue: (row) => row[key],
  };
}

export function getKpg(row) {
  return row.fuel_gallons > 0 && row.kms_ecm != null ? row.kms_ecm / row.fuel_gallons : null;
}

export function getGph(row) {
  return row.hours_ecm > 0 && row.fuel_gallons != null ? row.fuel_gallons / row.hours_ecm : null;
}

export function getConnPct(row, ctx) {
  const cs = ctx?.connStats?.[row.plate];
  return cs?.days_checked > 0 ? cs.connection_pct : null;
}

export function getAvailabilityPct(row, ctx) {
  const a = ctx?.availabilityByPlate?.[row.plate];
  if (!a || a.calculation_status === "not_in_cloudfleet" || a.calculation_status === "error") return null;
  return a.project_availability_pct ?? null;
}

/**
 * Columnas de la tabla. `getValue(row, ctx)` devuelve el texto de la celda;
 * `getSortValue(row, ctx)` el valor crudo para ordenar. `ctx` trae
 * connStats y availabilityByPlate (indexados por placa).
 */
export const RENDIMIENTOS_COLUMNS = [
  {
    key: "status",
    label: "Estado",
    getValue: (row) => getStatusLabel(row.calculation_status),
    getSortValue: (row) => getStatusLabel(row.calculation_status),
  },
  {
    key: "alerts",
    label: "Alertas",
    getValue: (row) => {
      const n = getRowAlerts(row).length;
      return n > 0 ? String(n) : "-";
    },
    getSortValue: (row) => getRowAlerts(row).length,
  },
  text("plate", "Placa"),
  { key: "client", label: "Cliente", getValue: (row) => row.client_name || "-", getSortValue: (row) => row.client_name || "" },
  { key: "database", label: "Database", getValue: (row) => row.database_name || "-", getSortValue: (row) => row.database_name || "" },
  { key: "motor", label: "Motor", getValue: (row) => row.engine_name || "Sin catalogar", getSortValue: (row) => row.engine_name || "Sin catalogar" },
  text("nombre_vehiculo", "Nombre"),
  text("marca", "Marca"),
  text("linea", "Linea"),
  text("ano_modelo", "Año"),
  text("tipo_combustible", "Combustible"),
  text("vin", "VIN", "Sin VIN"),
  text("cpl", "CPL", "Sin CPL"),
  text("technical_number", "TEC#"),
  text("source_provider", "Proveedor"),
  {
    key: "period_month",
    label: "Mes",
    getValue: (row) => {
      const months = Array.isArray(row.period_months) && row.period_months.length > 0
        ? row.period_months
        : [row.period_month];
      return months.map(formatMonthLabel).join(", ");
    },
    getSortValue: (row) => row.period_month || "",
  },
  metric("odo_start", "Odo ini", KM_DIGITS),
  metric("odo_end", "Odo fin", KM_DIGITS),
  metric("kms_ecm", "Kms ECM", KM_DIGITS),
  metric("kms_gps", "Kms GPS", KM_DIGITS),
  metric("horo_start", "Horo ini", HOUR_DIGITS),
  metric("horo_end", "Horo fin", HOUR_DIGITS),
  metric("hours_ecm", "Hrs ECM", HOUR_DIGITS),
  metric("hours_gps", "Hrs GPS", HOUR_DIGITS),
  metric("fuel_gallons", "Galones", 0),
  {
    key: "kpg",
    label: "KPG",
    getValue: (row) => {
      const v = getKpg(row);
      return v === null ? "-" : formatMetric(v, 2);
    },
    getSortValue: getKpg,
  },
  {
    key: "gph",
    label: "GPH",
    getValue: (row) => {
      const v = getGph(row);
      return v === null ? "-" : formatMetric(v, 2);
    },
    getSortValue: getGph,
  },
  {
    key: "conn_pct",
    label: "Conexion %",
    getValue: (row, ctx) => {
      const v = getConnPct(row, ctx);
      return v === null ? "--" : `${Math.round(v)}%`;
    },
    getSortValue: (row, ctx) => {
      const v = getConnPct(row, ctx);
      return v === null ? -1 : v;
    },
  },
  {
    key: "availability_pct",
    label: "Disp %",
    getValue: (row, ctx) => {
      const a = ctx?.availabilityByPlate?.[row.plate];
      if (!a) return "Sin Datos";
      if (a.calculation_status === "not_in_cloudfleet") return "No Aplica";
      if (a.calculation_status === "error") return "Error";
      return `${(a.project_availability_pct ?? 0).toFixed(1)}%`;
    },
    getSortValue: (row, ctx) => {
      const a = ctx?.availabilityByPlate?.[row.plate];
      if (!a) return -1;
      if (a.calculation_status === "not_in_cloudfleet") return -2;
      if (a.calculation_status === "error") return -3;
      return a.project_availability_pct ?? -1;
    },
  },
  {
    key: "calculated_at",
    label: "Último cálculo",
    getValue: (row) => formatDateTime(row.calculated_at),
    getSortValue: (row) => (row.calculated_at ? new Date(row.calculated_at).getTime() : 0),
  },
];

export const COLUMN_KEYS = RENDIMIENTOS_COLUMNS.map((column) => column.key);
export const COLUMN_BY_KEY = new Map(RENDIMIENTOS_COLUMNS.map((column) => [column.key, column]));

/** Columnas cuyo valor crudo se exporta como numero (sin formatear). */
export const EXPORT_RAW_METRIC_KEYS = new Set([
  "odo_start", "odo_end", "kms_ecm", "kms_gps",
  "horo_start", "horo_end", "hours_ecm", "hours_gps",
  "fuel_gallons", "ano_modelo",
]);
