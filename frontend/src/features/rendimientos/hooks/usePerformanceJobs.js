import { useCallback, useEffect, useRef, useState } from "react";

import {
  calculateMonthlyPerformance,
  cancelPerformanceJob,
  fetchActivePerformanceJobs,
  fetchPerformanceJob,
  fetchRecentPerformanceJobs,
} from "../../../api/vehicleApi";
import { formatMonthLabel, generateMonthRange, normalizeMonthRange } from "../../../utils/formatters";

const IDLE_PROGRESS = Object.freeze({
  current: 0,
  total: 0,
  currentMonth: "",
  processedTargets: 0,
  totalTargets: 0,
  jobId: null,
});

const POLL_MIN_MS = 3000;
const POLL_MAX_MS = 10000;
const POLL_BACKOFF_FACTOR = 1.5;
const HISTORY_REFRESH_MS = 5000;
const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

async function ensureNotificationPermission() {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  if (Notification.permission === "granted" || Notification.permission === "denied") {
    return Notification.permission;
  }
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

function fireBrowserNotification(title, body) {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body, tag: "rendimientos-job" });
    n.onclick = () => {
      window.focus();
      n.close();
    };
  } catch {
    // ignore: algunos navegadores requieren contexto de usuario fresco
  }
}

function isDocumentHidden() {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/**
 * Jobs de calculo de rendimientos: disparo por rango de meses, UN solo
 * poller (activeJobIdRef) con backoff 3s→10s que se pausa con la pestana
 * oculta, resume-on-mount, cancelacion e historial reciente (que solo se
 * auto-refresca mientras hay un job activo).
 *
 * @param {object} options
 * @param {(kind: string, text: string) => void} options.pushToast
 * @param {(range: {from: string, to: string}) => void} [options.onCompleted]
 *   Se invoca cuando termina un calculo (manual o retomado) con el rango
 *   procesado, para que la pagina refresque datos si aplica.
 */
export function usePerformanceJobs({ pushToast, onCompleted }) {
  const [calculating, setCalculating] = useState(false);
  const [progress, setProgress] = useState(IDLE_PROGRESS);
  const [recentJobs, setRecentJobs] = useState([]);
  const [recentJobsLoading, setRecentJobsLoading] = useState(false);

  const mountedRef = useRef(true);
  const cancelledRef = useRef(false);
  const activeJobIdRef = useRef(null);
  const wakeRef = useRef(null);
  const pushToastRef = useRef(pushToast);
  const onCompletedRef = useRef(onCompleted);
  pushToastRef.current = pushToast;
  onCompletedRef.current = onCompleted;

  const toast = useCallback((kind, text) => {
    if (mountedRef.current) pushToastRef.current?.(kind, text);
  }, []);

  // Sleep cancelable: cancel()/unmount despiertan la espera de inmediato.
  const sleep = useCallback((ms) => new Promise((resolve) => {
    const id = setTimeout(() => {
      wakeRef.current = null;
      resolve();
    }, ms);
    wakeRef.current = () => {
      clearTimeout(id);
      wakeRef.current = null;
      resolve();
    };
  }), []);

  // Pausa el polling mientras la pestana esta oculta.
  const waitForVisible = useCallback(() => {
    if (!isDocumentHidden()) return Promise.resolve();
    return new Promise((resolve) => {
      const finish = () => {
        document.removeEventListener("visibilitychange", onChange);
        wakeRef.current = null;
        resolve();
      };
      const onChange = () => {
        if (!isDocumentHidden()) finish();
      };
      document.addEventListener("visibilitychange", onChange);
      wakeRef.current = finish;
    });
  }, []);

  const wake = useCallback(() => {
    if (wakeRef.current) wakeRef.current();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    cancelledRef.current = false;
    return () => {
      mountedRef.current = false;
      cancelledRef.current = true;
      activeJobIdRef.current = null;
      wake();
    };
  }, [wake]);

  const resetProgress = useCallback(() => {
    if (!mountedRef.current) return;
    setCalculating(false);
    setProgress(IDLE_PROGRESS);
  }, []);

  /**
   * Hace polling de un job hasta estado terminal. Devuelve el job final o
   * null si se cancelo/desmonto.
   */
  const pollJobUntilDone = useCallback(async (jobId, monthLabel, monthIndex, monthsTotal) => {
    activeJobIdRef.current = jobId;
    let interval = POLL_MIN_MS;
    let lastProcessed = -1;
    try {
      while (mountedRef.current && !cancelledRef.current) {
        await waitForVisible();
        if (!mountedRef.current || cancelledRef.current) break;

        let job;
        try {
          job = await fetchPerformanceJob(jobId);
        } catch {
          await sleep(interval);
          interval = Math.min(POLL_MAX_MS, interval * POLL_BACKOFF_FACTOR);
          continue;
        }
        if (!mountedRef.current || cancelledRef.current) break;

        setProgress({
          current: monthIndex + 1,
          total: monthsTotal,
          currentMonth: monthLabel,
          processedTargets: job.processed_targets || 0,
          totalTargets: job.total_targets || 0,
          jobId: job.id,
        });
        if (!ACTIVE_JOB_STATUSES.has(job.status)) return job;

        // Backoff: si hubo avance volvemos a 3s; si no, crecemos hasta 10s.
        const processed = job.processed_targets || 0;
        interval = processed !== lastProcessed ? POLL_MIN_MS : Math.min(POLL_MAX_MS, interval * POLL_BACKOFF_FACTOR);
        lastProcessed = processed;
        await sleep(interval);
      }
      return null;
    } finally {
      if (activeJobIdRef.current === jobId) activeJobIdRef.current = null;
    }
  }, [sleep, waitForVisible]);

  const reloadRecentJobs = useCallback(async () => {
    if (!mountedRef.current) return;
    setRecentJobsLoading(true);
    try {
      const jobs = await fetchRecentPerformanceJobs(50);
      if (mountedRef.current) setRecentJobs(jobs);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "No fue posible cargar el historial");
    } finally {
      if (mountedRef.current) setRecentJobsLoading(false);
    }
  }, [toast]);

  // Historial: carga inicial; auto-refresco SOLO mientras hay un job activo;
  // refresco final cuando el calculo termina.
  useEffect(() => {
    reloadRecentJobs();
  }, [reloadRecentJobs]);

  const wasCalculatingRef = useRef(false);
  useEffect(() => {
    if (calculating) {
      wasCalculatingRef.current = true;
      const id = setInterval(() => { reloadRecentJobs(); }, HISTORY_REFRESH_MS);
      return () => clearInterval(id);
    }
    if (wasCalculatingRef.current) {
      wasCalculatingRef.current = false;
      reloadRecentJobs();
    }
    return undefined;
  }, [calculating, reloadRecentJobs]);

  const announceFinalJob = useCallback((finalJob, month) => {
    const label = formatMonthLabel(month);
    if (finalJob.status === "done") {
      const s = finalJob.summary || {};
      fireBrowserNotification(`Rendimiento ${label} listo`, `${s.calculated || 0} calculadas / ${s.total || 0} placas`);
      return true;
    }
    if (finalJob.status === "error") {
      const message = finalJob.error_message || "Error desconocido";
      toast("error", `Error en ${label}: ${message}`);
      fireBrowserNotification(`Rendimiento ${label} falló`, finalJob.error_message || "Revisa los logs");
      return false;
    }
    toast("info", `El cálculo de ${label} terminó con estado "${finalJob.status}".`);
    return false;
  }, [toast]);

  // Resume-on-mount: si hay un job activo (p. ej. el usuario navego y volvio)
  // retomamos visualmente el mas reciente.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const activeJobs = await fetchActivePerformanceJobs();
        if (cancelled || !mountedRef.current || activeJobs.length === 0) return;
        if (activeJobIdRef.current) return; // ya hay polling propio en curso
        const job = activeJobs[0];
        cancelledRef.current = false;
        setCalculating(true);
        setProgress({
          current: 1,
          total: 1,
          currentMonth: job.month,
          processedTargets: job.processed_targets || 0,
          totalTargets: job.total_targets || 0,
          jobId: job.id,
        });
        const finalJob = await pollJobUntilDone(job.id, job.month, 0, 1);
        if (!finalJob || cancelled || !mountedRef.current) return;
        if (announceFinalJob(finalJob, finalJob.month)) {
          const s = finalJob.summary || {};
          toast("success", `Rendimiento ${formatMonthLabel(finalJob.month)} listo (${s.calculated || 0} de ${s.total || 0} placas).`);
          onCompletedRef.current?.({ from: finalJob.month, to: finalJob.month });
        }
        resetProgress();
      } catch {
        // silencioso: si la API no responde el listado de jobs, no hacemos nada
      }
    })();
    return () => { cancelled = true; };
  }, [pollJobUntilDone, announceFinalJob, resetProgress, toast]);

  /**
   * Dispara el calculo mes a mes en [monthFrom, monthTo].
   * `buildPayload(month)` devuelve el body para /rendimientos/calculate.
   */
  const runCalculation = useCallback(async ({ monthFrom, monthTo, buildPayload }) => {
    if (activeJobIdRef.current) {
      toast("info", "Ya hay un cálculo en curso.");
      return;
    }
    const [from, to] = normalizeMonthRange(monthFrom, monthTo);
    const months = generateMonthRange(from, to);
    if (months.length === 0) return;

    cancelledRef.current = false;
    setCalculating(true);
    setProgress({ ...IDLE_PROGRESS, total: months.length });

    // Pedimos permiso para notificaciones del navegador la primera vez.
    await ensureNotificationPermission();

    let errors = 0;
    for (let i = 0; i < months.length; i += 1) {
      if (cancelledRef.current || !mountedRef.current) break;
      const m = months[i];
      setProgress({ ...IDLE_PROGRESS, current: i + 1, total: months.length, currentMonth: m });

      let createResponse;
      try {
        createResponse = await calculateMonthlyPerformance(buildPayload(m));
      } catch (err) {
        errors += 1;
        toast("error", `Error en ${formatMonthLabel(m)}: ${err instanceof Error ? err.message : "Error desconocido"}`);
        continue;
      }
      if (cancelledRef.current || !mountedRef.current) break;

      const { job: createdJob, reused } = createResponse;
      if (reused) {
        toast("info", `Ya hay un cálculo en curso para ${formatMonthLabel(m)} — siguiendo el job existente.`);
      }

      const finalJob = await pollJobUntilDone(createdJob.id, m, i, months.length);
      if (!finalJob) break; // cancelado o desmontado
      if (!announceFinalJob(finalJob, m)) errors += 1;
    }

    if (!mountedRef.current) return;
    if (!cancelledRef.current) {
      const ok = months.length - errors;
      if (ok > 0) toast("success", `Rendimientos calculados: ${ok} de ${months.length} mes(es).`);
      onCompletedRef.current?.({ from, to });
    }
    resetProgress();
  }, [pollJobUntilDone, announceFinalJob, resetProgress, toast]);

  const cancel = useCallback(async () => {
    cancelledRef.current = true;
    wake();
    const jobId = activeJobIdRef.current;
    activeJobIdRef.current = null;
    if (jobId) {
      try {
        await cancelPerformanceJob(jobId);
        toast("info", "Cálculo cancelado.");
      } catch {
        toast("error", "No se pudo cancelar el job en el servidor.");
      }
    }
    resetProgress();
    reloadRecentJobs();
  }, [wake, toast, resetProgress, reloadRecentJobs]);

  return {
    calculating,
    progress,
    recentJobs,
    recentJobsLoading,
    reloadRecentJobs,
    runCalculation,
    cancel,
  };
}
