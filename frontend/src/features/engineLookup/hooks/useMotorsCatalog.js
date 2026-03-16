import { useEffect, useState } from "react";

import {
  createMotor,
  deleteMotorAttachment,
  listMotors,
  updateMotor,
  updateMotorAttachment,
  uploadMotorAttachment
} from "../../../api/vehicleApi";

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
      const { attachmentFile, attachmentCpl, ...motorPayload } = payload;
      const created = await createMotor(motorPayload);
      if (attachmentFile) {
        await uploadMotorAttachment(created.id, {
          cpl: attachmentCpl,
          file: attachmentFile
        });
      }
      const records = await listMotors();
      setMotors(records);
      return created;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible registrar el motor";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const editMotor = async (motorId, payload) => {
    setLoading(true);
    setError("");
    try {
      const updated = await updateMotor(motorId, payload);
      setMotors((prev) =>
        prev.map((m) => (m.id === motorId ? updated : m))
      );
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible actualizar el motor";
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
    registerMotor,
    editMotor,
    uploadAttachment: async (motorId, payload) => {
      setLoading(true);
      setError("");
      try {
        await uploadMotorAttachment(motorId, payload);
        const records = await listMotors();
        setMotors(records);
      } catch (err) {
        const message = err instanceof Error ? err.message : "No fue posible subir el adjunto";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    updateAttachment: async (attachmentId, payload) => {
      setLoading(true);
      setError("");
      try {
        await updateMotorAttachment(attachmentId, payload);
        const records = await listMotors();
        setMotors(records);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "No fue posible actualizar el adjunto";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    deleteAttachment: async (attachmentId) => {
      setLoading(true);
      setError("");
      try {
        await deleteMotorAttachment(attachmentId);
        const records = await listMotors();
        setMotors(records);
      } catch (err) {
        const message = err instanceof Error ? err.message : "No fue posible eliminar el adjunto";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    }
  };
}
