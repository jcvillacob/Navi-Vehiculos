import { useMemo, useState } from "react";

import { getVehicleByPlate } from "../../../api/vehicleApi";

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

export function useEngineLookup() {
  const [loading, setLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState(null);
  const [error, setError] = useState("");
  const [registeredVehicles, setRegisteredVehicles] = useState([
    {
      id: "veh-1",
      plate: "TLK240",
      engineName: "",
      registeredAt: "2026-03-05",
      engineType: "Diesel"
    }
  ]);

  const canContinueToRegister = useMemo(
    () => Boolean(lookupResult && lookupResult.status === "ok"),
    [lookupResult]
  );

  const resetLookup = () => {
    setLookupResult(null);
    setError("");
    setLoading(false);
  };

  const searchVehicle = async (plate, fuelType) => {
    setLoading(true);
    setError("");
    setLookupResult(null);

    try {
      const response = await getVehicleByPlate(plate);
      setLookupResult({
        ...response,
        fuel_type: fuelType
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error inesperado consultando motor");
    } finally {
      setLoading(false);
    }
  };

  const addTemporaryVehicleFromLookup = () => {
    if (!lookupResult || lookupResult.status !== "ok") {
      return;
    }

    const newVehicle = {
      id: `veh-${Date.now()}`,
      plate: lookupResult.plate,
      engineName: "",
      registeredAt: todayIsoDate(),
      engineType: lookupResult.fuel_type || "Diesel"
    };

    setRegisteredVehicles((prev) => [newVehicle, ...prev]);
  };

  const updateVehicle = (vehicleId, nextData) => {
    setRegisteredVehicles((prev) =>
      prev.map((vehicle) => (vehicle.id === vehicleId ? { ...vehicle, ...nextData } : vehicle))
    );
  };

  const removeVehicle = (vehicleId) => {
    setRegisteredVehicles((prev) => prev.filter((vehicle) => vehicle.id !== vehicleId));
  };

  return {
    loading,
    lookupResult,
    error,
    registeredVehicles,
    canContinueToRegister,
    searchVehicle,
    addTemporaryVehicleFromLookup,
    updateVehicle,
    removeVehicle,
    resetLookup
  };
}