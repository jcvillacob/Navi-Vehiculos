import { createContext, useCallback, useContext, useRef, useState } from "react";

import { batchLookupVehiclesStream } from "../api/vehicleApi";

const BulkRefreshContext = createContext(null);

// State shape:
//   null                                                       → idle
//   { status: "running",  total, done, currentPlate, errors }  → in progress
//   { status: "finished", total, done, errors, wasCancelled }  → done, waiting for consumer to acknowledge

export function BulkRefreshProvider({ children }) {
  const [state, setState] = useState(null);
  const cancelRef = useRef(false);
  const runningRef = useRef(false);

  const start = useCallback(async (plates) => {
    if (runningRef.current || !plates.length) return;
    runningRef.current = true;
    cancelRef.current = false;

    const total = plates.length;
    const errors = [];
    let done = 0;

    setState({ status: "running", total, done: 0, currentPlate: plates[0], errors });

    try {
      await batchLookupVehiclesStream(plates, {
        force: true,
        onResult: (result, count) => {
          done = count;
          const plate = plates[count - 1] || "";

          if (result?.status === "error") {
            errors.push(plate);
          }

          setState({
            status: "running",
            total,
            done: count,
            currentPlate: count < total ? plates[count] : plate,
            errors,
          });
        },
      });
    } catch {
      // If the entire stream fails, mark remaining plates as errors
      const remaining = plates.slice(done);
      errors.push(...remaining);
      done = total;
    }

    const wasCancelled = cancelRef.current;
    runningRef.current = false;

    setState({
      status: "finished",
      total,
      done: wasCancelled ? done - errors.length : done,
      errors,
      wasCancelled,
    });
  }, []);

  const cancel = useCallback(() => {
    cancelRef.current = true;
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
