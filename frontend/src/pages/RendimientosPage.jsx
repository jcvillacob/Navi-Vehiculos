import { useEffect, useMemo, useState } from "react";

const PERFORMANCE_ROWS = [
  {
    id: 1,
    client: "Cemex",
    database: "cemex_prod",
    motorGroup: "Pesados",
    plate: "TLK240",
    date: "2026-03-02",
    kms: 1280,
    hours: 42.5,
    gallons: 108.4
  },
  {
    id: 2,
    client: "Cemex",
    database: "cemex_cali",
    motorGroup: "Livianos",
    plate: "SMR512",
    date: "2026-03-04",
    kms: 860,
    hours: 31.2,
    gallons: 79.5
  },
  {
    id: 3,
    client: "Postobon",
    database: "postobon_nal",
    motorGroup: "Pesados",
    plate: "JHT902",
    date: "2026-03-08",
    kms: 1545,
    hours: 49.8,
    gallons: 120.1
  },
  {
    id: 4,
    client: "Postobon",
    database: "postobon_nal",
    motorGroup: "Gas",
    plate: "RXP331",
    date: "2026-03-10",
    kms: 910,
    hours: 37.4,
    gallons: 88.2
  },
  {
    id: 5,
    client: "Argos",
    database: "argos_med",
    motorGroup: "Pesados",
    plate: "KLM774",
    date: "2026-03-12",
    kms: 1730,
    hours: 58.1,
    gallons: 133.7
  },
  {
    id: 6,
    client: "Argos",
    database: "argos_baq",
    motorGroup: "Livianos",
    plate: "BNQ418",
    date: "2026-03-14",
    kms: 690,
    hours: 24.6,
    gallons: 55.4
  },
  {
    id: 7,
    client: "TCC",
    database: "tcc_flotas",
    motorGroup: "Pesados",
    plate: "HZA205",
    date: "2026-03-16",
    kms: 2015,
    hours: 63.3,
    gallons: 149.8
  },
  {
    id: 8,
    client: "TCC",
    database: "tcc_regional",
    motorGroup: "Hibridos",
    plate: "QWE907",
    date: "2026-03-18",
    kms: 1125,
    hours: 28.7,
    gallons: 61.9
  }
];

function buildOptions(rows, getValue) {
  return [...new Set(rows.map(getValue).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function filterRows(rows, filters, omit = "") {
  return rows.filter((row) => {
    if (omit !== "client" && filters.client && row.client !== filters.client) return false;
    if (omit !== "database" && filters.database && row.database !== filters.database) return false;
    if (omit !== "motorGroup" && filters.motorGroup && row.motorGroup !== filters.motorGroup) return false;
    if (omit !== "plateSearch" && filters.plateSearch) {
      const normalizedPlate = row.plate.toUpperCase();
      if (!normalizedPlate.includes(filters.plateSearch)) return false;
    }
    if (omit !== "dateRange" && filters.startDate && row.date < filters.startDate) return false;
    if (omit !== "dateRange" && filters.endDate && row.date > filters.endDate) return false;
    return true;
  });
}

function formatNumber(value, options) {
  return new Intl.NumberFormat("es-CO", options).format(value);
}

export default function RendimientosPage() {
  const [filters, setFilters] = useState({
    client: "",
    database: "",
    motorGroup: "",
    plateSearch: "",
    startDate: "",
    endDate: ""
  });

  const clientOptions = useMemo(() => {
    const subset = filterRows(PERFORMANCE_ROWS, filters, "client");
    return buildOptions(subset, (row) => row.client);
  }, [filters]);

  const databaseOptions = useMemo(() => {
    const subset = filterRows(PERFORMANCE_ROWS, filters, "database");
    return buildOptions(subset, (row) => row.database);
  }, [filters]);

  const motorGroupOptions = useMemo(() => {
    const subset = filterRows(PERFORMANCE_ROWS, filters, "motorGroup");
    return buildOptions(subset, (row) => row.motorGroup);
  }, [filters]);

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
  }, [motorGroupOptions, filters.motorGroup]);

  const filteredRows = useMemo(
    () => filterRows(PERFORMANCE_ROWS, filters),
    [filters]
  );

  const summary = useMemo(() => {
    const totals = filteredRows.reduce(
      (accumulator, row) => {
        accumulator.kms += row.kms;
        accumulator.hours += row.hours;
        accumulator.gallons += row.gallons;
        return accumulator;
      },
      { kms: 0, hours: 0, gallons: 0 }
    );

    const kpg = totals.gallons > 0 ? totals.kms / totals.gallons : 0;
    const gph = totals.hours > 0 ? totals.gallons / totals.hours : 0;

    return {
      ...totals,
      kpg,
      gph,
      vehicles: filteredRows.length
    };
  }, [filteredRows]);

  const handleChange = (key) => (event) => {
    const value = key === "plateSearch"
      ? event.target.value.toUpperCase()
      : event.target.value;

    setFilters((current) => ({
      ...current,
      [key]: value
    }));
  };

  const handleClear = () => {
    setFilters({
      client: "",
      database: "",
      motorGroup: "",
      plateSearch: "",
      startDate: "",
      endDate: ""
    });
  };

  return (
    <section className="panel">
      <header className="page-header">
        <span className="eyebrow">Analitica operativa</span>
        <h2>Rendimientos</h2>
        <p>
          Vista inicial con datos hardcodeados para revisar kilometros recorridos, horas de uso,
          galones consumidos, KPG y GPH por placa, grupo de motor y rango de fechas.
        </p>
      </header>

      <section className="rendimientos-summary-grid">
        <article className="card metric-card">
          <span className="eyebrow">Kms recorridos</span>
          <strong>{formatNumber(summary.kms)}</strong>
          <p>Total acumulado en el conjunto filtrado</p>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Horas</span>
          <strong>{formatNumber(summary.hours, { maximumFractionDigits: 1, minimumFractionDigits: 1 })}</strong>
          <p>Horas operadas por las placas visibles</p>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">Galones</span>
          <strong>{formatNumber(summary.gallons, { maximumFractionDigits: 1, minimumFractionDigits: 1 })}</strong>
          <p>Consumo total consolidado</p>
        </article>

        <article className="card metric-card feature-card-accent">
          <span className="eyebrow">KPG</span>
          <strong>{formatNumber(summary.kpg, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}</strong>
          <p>Kilometros por galon sobre el total filtrado</p>
        </article>

        <article className="card metric-card">
          <span className="eyebrow">GPH</span>
          <strong>{formatNumber(summary.gph, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}</strong>
          <p>Galones por hora del conjunto actual</p>
        </article>
      </section>

      <section className="card rendimientos-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Filtros</span>
            <h3>Explorador de rendimientos</h3>
          </div>
          <span className="support-copy">
            {summary.vehicles} placa{summary.vehicles === 1 ? "" : "s"} encontradas
          </span>
        </header>

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
            <select
              id="rendimientos-client"
              value={filters.client}
              onChange={handleChange("client")}
            >
              <option value="">Todos</option>
              {clientOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-database">Database</label>
            <select
              id="rendimientos-database"
              value={filters.database}
              onChange={handleChange("database")}
            >
              <option value="">Todas</option>
              {databaseOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-motor-group">Grupo de motor</label>
            <select
              id="rendimientos-motor-group"
              value={filters.motorGroup}
              onChange={handleChange("motorGroup")}
            >
              <option value="">Todos</option>
              {motorGroupOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-start-date">Fecha inicial</label>
            <input
              id="rendimientos-start-date"
              type="date"
              value={filters.startDate}
              onChange={handleChange("startDate")}
            />
          </div>

          <div className="form-field">
            <label htmlFor="rendimientos-end-date">Fecha final</label>
            <input
              id="rendimientos-end-date"
              type="date"
              value={filters.endDate}
              onChange={handleChange("endDate")}
            />
          </div>

          <div className="actions-row rendimientos-filter-actions">
            <button type="button" className="button-secondary button-sm" onClick={handleClear}>
              Limpiar
            </button>
          </div>
        </div>

        {filteredRows.length === 0 ? (
          <article className="card empty-state-card rendimientos-empty-state">
            <span className="eyebrow">Sin resultados</span>
            <h3>No hay rendimientos para los filtros actuales.</h3>
            <p>Ajusta clientes, database, grupo de motor, placa o fechas para volver a explorar datos.</p>
          </article>
        ) : (
          <div className="rendimientos-table-shell">
            <table className="rendimientos-table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Cliente</th>
                  <th>Database</th>
                  <th>Grupo motor</th>
                  <th>Placa</th>
                  <th>Kms</th>
                  <th>Horas</th>
                  <th>Galones</th>
                  <th>KPG</th>
                  <th>GPH</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row) => {
                  const kpg = row.gallons > 0 ? row.kms / row.gallons : 0;
                  const gph = row.hours > 0 ? row.gallons / row.hours : 0;

                  return (
                    <tr key={row.id}>
                      <td data-label="Fecha">{row.date}</td>
                      <td data-label="Cliente">{row.client}</td>
                      <td data-label="Database">{row.database}</td>
                      <td data-label="Grupo motor">{row.motorGroup}</td>
                      <td data-label="Placa">
                        <strong>{row.plate}</strong>
                      </td>
                      <td data-label="Kms">{formatNumber(row.kms)}</td>
                      <td data-label="Horas">
                        {formatNumber(row.hours, { maximumFractionDigits: 1, minimumFractionDigits: 1 })}
                      </td>
                      <td data-label="Galones">
                        {formatNumber(row.gallons, { maximumFractionDigits: 1, minimumFractionDigits: 1 })}
                      </td>
                      <td data-label="KPG">
                        {formatNumber(kpg, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}
                      </td>
                      <td data-label="GPH">
                        {formatNumber(gph, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}
                      </td>
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
