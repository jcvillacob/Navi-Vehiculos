import { useMemo, useState } from "react";

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
    return "Sin consultas todavia";
  }
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

export default function MotorsPage() {
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const [selectedMotorForUpload, setSelectedMotorForUpload] = useState(null);
  const [message, setMessage] = useState("");
  const { loading, motors, error, registerMotor, uploadAttachment, updateAttachment, deleteAttachment } =
    useMotorsCatalog();

  const totals = useMemo(() => {
    return motors.reduce(
      (acc, motor) => {
        acc.motors += 1;
        acc.vehicles += motor.vehicle_count || 0;
        return acc;
      },
      { motors: 0, vehicles: 0 }
    );
  }, [motors]);

  const activeMotorForAttachments = useMemo(() => {
    if (!selectedMotorForUpload) {
      return null;
    }
    return motors.find((motor) => motor.id === selectedMotorForUpload.id) || selectedMotorForUpload;
  }, [motors, selectedMotorForUpload]);

  const handleSubmit = async (payload) => {
    try {
      await registerMotor(payload);
      setMessage("Motor registrado en el catalogo.");
      setIsRegisterOpen(false);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible registrar el motor");
    }
  };

  const handleCreateAttachment = async (motorId, payload) => {
    try {
      await uploadAttachment(motorId, payload);
      setMessage("Adjunto cargado.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible subir el adjunto");
    }
  };

  const handleUpdateAttachment = async (attachmentId, payload) => {
    try {
      await updateAttachment(attachmentId, payload);
      setMessage("Adjunto actualizado.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible actualizar el adjunto");
    }
  };

  const handleDeleteAttachment = async (attachmentId) => {
    try {
      await deleteAttachment(attachmentId);
      setMessage("Adjunto eliminado.");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "No fue posible eliminar el adjunto");
    }
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Catalogo tecnico</span>
          <h2>Motores</h2>
          <p>
            Una vista limpia de todas las familias de motor registradas, con contexto operativo y
            cobertura real sobre la flota consultada.
          </p>
        </div>

        <button type="button" onClick={() => setIsRegisterOpen(true)}>
          Registrar nuevo motor
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
          <span className="eyebrow">Operacion</span>
          <strong>Catalogo vivo</strong>
          <p>Cada lookup exitoso alimenta el conteo por motor sin friccion manual.</p>
        </article>
      </section>

      {error ? <p className="notice-banner notice-error">{error}</p> : null}
      {message ? <p className="notice-banner notice-info">{message}</p> : null}

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

        {motors.map((motor) => (
          <article className="card motor-card" key={motor.id}>
            <div className="motor-card-top">
              <span className="motor-count">{motor.vehicle_count} vehiculos unicos</span>
              <span className="status status-ok">activo</span>
            </div>

            <div className="motor-card-heading">
              <h3>{motor.engine_name}</h3>
              <p className="motor-technical-number">{motor.technical_number}</p>
            </div>

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
                <span>Adjuntos</span>
                <button
                  type="button"
                  className="button-secondary button-sm"
                  onClick={() => setSelectedMotorForUpload(motor)}
                >
                  Gestionar
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
                      title={`${attachment.original_filename} | CPL ${attachment.cpl || "Sin CPL"}`}
                      aria-label={`Abrir adjunto ${attachment.original_filename} del CPL ${attachment.cpl || "sin cpl"} en otra pestana`}
                    >
                      <AttachmentIcon contentType={attachment.content_type} />
                    </a>
                  ))}
                </div>
              ) : (
                <p className="support-copy">Sin adjuntos todavia.</p>
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
    </section>
  );
}
