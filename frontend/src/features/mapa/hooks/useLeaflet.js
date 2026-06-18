import L from "leaflet";

export function useLeaflet() {
  return {
    L,
    ready: true,
    loading: false,
    error: "",
  };
}
