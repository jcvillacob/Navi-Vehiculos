import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "";
const PAGE_SIZE = 50;

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("es-CO", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ActionBadge({ action }) {
  const map = {
    CREATE: "status-ok",
    UPDATE: "status-partial",
    DELETE: "status-error",
  };
  return <span className={`status ${map[action] ?? ""}`}>{action}</span>;
}

export default function AuditPage() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  const fetchLogs = async (newOffset) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: PAGE_SIZE + 1, offset: newOffset });
      const r = await fetch(`${API_BASE}/api/v1/audit?${params}`, {
        credentials: "include",
      });
      if (!r.ok) throw new Error("Error cargando logs");
      const data = await r.json();
      setHasMore(data.length > PAGE_SIZE);
      setLogs(data.slice(0, PAGE_SIZE));
      setOffset(newOffset);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs(0);
  }, []);

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Administracion</span>
          <h2>Auditoria</h2>
          <p>Registro de acciones realizadas por los usuarios del sistema.</p>
        </div>
      </header>

      {error && (
        <div className="notice-banner notice-error">
          <span className="notice-icon">✕</span>
          {error}
        </div>
      )}

      {loading ? (
        <article className="card empty-state-card">
          <p>Cargando...</p>
        </article>
      ) : logs.length === 0 ? (
        <article className="card empty-state-card">
          <span className="eyebrow">Sin resultados</span>
          <h3>No hay registros de auditoria aun.</h3>
          <p>Las acciones de creacion, edicion y eliminacion quedan registradas automaticamente.</p>
        </article>
      ) : (
        <div className="vehicles-table-shell">
          <table className="vehicles-table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Usuario</th>
                  <th>Accion</th>
                  <th>Recurso</th>
                  <th>ID Recurso</th>
                  <th>Ruta</th>
                  <th>Estado</th>
                  <th>IP</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td className="meta-text">{formatDate(log.created_at)}</td>
                    <td>
                      <span className="font-medium">{log.username ?? <span className="text-muted">—</span>}</span>
                    </td>
                    <td>
                      <ActionBadge action={log.action} />
                    </td>
                    <td className="font-medium">{log.resource_type}</td>
                    <td className="meta-text">{log.resource_id ?? "—"}</td>
                    <td className="meta-text audit-path">{log.detail?.path ?? "—"}</td>
                    <td className="meta-text">{log.detail?.status_code ?? "—"}</td>
                    <td className="meta-text">{log.ip_address ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      {!loading && (offset > 0 || hasMore) && (
        <div className="audit-pagination">
          <button
            className="button-secondary button-sm"
            disabled={offset === 0}
            onClick={() => fetchLogs(Math.max(0, offset - PAGE_SIZE))}
          >
            ← Anterior
          </button>
          <span className="meta-text">
            Pagina {Math.floor(offset / PAGE_SIZE) + 1}
          </span>
          <button
            className="button-secondary button-sm"
            disabled={!hasMore}
            onClick={() => fetchLogs(offset + PAGE_SIZE)}
          >
            Siguiente →
          </button>
        </div>
      )}
    </section>
  );
}
