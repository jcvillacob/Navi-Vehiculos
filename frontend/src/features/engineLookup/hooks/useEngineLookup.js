import { useMemo, useState } from "react";

import {
  assignVehicleDatabase,
  createMotor,
  lookupVehicle,
  uploadMotorAttachment
} from "../../../api/vehicleApi";

export function useEngineLookup() {
  const [loading, setLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState(null);
  const [error, setError] = useState("");

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
    () => Boolean(lookupResult && lookupResult.status === "ok" && lookupResult.plate),
    [lookupResult]
  );

  const resetLookup = () => {
    setLookupResult(null);
    setError("");
    setLoading(false);
  };

  const searchVehicle = async (identifier) => {
    setLoading(true);
    setError("");
    setLookupResult(null);

    try {
      const response = await lookupVehicle(identifier);
      setLookupResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error inesperado consultando motor");
    } finally {
      setLoading(false);
    }
  };

  const registerCurrentMotor = async (payload) => {
    if (!lookupResult || lookupResult.status !== "ok") {
      throw new Error("Debes consultar un vehiculo valido antes de registrar el motor.");
    }

    if (!lookupResult.plate) {
      throw new Error("La consulta actual no resolvio una placa valida.");
    }

    setLoading(true);
    setError("");
    try {
      let registeredMotor = lookupResult.registered_motor;

      if (!registeredMotor) {
        const technicalNumber =
          payload.technical_number?.trim() ||
          lookupResult.technical_engine_configuration?.trim() ||
          "";
        const cleanName = payload.engine_name.trim();
        if (!technicalNumber || !cleanName) {
          throw new Error("Debes informar nombre de motor y Technical Engine Configuration #.");
        }

        const motor = await createMotor({
          technical_number: technicalNumber,
          engine_name: cleanName
        });

        if (payload.attachmentFile) {
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
    canRegisterCurrentMotor,
    canConfigureCurrentVehicle,
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  };
}
