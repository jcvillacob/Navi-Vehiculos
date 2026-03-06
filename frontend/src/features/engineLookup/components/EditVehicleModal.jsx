import { useEffect, useState } from "react";

export default function EditVehicleModal({ open, vehicle, onClose, onSave }) {
  const [form, setForm] = useState({
    plate: "",
    engineName: "",
    registeredAt: "",
    engineType: "Diesel"
  });

  useEffect(() => {
    if (!vehicle) {
      return;
    }

    setForm({
      plate: vehicle.plate,
      engineName: vehicle.engineName || "",
      registeredAt: vehicle.registeredAt,
      engineType: vehicle.engineType
    });
  }, [vehicle]);

  if (!open || !vehicle) {
    return null;
  }

  const handleSubmit = (event) => {
    event.preventDefault();
    onSave(vehicle.id, {
      plate: form.plate.trim().toUpperCase(),
      engineName: form.engineName.trim(),
      registeredAt: form.registeredAt,
      engineType: form.engineType
    });
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="modal-card" role="dialog" aria-modal="true" aria-label="Editar vehiculo">
        <header className="modal-header">
          <h3>Editar vehiculo</h3>
          <button type="button" className="icon-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="edit-plate">Placa</label>
            <input
              id="edit-plate"
              value={form.plate}
              onChange={(event) => setForm((prev) => ({ ...prev, plate: event.target.value }))}
              maxLength={10}
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-engine-name">Nombre Motor</label>
            <input
              id="edit-engine-name"
              value={form.engineName}
              onChange={(event) => setForm((prev) => ({ ...prev, engineName: event.target.value }))}
              placeholder="Ej: ISX15"
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-date">Fecha de registro</label>
            <input
              id="edit-date"
              type="date"
              value={form.registeredAt}
              onChange={(event) => setForm((prev) => ({ ...prev, registeredAt: event.target.value }))}
            />
          </div>

          <div className="form-field">
            <label htmlFor="edit-engine-type">Tipo de motor</label>
            <select
              id="edit-engine-type"
              value={form.engineType}
              onChange={(event) => setForm((prev) => ({ ...prev, engineType: event.target.value }))}
            >
              <option value="Diesel">Diesel</option>
            </select>
          </div>

          <div className="actions-row">
            <button type="submit">Guardar cambios</button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}