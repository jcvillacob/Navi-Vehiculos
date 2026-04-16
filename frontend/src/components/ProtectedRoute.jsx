import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

/**
 * Protege rutas por autenticacion y opcionalmente por permiso o rol legacy.
 *
 * Props:
 *   children  - Contenido a renderizar si el usuario tiene acceso
 *   permissions - Array de permisos permitidos (opcional).
 *   roles       - Array de roles permitidos (opcional legacy).
 *
 * Ejemplo:
 *   <ProtectedRoute permissions={["audit.view"]}>
 *     <AuditPage />
 *   </ProtectedRoute>
 */
export default function ProtectedRoute({ children, permissions, roles }) {
  const { user, isLoading, hasPermission } = useAuth();

  if (isLoading) {
    return <div className="auth-loading">Verificando sesion...</div>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (permissions && !permissions.some((permission) => hasPermission(permission))) {
    return <Navigate to="/" replace />;
  }

  if (roles && !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  return children;
}
