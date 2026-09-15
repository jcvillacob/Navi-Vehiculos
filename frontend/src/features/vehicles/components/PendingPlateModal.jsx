import { useEffect, useState } from "react";

/**
 * Completa la placa real de un vehiculo que se registro sin placa (consulta
 * por VIN que Fenix no resuelve). El vehiculo ya existe con su cliente,
 * database y motor: aqui solo se reemplaza la placa temporal.
 */
export default function PendingPlateModal({ open, vehicle, loading = false, onClose, onSubmit }) {
  const [plate, setPlate] = useState("");
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setPlate("");
      setError(null);
    }
  }, [open, vehicle?.plate]);

  if (!open || !vehicle) return null;

  const handleSubmit = async (event) => {
    event.preventDefault();
    const normalized = plate.trim().toUpperCase();

    if (!normalized) {
      setError("Escribe la placa.");
      return;
    }
    if (normalized.length > 10) {
      setError("La placa no puede superar 10 caracteres.");
      return;
    }

    setError(null);
    try {
      await onSubmit(normalized);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible asignar la placa");
    }
  };

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Asignar placa">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Pendiente de placa</span>
            <h3>Asignar placa real</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          Registrado como <strong>{vehicle.plate}</strong>
          {vehicle.vin ? (
            <>
              {" "}con VIN <strong>{vehicle.vin}</strong>
            </>
          ) : null}
          . El cliente, la database y el historial se conservan: solo cambia la placa.
        </p>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="pending-plate-input">Placa</label>
            <input
              id="pending-plate-input"
              className="login-input"
              value={plate}
              maxLength={10}
              autoFocus
              onChange={(event) => setPlate(event.target.value.toUpperCase())}
              placeholder="ABC123"
            />
          </div>

          {error ? (
            <div className="notice-banner notice-error">
              <span aria-hidden="true">✕</span>
              <p>{error}</p>
            </div>
          ) : null}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Guardando..." : "Asignar placa"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose} disabled={loading}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
