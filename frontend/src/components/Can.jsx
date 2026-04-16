import { useAuth } from "../context/AuthContext";

export default function Can({ permission, children, fallback = null }) {
  const { hasPermission } = useAuth();

  if (!permission) {
    return children;
  }

  return hasPermission(permission) ? children : fallback;
}
