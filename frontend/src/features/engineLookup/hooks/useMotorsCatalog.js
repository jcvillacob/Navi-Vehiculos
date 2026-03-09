import { useEffect, useState } from "react";

import { createMotor, listMotors } from "../../../api/vehicleApi";

export function useMotorsCatalog() {
  const [loading, setLoading] = useState(false);
  const [motors, setMotors] = useState([]);
  const [error, setError] = useState("");

  const loadMotors = async () => {
    setLoading(true);
    setError("");
    try {
      const records = await listMotors();
      setMotors(records);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible cargar los motores");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMotors();
  }, []);

  const registerMotor = async (payload) => {
    setLoading(true);
    setError("");
    try {
      const created = await createMotor(payload);
      setMotors((prev) => [created, ...prev]);
      return created;
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
    motors,
    error,
    loadMotors,
    registerMotor
  };
}
