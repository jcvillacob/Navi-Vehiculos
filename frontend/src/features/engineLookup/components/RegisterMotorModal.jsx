import { useEffect, useState } from "react";

import FileDropzone from "../../../components/FileDropzone";

// null = "sin capturar" en el contrato con Portal Clientes; nunca 0.
function parseRpmInput(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

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
  const [governedSpeed, setGovernedSpeed] = useState("");
  const [maxOverspeed, setMaxOverspeed] = useState("");
  const [attachmentFile, setAttachmentFile] = useState(null);
  const [attachmentCpl, setAttachmentCpl] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }
    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");
    setGovernedSpeed("");
    setMaxOverspeed("");
    setAttachmentFile(null);
    setAttachmentCpl("");
  }, [initialTechnicalNumber, open]);

  if (!open) {
    return null;
  }

  const governedValue = parseRpmInput(governedSpeed);
  const overspeedValue = parseRpmInput(maxOverspeed);
  const speedsError =
    governedValue != null && overspeedValue != null && overspeedValue < governedValue
      ? "La sobrevelocidad maxima no puede ser menor que la velocidad gobernada."
      : "";

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (speedsError) return;
    await onSubmit({
      technical_number: technicalNumber.trim(),
      engine_name: engineName.trim(),
      governed_speed_rpm: governedValue,
      max_overspeed_rpm: overspeedValue,
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

          <div className="form-field">
            <label htmlFor="motor-governed-speed">
              Velocidad nominal gobernada sin carga (RPM)
            </label>
            <input
              id="motor-governed-speed"
              type="number"
              min="1"
              step="1"
              inputMode="numeric"
              value={governedSpeed}
              onChange={(event) => setGovernedSpeed(event.target.value)}
              placeholder="Ej: 2100"
            />
          </div>

          <div className="form-field">
            <label htmlFor="motor-max-overspeed">
              Capacidad maxima de sobrevelocidad (RPM)
            </label>
            <input
              id="motor-max-overspeed"
              type="number"
              min="1"
              step="1"
              inputMode="numeric"
              value={maxOverspeed}
              onChange={(event) => setMaxOverspeed(event.target.value)}
              placeholder="Ej: 2250"
            />
            <p className="support-copy">
              Datos de la hoja tecnica del motor. Opcionales.
            </p>
          </div>

          {speedsError ? (
            <div className="notice-banner notice-error">{speedsError}</div>
          ) : null}

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
            <button type="submit" disabled={loading || Boolean(speedsError)}>
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
