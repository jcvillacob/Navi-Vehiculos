import { useEffect, useMemo, useState } from "react";

// Bandas del eje de RPM, en orden ascendente (backend: rule_bands.RPM_RANGE_BANDS).
// 'ralenti' no esta: en modo por RPM el ralenti se deriva de velocidad 0.
const BANDS = [
  { key: "rango_bajo", label: "Rango bajo" },
  { key: "rango_economico", label: "Rango economico" },
  { key: "rango_balanceado", label: "Rango balanceado" },
  { key: "rango_potencia", label: "Rango potencia" },
  { key: "rango_potencia_ineficiente", label: "Potencia ineficiente" },
  { key: "exceso_rpm", label: "Exceso de RPM" }
];

// Cortes del reporte de referencia; solo se usan como punto de partida al
// configurar un motor que aun no tiene nada.
const DEFAULT_EDGES = ["600", "1100", "1450", "1800", "2300", "2750", ""];

function edgesFromBands(bands) {
  if (!bands || bands.length !== BANDS.length) {
    return [...DEFAULT_EDGES];
  }
  const byBand = new Map(bands.map((band) => [band.band, band]));
  const edges = BANDS.map((band) => {
    const found = byBand.get(band.key);
    return found ? String(found.rpm_min) : "";
  });
  const last = byBand.get(BANDS[BANDS.length - 1].key);
  edges.push(last && last.rpm_max != null ? String(last.rpm_max) : "");
  return edges;
}

/**
 * Los rangos se editan como CORTES (7 numeros), no como pares min/max: asi es
 * imposible dejar huecos o solapes, que es justo lo que el backend rechaza.
 * El ultimo corte puede quedar vacio = banda superior sin limite.
 */
export default function MotorRpmBandsModal({ motor, loading, onClose, onSubmit }) {
  const [edges, setEdges] = useState(() => edgesFromBands(motor?.rpm_bands));

  useEffect(() => {
    setEdges(edgesFromBands(motor?.rpm_bands));
  }, [motor]);

  const hasConfig = (motor?.rpm_bands || []).length > 0;

  const validationError = useMemo(() => {
    const required = edges.slice(0, BANDS.length);
    if (required.some((value) => String(value).trim() === "")) {
      return "Completa todos los cortes de RPM.";
    }
    const numbers = required.map((value) => Number(value));
    if (numbers.some((value) => !Number.isFinite(value) || value < 0)) {
      return "Los cortes deben ser numeros mayores o iguales a cero.";
    }
    const last = String(edges[BANDS.length]).trim();
    if (last !== "") {
      const lastNumber = Number(last);
      if (!Number.isFinite(lastNumber)) {
        return "El limite superior debe ser un numero o quedar vacio.";
      }
      numbers.push(lastNumber);
    }
    for (let index = 1; index < numbers.length; index += 1) {
      if (numbers[index] <= numbers[index - 1]) {
        return "Cada corte debe ser mayor que el anterior.";
      }
    }
    return "";
  }, [edges]);

  const handleEdgeChange = (index, value) => {
    setEdges((prev) => prev.map((edge, position) => (position === index ? value : edge)));
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (validationError) return;
    const bands = BANDS.map((band, index) => ({
      band: band.key,
      rpm_min: Number(edges[index]),
      rpm_max:
        index === BANDS.length - 1
          ? String(edges[BANDS.length]).trim() === ""
            ? null
            : Number(edges[BANDS.length])
          : Number(edges[index + 1])
    }));
    await onSubmit(bands);
  };

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Rangos de RPM">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Rangos por RPM</span>
            <h3>{motor.engine_name}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            &#10005;
          </button>
        </header>

        <p className="support-copy">
          Solo se usan en los clientes configurados como <strong>Rangos por RPM</strong>. Sin esta
          configuracion, Portal Clientes no calcula las bandas de los vehiculos de este motor.
        </p>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="rpm-bands-grid">
            {BANDS.map((band, index) => (
              <div className="rpm-band-row" key={band.key}>
                <span className="rpm-band-label">{band.label}</span>
                <div className="rpm-band-inputs">
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={edges[index]}
                    onChange={(event) => handleEdgeChange(index, event.target.value)}
                    aria-label={`RPM minimo de ${band.label}`}
                    required
                  />
                  <span className="rpm-band-separator">a</span>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={edges[index + 1]}
                    onChange={(event) => handleEdgeChange(index + 1, event.target.value)}
                    placeholder={index === BANDS.length - 1 ? "sin limite" : ""}
                    aria-label={`RPM maximo de ${band.label}`}
                  />
                </div>
              </div>
            ))}
          </div>

          {validationError ? (
            <div className="notice-banner notice-error">{validationError}</div>
          ) : null}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading || Boolean(validationError)}>
              {loading ? "Guardando..." : "Guardar rangos"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
            {hasConfig ? (
              <button
                type="button"
                className="button-danger-outline"
                disabled={loading}
                onClick={() => onSubmit([])}
              >
                Borrar configuracion
              </button>
            ) : null}
          </div>
        </form>
      </section>
    </div>
  );
}
