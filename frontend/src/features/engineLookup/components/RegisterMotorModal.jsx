import { useEffect, useState } from "react";

import FileDropzone from "../../../components/FileDropzone";

export default function RegisterMotorModal({
  open,
  loading,
  title = "Registrar motor",
  submitLabel = "Registrar motor",
  initialTechnicalNumber = "",
  lockTechnicalNumber = false,
  onClose,
  onSubmit
}) {
  const [technicalNumber, setTechnicalNumber] = useState(initialTechnicalNumber);
  const [engineName, setEngineName] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [attachmentCpl, setAttachmentCpl] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }
    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");
    setAttachmentFile(null);
    setAttachmentCpl("");
  }, [initialTechnicalNumber, open]);

  if (!open) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({
      technical_number: technicalNumber.trim(),
      engine_name: engineName.trim(),
      attachmentFile,
      attachmentCpl: attachmentCpl.trim()
    });
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Alta de motor</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          Define el nombre visible del motor y asocialo al Technical Engine Configuration # que
          identificara futuras consultas.
        </p>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="motor-technical-number">Technical Engine Configuration #</label>
            <input
              id="motor-technical-number"
              value={technicalNumber}
              onChange={(event) => setTechnicalNumber(event.target.value)}
              placeholder="Ej: D103042BX03"
              readOnly={lockTechnicalNumber}
              required
            />
          </div>

          <div className="form-field">
            <label htmlFor="motor-engine-name">Nombre del motor</label>
            <input
              id="motor-engine-name"
              value={engineName}
              onChange={(event) => setEngineName(event.target.value)}
              placeholder="Ej: ISX15"
              required
            />
          </div>

          <FileDropzone
            id="motor-attachment"
            file={attachmentFile}
            onChange={(nextFile) => {
              setAttachmentFile(nextFile);
              if (!nextFile) setAttachmentCpl("");
            }}
            label="Arrastra o selecciona una imagen o PDF"
            hint="Curvas de torque, potencia o respaldo tecnico. Opcional."
          />

          {attachmentFile ? (
            <div className="form-field">
              <label htmlFor="motor-attachment-cpl">CPL del adjunto</label>
              <input
                id="motor-attachment-cpl"
                value={attachmentCpl}
                onChange={(event) => setAttachmentCpl(event.target.value)}
                placeholder="Ej: 5248"
                required
              />
            </div>
          ) : null}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : submitLabel}
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
