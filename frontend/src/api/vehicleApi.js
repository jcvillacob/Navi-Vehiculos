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
  const response = await fetchWithAuth(buildUrl("/api/v1/rendimientos/calculate"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonOrThrow(response, "Error calculando rendimientos mensuales");
}

// ── Vehicle ───────────────────────────────────────────────────────────────────

export async function lookupVehicle(identifier, { force = false } = {}) {
  const query = new URLSearchParams({ identifier: identifier.trim().toUpperCase() });
  if (force) query.set("force", "true");
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/lookup?${query.toString()}`));
  return parseJsonOrThrow(response, "Error consultando la API");
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

export async function revalidateCustomerGeotab(plate) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetchWithAuth(buildUrl(`/api/v1/vehicle/${normalizedPlate}/revalidate-customer-geotab`), {
    method: "POST",
  });
  return parseJsonOrThrow(response, "Error revalidando Geotab del cliente");
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
