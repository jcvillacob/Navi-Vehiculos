import { useAuth } from "../context/AuthContext";

export default function Can({ permission, children, fallback = null }) {
  const { hasPermission } = useAuth();

  if (!permission) {
    return children;
  }

  const permissions = Array.isArray(permission) ? permission : [permission];
  return permissions.some((item) => hasPermission(item)) ? children : fallback;
}
