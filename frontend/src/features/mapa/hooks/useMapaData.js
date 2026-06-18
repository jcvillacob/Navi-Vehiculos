import { useEffect, useMemo, useState } from "react";

import { GEOFENCES, VEHICLES } from "../mockData";

export function useMapaData() {
  const [loading, setLoading] = useState(true);
  const [geofences, setGeofences] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    // Simula una carga asincrona para dejar el hueco por donde entrara la API real.
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setGeofences(GEOFENCES);
      setVehicles(
        VEHICLES.map((v) => ({
          ...v,
          geofenceName:
            GEOFENCES.find((g) => g.id === v.geofenceId)?.name ?? v.geofenceId,
        }))
      );
      setLoading(false);
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const vehiclesByGeofence = useMemo(() => {
    const map = Object.fromEntries(geofences.map((g) => [g.id, []]));
    for (const v of vehicles) {
      if (map[v.geofenceId]) map[v.geofenceId].push(v);
    }
    return map;
  }, [geofences, vehicles]);

  return { loading, error, geofences, vehicles, vehiclesByGeofence };
}
