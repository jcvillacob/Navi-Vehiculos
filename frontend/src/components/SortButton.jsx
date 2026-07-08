export function SortButton({ columnKey, currentSort, onSortChange }) {
  const isActive = currentSort.key === columnKey;
  const dir = isActive ? currentSort.dir : null;
  const Icon =
    dir === "asc"
      ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m5 12 7-7 7 7" />
          <path d="M12 19V5" />
        </svg>
      )
      : dir === "desc"
        ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 5v14" />
          <path d="m19 12-7 7-7-7" />
        </svg>
        )
        : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m7 15 5 5 5-5" />
          <path d="m7 9 5-5 5 5" />
        </svg>
        );
  const label =
    dir === "asc"
      ? "Orden ascendente (clic para descendente)"
      : dir === "desc"
        ? "Orden descendente (clic para quitar orden)"
        : "Ordenar ascendente";
  const handleClick = () => {
    if (!isActive) {
      onSortChange({ key: columnKey, dir: "asc" });
      return;
    }
    if (dir === "asc") onSortChange({ key: columnKey, dir: "desc" });
    else onSortChange({ key: null, dir: null });
  };
  return (
    <button
      type="button"
      className={`th-sort-btn${dir ? ` is-${dir}` : ""}`}
      onClick={(event) => {
        event.stopPropagation();
        handleClick();
      }}
      title={label}
      aria-label={label}
    >
      {Icon}
    </button>
  );
}
