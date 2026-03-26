import { useMemo, useState } from "react";

import {
  assignVehicleDatabase,
  createMotor,
  lookupVehicle,
  manualAssignVehicle,
  uploadMotorAttachment
} from "../../../api/vehicleApi";

export function useEngineLookup() {
  const [loading, setLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState(null);
  const [error, setError] = useState("");

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
    setLookupResult(null);
    setError("");
    setLoading(false);
  };

  const searchVehicle = async (identifier, { force = false } = {}) => {
    setLoading(true);
    setError("");
    setLookupResult(null);

    try {
      const response = await lookupVehicle(identifier, { force });
      setLookupResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error inesperado consultando motor");
    } finally {
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
    isManualAssignment,
    canRegisterCurrentMotor,
    canConfigureCurrentVehicle,
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  };
}
