import { useEffect, useMemo, useRef, useState } from "react";

export default function ColumnSelectorDrawer({
  open,
  title = "Columnas visibles",
  description = "Selecciona las columnas que quieres ver en la tabla y aplica los cambios.",
  columns = [],
  visibleKeys,
  onApply,
  onClose
}) {
  const [pending, setPending] = useState(() => new Set(visibleKeys || []));
  const [query, setQuery] = useState("");
  const panelRef = useRef(null);

  useEffect(() => {
    if (open) {
      setPending(new Set(visibleKeys || []));
      setQuery("");
    }
  }, [open, visibleKeys]);

  useEffect(() => {
    if (!open) return undefined;
    function handleKey(e) {
      if (e.key === "Escape") onClose?.();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return columns.map((c, i) => ({ col: c, index: i + 1 }));
    return columns
      .map((c, i) => ({ col: c, index: i + 1 }))
      .filter(({ col }) => col.label.toLowerCase().includes(q) || col.key.toLowerCase().includes(q));
  }, [columns, query]);

  const totalSelected = pending.size;
  const totalColumns = columns.length;
  const allSelected = totalSelected === totalColumns;
  const noneSelected = totalSelected === 0;

  const toggle = (key) => {
    setPending((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAll = () => setPending(new Set(columns.map((c) => c.key)));
  const selectNone = () => setPending(new Set());

  const handleApply = () => {
    onApply?.(pending);
    onClose?.();
  };

  const handleCancel = () => {
    setPending(new Set(visibleKeys || []));
    onClose?.();
  };

  return (
    <div
      className={`column-drawer-overlay ${open ? "is-open" : ""}`}
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleCancel();
      }}
      aria-hidden={!open}
    >
      <aside
        ref={panelRef}
        className={`column-drawer ${open ? "is-open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className="column-drawer-header">
          <div className="column-drawer-header-text">
            <span className="eyebrow">Personalizar tabla</span>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
          <button
            type="button"
            className="icon-button column-drawer-close"
            onClick={handleCancel}
            aria-label="Cerrar panel"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </svg>
          </button>
        </header>

        <div className="column-drawer-counter">
          <div className="column-drawer-counter-row">
            <span className="column-drawer-counter-label">Seleccionadas</span>
            <span className="column-drawer-counter-value">
              <strong>{totalSelected}</strong>
              <span className="column-drawer-counter-divider">/</span>
              <span>{totalColumns}</span>
            </span>
          </div>
          <div className="column-drawer-progress">
            <div
              className="column-drawer-progress-fill"
              style={{ width: `${totalColumns ? (totalSelected / totalColumns) * 100 : 0}%` }}
            />
          </div>
        </div>

        <div className="column-drawer-toolbar">
          <div className="column-drawer-search">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m21 21-4.3-4.3" />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Buscar columna..."
              aria-label="Buscar columna"
            />
            {query && (
              <button
                type="button"
                className="column-drawer-search-clear"
                onClick={() => setQuery("")}
                aria-label="Limpiar busqueda"
              >
                ✕
              </button>
            )}
          </div>
          <div className="column-drawer-quickactions">
            <button
              type="button"
              className="column-drawer-quickaction"
              onClick={selectAll}
              disabled={allSelected}
            >
              Todas
            </button>
            <span className="column-drawer-quickaction-divider" aria-hidden />
            <button
              type="button"
              className="column-drawer-quickaction"
              onClick={selectNone}
              disabled={noneSelected}
            >
              Ninguna
            </button>
          </div>
        </div>

        <div className="column-drawer-list">
          {filtered.length === 0 ? (
            <div className="column-drawer-empty">
              <span>Sin coincidencias para "{query}"</span>
            </div>
          ) : (
            filtered.map(({ col, index }) => {
              const checked = pending.has(col.key);
              return (
                <label
                  key={col.key}
                  className={`column-drawer-option ${checked ? "is-checked" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(col.key)}
                    aria-label={`Mostrar columna ${col.label}`}
                  />
                  <span className="column-drawer-option-mark" aria-hidden>
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </span>
                  <span className="column-drawer-option-body">
                    <span className="column-drawer-option-index">{String(index).padStart(2, "0")}</span>
                    <span className="column-drawer-option-text">
                      <span className="column-drawer-option-label">{col.label}</span>
                      <span className="column-drawer-option-key">{col.key}</span>
                    </span>
                  </span>
                </label>
              );
            })
          )}
        </div>

        <footer className="column-drawer-footer">
          <button type="button" className="button-secondary button-sm" onClick={handleCancel}>
            Cancelar
          </button>
          <button
            type="button"
            className="button button-sm"
            onClick={handleApply}
            disabled={noneSelected}
            title={noneSelected ? "Selecciona al menos una columna" : "Aplicar cambios"}
          >
            Aplicar cambios
          </button>
        </footer>
      </aside>
    </div>
  );
}
