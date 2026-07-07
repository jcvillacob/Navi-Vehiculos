import { useEffect, useMemo, useRef, useState } from "react";

export default function ColumnSelectorDrawer({
  open,
  title = "Columnas visibles",
  description = "Selecciona las columnas que quieres ver en la tabla, reordenalas y aplica los cambios.",
  columns = [],
  visibleKeys,
  onApply,
  onClose
}) {
  const [order, setOrder] = useState(() => (Array.isArray(visibleKeys) && visibleKeys.length > 0 ? visibleKeys : columns.map((c) => c.key)));
  const [selected, setSelected] = useState(() => new Set(Array.isArray(visibleKeys) ? visibleKeys : columns.map((c) => c.key)));
  const [query, setQuery] = useState("");
  const [draggedKey, setDraggedKey] = useState(null);
  const [dropPosition, setDropPosition] = useState(null);
  const panelRef = useRef(null);

  useEffect(() => {
    if (open) {
      const known = new Set(columns.map((c) => c.key));
      const initialOrder = Array.isArray(visibleKeys) && visibleKeys.length > 0
        ? visibleKeys.filter((k) => known.has(k))
        : [];
      const missing = columns.map((c) => c.key).filter((k) => !initialOrder.includes(k));
      const fullOrder = [...initialOrder, ...missing];
      setOrder(fullOrder);
      setSelected(new Set(Array.isArray(visibleKeys) && visibleKeys.length > 0 ? visibleKeys : fullOrder));
      setQuery("");
      setDraggedKey(null);
      setDropPosition(null);
    }
  }, [open, visibleKeys, columns]);

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

  const normalizedQuery = query.trim().toLowerCase();
  const hasQuery = normalizedQuery.length > 0;
  const filtered = useMemo(() => {
    if (!hasQuery) return order.map((k, originalIndex) => ({ key: k, originalIndex }));
    return order
      .map((k, originalIndex) => {
        const col = columns.find((c) => c.key === k);
        if (!col) return null;
        const matches =
          col.label.toLowerCase().includes(normalizedQuery) ||
          col.key.toLowerCase().includes(normalizedQuery);
        return matches ? { key: k, originalIndex } : null;
      })
      .filter(Boolean);
  }, [order, columns, normalizedQuery, hasQuery]);

  const totalSelected = selected.size;
  const totalColumns = columns.length;
  const allSelected = totalSelected === totalColumns;
  const noneSelected = totalSelected === 0;

  const toggle = (key) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(order));
  const selectNone = () => setSelected(new Set());

  const moveKey = (fromIndex, toIndex) => {
    if (fromIndex === toIndex) return;
    setOrder((prev) => {
      if (fromIndex < 0 || fromIndex >= prev.length) return prev;
      const next = [...prev];
      const [moved] = next.splice(fromIndex, 1);
      const target = toIndex > fromIndex ? toIndex - 1 : toIndex;
      next.splice(Math.max(0, Math.min(next.length, target)), 0, moved);
      return next;
    });
  };

  const handleMoveUp = (originalIndex) => {
    if (hasQuery) return;
    if (originalIndex <= 0) return;
    moveKey(originalIndex, originalIndex - 1);
  };

  const handleMoveDown = (originalIndex) => {
    if (hasQuery) return;
    if (originalIndex >= order.length - 1) return;
    moveKey(originalIndex, originalIndex + 2);
  };

  const handleDragStart = (event, key) => {
    setDraggedKey(key);
    setDropPosition(null);
    event.dataTransfer.effectAllowed = "move";
    try {
      event.dataTransfer.setData("text/plain", key);
    } catch {
      /* ignore */
    }
  };

  const handleDragOver = (event) => {
    if (!draggedKey) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  };

  const handleDragEnterRow = (event, originalIndex) => {
    if (!draggedKey) return;
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    const isAfter = event.clientY - rect.top > rect.height / 2;
    setDropPosition({ key: order[originalIndex], position: isAfter ? "after" : "before" });
  };

  const handleDrop = (event, targetOriginalIndex) => {
    event.preventDefault();
    if (!draggedKey) return;
    const fromIndex = order.indexOf(draggedKey);
    if (fromIndex === -1) {
      setDraggedKey(null);
      setDropPosition(null);
      return;
    }
    const position = dropPosition?.key === order[targetOriginalIndex] ? dropPosition.position : "before";
    const targetIndex = position === "after" ? targetOriginalIndex + 1 : targetOriginalIndex;
    moveKey(fromIndex, targetIndex);
    setDraggedKey(null);
    setDropPosition(null);
  };

  const handleDragEnd = () => {
    setDraggedKey(null);
    setDropPosition(null);
  };

  const handleApply = () => {
    if (noneSelected) return;
    const orderedSelected = order.filter((k) => selected.has(k));
    onApply?.(orderedSelected);
    onClose?.();
  };

  const handleCancel = () => {
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
            filtered.map(({ key, originalIndex }) => {
              const col = columns.find((c) => c.key === key);
              if (!col) return null;
              const checked = selected.has(key);
              const isDragging = draggedKey === key;
              const isDropBefore =
                dropPosition?.key === key && dropPosition.position === "before";
              const isDropAfter =
                dropPosition?.key === key && dropPosition.position === "after";
              const isFirst = originalIndex === 0;
              const isLast = originalIndex === order.length - 1;
              const disableMove = hasQuery;
              return (
                <div
                  key={key}
                  className={[
                    "column-drawer-option",
                    checked ? "is-checked" : "",
                    isDragging ? "is-dragging" : "",
                    isDropBefore ? "is-drop-before" : "",
                    isDropAfter ? "is-drop-after" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  draggable
                  onDragStart={(e) => handleDragStart(e, key)}
                  onDragOver={handleDragOver}
                  onDragEnter={(e) => handleDragEnterRow(e, originalIndex)}
                  onDrop={(e) => handleDrop(e, originalIndex)}
                  onDragEnd={handleDragEnd}
                >
                  <span
                    className="column-drawer-option-handle"
                    aria-hidden="true"
                    title="Arrastrar para reordenar"
                  >
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="9" cy="6" r="1" />
                      <circle cx="9" cy="12" r="1" />
                      <circle cx="9" cy="18" r="1" />
                      <circle cx="15" cy="6" r="1" />
                      <circle cx="15" cy="12" r="1" />
                      <circle cx="15" cy="18" r="1" />
                    </svg>
                  </span>
                  <label className="column-drawer-option-toggle">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(key)}
                      aria-label={`Mostrar columna ${col.label}`}
                    />
                    <span className="column-drawer-option-mark" aria-hidden>
                      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    </span>
                  </label>
                  <span className="column-drawer-option-body">
                    <span className="column-drawer-option-index">{String(originalIndex + 1).padStart(2, "0")}</span>
                    <span className="column-drawer-option-text">
                      <span className="column-drawer-option-label">{col.label}</span>
                      <span className="column-drawer-option-key">{col.key}</span>
                    </span>
                  </span>
                  <div className="column-drawer-option-move">
                    <button
                      type="button"
                      className="column-drawer-option-move-btn"
                      onClick={() => handleMoveUp(originalIndex)}
                      disabled={disableMove || isFirst}
                      aria-label={`Mover ${col.label} arriba`}
                      title="Mover arriba"
                    >
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="m18 15-6-6-6 6" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      className="column-drawer-option-move-btn"
                      onClick={() => handleMoveDown(originalIndex)}
                      disabled={disableMove || isLast}
                      aria-label={`Mover ${col.label} abajo`}
                      title="Mover abajo"
                    >
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="m6 9 6 6 6-6" />
                      </svg>
                    </button>
                  </div>
                </div>
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
