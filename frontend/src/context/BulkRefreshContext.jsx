import { createContext, useCallback, useContext, useRef, useState } from "react";

import { batchLookupVehiclesStream } from "../api/vehicleApi";

const BulkRefreshContext = createContext(null);

// State shape:
//   null                                                       → idle
//   { status: "running",  total, done, currentPlate, errors }  → in progress
//   { status: "finished", total, done, errors, wasCancelled }  → done, waiting for consumer to acknowledge

export function BulkRefreshProvider({ children }) {
  const [state, setState] = useState(null);
  const abortRef = useRef(null);
  const runningRef = useRef(false);

  const start = useCallback(async (plates, { scope = "all", skipGeotab = false } = {}) => {
    if (runningRef.current || !plates.length) return;
    runningRef.current = true;

    const BATCH_SIZE = 500;
    const total = plates.length;
    const errors = [];
    let done = 0;
    let cancelled = false;

    const abortController = new AbortController();
    abortRef.current = abortController;

    setState({ status: "running", total, done: 0, currentPlate: plates[0], errors });

    for (let offset = 0; offset < total; offset += BATCH_SIZE) {
      if (abortController.signal.aborted) {
        cancelled = true;
        break;
      }

      const chunk = plates.slice(offset, offset + BATCH_SIZE);

      try {
        await batchLookupVehiclesStream(chunk, {
          force: true,
          scope,
          skipGeotab,
          signal: abortController.signal,
          onResult: (result) => {
            done += 1;
            const plate = plates[done - 1] || "";

            if (result?.status === "error") {
              errors.push(plate);
            }

            setState({
              status: "running",
              total,
              done,
              currentPlate: done < total ? plates[done] : plate,
              errors,
            });
          },
        });
      } catch (err) {
        if (err?.name === "AbortError" || abortController.signal.aborted) {
          cancelled = true;
          break;
        }
        // If this chunk's stream fails, mark its remaining plates as errors
        const chunkProcessed = done - offset;
        const remaining = chunk.slice(chunkProcessed);
        errors.push(...remaining);
        done = offset + chunk.length;

        setState({
          status: "running",
          total,
          done,
          currentPlate: done < total ? plates[done] : plates[done - 1] || "",
          errors,
        });
      }
    }

    runningRef.current = false;
    abortRef.current = null;

    setState({
      status: "finished",
      total,
      done: cancelled ? done - errors.length : done,
      errors,
      wasCancelled: cancelled,
    });
  }, []);

  const cancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
  }, []);

  const acknowledge = useCallback(() => {
    setState(null);
  }, []);

  return (
    <BulkRefreshContext.Provider
      value={{
        bulkRefresh: state,
        startBulkRefresh: start,
        cancelBulkRefresh: cancel,
        acknowledgeBulkRefresh: acknowledge,
      }}
    >
      {children}
    </BulkRefreshContext.Provider>
  );
}

export function useBulkRefresh() {
  const ctx = useContext(BulkRefreshContext);
  if (!ctx) throw new Error("useBulkRefresh must be inside BulkRefreshProvider");
  return ctx;
}
