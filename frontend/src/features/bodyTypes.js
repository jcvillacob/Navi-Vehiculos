// Carrocerias y configuraciones de ejes de un vehiculo.
// Espeja app/services/vehicle_body_type.py: si cambia el catalogo del backend,
// hay que cambiarlo aqui tambien (el backend es quien valida).

export const BODY_TYPES = [
  "Tractocamion",
  "Mixer",
  "Volqueta",
  "Chasis camion",
  "Chasis bus",
  "Camioneta",
  "Camion",
  "Buseta",
  "Bus",
  "Van",
  "Grua"
];

// Configuraciones que aparecen en la flota. El backend acepta cualquier
// combinacion plausible; esta lista solo alimenta el desplegable.
export const AXLE_CONFIGS = ["4X2", "4X4", "6X2", "6X4", "6X6", "8X4"];

// Etiqueta para los vehiculos cuyo nombre Fenix no revela la carroceria
// (usados sin ficha comercial) y que aun no tienen override manual.
export const UNCLASSIFIED_BODY_TYPE = "Sin clasificar";

export function bodyTypeLabel(vehicle) {
  return vehicle?.body_type || UNCLASSIFIED_BODY_TYPE;
}

// La carroceria + los ejes es lo que en la calle se nombra "tractomula" o
// "dobletroque"; se muestran juntos en una sola celda.
export function bodyTypeWithAxles(vehicle) {
  const label = bodyTypeLabel(vehicle);
  return vehicle?.axle_config ? `${label} ${vehicle.axle_config}` : label;
}
