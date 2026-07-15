import { useCallback, useEffect, useState } from "react";

import { createBackup, fetchBackups } from "../api/vehicleApi";

function formatBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

export default function BackupsPage() {
  const [backups, setBackups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadBackups = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setBackups(await fetchBackups());
    } catch (err) {
      setError(err.message || "No fue posible cargar los backups.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadBackups();
  }, [loadBackups]);

  const handleCreate = async () => {
    setCreating(true);
    setError("");
    setMessage("");
    try {
      const backup = await createBackup();
      setMessage(`Backup creado: ${backup.filename}`);
      await loadBackups();
    } catch (err) {
      setError(err.message || "No fue posible crear el backup.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Gestion</span>
          <h2>Backups PostgreSQL</h2>
          <p>
            Copias diarias automáticas y respaldos manuales de la base de datos.
            Se conservan según la política configurada en el servidor.
          </p>
        </div>
        <div className="page-header-actions">
          <button type="button" onClick={handleCreate} disabled={creating}>
            {creating ? "Creando backup..." : "Crear backup ahora"}
          </button>
        </div>
      </header>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}
      {message ? <div className="notice-banner notice-info">{message}</div> : null}

      <article className="card">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Historial</span>
            <h3>Copias disponibles</h3>
          </div>
          <span className="status status-soft">{backups.length} copias</span>
        </div>

        {loading ? (
          <p>Cargando backups...</p>
        ) : backups.length === 0 ? (
          <div className="empty-state-card">
            <span className="eyebrow">Sin copias</span>
            <h3>Aún no hay backups registrados.</h3>
            <p>Usa “Crear backup ahora” para generar la primera copia.</p>
          </div>
        ) : (
          <div className="vehicles-table-shell">
            <table className="vehicles-table">
              <thead>
                <tr>
                  <th>Archivo</th>
                  <th>Fecha</th>
                  <th>Tipo</th>
                  <th>Tamaño</th>
                  <th>SHA-256</th>
                </tr>
              </thead>
              <tbody>
                {backups.map((backup) => (
                  <tr key={backup.filename}>
                    <td className="font-medium">{backup.filename}</td>
                    <td className="meta-text">{formatDate(backup.created_at)}</td>
                    <td>
                      <span className={`status ${backup.trigger === "manual" ? "status-partial" : "status-ok"}`}>
                        {backup.trigger === "manual" ? "Manual" : "Diario"}
                      </span>
                    </td>
                    <td className="meta-text">{formatBytes(backup.size_bytes)}</td>
                    <td className="meta-text" title={backup.sha256}>
                      {backup.sha256.slice(0, 16)}…
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  );
}
