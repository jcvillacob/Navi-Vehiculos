import { useState } from "react";

import { lookupVehicle } from "../../../api/vehicleApi";

export function usePlateLookup() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const searchByPlate = async (plate) => {
    setLoading(true);
    setError("");
    setResult(null);

    try {
      const data = await lookupVehicle(plate);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error inesperado");
    } finally {
      setLoading(false);
    }
  };

  return {
    loading,
    result,
    error,
    searchByPlate,
  };
}
