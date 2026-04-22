import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { lookupVehicle } from "../../../api/vehicleApi";

const DEFAULT_DELAY_MS = 1500;
const MIN_DELAY_MS = 500;
const MAX_DELAY_MS = 5000;
const STORAGE_KEY = "navi:bulk-lookup:last";
const MAX_RESTORE_AGE_MS = 24 * 60 * 60 * 1000;

function clampDelay(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_DELAY_MS;
  return Math.min(MAX_DELAY_MS, Math.max(MIN_DELAY_MS, Math.round(parsed)));
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function loadStoredBatch() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.results?.length || !parsed?.startedAt) return null;
    const ageMs = Date.now() - parsed.startedAt;
    if (ageMs > MAX_RESTORE_AGE_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}

function persistBatch(payload) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    /* ignore */
  }
}

function clearStoredBatch() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function useBulkLookup() {
  const [items, setItemsState] = useState([]);
  const [results, setResults] = useState([]);
  const [status, setStatus] = useState("idle");
  const [processed, setProcessed] = useState(0);
  const [currentIdentifier, setCurrentIdentifier] = useState(null);
  const [delayMs, setDelayMsState] = useState(DEFAULT_DELAY_MS);
  const [startedAt, setStartedAt] = useState(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [responseDurations, setResponseDurations] = useState([]);
  const [restoreCandidate, setRestoreCandidate] = useState(loadStoredBatch);

  const pauseRef = useRef(false);
  const cancelRef = useRef(false);
  const statusRef = useRef("idle");
  const itemsRef = useRef([]);
  const processedRef = useRef(0);
  const delayMsRef = useRef(DEFAULT_DELAY_MS);
  const resultsRef = useRef([]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    processedRef.current = processed;
  }, [processed]);

  useEffect(() => {
    delayMsRef.current = delayMs;
  }, [delayMs]);

  useEffect(() => {
    resultsRef.current = results;
  }, [results]);

  useEffect(() => {
    if (!startedAt || (status !== "running" && status !== "paused")) {
      return undefined;
    }

    const timerId = window.setInterval(() => {
      setElapsedMs(Date.now() - startedAt);
    }, 1000);

    return () => window.clearInterval(timerId);
  }, [startedAt, status]);

  const setItems = useCallback((nextItems) => {
    pauseRef.current = false;
    cancelRef.current = false;
    itemsRef.current = nextItems;
    processedRef.current = 0;
    resultsRef.current = [];
    setItemsState(nextItems);
    setResults([]);
    setProcessed(0);
    setStatus("idle");
    setCurrentIdentifier(null);
    setStartedAt(null);
    setElapsedMs(0);
    setResponseDurations([]);
  }, []);

  const appendResult = useCallback((entry, durationMs) => {
    const nextResults = [...resultsRef.current, entry];
    resultsRef.current = nextResults;
    setResults(nextResults);
    setProcessed((prev) => {
      const next = prev + 1;
      processedRef.current = next;
      persistBatch({
        startedAt: startedAt ?? Date.now(),
        items: itemsRef.current,
        results: nextResults,
      });
      return next;
    });
    setResponseDurations((prev) => [...prev, durationMs]);
    setRestoreCandidate({
      startedAt: startedAt ?? Date.now(),
      items: itemsRef.current,
      results: nextResults,
    });
  }, [startedAt]);

  const runLoop = useCallback(async () => {
    try {
      const currentItems = itemsRef.current;
      const startIndex = processedRef.current;

      for (let index = startIndex; index < currentItems.length; index += 1) {
        if (cancelRef.current) {
          setStatus("cancelled");
          setCurrentIdentifier(null);
          return;
        }

        if (pauseRef.current) {
          setStatus("paused");
          setCurrentIdentifier(null);
          return;
        }

        const item = currentItems[index] || {};
        const started = performance.now();

        try {
          setCurrentIdentifier(item.identifier ?? null);
        } catch {
          /* ignore setter errors */
        }

        let cameFromCache = false;
        try {
          const response = await lookupVehicle(item.identifier, { force: false });
          const durationMs = performance.now() - started;
          cameFromCache = response?.cached === true;

          appendResult(
            {
              identifier: item.identifier,
              rowNumber: item.rowNumber,
              status: response?.status || "error",
              response: response || null,
              error: null,
            },
            durationMs
          );
        } catch (err) {
          const durationMs = performance.now() - started;
          const message = err instanceof Error
            ? err.message
            : typeof err === "string"
              ? err
              : "Error consultando el backend";

          try {
            appendResult(
              {
                identifier: item.identifier,
                rowNumber: item.rowNumber,
                status: "error",
                response: null,
                error: message,
              },
              durationMs
            );
          } catch {
            /* never break the loop if appendResult itself fails */
          }
        }

        const isLast = index === currentItems.length - 1;
        if (!isLast && !cameFromCache && !cancelRef.current && !pauseRef.current) {
          try {
            await sleep(delayMsRef.current);
          } catch {
            /* ignore sleep errors */
          }
        }
      }

      setCurrentIdentifier(null);
      setStatus("done");
    } catch (fatal) {
      // eslint-disable-next-line no-console
      console.error("runLoop fatal error — cerrando lote como done", fatal);
      setCurrentIdentifier(null);
      setStatus("done");
    }
  }, [appendResult]);

  const start = useCallback(async () => {
    if (!itemsRef.current.length) return;
    if (statusRef.current === "running") return;
    if (statusRef.current === "cancelled") return;

    cancelRef.current = false;
    pauseRef.current = false;

    setStatus("running");
    if (!startedAt) {
      const now = Date.now();
      setStartedAt(now);
      setElapsedMs(0);
    }

    try {
      await runLoop();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Bulk lookup loop crashed — forzando cierre", err);
      setCurrentIdentifier(null);
      setStatus("done");
    }
  }, [runLoop, startedAt]);

  const pause = useCallback(() => {
    if (statusRef.current !== "running") return;
    pauseRef.current = true;
  }, []);

  const cancel = useCallback(() => {
    if (statusRef.current !== "running" && statusRef.current !== "paused") return;
    cancelRef.current = true;
    pauseRef.current = false;

    if (statusRef.current === "paused") {
      setStatus("cancelled");
      setCurrentIdentifier(null);
    }
  }, []);

  const reset = useCallback(() => {
    pauseRef.current = false;
    cancelRef.current = false;
    processedRef.current = 0;
    resultsRef.current = [];
    setResults([]);
    setStatus("idle");
    setProcessed(0);
    setCurrentIdentifier(null);
    setStartedAt(null);
    setElapsedMs(0);
    setResponseDurations([]);
    clearStoredBatch();
    setRestoreCandidate(null);
  }, []);

  const setDelayMs = useCallback((value) => {
    setDelayMsState(clampDelay(value));
  }, []);

  const total = items.length;
  const averageResponseMs = useMemo(() => {
    if (!responseDurations.length) return 0;
    return responseDurations.reduce((acc, item) => acc + item, 0) / responseDurations.length;
  }, [responseDurations]);
  const estimatedRemainingMs = Math.max(0, (total - processed) * (delayMs + averageResponseMs));

  const restoreLastBatch = useCallback(() => {
    if (!restoreCandidate?.results?.length) return;
    const restoredItems = restoreCandidate.items || [];
    const restoredResults = restoreCandidate.results || [];
    const restoredStartedAt = restoreCandidate.startedAt || Date.now();

    pauseRef.current = false;
    cancelRef.current = false;
    itemsRef.current = restoredItems;
    resultsRef.current = restoredResults;
    processedRef.current = restoredResults.length;

    setItemsState(restoredItems);
    setResults(restoredResults);
    setProcessed(restoredResults.length);
    setStatus("done");
    setCurrentIdentifier(null);
    setStartedAt(restoredStartedAt);
    setElapsedMs(Date.now() - restoredStartedAt);
  }, [restoreCandidate]);

  return {
    items,
    results,
    status,
    processed,
    total,
    currentIdentifier,
    delayMs,
    setDelayMs,
    setItems,
    start,
    pause,
    cancel,
    reset,
    startedAt,
    elapsedMs,
    estimatedRemainingMs,
    averageResponseMs,
    restoreCandidate,
    restoreLastBatch,
  };
}
