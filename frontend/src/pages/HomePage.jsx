import { Link } from "react-router-dom";

export default function HomePage() {
  return (
    <section className="panel">
      <section className="card home-hero">
        <div className="home-hero-copy">
          <span className="eyebrow">Centro de control</span>
          <h2>La forma clara de conectar placas, motores y configuraciones tecnicas.</h2>
          <p>
            Consulta un vehiculo, detecta a que motor pertenece y manten un catalogo operativo con
            contexto real de uso.
          </p>

          <div className="hero-actions">
            <Link className="button hero-link" to="/consulta-motor">
              Consultar vehiculo
            </Link>
            <Link className="button button-secondary hero-link" to="/motores">
              Ver catalogo
            </Link>
            <Link className="button button-secondary hero-link" to="/vehiculos">
              Ver vehiculos
            </Link>
          </div>
        </div>

        <div className="home-hero-panel">
          <div className="hero-stat">
            <span className="eyebrow">Flujo</span>
            <strong>Consulta -> identifica -> registra</strong>
          </div>
          <div className="hero-stat">
            <span className="eyebrow">Dato clave</span>
            <strong>Technical Engine Configuration #</strong>
          </div>
        </div>
      </section>

      <section className="home-grid">
        <article className="card feature-card">
          <span className="eyebrow">Consulta motor</span>
          <h3>Identifica el motor desde la placa</h3>
          <p>
            Obtiene VIN, numero de motor y configuracion tecnica, y valida de inmediato si ese
            motor ya existe en el catalogo.
          </p>
        </article>

        <article className="card feature-card feature-card-accent">
          <span className="eyebrow">Catalogo de motores</span>
          <h3>Mide cuanta flota hay por motor</h3>
          <p>
            Cada motor se convierte en una card limpia, con conteo de vehiculos unicos detectados y
            fecha de ultima actividad.
          </p>
        </article>

        <article className="card feature-card">
          <span className="eyebrow">Registro inteligente</span>
          <h3>Alta directa desde la consulta</h3>
          <p>
            Si un motor todavia no existe, puedes registrarlo sin salir del flujo de consulta y
            dejarlo listo para futuras placas.
          </p>
        </article>
      </section>
    </section>
  );
}
