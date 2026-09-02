import { useId, useState } from "react";

import { MultiSelectFilter } from "../../../components/MultiSelectFilter";
import { STATUS_FILTER_OPTIONS } from "../status";

function multiLabel(selected, allLabel) {
  if (!selected.length) return allLabel;
  if (selected.length === 1) return selected[0];
  return `${selected.length} seleccionados`;
}

function FilterField({ id, label, options, selected, onChange, allLabel, open, onOpenChange }) {
  return (
    <div className="form-field">
      <span id={id} className="rendimientos-filter-label">{label}</span>
      <div role="group" aria-labelledby={id} className="rendimientos-multi-filter">
        <MultiSelectFilter
          label={multiLabel(selected, allLabel)}
          options={options}
          selected={selected}
          onChange={onChange}
          open={open}
          onOpenChange={onOpenChange}
        />
      </div>
    </div>
  );
}

/**
 * Chips de estado (multi, OR) + rango de meses + filtros multi-select +
 * busqueda libre.
 */
export default function RendimientosFilterBar({
  monthFrom,
  monthTo,
  onMonthFromChange,
  onMonthToChange,
  filters,
  options,
  statusCounts,
  onToggleStatus,
  onFilterChange,
}) {
  const [openKey, setOpenKey] = useState(null);
  const baseId = useId();
  const openHandler = (key) => (isOpen) => setOpenKey(isOpen ? key : null);

  return (
    <>
      <div className="rendimientos-status-strip" role="group" aria-label="Filtrar por estado">
        {STATUS_FILTER_OPTIONS.map((option) => {
          const isActive = filters.statuses.includes(option.key);
          return (
            <button
              key={option.key}
              type="button"
              className={`status-chip ${option.className} ${isActive ? "is-active" : ""}`}
              onClick={() => onToggleStatus(option.key)}
              aria-pressed={isActive}
              title={isActive ? `Quitar filtro ${option.label}` : `Filtrar por ${option.label}`}
            >
              {option.label}: {statusCounts[option.key] ?? 0}
            </button>
          );
        })}
      </div>

      <div className="rendimientos-range-bar">
        <div className="form-field">
          <label htmlFor="rendimientos-month-from">Desde</label>
          <input
            id="rendimientos-month-from"
            type="month"
            value={monthFrom}
            onChange={(event) => onMonthFromChange(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="rendimientos-month-to">Hasta</label>
          <input
            id="rendimientos-month-to"
            type="month"
            value={monthTo}
            onChange={(event) => onMonthToChange(event.target.value)}
          />
        </div>
      </div>

      <div className="rendimientos-filter-bar">
        <div className="form-field rendimientos-search-field">
          <label htmlFor="rendimientos-plate-search">Buscar</label>
          <div className="search-input-wrap">
            <input
              id="rendimientos-plate-search"
              value={filters.plateSearch}
              onChange={(event) => onFilterChange("plateSearch", event.target.value.toUpperCase())}
              placeholder="Placa, nombre, marca o línea"
              autoComplete="off"
            />
            {filters.plateSearch ? (
              <button
                type="button"
                className="search-clear-button"
                onClick={() => onFilterChange("plateSearch", "")}
                aria-label="Limpiar busqueda"
              >
                ✕
              </button>
            ) : null}
          </div>
        </div>

        <FilterField
          id={`${baseId}-clients`}
          label="Clientes"
          allLabel="Todos"
          options={options.clients}
          selected={filters.clients}
          onChange={(next) => onFilterChange("clients", next)}
          open={openKey === "clients"}
          onOpenChange={openHandler("clients")}
        />
        <FilterField
          id={`${baseId}-categories`}
          label="Categoria"
          allLabel="Todas"
          options={options.categories}
          selected={filters.categories}
          onChange={(next) => onFilterChange("categories", next)}
          open={openKey === "categories"}
          onOpenChange={openHandler("categories")}
        />
        <FilterField
          id={`${baseId}-motors`}
          label="Grupo de motor"
          allLabel="Todos"
          options={options.motorGroups}
          selected={filters.motorGroups}
          onChange={(next) => onFilterChange("motorGroups", next)}
          open={openKey === "motorGroups"}
          onOpenChange={openHandler("motorGroups")}
        />
      </div>
    </>
  );
}
