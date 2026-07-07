const API_BASE = import.meta.env.VITE_API_URL ?? "";

function buildUrl(path) {
  return `${API_BASE}${path}`;
}

async function request(path, init = {}) {
  const response = await fetch(buildUrl(path), {
    credentials: "include",
    ...init,
  });
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
    throw new Error(`Error ${response.status}${detail}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function fetchMyPreferences() {
  return request("/api/v1/me/preferences");
}

export async function fetchMyPreference(key) {
  return request(`/api/v1/me/preferences/${encodeURIComponent(key)}`);
}

export async function updateMyPreference(key, value) {
  return request(`/api/v1/me/preferences/${encodeURIComponent(key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

export async function deleteMyPreference(key) {
  return request(`/api/v1/me/preferences/${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}
