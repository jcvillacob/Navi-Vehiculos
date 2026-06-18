import { useEffect, useState } from "react";

import { listVehicleAssignments } from "../../../api/vehicleApi";

export function useVehicleAssignments() {
  const [loading, setLoading] = useState(false);
  const [vehicles, setVehicles] = useState([]);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  const loadVehicles = async (nextSearch = search) => {
    setLoading(true);
    setError("");
    try {
      const records = await listVehicleAssignments(nextSearch);
      setVehicles(records);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible cargar los vehiculos");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      loadVehicles(search);
    }, 250);

    return () => window.clearTimeout(timeoutId);
  }, [search]);

  // Aplica un patch parcial a un solo vehiculo en memoria (por placa), sin
  // recargar toda la tabla.
  const patchVehicle = (plate, patch) => {
    const normalizedPlate = (plate || "").trim().toUpperCase();
    setVehicles((prev) =>
      prev.map((vehicle) =>
        vehicle.plate === normalizedPlate ? { ...vehicle, ...patch } : vehicle
      )
    );
  };

  return {
    loading,
    vehicles,
    error,
    search,
    setSearch,
    loadVehicles,
    patchVehicle
  };
}
