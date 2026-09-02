import { memo, useMemo } from "react";
import { Link } from "react-router-dom";

import { SortButton } from "../../../components/SortButton";
import { getRowAlerts, isRowFlagged } from "../filters";
import { getStatusClass, getStatusLabel } from "../status";
import RowAlertsCell from "./RowAlertsCell";

function ConnPctCell({ stats }) {
  if (!stats || !(stats.days_checked > 0)) {
    return <span className="conn-pct-badge conn-pct-none">--</span>;
  }
  const level = stats.connection_pct >= 80 ? "good" : stats.connection_pct >= 50 ? "warn" : "bad";
  const alert = stats.consecutive_disconnected >= 3;
  return (
    <span className={`conn-pct-badge conn-pct-${level}${alert ? " conn-pct-alert" : ""}`}>
      <span className="conn-pct-bar">
        <span className="conn-pct-fill" style={{ width: `${stats.connection_pct}%` }} />
      </span>
      <span className="conn-pct-label">{Math.round(stats.connection_pct)}%</span>
    </span>
  );
}

function AvailabilityCell({ availability: a }) {
  if (!a) return <span className="availability-badge availability-empty">Sin Datos</span>;
  if (a.calculation_status === "not_in_cloudfleet") {
    return <span className="availability-badge availability-na" title="La placa no aparece en CloudFleet">No Aplica</span>;
  }
  if (a.calculation_status === "error") {
    return <span className="availability-badge availability-error" title={a.error_message || "Error en el calculo"}>Error</span>;
  }
  const pct = a.project_availability_pct ?? 0;
  const level = pct >= 97 ? "good" : pct >= 96 ? "warn" : "bad";
  const title = a.calculation_status === "no_orders"
    ? "Sin ordenes en el mes"
    : `${a.orders_considered} orden(es) consideradas | h_no_disp=${Number(a.h_no_disp || 0).toFixed(1)} / h_total=${Number(a.h_total || 0).toFixed(1)}`;
  return (
    <span className={`availability-badge availability-${level}`} title={title}>
      {pct.toFixed(1)}%
    </span>
  );
}

function renderCell(col, row, ctx, alerts, handlers, linkState) {
  switch (col.key) {
    case "status":
      return (
        <span
          className={`status-dot ${getStatusClass(row.calculation_status)}`}
          title={getStatusLabel(row.calculation_status)}
        />
      );
    case "alerts":
      return <RowAlertsCell alerts={alerts} />;
    case "plate":
      return (
        <Link to={`/vehiculo/${row.plate}`} className="ficha-plate-link" state={linkState}>
          <strong>{row.plate}</strong>
        </Link>
      );
    case "client":
      return row.is_adhoc
        ? <span className="adhoc-badge" title="Calculado con credenciales Navitrans Geotab">Navitrans</span>
        : col.getValue(row, ctx);
    case "database":
      return row.is_adhoc ? "Geotab Global" : col.getValue(row, ctx);
    case "conn_pct":
      return <ConnPctCell stats={ctx.connStats[row.plate]} />;
    case "availability_pct":
      return <AvailabilityCell availability={ctx.availabilityByPlate[row.plate]} />;
    case "source_provider":
      return row.source_provider || "-";
    default: {
      const value = col.getValue(row, ctx);
      return (
        <span className="cell-truncate" title={typeof value === "string" ? value : undefined}>
          {value}
        </span>
      );
    }
  }
}

const RendimientosRow = memo(function RendimientosRow({ row, columns, ctx, handlers, linkState }) {
  const alerts = getRowAlerts(row);
  const flagged = isRowFlagged(row, alerts);
  return (
    <tr className={flagged ? "row-flagged" : undefined}>
      {columns.map((col) => {
        const isConn = col.key === "conn_pct";
        const hasConnData = isConn && ctx.connStats[row.plate]?.days_checked > 0;
        return (
          <td
            key={col.key}
            data-label={col.label}
            onMouseEnter={hasConnData ? (event) => handlers.onConnEnter(row.plate, event) : undefined}
            onMouseLeave={hasConnData ? handlers.onConnLeave : undefined}
          >
            {renderCell(col, row, ctx, alerts, handlers, linkState)}
          </td>
        );
      })}
    </tr>
  );
});

/**
 * Tabla principal. `rows` ya viene filtrada/ordenada/paginada; `ctx` debe ser
 * memoizado por el padre ({ connStats, availabilityByPlate }).
 */
export default function RendimientosTable({
  columns,
  rows,
  totalRows,
  ctx,
  sort,
  onSortChange,
  error,
  stale,
  loading,
  linkState,
  onConnEnter,
  onConnLeave,
}) {
  const handlers = useMemo(() => ({ onConnEnter, onConnLeave }), [onConnEnter, onConnLeave]);
  return (
    <>
      {error ? (
        <p className="notice-banner notice-error" role="alert">
          {error}
          {stale && totalRows > 0 ? " Se muestran los últimos datos cargados (pueden estar desactualizados)." : ""}
        </p>
      ) : null}
      <div
        className={`rendimientos-table-shell${stale ? " is-stale" : ""}`}
        aria-busy={loading || undefined}
      >
        <table className="rendimientos-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} scope="col">
                  <div className="th-content">
                    <span>{column.label}</span>
                    <SortButton columnKey={column.key} currentSort={sort} onSortChange={onSortChange} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {totalRows === 0 ? (
              <tr>
                <td colSpan={columns.length} className="table-empty-row">
                  {loading ? "Cargando..." : "No hay cortes para los filtros actuales."}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <RendimientosRow
                  key={`${row.customer_database_id}-${row.plate}-${row.period_month}`}
                  row={row}
                  columns={columns}
                  ctx={ctx}
                  handlers={handlers}
                  linkState={linkState}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
