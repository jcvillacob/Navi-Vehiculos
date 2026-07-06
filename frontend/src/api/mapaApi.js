import { buildUrl, fetchWithAuth } from "./vehicleApi";

export async function fetchMapaTaller({ etag } = {}) {
  const headers = {};
  if (etag) headers["If-None-Match"] = etag;
  const response = await fetchWithAuth(
    buildUrl("/api/v1/mapa/taller"),
    { headers }
  );
  if (response.status === 304) {
    return { snapshot: null, etag, notModified: true };
  }
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = `: ${payload.detail}`;
    } catch {
      detail = "";
    }
    throw new Error(`Error cargando mapa (${response.status})${detail}`);
  }
  const snapshot = await response.json();
  const newEtag =
    response.headers.get("ETag") || (snapshot && snapshot.etag) || null;
  return { snapshot, etag: newEtag, notModified: false };
}

export async function fetchTallerHistory({ plate, zoneId, days } = {}) {
  const params = new URLSearchParams();
  if (plate) params.set("plate", plate);
  if (zoneId) params.set("zone_id", zoneId);
  if (days) params.set("days", String(days));
  const qs = params.toString();
  const response = await fetchWithAuth(
    buildUrl(`/api/v1/mapa/taller/history${qs ? `?${qs}` : ""}`)
  );
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = `: ${payload.detail}`;
    } catch {
      detail = "";
    }
    throw new Error(`Error cargando historico (${response.status})${detail}`);
  }
  return response.json();
}

export async function postManualTallerAction(plate, action, enterTs = null) {
  const body = { plate, action };
  if (enterTs) body.enter_ts = enterTs;
  const response = await fetchWithAuth(buildUrl("/api/v1/mapa/taller/manual"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      if (typeof payload?.detail === "string") detail = `: ${payload.detail}`;
    } catch {
      detail = "";
    }
    throw new Error(`Error (${response.status})${detail}`);
  }
  return response.json();
}
