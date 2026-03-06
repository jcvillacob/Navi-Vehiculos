import { useState } from "react";

import LookupDetails from "./LookupDetails";

export default function CreateVehicleModal({
  open,
  loading,
  error,
  lookupResult,
  canContinueToRegister,
  onClose,
  onSearch,
  onRegister
}) {
  const [plate, setPlate] = useState("");
  const [fuelType, setFuelType] = useState("Diesel");

  if (!open) {
    return null;
  }

  const handleSearch = (event) => {
    event.preventDefault();
    const normalizedPlate = plate.trim().toUpperCase();
    if (!normalizedPlate) {
      return;
    }
    onSearch(normalizedPlate, fuelType);
  };

  return (
    <div className="modal-overlay" role="presentation">
      <section className="modal-card" role="dialog" aria-modal="true" aria-label="Nuevo vehiculo">
        <header className="modal-header">
          <h3>Nuevo vehiculo</h3>
          <button type="button" className="icon-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <form className="register-form" onSubmit={handleSearch}>
          <div className="form-field">
            <label htmlFor="new-plate">Placa</label>
            <input
              id="new-plate"
              name="new-plate"
              placeholder="Ej: TLK240"
              minLength={3}
              maxLength={10}
              value={plate}
              onChange={(event) => setPlate(event.target.value.toUpperCase())}
            />
          </div>

          <div className="form-field">
            <label htmlFor="new-fuel-type">Tipo de combustible</label>
            <select
              id="new-fuel-type"
              name="new-fuel-type"
              value={fuelType}
              onChange={(event) => setFuelType(event.target.value)}
            >
              <option value="Diesel">Diesel</option>
            </select>
          </div>

          <div className="actions-row">
            <button type="submit" disabled={loading}>
              {loading ? "Consultando..." : "Consultar motor"}
            </button>
            <button
              type="button"
              className="button-secondary"
              disabled={!canContinueToRegister || loading}
              onClick={onRegister}
            >
              Registrar (temporal)
            </button>
          </div>
        </form>

        {error ? <p className="error">{error}</p> : null}
        <LookupDetails result={lookupResult} />
      </section>
    </div>
  );
}