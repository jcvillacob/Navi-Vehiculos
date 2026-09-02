import { useCallback, useEffect, useMemo, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import {
  EMPTY_FILTERS,
  fromLegacyState,
  hasAnyFilterParam,
  parseFiltersFromSearchParams,
  serializeFiltersToSearchParams,
} from "../filters";

/**
 * Estado de filtros/rango/paginacion/orden respaldado en la URL
 * (useSearchParams). La URL es la unica fuente de verdad; los setters
 * reescriben los params con `replace` para no ensuciar el historial.
 *
 * Compat: si la pagina se abre con `location.state.rendimientosFilters`
 * (round-trip desde la ficha 360) y la URL no trae params, se hidrata la URL
 * desde ese state una sola vez.
 */
export function useRendimientosFilters() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const legacyAppliedRef = useRef(false);

  const state = useMemo(() => parseFiltersFromSearchParams(searchParams), [searchParams]);

  const write = useCallback(
    (updater) => {
      setSearchParams(
        (prev) => {
          const current = parseFiltersFromSearchParams(prev);
          const next = typeof updater === "function" ? updater(current) : { ...current, ...updater };
          return serializeFiltersToSearchParams(next, prev);
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // Hidratar desde location.state (ficha 360) una sola vez.
  useEffect(() => {
    if (legacyAppliedRef.current) return;
    legacyAppliedRef.current = true;
    const legacy = fromLegacyState(location.state?.rendimientosFilters);
    if (!legacy || hasAnyFilterParam(searchParams)) return;
    setSearchParams(serializeFiltersToSearchParams(legacy, searchParams), { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Normaliza from > to en la URL (swap una vez).
  useEffect(() => {
    const rawFrom = searchParams.get("from");
    const rawTo = searchParams.get("to");
    if (rawFrom && rawTo && rawFrom > rawTo) {
      write((current) => current); // parse ya devuelve el rango ordenado
    }
  }, [searchParams, write]);

  const setMonthFrom = useCallback((value) => write((c) => ({ ...c, monthFrom: value || c.monthFrom, page: 1 })), [write]);
  const setMonthTo = useCallback((value) => write((c) => ({ ...c, monthTo: value || c.monthTo, page: 1 })), [write]);

  const setFilter = useCallback(
    (key, value) => write((c) => ({ ...c, filters: { ...c.filters, [key]: value }, page: 1 })),
    [write],
  );

  const toggleStatus = useCallback(
    (status) =>
      write((c) => {
        const has = c.filters.statuses.includes(status);
        const statuses = has ? c.filters.statuses.filter((s) => s !== status) : [...c.filters.statuses, status];
        return { ...c, filters: { ...c.filters, statuses }, page: 1 };
      }),
    [write],
  );

  const clearFilters = useCallback(() => write((c) => ({ ...c, filters: { ...EMPTY_FILTERS }, page: 1 })), [write]);

  /**
   * Limpieza automatica: quita de la seleccion los valores que ya no existen
   * entre las opciones disponibles (filtra el array, no lo resetea).
   */
  const currentFilters = state.filters;
  const pruneFilters = useCallback(
    (options) => {
      const prune = (selected, available) => {
        if (!selected.length) return selected;
        const set = new Set(available);
        const next = selected.filter((v) => set.has(v));
        return next.length === selected.length ? selected : next;
      };
      const filters = {
        ...currentFilters,
        clients: prune(currentFilters.clients, options.clients),
        categories: prune(currentFilters.categories, options.categories),
        motorGroups: prune(currentFilters.motorGroups, options.motorGroups),
      };
      const changed =
        filters.clients !== currentFilters.clients ||
        filters.categories !== currentFilters.categories ||
        filters.motorGroups !== currentFilters.motorGroups;
      // Solo escribimos la URL si realmente cambio algo (evita bucles).
      if (changed) write((c) => ({ ...c, filters: { ...c.filters, ...filters } }));
    },
    [currentFilters, write],
  );

  const setPage = useCallback((page) => write((c) => ({ ...c, page })), [write]);
  const setPageSize = useCallback((pageSize) => write((c) => ({ ...c, pageSize, page: 1 })), [write]);
  const setSort = useCallback((sort) => write((c) => ({ ...c, sort, page: 1 })), [write]);

  return {
    ...state,
    searchString: searchParams.toString(),
    setMonthFrom,
    setMonthTo,
    setFilter,
    toggleStatus,
    clearFilters,
    pruneFilters,
    setPage,
    setPageSize,
    setSort,
  };
}
