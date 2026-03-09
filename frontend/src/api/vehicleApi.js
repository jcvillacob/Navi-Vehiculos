const API_BASE = import.meta.env.VITE_API_URL ?? "";

async function parseJsonOrThrow(response, fallbackMessage) {
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = payload?.detail ? `: ${payload.detail}` : "";
    } catch {
      detail = "";
    }
    throw new Error(`${fallbackMessage} (${response.status})${detail}`);
  }
  return response.json();
}

export async function lookupVehicle(identifier) {
  const query = new URLSearchParams({ identifier: identifier.trim().toUpperCase() });
  const response = await fetch(`${API_BASE}/api/v1/vehicle/lookup?${query.toString()}`);
  return parseJsonOrThrow(response, "Error consultando la API");
}

export async function listVehicleAssignments(search = "") {
  const normalizedSearch = search.trim().toUpperCase();
  const query = new URLSearchParams();
  if (normalizedSearch) {
    query.set("search", normalizedSearch);
  }

  const suffix = query.toString() ? `?${query.toString()}` : "";
  const response = await fetch(`${API_BASE}/api/v1/vehicle${suffix}`);
  return parseJsonOrThrow(response, "Error listando vehiculos");
}

export async function listMotors() {
  const response = await fetch(`${API_BASE}/api/v1/motors`);
  return parseJsonOrThrow(response, "Error listando motores");
}

export async function createMotor(payload) {
  const response = await fetch(`${API_BASE}/api/v1/motors`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return parseJsonOrThrow(response, "Error creando motor");
}
