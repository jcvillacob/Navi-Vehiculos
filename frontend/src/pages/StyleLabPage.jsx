import { useState } from "react";

/*
 * Pagina de laboratorio de estilos (solo DEV, no aparece en produccion).
 * Ejercita los controles globales (button/input/select/checkbox/radio) en los
 * contextos que historicamente los rompian: filas flex, grids, toolbars y
 * paginacion. Si un control se ve deforme aqui, el problema es global; si se
 * ve bien aqui y mal en una pagina, el problema es del CSS de esa pagina.
 */
export default function StyleLabPage() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [radio, setRadio] = useState("a");
  const [checks, setChecks] = useState({ uno: true, dos: false });

  return (
    <section className="panel">
      <header className="page-header">
        <div>
          <span className="eyebrow">Dev</span>
          <h2>Laboratorio de estilos</h2>
        </div>
      </header>

      <section className="card style-lab-card">
        <h3>Botones</h3>
        <div className="actions-row">
          <button type="button">Primario (bare)</button>
          <button type="button" className="button-secondary">Secundario</button>
          <button type="button" className="button-secondary button-sm">Compacto</button>
          <button type="button" className="button-lg">Grande</button>
          <button type="button" disabled>Deshabilitado</button>
          <button type="button" className="icon-button">✕</button>
          <button type="button" className="style-lab-custom-btn">
            Clase custom (hover NO debe volverse rojo)
          </button>
        </div>
      </section>

      <section className="card style-lab-card">
        <h3>Inputs de texto</h3>
        <div className="style-lab-grid-2">
          <div className="form-field">
            <label htmlFor="lab-text">En .form-field (debe ocupar todo el ancho)</label>
            <input id="lab-text" placeholder="Texto..." />
          </div>
          <div className="form-field">
            <label htmlFor="lab-ro">Readonly</label>
            <input id="lab-ro" readOnly value="Solo lectura" />
          </div>
        </div>
        <div className="form-field">
          <label htmlFor="lab-area">Textarea</label>
          <textarea id="lab-area" placeholder="Varias lineas..." />
        </div>
        <p className="form-caption">Fila flex: los controles nacen compactos, no se comen la fila.</p>
        <div className="actions-row">
          <input placeholder="Compacto en fila" />
          <input className="control-block" placeholder=".control-block = 100%" style={{ maxWidth: 260 }} />
          <button type="button" className="button-secondary button-sm">Accion</button>
        </div>
      </section>

      <section className="card style-lab-card">
        <h3>Selects</h3>
        <div className="style-lab-grid-2">
          <div className="form-field">
            <label htmlFor="lab-sel">En .form-field (100%, chevron visible)</label>
            <select id="lab-sel" defaultValue="b">
              <option value="a">Opcion A</option>
              <option value="b">Opcion B con texto largo para probar</option>
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="lab-sel-dis">Deshabilitado</label>
            <select id="lab-sel-dis" disabled>
              <option>No editable</option>
            </select>
          </div>
        </div>
        <p className="form-caption">Fila flex: select bare compacto + variante .control-sm.</p>
        <div className="actions-row">
          <select defaultValue="10">
            <option value="10">10</option>
            <option value="25">25</option>
          </select>
          <select className="control-sm" defaultValue="25">
            <option value="10">10</option>
            <option value="25">25</option>
            <option value="50">50</option>
          </select>
          <span className="form-caption">← .control-sm (28px)</span>
        </div>
      </section>

      <section className="card style-lab-card">
        <h3>Checkbox y radio</h3>
        <div className="actions-row">
          {["uno", "dos"].map((k) => (
            <label key={k} className="style-lab-check-label">
              <input
                type="checkbox"
                checked={checks[k]}
                onChange={(e) => setChecks((c) => ({ ...c, [k]: e.target.checked }))}
              />
              Checkbox {k}
            </label>
          ))}
          <label className="style-lab-check-label">
            <input type="checkbox" disabled /> Deshabilitado
          </label>
        </div>
        <div className="actions-row">
          {["a", "b", "c"].map((v) => (
            <label key={v} className="style-lab-check-label">
              <input
                type="radio"
                name="lab-radio"
                value={v}
                checked={radio === v}
                onChange={() => setRadio(v)}
              />
              Radio {v.toUpperCase()}
            </label>
          ))}
        </div>
        <p className="form-caption">
          Dentro de grid (antes se inflaban a 38px / 100% de ancho):
        </p>
        <div className="style-lab-grid-2">
          <label className="style-lab-check-label">
            <input type="checkbox" defaultChecked /> En celda de grid
          </label>
          <label className="style-lab-check-label">
            <input type="radio" name="lab-radio-grid" defaultChecked /> En celda de grid
          </label>
        </div>
      </section>

      <section className="card style-lab-card">
        <h3>Paginacion (replica Rendimientos)</h3>
        <div className="rendimientos-pagination">
          <div className="rendimientos-pagination-info">312 fila(s) en total</div>
          <div className="rendimientos-pagination-controls">
            <span className="rendimientos-pagination-label">Filas por pág.:</span>
            <select
              className="rendimientos-pagination-select"
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
            >
              {[10, 25, 50, 100].map((opt) => <option key={opt} value={opt}>{opt}</option>)}
            </select>
            <button
              type="button"
              className="rendimientos-pagination-btn"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              ‹
            </button>
            <span className="rendimientos-pagination-current">Pág. {page} de 13</span>
            <button
              type="button"
              className="rendimientos-pagination-btn"
              disabled={page >= 13}
              onClick={() => setPage((p) => p + 1)}
            >
              ›
            </button>
          </div>
        </div>
      </section>

      <section className="card style-lab-card">
        <h3>Toolbar mixta (grid de filtros)</h3>
        <div className="style-lab-filterbar">
          <input placeholder="Buscar..." />
          <select defaultValue="">
            <option value="">Todos los clientes</option>
            <option value="x">Cliente X</option>
          </select>
          <select defaultValue="">
            <option value="">Estado</option>
            <option value="ok">OK</option>
          </select>
          <button type="button" className="button-secondary">Limpiar</button>
        </div>
      </section>
    </section>
  );
}
