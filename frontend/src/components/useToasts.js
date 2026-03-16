import { useState, useCallback } from "react";

export function useToasts() {
  const [toasts, setToasts] = useState([]);

  const pushToast = useCallback((kind, text) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setToasts((current) => [...current, { id, kind, text }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, kind === "error" ? 5000 : 3200);
  }, []);

  return { toasts, pushToast };
}
