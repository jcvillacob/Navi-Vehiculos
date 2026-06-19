const API_BASE = import.meta.env.VITE_API_URL ?? "";
const REFRESH_URL = `${API_BASE}/api/v1/auth/refresh`;
let refreshPromise = null;

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

function dispatchAuthEvent(type, detail = {}) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

async function attemptRefresh() {
  if (!refreshPromise) {
    refreshPromise = fetch(REFRESH_URL, {
      method: "POST",
      credentials: "include",
    })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error("Refresh fallido");
        }
        const data = await response.json();
        dispatchAuthEvent("auth:updated", { user: data });
        return data;
      })
      .catch((error) => {
        dispatchAuthEvent("auth:expired");
        throw error;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }

  return refreshPromise;
}

async function fetchWithAuth(input, init = {}, retry = true) {
  const response = await fetch(input, {
    credentials: "include",
    ...init,
  });

  if (response.status === 401 && retry && input !== REFRESH_URL) {
    try {
      await attemptRefresh();
    } catch {
      return response;
    }
    return fetchWithAuth(input, init, false);
  }

  return response;
}

async function parseJsonOrThrow(response, fallbackMessage) {
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") {
        detail = `: ${payload.detail}`;
      } else if (Array.isArray(payload?.detail)) {
        detail = `: ${payload.detail.map((e) => e.msg || e.message || JSON.stringify(e)).join("; ")}`;
      }
    } catch {
      detail = "";
    }
    throw new Error(`${fallbackMessage} (${response.status})${detail}`);
  }
  return response.json();
}

// ── Auth ─────────────────────────────────────────────────────────────────────

export async function loginUser(username, password) {
  const response = await fetch(buildUrl("/api/v1/auth/login"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return parseJsonOrThrow(response, "Error iniciando sesion");
}

export async function logoutUser() {
  const response = await fetch(buildUrl("/api/v1/auth/logout"), {
    method: "POST",
    credentials: "include",
  });
  return parseJsonOrThrow(response, "Error cerrando sesion");
}

export async function fetchMe() {
  const response = await fetchWithAuth(buildUrl("/api/v1/auth/me"));
  return parseJsonOrThrow(response, "Error verificando sesion");
}

export async function changeOwnPassword(payload) {
  const response = await fetchWithAuth(buildUrl("/api/v1/auth/password"), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error cambiando contrasena");
}

export async function fetchSessions(userId = null) {
  const suffix = userId ? `?user_id=${userId}` : "";
  const response = await fetchWithAuth(buildUrl(`/api/v1/auth/sessions${suffix}`));
  return parseJsonOrThrow(response, "Error cargando sesiones");
}

export async function revokeSession(sessionId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/auth/sessions/${sessionId}`), {
    method: "DELETE",
  });
  return parseJsonOrThrow(response, "Error cerrando sesion");
}

export async function revokeOtherSessions() {
  const response = await fetchWithAuth(buildUrl("/api/v1/auth/sessions"), {
    method: "DELETE",
  });
  return parseJsonOrThrow(response, "Error cerrando otras sesiones");
}

// ── Users (admin) ─────────────────────────────────────────────────────────────

export async function listUsers() {
  const response = await fetchWithAuth(buildUrl("/api/v1/users"));
  return parseJsonOrThrow(response, "Error listando usuarios");
}

export async function createUser(payload) {
  const response = await fetchWithAuth(buildUrl("/api/v1/users"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando usuario");
}

export async function updateUser(userId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/users/${userId}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando usuario");
}

export async function resetUserPassword(userId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/users/${userId}/reset-password`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error reseteando contrasena");
}

// ── Audit logs (admin) ────────────────────────────────────────────────────────

export async function fetchAuditLogs(limit = 100, offset = 0) {
  const query = new URLSearchParams({ limit, offset });
  const response = await fetchWithAuth(buildUrl(`/api/v1/audit?${query}`));
  return parseJsonOrThrow(response, "Error cargando logs de auditoria");
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export async function fetchDashboardSummary() {
  const response = await fetchWithAuth(buildUrl("/api/v1/dashboard/summary"));
  return parseJsonOrThrow(response, "Error obteniendo resumen del dashboard");
}

// ── Rendimientos ─────────────────────────────────────────────────────────────

export async function fetchMonthlyPerformance(params) {
  const query = new URLSearchParams();
  if (params.month_from) {
    query.set("month_from", params.month_from);
    query.set("month_to", params.month_to || params.month_from);
  } else if (params.month) {
    query.set("month", params.month);
  }
  if (params.customer_id) {
    query.set("customer_id", String(params.customer_id));
  }
  if (Array.isArray(params.customer_ids)) {
    for (const customerId of params.customer_ids) {
      query.append("customer_ids", String(customerId));
    }
  }
  if (params.customer_database_id) {
    query.set("customer_database_id", String(params.customer_database_id));
  }
  if (params.plate_search) {
    query.set("plate_search", params.plate_search.trim().toUpperCase());
  }
  if (params.motor_group) {
    query.set("motor_group", params.motor_group);
  }

  const response = await fetchWithAuth(buildUrl(`/api/v1/rendimientos?${query.toString()}`));
  return parseJsonOrThrow(response, "Error cargando rendimientos mensuales");
}

export async function calculateMonthlyPerformance(payload) {
  // Endpoint async: retorna 202 con el job nuevo, 409 con el job ya activo, o
  // un error >=400 normal. En 202 y 409 el body es el mismo schema (job).
  const response = await fetchWithAuth(buildUrl("/api/v1/rendimientos/calculate"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (response.status === 202 || response.status === 409 || response.status === 200) {
    const job = await response.json();
    return { job, reused: response.status === 409 };
  }
  // Forzar el camino de error de parseJsonOrThrow
  return parseJsonOrThrow(response, "Error calculando rendimientos mensuales");
}

export async function fetchAdhocFilterOptions() {
  const response = await fetchWithAuth(buildUrl("/api/v1/rendimientos/adhoc-filters"));
  return parseJsonOrThrow(response, "Error cargando filtros ad-hoc");
}

export async function fetchPerformanceJob(jobId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/rendimientos/jobs/${jobId}`));
  return parseJsonOrThrow(response, "Error consultando el job de rendimientos");
}

export async function cancelPerformanceJob(jobId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/rendimientos/jobs/${jobId}/cancel`), {
    method: "POST",
  });
  return parseJsonOrThrow(response, "Error cancelando el job de rendimientos");
}

export async function fetchActivePerformanceJobs() {
  const response = await fetchWithAuth(buildUrl("/api/v1/rendimientos/jobs?active=true"));
  const data = await parseJsonOrThrow(response, "Error listando jobs activos de rendimientos");
  return Array.isArray(data?.jobs) ? data.jobs : [];
}

export async function fetchRecentPerformanceJobs(limit = 50) {
  const params = new URLSearchParams({ active: "false", limit: String(limit) });
  const response = await fetchWithAuth(buildUrl(`/api/v1/rendimientos/jobs?${params.toString()}`));
  const data = await parseJsonOrThrow(response, "Error cargando historial de rendimientos");
  return Array.isArray(data?.jobs) ? data.jobs : [];
}

export async function fetchMonthlyAvailability({ month_from, month_to }) {
  const query = new URLSearchParams({ month_from, month_to });
  const response = await fetchWithAuth(buildUrl(`/api/v1/rendimientos/availability?${query.toString()}`));
  const data = await parseJsonOrThrow(response, "Error cargando disponibilidad mensual");
  return Array.isArray(data?.rows) ? data.rows : [];
}

// ── Disponibilidad (dashboard) ────────────────────────────────────────────────

export async function fetchAvailabilityOverview({ month }) {
  const query = new URLSearchParams({ month });
  const response = await fetchWithAuth(buildUrl(`/api/v1/disponibilidad/overview?${query.toString()}`));
  return parseJsonOrThrow(response, "Error cargando el resumen de disponibilidad");
}

export async function fetchAvailabilityRanking({ month, customer_id = null, limit = 20, order = "worst" }) {
  const query = new URLSearchParams({ month, limit: String(limit), order });
  if (customer_id) query.set("customer_id", String(customer_id));
  const response = await fetchWithAuth(buildUrl(`/api/v1/disponibilidad/ranking?${query.toString()}`));
  const data = await parseJsonOrThrow(response, "Error cargando el ranking de vehiculos");
  return Array.isArray(data?.items) ? data.items : [];
}

export async function fetchAvailabilityTrend({ month_to, months = 6, customer_id = null }) {
  const query = new URLSearchParams({ month_to, months: String(months) });
  if (customer_id) query.set("customer_id", String(customer_id));
  const response = await fetchWithAuth(buildUrl(`/api/v1/disponibilidad/trend?${query.toString()}`));
  return parseJsonOrThrow(response, "Error cargando la tendencia de disponibilidad");
}

// ── Vehicle ───────────────────────────────────────────────────────────────────

export async function lookupVehicle(identifier, { force = false } = {}) {
  const query = new URLSearchParams({ identifier: identifier.trim().toUpperCase() });
  if (force) query.set("force", "true");
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/lookup?${query.toString()}`));
  return parseJsonOrThrow(response, "Error consultando la API");
}

export async function batchLookupVehiclesStream(identifiers, { force = false, scope = "all", skipGeotab = false, onResult, signal } = {}) {
  const response = await fetchWithAuth(buildUrl("/api/v1/vehicle/batch-lookup"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      identifiers: identifiers.map((id) => id.trim().toUpperCase()),
      force,
      scope,
      skip_geotab: skipGeotab,
    }),
    signal,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => "Error en consulta masiva");
    throw new Error(text);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const results = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const parsed = JSON.parse(trimmed);
        results.push(parsed);
        if (onResult) onResult(parsed, results.length);
      } catch {
        /* skip malformed lines */
      }
    }
  }

  // Process any remaining buffer
  if (buffer.trim()) {
    try {
      const parsed = JSON.parse(buffer.trim());
      results.push(parsed);
      if (onResult) onResult(parsed, results.length);
    } catch {
      /* skip */
    }
  }

  return results;
}

export async function listVehicleAssignments(search = "") {
  const normalizedSearch = search.trim().toUpperCase();
  const query = new URLSearchParams();
  if (normalizedSearch) {
    query.set("search", normalizedSearch);
  }

  const suffix = query.toString() ? `?${query.toString()}` : "";
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle${suffix}`));
  return parseJsonOrThrow(response, "Error listando vehiculos");
}

export async function manualAssignVehicle(plate, payload) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/manual-assign`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error registrando asignacion manual del vehiculo");
}

/**
 * Asigna una database a un vehiculo.
 * @param {string} plate
 * @param {object} payload
 * @param {number|null} payload.customer_database_id
 * @param {string|null} payload.access_url
 * @param {string|null} [payload.provider_vehicle_id] - ID externo manual del vehiculo para el
 *   proveedor GPS (geotab, artimo, frotcom). Si es un string no vacio se persiste como manual.
 *   Si es string vacio se elimina el binding manual existente.
 *   Si es undefined/null no se modifica el binding (comportamiento existente).
 */
export async function assignVehicleDatabase(plate, payload) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/database`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando cliente y database del vehiculo");
}

export async function refreshVehicle(plate) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/refresh`), {
    method: "POST",
  });
  return parseJsonOrThrow(response, "Error actualizando datos del vehiculo");
}

/**
 * Fija (o limpia) el override de categoria de un vehiculo.
 * @param {string} plate
 * @param {string|null} category - "Ninguna" | "Experiencia Superior" | "Flota Administrada"
 *   o null para volver a heredar la categoria del cliente.
 */
export async function setVehicleCategory(plate, category) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/category`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category: category ?? null }),
  });
  return parseJsonOrThrow(response, "Error actualizando la categoria del vehiculo");
}

/**
 * Marca o desmarca un vehiculo como vocacional.
 * @param {string} plate
 * @param {boolean} vocacional
 */
export async function setVehicleVocacional(plate, vocacional) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/vocacional`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vocacional: Boolean(vocacional) }),
  });
  return parseJsonOrThrow(response, "Error actualizando el flag vocacional del vehiculo");
}

export async function revalidateCustomerGeotab(plate) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/revalidate-customer-geotab`), {
    method: "POST",
  });
  return parseJsonOrThrow(response, "Error revalidando Geotab del cliente");
}

export async function checkVehicleConnections() {
  const response = await fetchWithAuth(buildUrl("/api/v1/vehicle/check-connections"), {
    method: "POST",
  });
  return parseJsonOrThrow(response, "Error revisando conexiones de vehiculos");
}

export async function fetchConnectionStats(month) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/connection-stats?month=${month}`));
  return parseJsonOrThrow(response, "Error obteniendo estadisticas de conexion");
}

// ── Motors ────────────────────────────────────────────────────────────────────

export async function listMotors() {
  const response = await fetchWithAuth(buildUrl("/api/v1/motors"));
  return parseJsonOrThrow(response, "Error listando motores");
}

export async function createMotor(payload) {
  const response = await fetchWithAuth(buildUrl("/api/v1/motors"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando motor");
}

export async function updateMotor(motorId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/motors/${motorId}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando motor");
}

export async function deleteMotor(motorId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/motors/${motorId}`), {
    method: "DELETE",
  });
  return parseJsonOrThrow(response, "Error eliminando motor");
}

export async function uploadMotorAttachment(motorId, payload) {
  const formData = new FormData();
  formData.append("cpl", payload.cpl);
  formData.append("attachment", payload.file);

  const response = await fetchWithAuth(buildUrl(`/api/v1/motors/${motorId}/attachments`), {
    method: "POST",
    body: formData,
  });
  return parseJsonOrThrow(response, "Error subiendo adjunto del motor");
}

export async function updateMotorAttachment(attachmentId, payload) {
  const formData = new FormData();
  formData.append("cpl", payload.cpl);
  if (payload.file) {
    formData.append("attachment", payload.file);
  }

  const response = await fetchWithAuth(buildUrl(`/api/v1/motors/attachments/${attachmentId}`), {
    method: "PUT",
    body: formData,
  });
  return parseJsonOrThrow(response, "Error actualizando adjunto del motor");
}

export async function deleteMotorAttachment(attachmentId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/motors/attachments/${attachmentId}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Error eliminando adjunto del motor (${response.status})`);
  }
}

// ── Customers ─────────────────────────────────────────────────────────────────

export async function listCustomers() {
  const response = await fetchWithAuth(buildUrl("/api/v1/customers"));
  return parseJsonOrThrow(response, "Error listando clientes");
}

export async function createCustomer(payload) {
  const response = await fetchWithAuth(buildUrl("/api/v1/customers"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando cliente");
}

export async function updateCustomer(customerId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/${customerId}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando cliente");
}

export async function setCustomerActive(customerId, isActive) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/${customerId}/active`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  });
  return parseJsonOrThrow(response, "Error actualizando el estado del cliente");
}

export async function createCustomerDatabase(customerId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/${customerId}/databases`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando database del cliente");
}

export async function updateCustomerDatabase(databaseId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/databases/${databaseId}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando database del cliente");
}

export async function listDatabaseCredentials(databaseId) {
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/customers/databases/${databaseId}/credentials`)
  );
  return parseJsonOrThrow(response, "Error listando credenciales");
}

export async function createDatabaseCredential(databaseId, payload) {
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/customers/databases/${databaseId}/credentials`),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
  return parseJsonOrThrow(response, "Error creando credencial");
}

export async function updateDatabaseCredential(credentialId, payload) {
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/customers/databases/credentials/${credentialId}`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }
  );
  return parseJsonOrThrow(response, "Error actualizando credencial");
}

export async function deleteDatabaseCredential(credentialId) {
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/customers/databases/credentials/${credentialId}`),
    { method: "DELETE" }
  );
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = body?.detail || "";
    } catch {
      detail = "";
    }
    throw new Error(detail || `Error eliminando credencial (${response.status})`);
  }
}

export async function createGeotabRule(databaseId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/databases/${databaseId}/rules`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando regla Geotab");
}

export async function createGeotabRuleGroup(databaseId, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/databases/${databaseId}/rule-groups`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando grupo de reglas");
}

export async function resolveGeotabRule(databaseId, ruleId) {
  const query = new URLSearchParams({ rule_id: ruleId.trim() });
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/customers/databases/${databaseId}/rules/resolve?${query.toString()}`)
  );
  return parseJsonOrThrow(response, "Error resolviendo regla Geotab");
}

export async function inspectGeotabRule(ruleRecordId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/rules/${ruleRecordId}/inspection`));
  return parseJsonOrThrow(response, "Error inspeccionando regla Geotab");
}

export async function deleteGeotabRule(ruleId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/rules/${ruleId}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Error eliminando regla Geotab (${response.status})`);
  }
}

export async function deleteGeotabRuleGroup(groupId) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/customers/rule-groups/${groupId}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Error eliminando grupo de reglas (${response.status})`);
  }
}

/* ── Roles & permissions ────────────────────────────────────────────── */

export async function fetchModules() {
  const response = await fetchWithAuth(buildUrl("/api/v1/roles/modules"));
  return parseJsonOrThrow(response, "Error cargando catalogo de modulos");
}

export async function listRoles() {
  const response = await fetchWithAuth(buildUrl("/api/v1/roles"));
  return parseJsonOrThrow(response, "Error listando roles");
}

export async function createRole(payload) {
  const response = await fetchWithAuth(buildUrl("/api/v1/roles"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error creando rol");
}

export async function updateRole(key, payload) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/roles/${encodeURIComponent(key)}`), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error actualizando rol");
}

export async function deleteRole(key) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/roles/${encodeURIComponent(key)}`), {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(`Error eliminando rol (${response.status})`);
  }
}

export async function fetchRolePermissions(key) {
  const response = await fetchWithAuth(buildUrl(`/api/v1/roles/${encodeURIComponent(key)}/permissions`));
  return parseJsonOrThrow(response, "Error cargando permisos del rol");
}

export async function updateRolePermissions(key, matrix) {
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/roles/${encodeURIComponent(key)}/permissions`),
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modules: matrix }),
    }
  );
  return parseJsonOrThrow(response, "Error guardando permisos del rol");
}
