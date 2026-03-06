import { useState } from "react";

export default function PlateLookupForm({ onSubmit, loading }) {
  const [plate, setPlate] = useState("");

  const handleSubmit = (event) => {
    event.preventDefault();
    const normalized = plate.trim().toUpperCase();
    if (!normalized) {
      return;
    }
    onSubmit(normalized);
  };

  return (
    <form className="lookup-form" onSubmit={handleSubmit}>
      <label htmlFor="plate">Placa</label>
      <div className="lookup-row">
        <input
          id="plate"
          name="plate"
          value={plate}
          onChange={(event) => setPlate(event.target.value.toUpperCase())}
          placeholder="Ej: TLK240"
          minLength={3}
          maxLength={10}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Consultando..." : "Consultar"}
        </button>
      </div>
    </form>
  );
}