import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { assignVehicleDatabase, batchLookupVehiclesStream } from "../../../api/vehicleApi";

const BATCH_SIZE = 500;
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
  const [customerDatabaseId, setCustomerDatabaseIdState] = useState(null);
  const [assignmentSummary, setAssignmentSummary] = useState({
    attempted: 0,
    success: 0,
    failed: 0,
  });

  const pauseRef = useRef(false);
  const cancelRef = useRef(false);
  const statusRef = useRef("idle");
  const itemsRef = useRef([]);
  const processedRef = useRef(0);
  const delayMsRef = useRef(DEFAULT_DELAY_MS);
  const resultsRef = useRef([]);
  const customerDatabaseIdRef = useRef(null);

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
    customerDatabaseIdRef.current = customerDatabaseId;
  }, [customerDatabaseId]);

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
    setAssignmentSummary({ attempted: 0, success: 0, failed: 0 });
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
      const remaining = currentItems.slice(startIndex);

      if (!remaining.length) {
        setCurrentIdentifier(null);
        setStatus("done");
        return;
      }

      // Split into batches of BATCH_SIZE (API max=500) for very large lists
      const batches = [];
      for (let i = 0; i < remaining.length; i += BATCH_SIZE) {
        batches.push(remaining.slice(i, i + BATCH_SIZE));
      }

      for (let batchIdx = 0; batchIdx < batches.length; batchIdx += 1) {
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

        const batch = batches[batchIdx];
        const batchIdentifiers = batch.map((item) => item.identifier);
        let itemIndex = 0;

        try {
          await batchLookupVehiclesStream(batchIdentifiers, {
            force: false,
            onResult: (response) => {
              const item = batch[itemIndex] || {};
              const started = performance.now();

              try {
                setCurrentIdentifier(item.identifier ?? null);
              } catch {
                /* ignore */
              }

              try {
                appendResult(
                  {
                    identifier: item.identifier,
                    rowNumber: item.rowNumber,
                    status: response?.status || "error",
                    response,
                    error: null,
                  },
                  0
                );
              } catch {
                /* never break on appendResult failure */
              }

              // Asignar cliente/database si el usuario lo eligio y la consulta
              // devolvio una placa valida. No bloquea el stream.
              const dbId = customerDatabaseIdRef.current;
              if (dbId && response?.plate && response.status !== "not_found" && response.status !== "error") {
                setAssignmentSummary((prev) => ({ ...prev, attempted: prev.attempted + 1 }));
                assignVehicleDatabase(response.plate, { customer_database_id: dbId })
                  .then(() => {
                    setAssignmentSummary((prev) => ({ ...prev, success: prev.success + 1 }));
                  })
                  .catch((assignErr) => {
                    setAssignmentSummary((prev) => ({ ...prev, failed: prev.failed + 1 }));
                    // eslint-disable-next-line no-console
                    console.warn(
                      `No se pudo asignar database a ${response.plate}:`,
                      assignErr?.message || assignErr
                    );
                  });
              }

              itemIndex += 1;
            },
          });
        } catch (err) {
          const message = err instanceof Error
            ? err.message
            : typeof err === "string"
              ? err
              : "Error en consulta masiva";

          // Mark remaining items in this batch as errors
          for (let i = itemIndex; i < batch.length; i += 1) {
            const item = batch[i] || {};
            try {
              appendResult(
                {
                  identifier: item.identifier,
                  rowNumber: item.rowNumber,
                  status: "error",
                  response: null,
                  error: message,
                },
                0
              );
            } catch {
              /* never break */
            }
          }
        }

        // Delay between batches (only for very large lists with multiple batches)
        const isLastBatch = batchIdx === batches.length - 1;
        if (!isLastBatch && !cancelRef.current && !pauseRef.current) {
          try {
            await sleep(delayMsRef.current);
          } catch {
            /* ignore */
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
    setAssignmentSummary({ attempted: 0, success: 0, failed: 0 });
    clearStoredBatch();
    setRestoreCandidate(null);
  }, []);

  const setDelayMs = useCallback((value) => {
    setDelayMsState(clampDelay(value));
  }, []);

  const setCustomerDatabaseId = useCallback((value) => {
    setCustomerDatabaseIdState(value || null);
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
    setAssignmentSummary({ attempted: 0, success: 0, failed: 0 });
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
    customerDatabaseId,
    setCustomerDatabaseId,
    assignmentSummary,
  };
}
