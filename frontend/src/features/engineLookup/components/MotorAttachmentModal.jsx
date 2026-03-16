import { useEffect, useMemo, useState } from "react";

import FileDropzone from "../../../components/FileDropzone";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

function AttachmentIcon({ contentType }) {
  const isPdf = contentType === "application/pdf";

  return (
    <span className="attachment-manager-icon" aria-hidden="true">
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

function formatAttachmentDate(value) {
  if (!value) {
    return "Sin fecha";
  }
  return new Date(value).toLocaleString("es-CO", {
    dateStyle: "medium",
    timeStyle: "short"
  });
}

export default function MotorAttachmentModal({
  open,
  loading,
  motor,
  onClose,
  onCreate,
  onUpdate,
  onDelete
}) {
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [selectedCpl, setSelectedCpl] = useState("");
  const [manualCpl, setManualCpl] = useState("");
  const [editingAttachment, setEditingAttachment] = useState(null);

  const availableCpls = useMemo(() => motor?.available_cpls || [], [motor?.available_cpls]);

  useEffect(() => {
    if (!open) {
      return;
    }
    setAttachmentFile(null);
    setSelectedCpl(availableCpls[0] || "manual");
    setManualCpl("");
    setEditingAttachment(null);
  }, [availableCpls, open]);

  if (!open || !motor) {
    return null;
  }

  const resolvedCpl = selectedCpl === "manual" ? manualCpl.trim() : selectedCpl;

  const handleSelectEdit = (attachment) => {
    setEditingAttachment(attachment);
    setAttachmentFile(null);
    if (availableCpls.includes(attachment.cpl)) {
      setSelectedCpl(attachment.cpl);
      setManualCpl("");
    } else {
      setSelectedCpl("manual");
      setManualCpl(attachment.cpl || "");
    }
  };

  const resetForm = () => {
    setAttachmentFile(null);
    setEditingAttachment(null);
    setSelectedCpl(availableCpls[0] || "manual");
    setManualCpl("");
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!resolvedCpl) {
      return;
    }

    if (editingAttachment) {
      await onUpdate(editingAttachment.id, {
        cpl: resolvedCpl,
        file: attachmentFile
      });
    } else {
      if (!attachmentFile) {
        return;
      }
      await onCreate(motor.id, {
        cpl: resolvedCpl,
        file: attachmentFile
      });
    }

    resetForm();
  };

  const handleDelete = async (attachmentId) => {
    await onDelete(attachmentId);
    if (editingAttachment?.id === attachmentId) {
      resetForm();
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card attachment-manager-modal" role="dialog" aria-modal="true" aria-label="Gestionar adjuntos del motor">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Adjuntos del motor</span>
            <h3>{motor.engine_name}</h3>
            <p className="support-copy">{motor.technical_number}</p>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <section className="attachment-manager-layout">
          <form className="attachment-manager-form" onSubmit={handleSubmit}>
            <div className="attachment-manager-headline">
              <span className="eyebrow">{editingAttachment ? "Editar adjunto" : "Nuevo adjunto"}</span>
              <h4>{editingAttachment ? "Actualiza CPL o reemplaza el archivo" : "Carga una curva o respaldo tecnico"}</h4>
            </div>

            <div className="form-field">
              <label htmlFor="motor-attachment-cpl-select">CPL</label>
              <select
                id="motor-attachment-cpl-select"
                value={selectedCpl}
                onChange={(event) => setSelectedCpl(event.target.value)}
              >
                {availableCpls.map((cpl) => (
                  <option key={cpl} value={cpl}>
                    {cpl}
                  </option>
                ))}
                <option value="manual">Ingresar otro CPL</option>
              </select>
            </div>

            {selectedCpl === "manual" ? (
              <div className="form-field">
                <label htmlFor="motor-attachment-cpl-manual">CPL manual</label>
                <input
                  id="motor-attachment-cpl-manual"
                  value={manualCpl}
                  onChange={(event) => setManualCpl(event.target.value)}
                  placeholder="Ej: 5248"
                  required
                />
              </div>
            ) : null}

            <FileDropzone
              id="motor-existing-attachment"
              file={attachmentFile}
              onChange={(nextFile) => setAttachmentFile(nextFile)}
              label={editingAttachment ? "Arrastra o selecciona para reemplazar" : "Arrastra o selecciona una imagen o PDF"}
              hint={editingAttachment ? "Si no eliges archivo, solo se actualiza el CPL." : "Curvas de torque, potencia o respaldo tecnico."}
            />

            <div className="actions-row modal-actions">
              <button
                type="submit"
                disabled={loading || !resolvedCpl || (!editingAttachment && !attachmentFile)}
              >
                {loading ? "Guardando..." : editingAttachment ? "Actualizar adjunto" : "Guardar adjunto"}
              </button>
              <button type="button" className="button-secondary" onClick={resetForm}>
                Limpiar
              </button>
            </div>
          </form>

          <section className="attachment-manager-list">
            <header className="attachment-manager-headline">
              <span className="eyebrow">Registrados</span>
              <h4>{motor.attachments?.length ? `${motor.attachments.length} adjuntos cargados` : "Sin adjuntos todavia"}</h4>
            </header>

            {motor.attachments?.length ? (
              <div className="attachment-records">
                {motor.attachments.map((attachment) => (
                  <article className="attachment-record-card" key={attachment.id}>
                    <div className="attachment-record-main">
                      <AttachmentIcon contentType={attachment.content_type} />
                      <div>
                        <strong>{attachment.original_filename}</strong>
                        <p>CPL {attachment.cpl || "Sin CPL"}</p>
                        <small>Actualizado {formatAttachmentDate(attachment.updated_at)}</small>
                      </div>
                    </div>

                    <div className="attachment-record-actions">
                      <a
                        className="button-secondary button-sm"
                        href={`${API_BASE}${attachment.download_url}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Abrir
                      </a>
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() => handleSelectEdit(attachment)}
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        className="button-secondary button-sm"
                        onClick={() => handleDelete(attachment.id)}
                        disabled={loading}
                      >
                        Eliminar
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="support-copy">Aun no hay archivos para este motor.</p>
            )}
          </section>
        </section>
      </section>
    </div>
  );
}
