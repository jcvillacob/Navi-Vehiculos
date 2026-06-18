// Categorias compartidas para clientes y vehiculos.
// "Ninguna" es el valor neutro / por defecto.
export const CUSTOMER_CATEGORIES = ["Ninguna", "Experiencia Superior", "Flota Administrada"];

// Sufijo de clase CSS para el badge de cada categoria (ver styles.css).
const CATEGORY_CLASS = {
  "Experiencia Superior": "is-experiencia",
  "Flota Administrada": "is-flota",
  Ninguna: "is-ninguna"
};

export function categoryBadgeClass(category) {
  return `category-badge ${CATEGORY_CLASS[category] || "is-ninguna"}`;
}
