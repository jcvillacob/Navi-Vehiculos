import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { fetchMe, loginUser, logoutUser } from "../api/vehicleApi";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  // Al montar: restaurar sesion desde la cookie httpOnly
  useEffect(() => {
    fetchMe()
      .then((data) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    const handleUpdated = (event) => setUser(event.detail?.user ?? null);
    const handleExpired = () => setUser(null);

    window.addEventListener("auth:updated", handleUpdated);
    window.addEventListener("auth:expired", handleExpired);
    return () => {
      window.removeEventListener("auth:updated", handleUpdated);
      window.removeEventListener("auth:expired", handleExpired);
    };
  }, []);

  const login = useCallback(async (username, password) => {
    const data = await loginUser(username, password);
    setUser(data);
    return data;
  }, []);

  const logout = useCallback(async () => {
    await logoutUser().catch(() => {});
    setUser(null);
  }, []);

  const hasPermission = useCallback(
    (permission) => Boolean(user?.permissions?.includes(permission)),
    [user]
  );

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, setUser, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de AuthProvider");
  return ctx;
}

export function usePermission(permission) {
  const { hasPermission } = useAuth();
  return hasPermission(permission);
}
