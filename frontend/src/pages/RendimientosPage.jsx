import { useEffect, useMemo, useRef, useState, useCallback } from "react";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import {
  calculateMonthlyPerformance,
  fetchMonthlyPerformance,
  listCustomers,
  listVehicleAssignments
} from "../api/vehicleApi";

function getCurrentMonth() {
  return new Date().toISOString().slice(0, 7);
}

function buildOptions(rows, getValue) {
  return [...new Set(rows.map(getValue).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function filterRows(rows, filters, omit = "") {
  return rows.filter((row) => {
    if (omit !== "client" && filters.client && row.client_name !== filters.client) return false;
    if (omit !== "database" && filters.database && row.database_name !== filters.database) return false;
    if (omit !== "motorGroup" && filters.motorGroup && (row.engine_name || "Sin catalogar") !== filters.motorGroup) {
      return false;
    }
    if (omit !== "plateSearch" && filters.plateSearch) {
      const normalizedPlate = (row.plate || "").toUpperCase();
      if (!normalizedPlate.includes(filters.plateSearch)) return false;
    }
    return true;
  });
}

function formatNumber(value, options) {
  if (value === null || value === undefined) return "-";
  return new Intl.NumberFormat("es-CO", options).format(value);
}

function formatMetric(value, digits = 1) {
  return formatNumber(value, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function getStatusLabel(status) {
  if (status === "calculated") return "Calculado";
  if (status === "partial") return "Parcial";
  if (status === "unbound") return "Sin binding";
  if (status === "no_data") return "Sin datos";
  if (status === "error") return "Error";
  return status || "Desconocido";
}

function getStatusClass(status) {
  if (status === "calculated") return "status-ok";
  if (status === "partial") return "status-soft";
  if (status === "unbound" || status === "no_data") return "status-partial";
  return "status-error";
}

function buildClientSelectionLabel(eligibleClients, selectedCustomerIds) {
  if (!eligibleClients.length) {
    return "Sin clientes";
  }
  if (!selectedCustomerIds.length) {
    return "Todos los clientes";
  }
  const selected = eligibleClients.filter((client) => selectedCustomerIds.includes(client.id));
  if (selected.length === 1) {
    return selected[0].name;
  }
  if (selected.length === 2) {
    return `${selected[0].name} y ${selected[1].name}`;
  }
  return `${selected.length} clientes seleccionados`;
}

function formatMonthLabel(monthStr) {
  if (!monthStr) return "";
  const [year, m] = monthStr.split("-");
  const names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  return `${names[parseInt(m, 10) - 1]} ${year}`;
}

export default function RendimientosPage() {
  const { toasts, pushToast } = useToasts();

  // ── Calculation controls (header) ──
  const [calcMonth, setCalcMonth] = useState(getCurrentMonth);
  const [calculating, setCalculating] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [eligibleClients, setEligibleClients] = useState([]);
  const [selectedCustomerIds, setSelectedCustomerIds] = useState([]);

  // ── Table range controls ──
  const [monthFrom, setMonthFrom] = useState(getCurrentMonth);
  const [monthTo, setMonthTo] = useState(getCurrentMonth);
  const [loading, setLoading] = useState(false);
  const [payload, setPayload] = useState({ month: getCurrentMonth(), summary: null, rows: [] });
  const [filters, setFilters] = useState({
    client: "",
    database: "",
    motorGroup: "",
    plateSearch: ""
  });

  const isRange = monthFrom !== monthTo;
  const pickerRef = useRef(null);

  // Close client picker on click outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) {
        pickerRef.current.removeAttribute("open");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const loadRecords = useCallback(async (from, to) => {
    setLoading(true);
    try {
      const response = await fetchMonthlyPerformance({ month_from: from, month_to: to });
      setPayload(response);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar rendimientos");
    } finally {
      setLoading(false);
    }
  }, [pushToast]);

  // Load eligible clients on mount
  useEffect(() => {
    let cancelled = false;
    setCatalogLoading(true);

    Promise.all([listCustomers(), listVehicleAssignments()])
      .then(([customers, vehicles]) => {
        if (cancelled) return;

        const readyByCustomerId = new Map();
        for (const vehicle of vehicles) {
          const providerType = vehicle.database_connection_type;
          if ((providerType !== "artimo" && providerType !== "geotab") || !vehicle.customer_id || !vehicle.plate) {
            continue;
          }
          if (!readyByCustomerId.has(vehicle.customer_id)) {
            readyByCustomerId.set(vehicle.customer_id, new Set());
          }
          readyByCustomerId.get(vehicle.customer_id).add(vehicle.plate);
        }

        const eligible = customers
          .map((customer) => ({
            id: customer.id,
            name: customer.name,
            readyVehicles: readyByCustomerId.get(customer.id)?.size || 0,
            hasPerformanceDatabase: (customer.databases || []).some((database) => database.connection_type === "artimo" || database.connection_type === "geotab")
          }))
          .filter(Boolean)
          .sort((a, b) => a.name.localeCompare(b.name));

        setEligibleClients(eligible);
      })
      .catch((err) => {
        if (!cancelled) {
          pushToast("error", err instanceof Error ? err.message : "No fue posible cargar clientes");
        }
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });

    return () => { cancelled = true; };
  }, [pushToast]);

  useEffect(() => {
    setSelectedCustomerIds((current) =>
      current.filter((customerId) => eligibleClients.some((client) => client.id === customerId))
    );
  }, [eligibleClients]);

  // Auto-load table when range changes
  useEffect(() => {
    loadRecords(monthFrom, monthTo).catch(() => {});
  }, [monthFrom, monthTo, loadRecords]);

  const clientOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "client");
    return buildOptions(subset, (row) => row.client_name);
  }, [filters, payload.rows]);

  const databaseOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "database");
    return buildOptions(subset, (row) => row.database_name);
  }, [filters, payload.rows]);

  const motorGroupOptions = useMemo(() => {
    const subset = filterRows(payload.rows, filters, "motorGroup");
    return buildOptions(subset, (row) => row.engine_name || "Sin catalogar");
  }, [filters, payload.rows]);

  useEffect(() => {
    if (filters.client && !clientOptions.includes(filters.client)) {
      setFilters((current) => ({ ...current, client: "" }));
    }
  }, [clientOptions, filters.client]);

  useEffect(() => {
    if (filters.database && !databaseOptions.includes(filters.database)) {
      setFilters((current) => ({ ...current, database: "" }));
    }
  }, [databaseOptions, filters.database]);

  useEffect(() => {
    if (filters.motorGroup && !motorGroupOptions.includes(filters.motorGroup)) {
      setFilters((current) => ({ ...current, motorGroup: "" }));
    }
  }, [filters.motorGroup, motorGroupOptions]);

  const filteredRows = useMemo(() => filterRows(payload.rows, filters), [filters, payload.rows]);

  const visibleSummary = useMemo(() => {
    const totals = filteredRows.reduce(
      (accumulator, row) => {
        accumulator.kms += row.kms_ecm || 0;
        accumulator.hours += row.hours_ecm || 0;
        accumulator.gallons += row.fuel_gallons || 0;
        return accumulator;
      },
      { kms: 0, hours: 0, gallons: 0 }
    );

    return {
      ...totals,
      vehicles: filteredRows.length,
      kpg: totals.gallons > 0 ? totals.kms / totals.gallons : 0,
      gph: totals.hours > 0 ? totals.gallons / totals.hours : 0
    };
  }, [filteredRows]);

  const handleChange = (key) => (event) => {
    const value = key === "plateSearch" ? event.target.value.toUpperCase() : event.target.value;
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const handleClear = () => {
    setFilters({ client: "", database: "", motorGroup: "", plateSearch: "" });
  };

  const handleCalculate = async () => {
    setCalculating(true);
    try {
      await calculateMonthlyPerformance({
        month: calcMonth,
        customer_ids: selectedCustomerIds,
        force_recalculate: true
      });
      pushToast("success", `Rendimientos calculados para ${formatMonthLabel(calcMonth)}.`);
      // Reload the table if the calculated month falls within the visible range
      if (calcMonth >= monthFrom && calcMonth <= monthTo) {
        await loadRecords(monthFrom, monthTo);
      }
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible calcular rendimientos");
    } finally {
      setCalculating(false);
    }
  };

  const rangeLabel = isRange
    ? `${formatMonthLabel(monthFrom)} – ${formatMonthLabel(monthTo)}`
    : formatMonthLabel(monthFrom);

  return (
    <section className="panel">
      <header className="page-header page-header-row">
        <div>
          <span className="eyebrow">Analitica operativa</span>
          <h2>Rendimientos</h2>
        </div>

        <div className="rendimientos-month-actions">
          <div className="form-field">
            <label htmlFor="rendimientos-calc-month">Mes a calcular</label>
            <input
              id="rendimientos-calc-month"
              type="month"
              value={calcMonth}
              onChange={(event) => setCalcMonth(event.target.value)}
            />
          </div>

          <details className="client-picker" ref={pickerRef}>
            <summary className="client-picker-summary">
              <span className="client-picker-label">Clientes</span>
              <span className="client-picker-value">{buildClientSelectionLabel(eligibleClients, selectedCustomerIds)}</span>
            </summary>

            <div className="client-picker-panel">
              <label className="client-picker-option" key="all-artimo-clients">
                <input
                  type="checkbox"
                  checked={selectedCustomerIds.length === 0}
                  onChange={() => setSelectedCustomerIds([])}
                />
                <span>
                  Todos los clientes
                  <small>
                    {eligibleClients.reduce((total, client) => total + client.readyVehicles, 0)} placas listas
                  </small>
                </span>
              </label>

              {catalogLoading ? (
                <p className="support-copy">Cargando clientes...</p>
              ) : eligibleClients.length === 0 ? (
                <p className="support-copy">No hay clientes registrados.</p>
              ) : (
                eligibleClients.map((client) => {
                  const checked = selectedCustomerIds.includes(client.id);
                  return (
                    <label className="client-picker-option" key={client.id}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          setSelectedCustomerIds((current) =>
                            checked
                              ? current.filter((value) => value !== client.id)
                              : [...current, client.id].sort((a, b) => a - b)
                          );
                        }}
                      />
                      <span>
                        {client.name}
                        <small>
                          {client.readyVehicles > 0
                            ? `${client.readyVehicles} placas listas`
                            : client.hasPerformanceDatabase
                              ? "Sin placas listas"
                              : "Sin database activa"}
                        </small>
                      </span>
                    </label>
                  );
                })
              )}
            </div>
          </details>

          <button type="button" onClick={handleCalculate} disabled={calculating || !calcMonth}>
            {calculating ? "Calculando..." : "Calcular"}
          </button>
        </div>
      </header>

      <ToastStack toasts={toasts} />

      <section className="rendimientos-summary-grid">
        <article className="card metric-card">
          <span className="eyebrow">Placas visibles</span>
          <strong>{visibleSummary.vehicles}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Kms ECM</span>
          <strong>{formatMetric(visibleSummary.kms, 0)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Horas ECM</span>
          <strong>{formatMetric(visibleSummary.hours, 0)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Galones</span>
          <strong>{formatMetric(visibleSummary.gallons, 0)}</strong>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">KPG</span>
          <strong>{formatMetric(visibleSummary.kpg, 2)}</strong>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">GPH</span>
          <strong>{formatMetric(visibleSummary.gph, 2)}</strong>
        </article>
      </section>

      <section className="card rendimientos-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">{isRange ? "Acumulado" : "Lote"} {rangeLabel}</span>
            <h3>Explorador {isRange ? "por rango" : "mensual"}</h3>
          </div>
          <span className="support-copy">
            {payload.summary?.total ?? 0} placas {isRange ? "acumuladas en el rango" : "calculadas o cargadas para el mes"}
          </span>
        </header>

        <div className="rendimientos-status-strip">
          <span className="status-chip is-calculated">Calculadas: {payload.summary?.calculated ?? 0}</span>
          <span className="status-chip is-partial">Parciales: {payload.summary?.partial ?? 0}</span>
          <span className="status-chip is-unbound">Sin binding: {payload.summary?.unbound ?? 0}</span>
          <span className="status-chip is-no-data">Sin datos: {payload.summary?.no_data ?? 0}</span>
          <span className="status-chip is-error">Error: {payload.summary?.error ?? 0}</span>
        </div>

        <div className="rendimientos-range-bar">
          <div className="form-field">
            <label htmlFor="rendimientos-month-from">Desde</label>
            <input
              id="rendimientos-month-from"
              type="month"
              value={monthFrom}
              onChange={(event) => setMonthFrom(event.target.value)}
            />
          </div>
          <div className="form-field">
            <label htmlFor="rendimientos-month-to">Hasta</label>
            <input
              id="rendimientos-month-to"
              type="month"
              value={monthTo}
              onChange={(event) => setMonthTo(event.target.value)}
            />
          </div>
        </div>

        <div className="rendimientos-filter-bar">
          <div className="form-field rendimientos-search-field">
            <label htmlFor="rendimientos-plate-search">Placas</label>
            <input
              id="rendimientos-plate-search"
              value={filters.plateSearch}
              onChange={handleChange("plateSearch")}
              placeholder="Buscar por placa"
            />
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-client">Clientes</label>
            <select id="rendimientos-client" value={filters.client} onChange={handleChange("client")}>
              <option value="">Todos</option>
              {clientOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-database">Database</label>
            <select id="rendimientos-database" value={filters.database} onChange={handleChange("database")}>
              <option value="">Todas</option>
              {databaseOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-motor-group">Grupo de motor</label>
            <select id="rendimientos-motor-group" value={filters.motorGroup} onChange={handleChange("motorGroup")}>
              <option value="">Todos</option>
              {motorGroupOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="actions-row rendimientos-filter-actions">
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => loadRecords(monthFrom, monthTo)}
            >
              {loading ? "Cargando..." : "Recargar"}
            </button>
            <button type="button" className="button-secondary button-sm" onClick={handleClear}>
              Limpiar
            </button>
          </div>
        </div>

        {filteredRows.length === 0 ? (
          <article className="card empty-state-card rendimientos-empty-state">
            <span className="eyebrow">Sin resultados</span>
            <h3>No hay cortes para los filtros actuales.</h3>
            <p>
              Selecciona un rango de meses, ejecuta el calculo y luego filtra por cliente, database, motor o
              placa.
            </p>
          </article>
        ) : (
          <div className="rendimientos-table-shell">
            <table className="rendimientos-table">
              <thead>
                <tr>
                  <th>Estado</th>
                  <th>Cliente</th>
                  <th>Database</th>
                  <th>Motor</th>
                  <th>Placa</th>
                  <th>Odo ini</th>
                  <th>Odo fin</th>
                  <th>Kms ECM</th>
                  <th>Kms GPS</th>
                  <th>Horo ini</th>
                  <th>Horo fin</th>
                  <th>Hrs ECM</th>
                  <th>Hrs GPS</th>
                  <th>Galones</th>
                  <th>KPG</th>
                  <th>GPH</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const kpg = row.fuel_gallons > 0 && row.kms_ecm !== null && row.kms_ecm !== undefined
                    ? row.kms_ecm / row.fuel_gallons
                    : null;
                  const gph = row.hours_ecm > 0 && row.fuel_gallons !== null && row.fuel_gallons !== undefined
                    ? row.fuel_gallons / row.hours_ecm
                    : null;

                  return (
                    <tr key={`${row.customer_database_id}-${row.plate}-${row.period_month}`}>
                      <td data-label="Estado">
                        <span
                          className={`status-dot ${getStatusClass(row.calculation_status)}`}
                          title={getStatusLabel(row.calculation_status)}
                        />
                      </td>
                      <td data-label="Cliente">{row.client_name || "-"}</td>
                      <td data-label="Database">{row.database_name || "-"}</td>
                      <td data-label="Motor">{row.engine_name || "Sin catalogar"}</td>
                      <td data-label="Placa">
                        <strong>{row.plate}</strong>
                      </td>
                      <td data-label="Odo ini">{formatMetric(row.odo_start, 0)}</td>
                      <td data-label="Odo fin">{formatMetric(row.odo_end, 0)}</td>
                      <td data-label="Kms ECM">{formatMetric(row.kms_ecm, 0)}</td>
                      <td data-label="Kms GPS">{formatMetric(row.kms_gps, 0)}</td>
                      <td data-label="Horo ini">{formatMetric(row.horo_start, 0)}</td>
                      <td data-label="Horo fin">{formatMetric(row.horo_end, 0)}</td>
                      <td data-label="Hrs ECM">{formatMetric(row.hours_ecm, 0)}</td>
                      <td data-label="Hrs GPS">{formatMetric(row.hours_gps, 0)}</td>
                      <td data-label="Galones">{formatMetric(row.fuel_gallons, 0)}</td>
                      <td data-label="KPG">{formatMetric(kpg, 2)}</td>
                      <td data-label="GPH">{formatMetric(gph, 2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
