// Datos harcodeados de geocercas y vehiculos.
// Mas adelante se reemplazan por datos reales (Geotab / API); el shape se mantiene.

export const GEOFENCES = [
  {
    id: "bogota",
    name: "Bogota",
    lat: 4.711,
    lng: -74.0721,
    radiusM: 5000,
    color: "#ee2e2f",
  },
  {
    id: "medellin",
    name: "Medellin",
    lat: 6.2442,
    lng: -75.5812,
    radiusM: 5000,
    color: "#185979",
  },
  {
    id: "cali",
    name: "Cali",
    lat: 3.4516,
    lng: -76.532,
    radiusM: 5000,
    color: "#d4a017",
  },
];

export const VEHICLES = [
  // Bogota
  { plate: "ABC123", lat: 4.708, lng: -74.07, geofenceId: "bogota", hoursInside: 3.5, motor: "ISD", cliente: "Transportes Andina" },
  { plate: "DEF456", lat: 4.713, lng: -74.075, geofenceId: "bogota", hoursInside: 1.2, motor: "X15", cliente: "Logistik SA" },
  { plate: "GHI789", lat: 4.705, lng: -74.069, geofenceId: "bogota", hoursInside: 8.0, motor: "ISX", cliente: "Carga Express" },
  { plate: "JKL012", lat: 4.716, lng: -74.071, geofenceId: "bogota", hoursInside: 0.5, motor: "ISB", cliente: "Transportes Andina" },

  // Medellin
  { plate: "MNO345", lat: 6.242, lng: -75.58, geofenceId: "medellin", hoursInside: 2.1, motor: "ISD", cliente: "Antioquia Freight" },
  { plate: "PQR678", lat: 6.246, lng: -75.583, geofenceId: "medellin", hoursInside: 5.4, motor: "X15", cliente: "Valle Cargo" },
  { plate: "STU901", lat: 6.24, lng: -75.579, geofenceId: "medellin", hoursInside: 0.8, motor: "ISM", cliente: "Antioquia Freight" },

  // Cali
  { plate: "VWX234", lat: 3.45, lng: -76.531, geofenceId: "cali", hoursInside: 6.2, motor: "ISX", cliente: "Pacifico Logistica" },
  { plate: "YZA567", lat: 3.453, lng: -76.533, geofenceId: "cali", hoursInside: 1.9, motor: "ISD", cliente: "Sucursal Occidente" },
  { plate: "BCD890", lat: 3.449, lng: -76.53, geofenceId: "cali", hoursInside: 12.0, motor: "X15", cliente: "Pacifico Logistica" },
];
