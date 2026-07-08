import { useEffect, useRef, useState } from "react";

function normalizeFilterText(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u0303\u0305]/g, "");
}

export function MultiSelectFilter({ label, options, selected, onChange, open, onOpenChange }) {
  const [query, setQuery] = useState("");
  const ref = useRef(null);
  const searchRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) onOpenChange(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onOpenChange]);

  useEffect(() => {
    if (open) {
      setQuery("");
    }
  }, [open]);

  useEffect(() => {
    if (open) {
      const id = requestAnimationFrame(() => {
        searchRef.current?.focus();
      });
      return () => cancelAnimationFrame(id);
    }
    return undefined;
  }, [open]);

  const items = options.map((opt) =>
    typeof opt === "string" ? { value: opt, label: opt } : opt
  );

  const filteredItems = (() => {
    const q = normalizeFilterText(query);
    if (!q) return items;
    return items.filter((item) => normalizeFilterText(item.label).includes(q));
  })();

  const toggle = (value) => {
    const next = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    onChange(next);
  };

  const handleSearchKeyDown = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (query) {
        setQuery("");
      } else {
        onOpenChange(false);
      }
    }
  };

  return (
    <div className={`th-multifilter ${selected.length ? "is-active" : ""}`} ref={ref}>
      <button
        type="button"
        className="th-multifilter-trigger"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span className="th-multifilter-text">{label}</span>
        <span className="th-multifilter-arrow">▼</span>
      </button>
      {open && (
        <div
          className="th-multifilter-dropdown"
          role="listbox"
          onClick={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <div className="th-multifilter-dropdown-header">
            <div className="th-multifilter-search-wrap">
              <input
                ref={searchRef}
                type="text"
                className="th-multifilter-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleSearchKeyDown}
                placeholder="Buscar..."
                onClick={(event) => event.stopPropagation()}
                onMouseDown={(event) => event.stopPropagation()}
              />
              {query ? (
                <button
                  type="button"
                  className="th-multifilter-search-clear"
                  onClick={(event) => {
                    event.stopPropagation();
                    setQuery("");
                    searchRef.current?.focus();
                  }}
                  aria-label="Limpiar busqueda"
                  title="Limpiar busqueda"
                >
                  ✕
                </button>
              ) : null}
            </div>
          </div>
          {filteredItems.length > 0 ? (
            filteredItems.map((item) => (
              <label key={item.value} className="th-multifilter-option">
                <input
                  type="checkbox"
                  checked={selected.includes(item.value)}
                  onChange={() => toggle(item.value)}
                />
                <span>{item.label}</span>
              </label>
            ))
          ) : (
            <div className="th-multifilter-empty">Sin coincidencias</div>
          )}
        </div>
      )}
    </div>
  );
}
