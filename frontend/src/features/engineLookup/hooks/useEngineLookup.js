import { useMemo, useRef, useState } from "react";

import {
  assignVehicleDatabase,
  createMotor,
  lookupVehicleStream,
  manualAssignVehicle,
  uploadMotorAttachment
} from "../../../api/vehicleApi";

const MIN_STEP_MS = 220;

export function useEngineLookup() {
  const [loading, setLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState(null);
  const [error, setError] = useState("");
  const [steps, setSteps] = useState([]);
  const abortRef = useRef(null);
  const lastStepAtRef = useRef(0);

  const isManualAssignment = useMemo(
    () =>
      Boolean(
        lookupResult &&
          lookupResult.status === "partial" &&
          lookupResult.plate &&
          !lookupResult.technical_engine_configuration
      ),
    [lookupResult]
  );

  const canRegisterCurrentMotor = useMemo(
    () =>
      Boolean(
        lookupResult &&
          lookupResult.status === "ok" &&
          lookupResult.technical_engine_configuration &&
          !lookupResult.registered_motor
      ),
    [lookupResult]
  );

  const canConfigureCurrentVehicle = useMemo(
    () =>
      Boolean(
        lookupResult &&
          (lookupResult.status === "ok" || isManualAssignment) &&
          lookupResult.plate
      ),
    [lookupResult, isManualAssignment]
  );

  const resetLookup = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    lastStepAtRef.current = 0;
    setLookupResult(null);
    setError("");
    setLoading(false);
    setSteps([]);
  };

  const searchVehicle = async (identifier, { force = false } = {}) => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError("");
    setLookupResult(null);
    setSteps([]);
    lastStepAtRef.current = 0;

    try {
      const response = await lookupVehicleStream(identifier, {
        force,
        signal: controller.signal,
        onStep: async (step) => {
          const now = Date.now();
          const last = lastStepAtRef.current;
          if (last > 0) {
            const elapsed = now - last;
            if (elapsed < MIN_STEP_MS) {
              await new Promise((r) => setTimeout(r, MIN_STEP_MS - elapsed));
            }
          }
          lastStepAtRef.current = Date.now();
          setSteps((prev) => [...prev, step]);
        },
      });
      setLookupResult(response);
    } catch (err) {
      if (err?.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Error inesperado consultando motor");
    } finally {
      // Mantener el timeline visible un instante para que el usuario lea el
      // ultimo step antes de que se oculte al mostrarse el resultado.
      const last = lastStepAtRef.current;
      const elapsed = last > 0 ? Date.now() - last : 0;
      const remain = Math.max(0, MIN_STEP_MS - elapsed);
      if (remain > 0) {
        await new Promise((r) => setTimeout(r, remain));
      }
      setSteps([]);
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  const registerCurrentMotor = async (payload) => {
    const isOk = lookupResult && lookupResult.status === "ok";
    const isPartialManual =
      lookupResult &&
      lookupResult.status === "partial" &&
      lookupResult.plate &&
      !lookupResult.technical_engine_configuration;

    if (!isOk && !isPartialManual) {
      throw new Error("Debes consultar un vehiculo valido antes de registrar el motor.");
    }

    if (!lookupResult.plate) {
      throw new Error("La consulta actual no resolvio una placa valida.");
    }

    setLoading(true);
    setError("");
    try {
      let registeredMotor = lookupResult.registered_motor;
      let technicalNumber =
        payload.technical_number?.trim() ||
        lookupResult.technical_engine_configuration?.trim() ||
        "";

      if (!registeredMotor) {
        const cleanName = (payload.engine_name || "").trim();
        if (!technicalNumber || !cleanName) {
          throw new Error("Debes informar nombre de motor y Technical Engine Configuration #.");
        }

        let motor;
        try {
          motor = await createMotor({
            technical_number: technicalNumber,
            engine_name: cleanName
          });
        } catch (err) {
          // If motor already exists (409), that's ok for manual assignment
          if (err.message && err.message.includes("409")) {
            motor = { id: null, technical_number: technicalNumber, engine_name: cleanName };
          } else {
            throw err;
          }
        }

        if (payload.attachmentFile && motor.id) {
          await uploadMotorAttachment(motor.id, {
            cpl: payload.attachmentCpl,
            file: payload.attachmentFile
          });
        }

        registeredMotor = {
          id: motor.id,
          technical_number: motor.technical_number,
          engine_name: motor.engine_name
        };
      }

      // For manual assignment (partial without TEC#), register the vehicle assignment first
      if (isPartialManual) {
        await manualAssignVehicle(lookupResult.plate, {
          technical_number: technicalNumber,
          cpl: payload.attachmentCpl || null,
          marketing_model_name: lookupResult.marketing_model_name || null,
          service_model_name: lookupResult.service_model_name || null,
          vin: lookupResult.vin || null,
          engine_number: lookupResult.engine_number || null,
          marca: lookupResult.marca || null,
          linea: lookupResult.linea || null,
          ano_modelo: lookupResult.ano_modelo || null,
          tipo_combustible: lookupResult.tipo_combustible || null,
          geotab_status: lookupResult.geotab_status || "unknown"
        });
      }

      let assignedDatabase = lookupResult.assigned_database || null;

      if (payload.customer_database_id) {
        assignedDatabase = await assignVehicleDatabase(lookupResult.plate, {
          customer_database_id: payload.customer_database_id
        });
      }

      setLookupResult((current) =>
        current
          ? {
              ...current,
              status: "ok",
              technical_engine_configuration: technicalNumber,
              registered_motor: registeredMotor,
              assigned_database: assignedDatabase
            }
          : current
      );

      return {
        registeredMotor,
        assignedDatabase
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible registrar el motor";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return {
    loading,
    lookupResult,
    error,
    steps,
    isManualAssignment,
    canRegisterCurrentMotor,
    canConfigureCurrentVehicle,
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  };
}
