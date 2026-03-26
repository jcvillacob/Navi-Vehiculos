import { createContext, useCallback, useContext, useRef, useState } from "react";

import { refreshVehicle } from "../api/vehicleApi";

const BULK_DELAY_MS = 4000;

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
    setState({ status: "running", total, done: 0, currentPlate: plates[0], errors });

    for (let i = 0; i < total; i++) {
      if (cancelRef.current) break;

      const plate = plates[i];
      setState((prev) => ({ ...prev, done: i, currentPlate: plate }));

      try {
        await refreshVehicle(plate);
      } catch {
        errors.push(plate);
      }

      // Update done count after processing (so bar reflects completed, not started)
      setState((prev) => ({ ...prev, done: i + 1 }));

      if (i < total - 1 && !cancelRef.current) {
        await new Promise((resolve) => setTimeout(resolve, BULK_DELAY_MS));
      }
    }

    const wasCancelled = cancelRef.current;
    runningRef.current = false;

    // Transition to "finished" — stays visible until the consumer acknowledges it
    setState({
      status: "finished",
      total,
      done: wasCancelled ? errors.length > 0 ? total - errors.length : 0 : total,
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
