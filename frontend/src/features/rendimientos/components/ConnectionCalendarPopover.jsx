import { createPortal } from "react-dom";

const CAL_STATUS_CLASS = {
  connected: "cal-c",
  disconnected: "cal-d",
  not_found: "cal-nf",
  error: "cal-e",
};
const CAL_MONTH_NAMES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

// Construye columnas semanales (lunes arriba) que cubren [from-01 .. fin de to].
// Cada celda: { date, status } o null (fuera de rango). Dias sin registro = status null.
export function buildCalendarWeeks(days, from, to) {
  const statusByDate = new Map((days || []).map((d) => [d.date, d.status]));
  const [fy, fm] = from.split("-").map(Number);
  const [ty, tm] = to.split("-").map(Number);
  const rangeStart = new Date(fy, fm - 1, 1);
  const rangeEnd = new Date(ty, tm, 0); // dia 0 del mes siguiente = ultimo dia de `to`

  // Alinea el inicio al lunes de su semana (getDay: 0=Dom..6=Sab -> lunes=1).
  const gridStart = new Date(rangeStart);
  const dow = (gridStart.getDay() + 6) % 7; // 0=lunes
  gridStart.setDate(gridStart.getDate() - dow);

  const weeks = [];
  const cursor = new Date(gridStart);
  let connected = 0;
  let checked = 0;
  while (cursor <= rangeEnd) {
    const week = [];
    let weekMonth = null;
    for (let i = 0; i < 7; i += 1) {
      if (cursor < rangeStart || cursor > rangeEnd) {
        week.push(null);
      } else {
        const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, "0")}-${String(cursor.getDate()).padStart(2, "0")}`;
        const status = statusByDate.get(iso) || null;
        if (status === "connected") {
          connected += 1;
          checked += 1;
        } else if (status === "disconnected") {
          checked += 1;
        }
        if (weekMonth === null) weekMonth = cursor.getMonth();
        week.push({ date: iso, status });
      }
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push({ cells: week, month: weekMonth });
  }
  return { weeks, connected, checked };
}

export default function ConnectionCalendarPopover({ popover }) {
  if (!popover) return null;
  const { plate, rect, days, loading, from, to } = popover;
  const { weeks, connected, checked } = loading || !days
    ? { weeks: [], connected: 0, checked: 0 }
    : buildCalendarWeeks(days, from, to);

  // Ancho: 11px celda + 3px gap por semana, + padding lateral (~28px).
  // Piso de 240px para que el encabezado y la leyenda (4 items) no se
  // apilen cuando el rango es corto (pocas semanas). Se limita al viewport;
  // el grid interno hace scroll-x si excede.
  const gridWidth = weeks.length > 0 ? weeks.length * 14 - 3 + 28 : 260;
  const contentWidth = Math.max(240, gridWidth);
  const width = Math.min(contentWidth, window.innerWidth - 16);

  // Posicion: arriba de la celda, centrado; con clamp para no salir de viewport.
  const left = Math.min(
    Math.max(8, rect.left + rect.width / 2 - width / 2),
    window.innerWidth - width - 8,
  );
  // Flip: si no hay espacio arriba (filas bajo el header sticky), abre abajo.
  const flipDown = rect.top < 260;
  const top = flipDown ? rect.bottom + 8 : rect.top - 8;

  // Etiquetas de mes: muestra abreviatura en la primera semana de cada mes.
  let lastMonth = null;
  const monthLabels = weeks.map((w) => {
    if (w.month !== null && w.month !== lastMonth) {
      lastMonth = w.month;
      return CAL_MONTH_NAMES[w.month];
    }
    return "";
  });

  const pct = checked > 0 ? Math.round((connected / checked) * 1000) / 10 : 0;

  return createPortal(
    <div
      className="conn-cal-popover"
      data-flip={flipDown ? "down" : "up"}
      style={{ left: `${left}px`, top: `${top}px`, width: `${width}px` }}
      role="tooltip"
    >
      <div className="conn-cal-header">
        <strong>{plate}</strong>
        {!loading && <span className="conn-cal-pct">{connected}/{checked} dias · {pct}%</span>}
      </div>
      {loading ? (
        <div className="conn-cal-loading">Cargando…</div>
      ) : days && days.length === 0 ? (
        <div className="conn-cal-loading">Sin registros de conexion</div>
      ) : (
        <>
          <div className="conn-cal-grid-wrap">
            <div className="conn-cal-months">
              {monthLabels.map((label, i) => (
                <span key={i} className="conn-cal-month">{label}</span>
              ))}
            </div>
            <div className="conn-cal-grid">
              {weeks.map((w, wi) => (
                <div key={wi} className="conn-cal-week">
                  {w.cells.map((cell, ci) => (
                    <span
                      key={ci}
                      className={`conn-cal-day ${cell ? (CAL_STATUS_CLASS[cell.status] || "cal-empty") : "cal-void"}`}
                      title={cell ? `${cell.date}: ${cell.status || "sin dato"}` : ""}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
          <div className="conn-cal-legend">
            <span className="conn-cal-legend-item"><span className="conn-cal-day cal-c" /> Conectado</span>
            <span className="conn-cal-legend-item"><span className="conn-cal-day cal-d" /> Desconectado</span>
            <span className="conn-cal-legend-item"><span className="conn-cal-day cal-nf" /> No hallado</span>
            <span className="conn-cal-legend-item"><span className="conn-cal-day cal-e" /> Fallo al medir</span>
            <span className="conn-cal-legend-item"><span className="conn-cal-day cal-empty" /> Sin medir</span>
          </div>
          <div className="conn-cal-note">% = días conectados sobre días medidos (conectado + desconectado). Los días amarillos/grises no cuentan.</div>
        </>
      )}
    </div>,
    document.body,
  );
}
