import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import {
  acknowledgeVehicleReprocessJob,
  cancelVehicleReprocessJob,
  createVehicleReprocessJob,
  fetchCurrentVehicleReprocessJob,
  fetchVehicleReprocessJob,
} from "../api/vehicleApi";

const BulkRefreshContext = createContext(null);
const POLL_INTERVAL_MS = 2000;
const ACTIVE_STATUSES = new Set(["queued", "running"]);

function normalizeJob(job) {
  if (!job) return null;
  const wasCancelled = job.status === "cancelled";
  const finished = ["done", "error", "cancelled"].includes(job.status);
  const errors = Array.isArray(job.errors) ? job.errors : [];
  return {
    id: job.id,
    status: finished ? "finished" : "running",
    backendStatus: job.status,
    total: job.total_targets || 0,
    done: job.processed_targets || 0,
    currentPlate: job.current_identifier || "",
    errors,
    errorMessage: job.error_message || "",
    wasCancelled,
  };
}

export function BulkRefreshProvider({ children }) {
  const [state, setState] = useState(null);
  const pollTimerRef = useRef(null);
  const activeJobIdRef = useRef(null);
  const mountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current);
    pollTimerRef.current = null;
  }, []);

  const poll = useCallback(async (jobId) => {
    if (!mountedRef.current || activeJobIdRef.current !== jobId) return;
    try {
      const job = await fetchVehicleReprocessJob(jobId);
      if (!mountedRef.current || activeJobIdRef.current !== jobId) return;
      setState(normalizeJob(job));
      if (!ACTIVE_STATUSES.has(job.status)) {
        activeJobIdRef.current = null;
        stopPolling();
        return;
      }
    } catch {
      // Una falla transitoria no debe perder un job que sigue en backend.
    }
    pollTimerRef.current = window.setTimeout(() => poll(jobId), POLL_INTERVAL_MS);
  }, [stopPolling]);

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;
    fetchCurrentVehicleReprocessJob()
      .then((job) => {
        if (cancelled || !job) return;
        setState(normalizeJob(job));
        if (ACTIVE_STATUSES.has(job.status)) {
          activeJobIdRef.current = job.id;
          poll(job.id);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      mountedRef.current = false;
      stopPolling();
    };
  }, [poll, stopPolling]);

  const start = useCallback(async (plates, { scope = "all", skipGeotab = false } = {}) => {
    if (activeJobIdRef.current || !plates.length) return;
    stopPolling();
    try {
      const job = await createVehicleReprocessJob(plates, { scope, skipGeotab });
      activeJobIdRef.current = job.id;
      setState(normalizeJob(job));
      if (ACTIVE_STATUSES.has(job.status)) poll(job.id);
    } catch (error) {
      activeJobIdRef.current = null;
      setState({
        id: null,
        status: "finished",
        backendStatus: "error",
        total: plates.length,
        done: 0,
        currentPlate: "",
        errors: [],
        errorMessage: error instanceof Error ? error.message : "No fue posible iniciar el reprocesamiento",
        wasCancelled: false,
      });
    }
  }, [poll, stopPolling]);

  const cancel = useCallback(async () => {
    const jobId = activeJobIdRef.current || state?.id;
    if (!jobId) return;
    const job = await cancelVehicleReprocessJob(jobId);
    activeJobIdRef.current = null;
    stopPolling();
    setState(normalizeJob(job));
  }, [state?.id, stopPolling]);

  const acknowledge = useCallback(async () => {
    const jobId = state?.id;
    if (!jobId) {
      setState(null);
      return;
    }
    try {
      await acknowledgeVehicleReprocessJob(jobId);
    } finally {
      setState(null);
    }
  }, [state?.id]);

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
