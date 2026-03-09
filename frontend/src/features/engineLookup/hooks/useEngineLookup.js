import { useMemo, useState } from "react";

import { createMotor, getVehicleByPlate } from "../../../api/vehicleApi";

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

  const resetLookup = () => {
    setLookupResult(null);
    setError("");
    setLoading(false);
  };

  const searchVehicle = async (plate) => {
    setLoading(true);
    setError("");
    setLookupResult(null);

    try {
      const response = await getVehicleByPlate(plate);
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

    const technicalNumber =
      payload.technical_number?.trim() ||
      lookupResult.technical_engine_configuration?.trim() ||
      "";
    const cleanName = payload.engine_name.trim();
    if (!technicalNumber || !cleanName) {
      throw new Error("Debes informar nombre de motor y Technical Engine Configuration #.");
    }

    setLoading(true);
    setError("");
    try {
      const motor = await createMotor({
        technical_number: technicalNumber,
        engine_name: cleanName
      });

      setLookupResult((current) =>
        current
          ? {
              ...current,
              registered_motor: {
                id: motor.id,
                technical_number: motor.technical_number,
                engine_name: motor.engine_name
              }
            }
          : current
      );

      return motor;
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
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  };
}
