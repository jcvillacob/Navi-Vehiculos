import { useState } from "react";

import LookupDetails from "../features/engineLookup/components/LookupDetails";
import RegisterMotorModal from "../features/engineLookup/components/RegisterMotorModal";
import { useEngineLookup } from "../features/engineLookup/hooks/useEngineLookup";

export default function EngineLookupPage() {
  const [plate, setPlate] = useState("");
  const [registerMessage, setRegisterMessage] = useState("");
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);

  const {
    loading,
    lookupResult,
    error,
    canRegisterCurrentMotor,
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  } = useEngineLookup();

  const handleSubmit = async (event) => {
    event.preventDefault();
    const normalizedPlate = plate.trim().toUpperCase();
    if (!normalizedPlate) {
      return;
    }

    setRegisterMessage("");
    await searchVehicle(normalizedPlate);
  };

  const handleRegisterMotor = async (payload) => {
    try {
      await registerCurrentMotor(payload);
      setRegisterMessage("Motor registrado y asociado a este Technical Engine Configuration #.");
      setIsRegisterOpen(false);
    } catch (err) {
      setRegisterMessage(err instanceof Error ? err.message : "No fue posible registrar el motor");
    }
  };

  const clearLookup = () => {
    setPlate("");
    setRegisterMessage("");
    resetLookup();
  };

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Lookup por placa</span>
        <h2>Consulta de motor</h2>
        <p>
          Busca un vehiculo, identifica su configuracion tecnica y valida si ya forma parte del
          catalogo de motores.
        </p>
      </header>

      <section className="card lookup-hero">
        <div className="lookup-hero-copy">
          <span className="eyebrow">Tiempo real</span>
          <h3>Convierte una consulta operativa en una decision clara.</h3>
          <p>
            La pantalla te muestra el motor detectado, su configuracion tecnica y si ya pertenece a
            una familia registrada.
          </p>

          <div className="lookup-steps">
            <div className="mini-step">
              <span>01</span>
              <p>Consulta por placa</p>
            </div>
            <div className="mini-step">
              <span>02</span>
              <p>Detecta el TEC#</p>
            </div>
            <div className="mini-step">
              <span>03</span>
              <p>Relaciona el motor</p>
            </div>
          </div>
        </div>

        <form className="lookup-form-panel" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="lookup-plate">Placa</label>
            <input
              id="lookup-plate"
              value={plate}
              onChange={(event) => setPlate(event.target.value.toUpperCase())}
              placeholder="Ej: TLK240"
              minLength={3}
              maxLength={10}
            />
          </div>

          <div className="actions-row">
            <button type="submit" disabled={loading}>
              {loading ? "Consultando..." : "Consultar vehiculo"}
            </button>
            <button type="button" className="button-secondary" onClick={clearLookup}>
              Limpiar
            </button>
          </div>

          <p className="form-caption">
            La clasificacion del motor se actualiza automaticamente cuando la consulta encuentra un
            Technical Engine Configuration # valido.
          </p>
        </form>
      </section>

      {error ? <p className="notice-banner notice-error">{error}</p> : null}
      {registerMessage ? <p className="notice-banner notice-info">{registerMessage}</p> : null}

      {lookupResult ? (
        <section className="lookup-layout">
          <LookupDetails result={lookupResult} />

          <section className="card insight-card">
            <header className="section-heading">
              <span className="eyebrow">Clasificacion</span>
              <h3>Estado del motor</h3>
            </header>

            <div className="state-panel">
              <span
                className={`status ${
                  lookupResult.registered_motor ? "status-ok" : "status-partial"
                }`}
              >
                {lookupResult.registered_motor ? "registrado" : "sin catalogar"}
              </span>

              {lookupResult.registered_motor ? (
                <div className="motor-match">
                  <p className="support-copy">Este vehiculo ya cae dentro de una familia conocida.</p>
                  <div className="motor-chip">
                    <strong>{lookupResult.registered_motor.engine_name}</strong>
                    <span>{lookupResult.registered_motor.technical_number}</span>
                  </div>
                </div>
              ) : (
                <div className="motor-match">
                  <p className="support-copy">
                    Aun no existe un motor registrado para este Technical Engine Configuration #.
                  </p>
                  <button
                    type="button"
                    disabled={!canRegisterCurrentMotor || loading}
                    onClick={() => setIsRegisterOpen(true)}
                  >
                    Registrar este motor
                  </button>
                </div>
              )}
            </div>
          </section>
        </section>
      ) : (
        <section className="empty-lookup-grid">
          <article className="card empty-prompt-card">
            <span className="eyebrow">Antes de consultar</span>
            <h3>Empieza con una placa y deja que la app trace el motor.</h3>
            <p>
              Cuando exista un match con el catalogo, veras el nombre del motor. Si no, podras
              crear el registro desde el mismo flujo.
            </p>
          </article>

          <article className="card soft-note-card">
            <span className="eyebrow">Sugerencia</span>
            <p>
              Usa primero la vista de Motores para registrar familias tecnicas conocidas y acelerar
              futuras clasificaciones.
            </p>
          </article>
        </section>
      )}

      <RegisterMotorModal
        open={isRegisterOpen}
        loading={loading}
        title="Registrar motor desde consulta"
        initialTechnicalNumber={lookupResult?.technical_engine_configuration || ""}
        lockTechnicalNumber
        onClose={() => setIsRegisterOpen(false)}
        onSubmit={handleRegisterMotor}
      />
    </section>
  );
}
