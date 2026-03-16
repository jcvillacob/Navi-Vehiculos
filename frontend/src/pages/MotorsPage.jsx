import { useEffect, useMemo, useState } from "react";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import MotorAttachmentModal from "../features/engineLookup/components/MotorAttachmentModal";
import RegisterMotorModal from "../features/engineLookup/components/RegisterMotorModal";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

function AttachmentIcon({ contentType }) {
  const isPdf = contentType === "application/pdf";

  return (
    <span className="attachment-icon" aria-hidden="true">
      {isPdf ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M8 13h8" />
          <path d="M8 17h5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.2" />
          <path d="m21 15-4.5-4.5L8 19" />
        </svg>
      )}
    </span>
  );
}

function formatLastSeen(value) {
  if (!value) {
    return "Sin consultas";
  }
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

/* ── Edit Motor Modal ──────────────────────────────────────────────── */
function EditMotorModal({ motor, loading, onClose, onSubmit }) {
  const [engineName, setEngineName] = useState(motor.engine_name);

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({ engine_name: engineName.trim() });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Editar motor">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Editar</span>
            <h3>{motor.technical_number}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            &#10005;
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="edit-motor-name">Nombre del motor</label>
            <input
              id="edit-motor-name"
              value={engineName}
              onChange={(event) => setEngineName(event.target.value)}
              required
              autoFocus
            />
          </div>

          <div className="form-field">
            <label>Technical Engine Configuration #</label>
            <input value={motor.technical_number} readOnly />
          </div>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || !engineName.trim()}>
              {loading ? "Guardando..." : "Guardar cambios"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

/* ── Main Page ─────────────────────────────────────────────────────── */
export default function MotorsPage() {
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const [selectedMotorForUpload, setSelectedMotorForUpload] = useState(null);
  const [editingMotor, setEditingMotor] = useState(null);
  const [search, setSearch] = useState("");
  const { toasts, pushToast } = useToasts();

  const {
    loading,
    motors,
    error,
    registerMotor,
    editMotor,
    uploadAttachment,
    updateAttachment,
    deleteAttachment
  } = useMotorsCatalog();

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const totals = useMemo(() => {
    return motors.reduce(
      (acc, motor) => {
        acc.motors += 1;
        acc.vehicles += motor.vehicle_count || 0;
        acc.attachments += motor.attachments?.length || 0;
        return acc;
      },
      { motors: 0, vehicles: 0, attachments: 0 }
    );
  }, [motors]);

  const filteredMotors = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return motors;
    return motors.filter(
      (motor) =>
        motor.engine_name.toLowerCase().includes(query) ||
        motor.technical_number.toLowerCase().includes(query) ||
        (motor.available_cpls || []).some((cpl) => cpl.toLowerCase().includes(query))
    );
  }, [motors, search]);

  const activeMotorForAttachments = useMemo(() => {
    if (!selectedMotorForUpload) {
      return null;
    }
    return motors.find((motor) => motor.id === selectedMotorForUpload.id) || selectedMotorForUpload;
  }, [motors, selectedMotorForUpload]);

  const handleSubmit = async (payload) => {
    try {
      await registerMotor(payload);
      pushToast("success", "Motor registrado en el catalogo.");
      setIsRegisterOpen(false);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible registrar el motor");
    }
  };

  const handleEditMotor = async (payload) => {
    try {
      await editMotor(editingMotor.id, payload);
      setEditingMotor(null);
      pushToast("success", "Motor actualizado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar el motor");
    }
  };

  const handleCreateAttachment = async (motorId, payload) => {
    try {
      await uploadAttachment(motorId, payload);
      pushToast("success", "Adjunto cargado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible subir el adjunto");
    }
  };

  const handleUpdateAttachment = async (attachmentId, payload) => {
    try {
      await updateAttachment(attachmentId, payload);
      pushToast("success", "Adjunto actualizado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible actualizar el adjunto");
    }
  };

  const handleDeleteAttachment = async (attachmentId) => {
    try {
      await deleteAttachment(attachmentId);
      pushToast("success", "Adjunto eliminado.");
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible eliminar el adjunto");
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Catalogo tecnico</span>
          <h2>Motores</h2>
          <p>
            Familias de motor registradas con cobertura real sobre la flota consultada.
          </p>
        </div>

        <button type="button" onClick={() => setIsRegisterOpen(true)}>
          + Motor
        </button>
      </header>

      <section className="motor-overview-grid">
        <article className="card metric-card">
          <span className="eyebrow">Motores</span>
          <strong>{totals.motors}</strong>
          <p>Familias tecnicas activas</p>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">Cobertura</span>
          <strong>{totals.vehicles}</strong>
          <p>Vehiculos unicos asociados</p>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Adjuntos</span>
          <strong>{totals.attachments}</strong>
          <p>Archivos cargados en el catalogo</p>
        </article>
      </section>

      <div className="motors-search-bar">
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Buscar por nombre, TEC# o CPL..."
          className="motors-search-input"
        />
        {search ? (
          <button
            type="button"
            className="button-secondary button-sm"
            onClick={() => setSearch("")}
          >
            Limpiar
          </button>
        ) : null}
      </div>

      <ToastStack toasts={toasts} />

      <section className="motor-cards-grid">
        {loading && motors.length === 0 ? <p className="notice-banner notice-soft">Cargando motores...</p> : null}

        {!loading && motors.length === 0 ? (
          <article className="card empty-state-card">
            <span className="eyebrow">Sin registros</span>
            <h3>El catalogo aun esta vacio.</h3>
            <p>
              Registra el primer motor con su Technical Engine Configuration # para empezar a
              agrupar vehiculos automaticamente.
            </p>
          </article>
        ) : null}

        {!loading && motors.length > 0 && filteredMotors.length === 0 ? (
          <p className="support-copy">Sin resultados para "{search}".</p>
        ) : null}

        {filteredMotors.map((motor) => (
          <article className="card motor-card" key={motor.id}>
            <div className="motor-card-top">
              <span className="motor-count">{motor.vehicle_count} vehiculos</span>
              <span className="status status-ok">activo</span>
            </div>

            <div className="motor-card-heading">
              <h3>{motor.engine_name}</h3>
              <div className="motor-card-heading-row">
                <p className="motor-technical-number">{motor.technical_number}</p>
                <div className="motor-card-heading-actions">
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => setEditingMotor(motor)}
                    title="Editar motor"
                  >
                    &#9998;
                  </button>
                </div>
              </div>
            </div>

            {(motor.available_cpls || []).length > 0 ? (
              <div className="motor-cpls">
                {motor.available_cpls.map((cpl) => (
                  <span className="cpl-chip" key={cpl}>CPL {cpl}</span>
                ))}
              </div>
            ) : null}

            <div className="motor-card-meta">
              <div>
                <span>Ultima deteccion</span>
                <strong>{formatLastSeen(motor.last_seen_at)}</strong>
              </div>
              <div>
                <span>Creado</span>
                <strong>{formatLastSeen(motor.created_at)}</strong>
              </div>
            </div>

            <div className="motor-card-attachments">
              <div className="motor-attachments-header">
                <span>Adjuntos ({motor.attachments?.length || 0})</span>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => setSelectedMotorForUpload(motor)}
                  title="Gestionar adjuntos"
                >
                  &#8943;
                </button>
              </div>

              {motor.attachments?.length ? (
                <div className="attachment-list">
                  {motor.attachments.map((attachment) => (
                    <a
                      key={attachment.id}
                      className="attachment-chip"
                      href={`${API_BASE}${attachment.download_url}`}
                      target="_blank"
                      rel="noreferrer"
                      title={attachment.original_filename}
                      aria-label={`Abrir ${attachment.original_filename}`}
                    >
                      <AttachmentIcon contentType={attachment.content_type} />
                      <span className="attachment-chip-cpl">{attachment.cpl || "—"}</span>
                    </a>
                  ))}
                </div>
              ) : (
                <p className="support-copy">Sin adjuntos.</p>
              )}
            </div>
          </article>
        ))}
      </section>

      <RegisterMotorModal
        open={isRegisterOpen}
        loading={loading}
        title="Registrar nuevo motor"
        onClose={() => setIsRegisterOpen(false)}
        onSubmit={handleSubmit}
      />

      <MotorAttachmentModal
        open={Boolean(selectedMotorForUpload)}
        loading={loading}
        motor={activeMotorForAttachments}
        onClose={() => setSelectedMotorForUpload(null)}
        onCreate={handleCreateAttachment}
        onUpdate={handleUpdateAttachment}
        onDelete={handleDeleteAttachment}
      />

      {editingMotor ? (
        <EditMotorModal
          motor={editingMotor}
          loading={loading}
          onClose={() => setEditingMotor(null)}
          onSubmit={handleEditMotor}
        />
      ) : null}
    </section>
  );
}
