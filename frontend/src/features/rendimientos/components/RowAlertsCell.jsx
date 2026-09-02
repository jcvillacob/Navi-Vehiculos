/**
 * Badge compacto con el numero de alertas de la fila; el tooltip (title)
 * lista cada alerta en una linea.
 */
export default function RowAlertsCell({ alerts }) {
  if (!alerts || alerts.length === 0) {
    return <span className="alerts-badge alerts-badge-none" aria-label="Sin alertas">-</span>;
  }
  const title = alerts.map((a) => `• ${a}`).join("\n");
  return (
    <span
      className="alerts-badge"
      title={title}
      aria-label={`${alerts.length} alerta(s): ${alerts.join("; ")}`}
    >
      <span aria-hidden="true">⚠</span> {alerts.length}
    </span>
  );
}
