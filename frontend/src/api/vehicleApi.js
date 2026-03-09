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

export async function getVehicleByPlate(plate) {
  const query = new URLSearchParams({ plate: plate.trim().toUpperCase() });
  const response = await fetch(`${API_BASE}/api/v1/vehicle/lookup?${query.toString()}`);
  return parseJsonOrThrow(response, "Error consultando la API");
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
