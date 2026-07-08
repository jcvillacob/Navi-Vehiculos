import { useEffect, useMemo, useRef, useState } from "react";

function normalizeFilterText(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u0303\u0305]/g, "");
}

export function SingleSelectFilter({ label, options, value, onChange, placeholder = "Todos" }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef(null);
  const searchRef = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

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

  const items = useMemo(
    () => options.map((opt) => (typeof opt === "string" ? { value: opt, label: opt } : opt)),
    [options]
  );

  const filteredItems = useMemo(() => {
    const q = normalizeFilterText(query);
    if (!q) return items;
    return items.filter((item) => normalizeFilterText(item.label).includes(q));
  }, [items, query]);

  const selectedItem = items.find((item) => item.value === value);
  const isActive = Boolean(value);
  const displayLabel = selectedItem ? selectedItem.label : label;

  const handleSelect = (next) => {
    onChange(next);
    setOpen(false);
  };

  const handleSearchKeyDown = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (query) {
        setQuery("");
      } else {
        setOpen(false);
      }
    }
  };

  return (
    <div className={`th-multifilter ${isActive ? "is-active" : ""}`} ref={ref}>
      <button
        type="button"
        className="th-multifilter-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span className="th-multifilter-text">{displayLabel}</span>
        <span className="th-multifilter-arrow">▼</span>
      </button>
      {open && (
        <div className="th-multifilter-dropdown" role="listbox">
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
            <>
              <label className="th-multifilter-option">
                <input
                  type="radio"
                  name={`single-select-${label}`}
                  checked={!value}
                  onChange={() => handleSelect("")}
                />
                <span>{placeholder}</span>
              </label>
              {filteredItems.map((item) => (
                <label key={item.value} className="th-multifilter-option">
                  <input
                    type="radio"
                    name={`single-select-${label}`}
                    checked={value === item.value}
                    onChange={() => handleSelect(item.value)}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </>
          ) : (
            <div className="th-multifilter-empty">Sin coincidencias</div>
          )}
        </div>
      )}
    </div>
  );
}
