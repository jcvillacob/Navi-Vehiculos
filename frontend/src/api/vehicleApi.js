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

export async function assignVehicleDatabase(plate, payload) {
  const normalizedPlate = plate.trim().toUpperCase();
  const response = await fetch(`${API_BASE}/api/v1/vehicle/${normalizedPlate}/database`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return parseJsonOrThrow(response, "Error actualizando cliente y database del vehiculo");
}

export async function listCustomers() {
  const response = await fetch(`${API_BASE}/api/v1/customers`);
  return parseJsonOrThrow(response, "Error listando clientes");
}

export async function createCustomer(payload) {
  const response = await fetch(`${API_BASE}/api/v1/customers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return parseJsonOrThrow(response, "Error creando cliente");
}

export async function createCustomerDatabase(customerId, payload) {
  const response = await fetch(`${API_BASE}/api/v1/customers/${customerId}/databases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return parseJsonOrThrow(response, "Error creando database del cliente");
}
