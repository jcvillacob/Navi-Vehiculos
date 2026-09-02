import { formatMetric } from "../../../utils/formatters";

/**
 * Tarjetas de resumen de las filas visibles (ya filtradas).
 */
export default function RendimientosSummary({ summary }) {
  return (
    <section className="rendimientos-summary-grid" aria-label="Resumen de filas visibles">
      <article className="card metric-card">
        <span className="eyebrow">Placas visibles</span>
        <strong>{summary.vehicles}</strong>
      </article>
      <article className="card metric-card">
        <span className="eyebrow">Kms ECM</span>
        <strong>{formatMetric(summary.kms, 0)}</strong>
      </article>
      <article className="card metric-card">
        <span className="eyebrow">Horas ECM</span>
        <strong>{formatMetric(summary.hours, 1)}</strong>
      </article>
      <article className="card metric-card">
        <span className="eyebrow">Galones</span>
        <strong>{formatMetric(summary.gallons, 0)}</strong>
      </article>
      <article className="card metric-card feature-card-accent">
        <span className="eyebrow">KPG</span>
        <strong>{formatMetric(summary.kpg, 2)}</strong>
      </article>
      <article className="card metric-card">
        <span className="eyebrow">GPH</span>
        <strong>{formatMetric(summary.gph, 2)}</strong>
      </article>
    </section>
  );
}

/**
 * Totales de las filas filtradas. KPG/GPH solo suman filas con ambos valores
 * presentes: una fila con kms_ecm pero sin fuel_gallons (status "partial") no
 * debe aportar km al numerador sin aportar galones al denominador.
 */
export function computeVisibleSummary(rows) {
  let kms = 0;
  let hours = 0;
  let gallons = 0;
  let kpgKms = 0;
  let kpgGallons = 0;
  let gphGallons = 0;
  let gphHours = 0;
  for (const row of rows) {
    kms += row.kms_ecm || 0;
    hours += row.hours_ecm || 0;
    gallons += row.fuel_gallons || 0;
    if (row.fuel_gallons > 0 && row.kms_ecm != null) {
      kpgKms += row.kms_ecm;
      kpgGallons += row.fuel_gallons;
    }
    if (row.hours_ecm > 0 && row.fuel_gallons != null) {
      gphGallons += row.fuel_gallons;
      gphHours += row.hours_ecm;
    }
  }
  return {
    kms,
    hours,
    gallons,
    vehicles: rows.length,
    kpg: kpgGallons > 0 ? kpgKms / kpgGallons : 0,
    gph: gphHours > 0 ? gphGallons / gphHours : 0,
  };
}
