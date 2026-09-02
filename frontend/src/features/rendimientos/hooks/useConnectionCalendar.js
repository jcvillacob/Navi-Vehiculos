import { useCallback, useEffect, useRef, useState } from "react";

import { fetchConnectionCalendar } from "../../../api/vehicleApi";

const HOVER_DEBOUNCE_MS = 200;

/**
 * Popover de calendario de conexion (heatmap estilo GitHub).
 * UN solo popover compartido posicionado en la celda con hover; lazy + cache
 * por `plate|from|to` + debounce + guard anti-stale.
 */
export function useConnectionCalendar(monthFrom, monthTo) {
  const [popover, setPopover] = useState(null); // { plate, rect, from, to, days, loading }
  const cacheRef = useRef(new Map());
  const hoverRef = useRef(null);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const onCellEnter = useCallback(
    (plate, event) => {
      if (!plate) return;
      const rect = event.currentTarget.getBoundingClientRect();
      const from = monthFrom;
      const to = monthTo;
      const cacheKey = `${plate}|${from}|${to}`;
      hoverRef.current = plate;

      if (timerRef.current) clearTimeout(timerRef.current);

      const cached = cacheRef.current.get(cacheKey);
      if (cached) {
        setPopover({ plate, rect, from, to, days: cached, loading: false });
        return;
      }

      timerRef.current = setTimeout(() => {
        if (!mountedRef.current || hoverRef.current !== plate) return;
        setPopover({ plate, rect, from, to, days: null, loading: true });
        fetchConnectionCalendar(plate, from, to)
          .then((data) => {
            const days = data?.days || [];
            cacheRef.current.set(cacheKey, days);
            if (!mountedRef.current || hoverRef.current !== plate) return;
            setPopover({ plate, rect, from, to, days, loading: false });
          })
          .catch(() => {
            if (!mountedRef.current || hoverRef.current !== plate) return;
            setPopover({ plate, rect, from, to, days: [], loading: false });
          });
      }, HOVER_DEBOUNCE_MS);
    },
    [monthFrom, monthTo],
  );

  const onCellLeave = useCallback(() => {
    hoverRef.current = null;
    if (timerRef.current) clearTimeout(timerRef.current);
    setPopover(null);
  }, []);

  return { popover, onCellEnter, onCellLeave };
}
