import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import Can from "../components/Can";
import ColumnSelectorDrawer from "../components/ColumnSelectorDrawer";
import TablePagination from "../components/TablePagination";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { useUserPreference } from "../hooks/useUserPreference";
import { compareValues, formatMonthLabel } from "../utils/formatters";
import { COLUMN_BY_KEY, COLUMN_KEYS, RENDIMIENTOS_COLUMNS } from "../features/rendimientos/columns";
import { PAGE_SIZE_OPTIONS, analyzeRows, countActiveFilters } from "../features/rendimientos/filters";
import { exportRendimientosExcel } from "../features/rendimientos/exportExcel";
import { useRendimientosFilters } from "../features/rendimientos/hooks/useRendimientosFilters";
import { useRendimientosData } from "../features/rendimientos/hooks/useRendimientosData";
import { usePerformanceJobs } from "../features/rendimientos/hooks/usePerformanceJobs";
import { useConnectionCalendar } from "../features/rendimientos/hooks/useConnectionCalendar";
import CalculateModal from "../features/rendimientos/components/CalculateModal";
import ConnectionCalendarPopover from "../features/rendimientos/components/ConnectionCalendarPopover";
import JobHistoryCard from "../features/rendimientos/components/JobHistoryCard";
import JobProgressBar, { LastRunBadge } from "../features/rendimientos/components/JobProgressBar";
import RendimientosFilterBar from "../features/rendimientos/components/RendimientosFilterBar";
import RendimientosSummary, { computeVisibleSummary } from "../features/rendimientos/components/RendimientosSummary";
import RendimientosTable from "../features/rendimientos/components/RendimientosTable";

const VALID_COLUMN_KEYS = new Set(COLUMN_KEYS);

function validateVisibleColumns(raw) {
  if (!Array.isArray(raw)) return null;
  const filtered = raw.filter((k) => typeof k === "string" && VALID_COLUMN_KEYS.has(k));
  return filtered.length > 0 ? filtered : null;
}

function validateSort(raw) {
  if (!raw || typeof raw !== "object") return null;
  const { key, dir } = raw;
  if (typeof key !== "string" || !VALID_COLUMN_KEYS.has(key)) return null;
  if (dir !== "asc" && dir !== "desc" && dir !== null) return null;
  return { key, dir };
}

function rangesOverlap(aFrom, aTo, bFrom, bTo) {
  return aTo >= bFrom && aFrom <= bTo;
}

export default function RendimientosPage() {
  const { toasts, pushToast } = useToasts();
  const filtersState = useRendimientosFilters();
  const { monthFrom, monthTo, filters, page, pageSize } = filtersState;
  const data = useRendimientosData(monthFrom, monthTo);
  const calendar = useConnectionCalendar(monthFrom, monthTo);
  const [consultOpen, setConsultOpen] = useState(false);
  const [columnSelectorOpen, setColumnSelectorOpen] = useState(false);

  // Refs para que el callback de jobs vea siempre el rango/reload vigentes.
  const rangeRef = useRef({ monthFrom, monthTo });
  rangeRef.current = { monthFrom, monthTo };
  const reloadRef = useRef(data.reload);
  reloadRef.current = data.reload;

  const handleJobsCompleted = useCallback(({ from, to }) => {
    const { monthFrom: visFrom, monthTo: visTo } = rangeRef.current;
    if (rangesOverlap(from, to, visFrom, visTo)) reloadRef.current();
  }, []);
  const jobs = usePerformanceJobs({ pushToast, onCompleted: handleJobsCompleted });

  // ── Preferencias: columnas visibles y orden ──
  const { value: savedColumns, setValue: persistColumns } = useUserPreference(
    "rendimientos.visible_columns",
    null,
    { validator: validateVisibleColumns },
  );
  const { value: savedSort, setValue: persistSort } = useUserPreference(
    "rendimientos.sort",
    null,
    { validator: validateSort },
  );

  const visibleColumns = Array.isArray(savedColumns) && savedColumns.length > 0 ? savedColumns : COLUMN_KEYS;
  const activeColumns = useMemo(
    () => visibleColumns.map((key) => COLUMN_BY_KEY.get(key)).filter(Boolean),
    [visibleColumns],
  );

  // El orden en la URL manda; si no hay, cae a la preferencia guardada.
  const sort = useMemo(() => {
    if (filtersState.sort) return filtersState.sort;
    if (savedSort?.key && savedSort?.dir) return { key: savedSort.key, dir: savedSort.dir };
    return { key: null, dir: null };
  }, [filtersState.sort, savedSort]);

  const { setSort, setPage, pruneFilters } = filtersState;
  const handleSortChange = useCallback((next) => {
    persistSort(next);
    setSort(next?.key && next?.dir ? next : null);
  }, [persistSort, setSort]);

  // ── Filtrado (una sola pasada) + opciones + conteos ──
  const deferredSearch = useDeferredValue(filters.plateSearch);
  const effectiveFilters = useMemo(
    () => ({ ...filters, plateSearch: deferredSearch }),
    [filters, deferredSearch],
  );
  const { filteredRows, options, statusCounts } = useMemo(
    () => analyzeRows(data.rows, effectiveFilters),
    [data.rows, effectiveFilters],
  );

  // Limpieza automatica de valores seleccionados que ya no existen.
  useEffect(() => {
    if (data.loading || data.rows.length === 0) return;
    pruneFilters(options);
  }, [options, data.loading, data.rows.length, pruneFilters]);

  const ctx = useMemo(
    () => ({ connStats: data.connStats, availabilityByPlate: data.availabilityByPlate }),
    [data.connStats, data.availabilityByPlate],
  );

  const sortedRows = useMemo(() => {
    if (!sort.key || !sort.dir) return filteredRows;
    const col = COLUMN_BY_KEY.get(sort.key);
    if (!col) return filteredRows;
    const getter = col.getSortValue || col.getValue;
    // Calculamos el valor de orden una sola vez por fila.
    const keyed = filteredRows.map((row) => ({ row, value: getter(row, ctx) }));
    keyed.sort((left, right) => {
      const comparison = compareValues(left.value, right.value, sort.dir);
      if (comparison !== 0) return comparison;
      return compareValues(left.row.plate || "", right.row.plate || "", "asc");
    });
    return keyed.map((item) => item.row);
  }, [filteredRows, sort, ctx]);

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const paginatedRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return sortedRows.slice(start, start + pageSize);
  }, [sortedRows, page, pageSize]);

  useEffect(() => {
    if (data.loading || data.rows.length === 0) return;
    if (page > totalPages) setPage(1);
  }, [page, totalPages, data.loading, data.rows.length, setPage]);

  const visibleSummary = useMemo(() => computeVisibleSummary(filteredRows), [filteredRows]);
  const activeFilterCount = countActiveFilters(filters);
  const isRange = monthFrom !== monthTo;
  const rangeLabel = isRange
    ? `${formatMonthLabel(monthFrom)} – ${formatMonthLabel(monthTo)}`
    : formatMonthLabel(monthFrom);

  // Estado para el round-trip con la ficha 360 (vuelve a esta misma URL).
  const linkState = useMemo(() => ({
    returnTo: `/rendimientos${filtersState.searchString ? `?${filtersState.searchString}` : ""}`,
    returnLabel: "Rendimientos",
    rendimientosFilters: { monthFrom, monthTo, filters, page, pageSize, sort: filtersState.sort },
  }), [filtersState.searchString, filtersState.sort, monthFrom, monthTo, filters, page, pageSize]);

  const handleExport = async () => {
    if (!sortedRows.length) {
      pushToast("error", "No hay filas para exportar con los filtros actuales.");
      return;
    }
    try {
      await exportRendimientosExcel({
        rows: sortedRows,
        columns: activeColumns,
        ctx,
        monthFrom,
        monthTo,
        filters: effectiveFilters,
      });
      pushToast("success", `Excel exportado con ${sortedRows.length} filas.`);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible exportar el Excel");
    }
  };

  const handleCalculate = (request) => {
    setConsultOpen(false);
    jobs.runCalculation(request);
  };

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Analitica operativa</span>
          <h2>Rendimientos</h2>
        </div>

        <div className="rendimientos-month-actions">
          <LastRunBadge job={jobs.recentJobs[0]} />
          <Can permission="rendimientos.refresh">
            <button type="button" onClick={() => setConsultOpen(true)} disabled={jobs.calculating}>
              {jobs.calculating ? "Calculando..." : "Consultar"}
            </button>
          </Can>
        </div>

        {jobs.calculating && <JobProgressBar progress={jobs.progress} onCancel={jobs.cancel} />}
      </header>

      <ToastStack toasts={toasts} />
      <ConnectionCalendarPopover popover={calendar.popover} />

      <RendimientosSummary summary={visibleSummary} />

      <section className="card rendimientos-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">{isRange ? "Acumulado" : "Lote"} {rangeLabel}</span>
            <h3>Explorador {isRange ? "por rango" : "mensual"}</h3>
          </div>

          <div className="actions-row section-heading-actions">
            <button
              type="button"
              className="button button-sm rendimientos-button-reload"
              onClick={data.reload}
              disabled={data.loading}
            >
              {data.loading ? "Cargando..." : "Recargar"}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={filtersState.clearFilters}
              disabled={activeFilterCount === 0}
            >
              Limpiar ({activeFilterCount})
            </button>
            <button
              type="button"
              className="button button-sm rendimientos-button-export"
              onClick={handleExport}
              disabled={!filteredRows.length}
            >
              Exportar
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => setColumnSelectorOpen(true)}
              aria-haspopup="dialog"
              aria-expanded={columnSelectorOpen}
            >
              Columnas ({visibleColumns.length}/{RENDIMIENTOS_COLUMNS.length})
            </button>
          </div>
        </header>

        <RendimientosFilterBar
          monthFrom={monthFrom}
          monthTo={monthTo}
          onMonthFromChange={filtersState.setMonthFrom}
          onMonthToChange={filtersState.setMonthTo}
          filters={filters}
          options={options}
          statusCounts={statusCounts}
          onToggleStatus={filtersState.toggleStatus}
          onFilterChange={filtersState.setFilter}
        />

        <RendimientosTable
          columns={activeColumns}
          rows={paginatedRows}
          totalRows={filteredRows.length}
          ctx={ctx}
          sort={sort}
          onSortChange={handleSortChange}
          error={data.error}
          stale={data.stale}
          loading={data.loading}
          linkState={linkState}
          onConnEnter={calendar.onCellEnter}
          onConnLeave={calendar.onCellLeave}
        />

        <TablePagination
          page={page}
          pageSize={pageSize}
          total={filteredRows.length}
          onPageChange={setPage}
          onPageSizeChange={filtersState.setPageSize}
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          itemLabel="fila(s)"
        />
      </section>

      <JobHistoryCard
        jobs={jobs.recentJobs}
        loading={jobs.recentJobsLoading}
        onReload={jobs.reloadRecentJobs}
      />

      <CalculateModal
        open={consultOpen}
        onClose={() => setConsultOpen(false)}
        onSubmit={handleCalculate}
        calculating={jobs.calculating}
        pushToast={pushToast}
      />

      <ColumnSelectorDrawer
        open={columnSelectorOpen}
        title="Columnas de rendimientos"
        description="Selecciona las columnas que quieres ver en el reporte y aplica los cambios al final."
        columns={RENDIMIENTOS_COLUMNS}
        visibleKeys={visibleColumns}
        onApply={persistColumns}
        onClose={() => setColumnSelectorOpen(false)}
      />
    </section>
  );
}
