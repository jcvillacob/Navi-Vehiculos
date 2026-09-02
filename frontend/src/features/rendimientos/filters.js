import { getCurrentMonth, isValidMonth, normalizeMonthRange } from "../../utils/formatters";
import { STATUS_KEYS } from "./status";

export const CATEGORY_FALLBACK = "Ninguna";
export const MOTOR_FALLBACK = "Sin catalogar";
export const DEFAULT_PAGE_SIZE = 25;
export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

export const EMPTY_FILTERS = Object.freeze({
  statuses: [],
  clients: [],
  categories: [],
  motorGroups: [],
  plateSearch: "",
});

function rowCategory(row) {
  return row.category || CATEGORY_FALLBACK;
}

function rowMotor(row) {
  return row.engine_name || MOTOR_FALLBACK;
}

function matchesSearch(row, q) {
  if (!q) return true;
  const fields = [row.plate, row.nombre_vehiculo, row.marca, row.linea];
  return fields.some((value) => (value || "").toUpperCase().includes(q));
}

/**
 * Pre-procesa filtros a Sets para O(1) en el recorrido.
 */
function compileFilters(filters) {
  return {
    statuses: filters.statuses?.length ? new Set(filters.statuses) : null,
    clients: filters.clients?.length ? new Set(filters.clients) : null,
    categories: filters.categories?.length ? new Set(filters.categories) : null,
    motorGroups: filters.motorGroups?.length ? new Set(filters.motorGroups) : null,
    q: (filters.plateSearch || "").trim().toUpperCase(),
  };
}

/**
 * Filtra filas con filtros multi-valor (OR dentro de cada facet, AND entre
 * facets). `omit` permite excluir un facet (para construir sus opciones).
 */
export function filterRows(rows, filters, omit = "") {
  const c = compileFilters(filters);
  return rows.filter((row) => {
    if (omit !== "statuses" && c.statuses && !c.statuses.has(row.calculation_status)) return false;
    if (omit !== "clients" && c.clients && !c.clients.has(row.client_name)) return false;
    if (omit !== "categories" && c.categories && !c.categories.has(rowCategory(row))) return false;
    if (omit !== "motorGroups" && c.motorGroups && !c.motorGroups.has(rowMotor(row))) return false;
    if (omit !== "plateSearch" && !matchesSearch(row, c.q)) return false;
    return true;
  });
}

function sortedUnique(set) {
  return [...set].sort((a, b) => a.localeCompare(b));
}

/**
 * UNA sola pasada sobre las filas que produce:
 *  - filteredRows: filas que pasan todos los filtros
 *  - options.{clients,categories,motorGroups}: valores disponibles para cada
 *    facet considerando los DEMAS filtros (comportamiento "omit")
 *  - statusCounts: conteo por estado considerando los demas filtros
 */
export function analyzeRows(rows, filters) {
  const c = compileFilters(filters);
  const filteredRows = [];
  const clients = new Set();
  const categories = new Set();
  const motorGroups = new Set();
  const statusCounts = Object.fromEntries(STATUS_KEYS.map((key) => [key, 0]));

  for (const row of rows) {
    const category = rowCategory(row);
    const motor = rowMotor(row);
    const okStatus = !c.statuses || c.statuses.has(row.calculation_status);
    const okClient = !c.clients || c.clients.has(row.client_name);
    const okCategory = !c.categories || c.categories.has(category);
    const okMotor = !c.motorGroups || c.motorGroups.has(motor);
    const okSearch = matchesSearch(row, c.q);

    if (okStatus && okClient && okCategory && okMotor && okSearch) {
      filteredRows.push(row);
    }
    if (okStatus && okCategory && okMotor && okSearch && row.client_name) clients.add(row.client_name);
    if (okStatus && okClient && okMotor && okSearch) categories.add(category);
    if (okStatus && okClient && okCategory && okSearch) motorGroups.add(motor);
    if (okClient && okCategory && okMotor && okSearch && statusCounts[row.calculation_status] !== undefined) {
      statusCounts[row.calculation_status] += 1;
    }
  }

  return {
    filteredRows,
    options: {
      clients: sortedUnique(clients),
      categories: sortedUnique(categories),
      motorGroups: sortedUnique(motorGroups),
    },
    statusCounts,
  };
}

export function buildFilterOptions(rows, filters = EMPTY_FILTERS) {
  return analyzeRows(rows, filters).options;
}

export function countActiveFilters(filters) {
  return (
    filters.statuses.length +
    filters.clients.length +
    filters.categories.length +
    filters.motorGroups.length +
    (filters.plateSearch ? 1 : 0)
  );
}

// ── Flags de validacion por fila (cliente) ──────────────────────────────────

export const ROW_FLAG_LABELS = {
  kms_negative: "Kms ECM negativos",
  hours_without_kms: "Horas ECM sin kilometros",
  kms_without_hours: "Kilometros sin horas ECM",
  odo_regression: "Odometro final menor al inicial",
  horo_regression: "Horometro final menor al inicial",
  kpg_out_of_range: "KPG fuera de rango (<1 o >60)",
};

function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

/** Flags calculados en cliente a partir de los valores de la fila. */
export function getRowFlags(row) {
  const flags = [];
  const kms = row.kms_ecm;
  const hours = row.hours_ecm;
  const fuel = row.fuel_gallons;

  if (isNum(kms) && kms < 0) flags.push("kms_negative");
  if (isNum(hours) && hours > 0 && kms === 0) flags.push("hours_without_kms");
  if (isNum(kms) && kms > 0 && hours === 0) flags.push("kms_without_hours");
  if (isNum(row.odo_start) && isNum(row.odo_end) && row.odo_end < row.odo_start) flags.push("odo_regression");
  if (isNum(row.horo_start) && isNum(row.horo_end) && row.horo_end < row.horo_start) flags.push("horo_regression");
  if (isNum(fuel) && fuel > 0 && isNum(kms)) {
    const kpg = kms / fuel;
    if (kpg > 60 || kpg < 1) flags.push("kpg_out_of_range");
  }
  return flags;
}

/**
 * Lista consolidada de alertas legibles para una fila: warnings del backend,
 * validation_flags (nuevo, opcional), retrocesos Geotab y flags de cliente.
 */
export function getRowAlerts(row) {
  const alerts = [];
  if (Array.isArray(row.warnings)) {
    for (const w of row.warnings) if (w) alerts.push(String(w));
  }
  if (Array.isArray(row.validation_flags)) {
    for (const f of row.validation_flags) if (f) alerts.push(ROW_FLAG_LABELS[f] || String(f));
  }
  if (isNum(row.geotab_regression_count) && row.geotab_regression_count > 0) {
    const km = isNum(row.geotab_regression_total_km) ? ` (${Math.round(row.geotab_regression_total_km)} km)` : "";
    alerts.push(`${row.geotab_regression_count} retroceso(s) de odometro Geotab${km}`);
  }
  for (const flag of getRowFlags(row)) {
    const label = ROW_FLAG_LABELS[flag];
    if (label && !alerts.includes(label)) alerts.push(label);
  }
  return alerts;
}

export function isRowFlagged(row, alerts = getRowAlerts(row)) {
  return alerts.length > 0 || row.calculation_status === "partial" || row.calculation_status === "error";
}

// ── URL <-> estado ───────────────────────────────────────────────────────────
//
// Contrato de query params (repetidos para multi-valor):
//   ?from=2026-06&to=2026-08&client=A&client=B&category=X&motor=Y
//   &status=partial&status=error&q=ABC&page=2&size=50&sort=kms_ecm&dir=desc

const PARAM = {
  from: "from",
  to: "to",
  client: "client",
  category: "category",
  motor: "motor",
  status: "status",
  q: "q",
  page: "page",
  size: "size",
  sort: "sort",
  dir: "dir",
};

export const FILTER_PARAM_KEYS = Object.values(PARAM);

function uniqueStrings(values) {
  return [...new Set(values.map((v) => String(v || "").trim()).filter(Boolean))];
}

export function parseFiltersFromSearchParams(searchParams) {
  const current = getCurrentMonth();
  const rawFrom = searchParams.get(PARAM.from);
  const rawTo = searchParams.get(PARAM.to);
  const from = isValidMonth(rawFrom) ? rawFrom : (isValidMonth(rawTo) ? rawTo : current);
  const to = isValidMonth(rawTo) ? rawTo : from;
  const [monthFrom, monthTo] = normalizeMonthRange(from, to);

  const statuses = uniqueStrings(searchParams.getAll(PARAM.status)).filter((s) => STATUS_KEYS.includes(s));
  const rawPage = Number.parseInt(searchParams.get(PARAM.page) || "", 10);
  const rawSize = Number.parseInt(searchParams.get(PARAM.size) || "", 10);
  const sortKey = searchParams.get(PARAM.sort) || null;
  const sortDir = searchParams.get(PARAM.dir);

  return {
    monthFrom,
    monthTo,
    filters: {
      statuses,
      clients: uniqueStrings(searchParams.getAll(PARAM.client)),
      categories: uniqueStrings(searchParams.getAll(PARAM.category)),
      motorGroups: uniqueStrings(searchParams.getAll(PARAM.motor)),
      plateSearch: (searchParams.get(PARAM.q) || "").toUpperCase(),
    },
    page: Number.isInteger(rawPage) && rawPage > 0 ? rawPage : 1,
    pageSize: PAGE_SIZE_OPTIONS.includes(rawSize) ? rawSize : DEFAULT_PAGE_SIZE,
    sort: sortKey && (sortDir === "asc" || sortDir === "desc") ? { key: sortKey, dir: sortDir } : null,
  };
}

/**
 * Serializa el estado a URLSearchParams. Omite valores por defecto para que
 * la URL quede limpia (mes actual, pagina 1, tamano 25, sin filtros).
 * Conserva params ajenos que ya estuvieran en la URL.
 */
export function serializeFiltersToSearchParams(state, base = null) {
  const params = new URLSearchParams(base || undefined);
  for (const key of FILTER_PARAM_KEYS) params.delete(key);

  const current = getCurrentMonth();
  const [from, to] = normalizeMonthRange(state.monthFrom, state.monthTo);
  if (from !== current || to !== current) {
    params.set(PARAM.from, from);
    params.set(PARAM.to, to);
  }
  const f = state.filters || EMPTY_FILTERS;
  for (const v of f.clients || []) params.append(PARAM.client, v);
  for (const v of f.categories || []) params.append(PARAM.category, v);
  for (const v of f.motorGroups || []) params.append(PARAM.motor, v);
  for (const v of f.statuses || []) params.append(PARAM.status, v);
  if (f.plateSearch) params.set(PARAM.q, f.plateSearch);
  if (state.page && state.page > 1) params.set(PARAM.page, String(state.page));
  if (state.pageSize && state.pageSize !== DEFAULT_PAGE_SIZE) params.set(PARAM.size, String(state.pageSize));
  if (state.sort?.key && state.sort?.dir) {
    params.set(PARAM.sort, state.sort.key);
    params.set(PARAM.dir, state.sort.dir);
  }
  return params;
}

/** true si la URL ya trae al menos un param del contrato. */
export function hasAnyFilterParam(searchParams) {
  return FILTER_PARAM_KEYS.some((key) => searchParams.has(key));
}

/**
 * Convierte el shape legado de `location.state.rendimientosFilters`
 * (ficha 360 round-trip) al estado actual. Acepta tanto el shape viejo
 * (filters.status/client/... escalares) como el nuevo (arrays).
 */
export function fromLegacyState(legacy) {
  if (!legacy || typeof legacy !== "object") return null;
  const lf = legacy.filters || {};
  const toArray = (single, multi) => {
    if (Array.isArray(multi)) return uniqueStrings(multi);
    if (typeof single === "string" && single) return [single];
    return [];
  };
  const current = getCurrentMonth();
  const from = isValidMonth(legacy.monthFrom) ? legacy.monthFrom : current;
  const to = isValidMonth(legacy.monthTo) ? legacy.monthTo : from;
  const [monthFrom, monthTo] = normalizeMonthRange(from, to);
  return {
    monthFrom,
    monthTo,
    filters: {
      statuses: toArray(lf.status, lf.statuses).filter((s) => STATUS_KEYS.includes(s)),
      clients: toArray(lf.client, lf.clients),
      categories: toArray(lf.category, lf.categories),
      motorGroups: toArray(lf.motorGroup, lf.motorGroups),
      plateSearch: typeof lf.plateSearch === "string" ? lf.plateSearch.toUpperCase() : "",
    },
    page: Number.isInteger(legacy.page) && legacy.page > 0 ? legacy.page : 1,
    pageSize: PAGE_SIZE_OPTIONS.includes(legacy.pageSize) ? legacy.pageSize : DEFAULT_PAGE_SIZE,
    sort: legacy.sort?.key && legacy.sort?.dir ? { key: legacy.sort.key, dir: legacy.sort.dir } : null,
  };
}
