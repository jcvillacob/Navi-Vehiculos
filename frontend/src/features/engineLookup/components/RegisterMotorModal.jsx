import { useEffect, useState } from "react";

export default function RegisterMotorModal({
  open,
  loading,
  title = "Registrar motor",
  initialTechnicalNumber = "",
  lockTechnicalNumber = false,
  onClose,
  onSubmit
}) {
  const [technicalNumber, setTechnicalNumber] = useState(initialTechnicalNumber);
  const [engineName, setEngineName] = useState("");

  useEffect(() => {
    if (!open) {
      return;
    }
    setTechnicalNumber(initialTechnicalNumber || "");
    setEngineName("");
  }, [initialTechnicalNumber, open]);

  if (!open) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    await onSubmit({
      technical_number: technicalNumber.trim(),
      engine_name: engineName.trim()
    });
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-header">
          <div>
            <span className="eyebrow">Alta de motor</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="icon-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy">
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

          <div className="actions-row">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : "Registrar motor"}
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
