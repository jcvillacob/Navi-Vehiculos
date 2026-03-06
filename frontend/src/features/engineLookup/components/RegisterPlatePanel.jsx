import { useState } from "react";

export default function RegisterPlatePanel({
  loading,
  onSearch,
  onRegisterAttempt,
  canContinueToRegister
}) {
  const [plate, setPlate] = useState("");
  const [fuelType, setFuelType] = useState("Diesel");

  const handleSubmit = (event) => {
    event.preventDefault();
    const normalizedPlate = plate.trim().toUpperCase();
    if (!normalizedPlate) {
      return;
    }
    onSearch(normalizedPlate, fuelType);
  };

  return (
    <section className="card action-card">
      <header className="action-header">
        <h3>Registrar nueva placa</h3>
      </header>

      <form className="register-form" onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="plate">Placa</label>
          <input
            id="plate"
            name="plate"
            placeholder="Ej: TLK240"
            minLength={3}
            maxLength={10}
            value={plate}
            onChange={(event) => setPlate(event.target.value.toUpperCase())}
          />
        </div>

        <div className="form-field">
          <label htmlFor="fuelType">Tipo de combustible</label>
          <select
            id="fuelType"
            name="fuelType"
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
            onClick={onRegisterAttempt}
          >
            Registrar placa
          </button>
        </div>

        <p className="helper-text">
          {canContinueToRegister
            ? "Consulta completada. Registro persistente en construccion."
            : "Primero consulta el motor para habilitar el proceso de registro."}
        </p>
      </form>
    </section>
  );
}
