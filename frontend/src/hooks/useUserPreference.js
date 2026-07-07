import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "../context/AuthContext";
import {
  deleteMyPreference,
  fetchMyPreference,
  updateMyPreference,
} from "../api/userPreferencesApi";

function isNotFoundError(err) {
  if (!err) return false;
  const message = String(err?.message || err || "");
  return /404/.test(message) || /no encontrada/i.test(message);
}

export function useUserPreference(key, defaultValue, options = {}) {
  const { validator } = options;
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const validatorRef = useRef(validator);
  validatorRef.current = validator;
  const defaultValueRef = useRef(defaultValue);
  defaultValueRef.current = defaultValue;

  const [value, setValueState] = useState(defaultValue);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const lastSavedRef = useRef(defaultValue);

  useEffect(() => {
    if (!userId) {
      setValueState(defaultValueRef.current);
      lastSavedRef.current = defaultValueRef.current;
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    setError(null);
    let cancelled = false;
    fetchMyPreference(key)
      .then((data) => {
        if (cancelled) return;
        const raw = data?.value;
        const next = validatorRef.current ? validatorRef.current(raw) : raw;
        if (next === undefined || next === null) {
          setValueState(defaultValueRef.current);
          lastSavedRef.current = defaultValueRef.current;
        } else {
          setValueState(next);
          lastSavedRef.current = next;
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (isNotFoundError(err)) {
          setValueState(defaultValueRef.current);
          lastSavedRef.current = defaultValueRef.current;
        } else {
          setValueState(defaultValueRef.current);
          lastSavedRef.current = defaultValueRef.current;
          setError(err);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId, key]);

  const setValue = useCallback(
    (next) => {
      const previous = lastSavedRef.current;
      setValueState(next);
      lastSavedRef.current = next;
      updateMyPreference(key, next).catch((err) => {
        lastSavedRef.current = previous;
        setValueState(previous);
        setError(err);
      });
    },
    [key]
  );

  const reset = useCallback(async () => {
    const previous = lastSavedRef.current;
    setValueState(defaultValueRef.current);
    lastSavedRef.current = defaultValueRef.current;
    try {
      await deleteMyPreference(key);
    } catch (err) {
      lastSavedRef.current = previous;
      setValueState(previous);
      setError(err);
    }
  }, [key]);

  return { value, setValue, reset, loading, error };
}
