import { useId } from "react";

/**
 * Paginacion generica client-side.
 * Reusa las clases .rendimientos-pagination* (look compartido en la app).
 */
export default function TablePagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
  itemLabel = "fila(s)",
}) {
  const selectId = useId();
  const totalPages = Math.max(1, Math.ceil((total || 0) / (pageSize || 1)));
  const safePage = Math.min(Math.max(1, page || 1), totalPages);

  return (
    <nav className="rendimientos-pagination" aria-label="Paginacion de la tabla">
      <div className="rendimientos-pagination-info">
        {total} {itemLabel} en total
      </div>
      <div className="rendimientos-pagination-controls">
        <label className="rendimientos-pagination-label" htmlFor={selectId}>Filas por pág.:</label>
        <select
          id={selectId}
          className="rendimientos-pagination-select"
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          {pageSizeOptions.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <button
          type="button"
          className="rendimientos-pagination-btn"
          disabled={safePage <= 1}
          onClick={() => onPageChange(safePage - 1)}
          aria-label="Página anterior"
        >
          ‹
        </button>
        <span className="rendimientos-pagination-current" aria-live="polite">
          Pág. {safePage} de {totalPages}
        </span>
        <button
          type="button"
          className="rendimientos-pagination-btn"
          disabled={safePage >= totalPages}
          onClick={() => onPageChange(safePage + 1)}
          aria-label="Página siguiente"
        >
          ›
        </button>
      </div>
    </nav>
  );
}
