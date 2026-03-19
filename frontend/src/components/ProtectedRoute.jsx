import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

/**
 * Protege rutas por autenticacion y opcionalmente por rol.
 *
 * Props:
 *   children  - Contenido a renderizar si el usuario tiene acceso
 *   roles     - Array de roles permitidos (opcional). Si se omite, cualquier usuario autenticado puede acceder.
 *
 * Ejemplo:
 *   <ProtectedRoute roles={["admin"]}>
 *     <AuditPage />
 *   </ProtectedRoute>
 */
export default function ProtectedRoute({ children, roles }) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return <div className="auth-loading">Verificando sesion...</div>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (roles && !roles.includes(user.role)) {
    return <Navigate to="/" replace />;
  }

  return children;
}
