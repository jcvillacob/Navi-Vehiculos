const API_BASE = import.meta.env.VITE_API_URL ?? "";

export async function getVehicleByPlate(plate) {
  const query = new URLSearchParams({ plate: plate.trim().toUpperCase() });
  const response = await fetch(`${API_BASE}/api/v1/vehicle/lookup?${query.toString()}`);

  if (!response.ok) {
    throw new Error(`Error consultando la API (${response.status})`);
  }

  return response.json();
}