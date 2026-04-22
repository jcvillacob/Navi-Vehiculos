import { useMemo, useState } from "react";

import { downloadBulkResultsWorkbook } from "../utils/exportExcel";

const FILTER_OPTIONS = [
  { key: "all", label: "Todos" },
  { key: "ok", label: "OK" },
  { key: "partial", label: "Parcial" },
  { key: "not_found", label: "No encontrado" },
  { key: "error", label: "Error" },
];

function getStatusClass(status) {
  if (status === "ok") return "status-ok";
  if (status === "partial") return "status-partial";
  if (status === "not_found") return "status-not_found";
  return "status-error";
}

function getRowClass(status) {
  if (status === "ok") return "bulk-row-ok";
  if (status === "partial") return "bulk-row-partial";
  if (status === "not_found") return "bulk-row-not-found";
  return "bulk-row-error";
}

function getMessage(result) {
  return result.error || result.response?.message || "-";
}

export default function BulkLookupResults({ results }) {
  const [selectedFilters, setSelectedFilters] = useState(["all"]);

  const activeFilters = selectedFilters.includes("all")
    ? ["ok", "partial", "not_found", "error"]
    : selectedFilters;

  const filteredResults = useMemo(() => {
    if (selectedFilters.includes("all")) return results;
    return results.filter((item) => activeFilters.includes(item.status));
  }, [activeFilters, results, selectedFilters]);

  const errorResults = useMemo(
    () => results.filter((item) => item.status === "error" || item.status === "not_found"),
    [results]
  );

  const toggleFilter = (filterKey) => {
    if (filterKey === "all") {
      setSelectedFilters(["all"]);
      return;
    }

    setSelectedFilters((current) => {
      const base = current.includes("all") ? [] : current;
      const exists = base.includes(filterKey);
      const next = exists ? base.filter((item) => item !== filterKey) : [...base, filterKey];
      return next.length ? next : ["all"];
    });
  };

  const handleDownloadAll = () => {
    if (!results.length) return;
    downloadBulkResultsWorkbook(results);
  };

  const handleDownloadErrors = () => {
    if (!errorResults.length) return;
    downloadBulkResultsWorkbook(errorResults);
  };

  const handleCopyVin = async (vin) => {
    if (!vin) return;
    try {
      await navigator.clipboard.writeText(vin);
    } catch {
      /* ignore */
    }
  };

  return (
    <article className="card bulk-upload-card">
      <header className="bulk-results-header">
        <div className="dash-search-heading">
          <span className="eyebrow">Resultados</span>
          <h3>Resumen del lote</h3>
        </div>
        <div className="actions-row">
          <button type="button" className="button-secondary" onClick={handleDownloadErrors} disabled={!errorResults.length}>
            Descargar solo errores
          </button>
          <button type="button" onClick={handleDownloadAll} disabled={!results.length}>
            Descargar Excel
          </button>
        </div>
      </header>

      <div className="bulk-filter-chips">
        {FILTER_OPTIONS.map((filter) => {
          const isActive = selectedFilters.includes("all")
            ? filter.key === "all"
            : selectedFilters.includes(filter.key);

          return (
            <button
              key={filter.key}
              type="button"
              className={`lookup-history-chip ${isActive ? "is-active" : ""}`}
              onClick={() => toggleFilter(filter.key)}
            >
              {filter.label}
            </button>
          );
        })}
      </div>

      <div className="vehicles-table-shell">
        <table className="vehicles-table bulk-results-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Dispositivo</th>
              <th>Placa</th>
              <th>Marca</th>
              <th>Linea</th>
              <th>Modelo</th>
              <th>Configuracion</th>
              <th>Año</th>
              <th>Combustible</th>
              <th>VIN</th>
              <th>Copiar</th>
              <th>ESN</th>
              <th>TEC#</th>
              <th>CPL</th>
              <th>Motor</th>
              <th>Cliente</th>
              <th>Geotab Navi</th>
              <th>Geotab Cliente</th>
              <th>Estado</th>
              <th>Mensaje</th>
            </tr>
          </thead>
          <tbody>
            {filteredResults.map((result) => (
              <tr key={`${result.identifier}-${result.rowNumber}`} className={getRowClass(result.status)}>
                <td data-label="#">{result.rowNumber}</td>
                <td data-label="Dispositivo"><strong>{result.identifier}</strong></td>
                <td data-label="Placa">{result.response?.plate || "-"}</td>
                <td data-label="Marca">{result.response?.marca || "-"}</td>
                <td data-label="Linea">{result.response?.linea || "-"}</td>
                <td data-label="Modelo">{result.response?.source_details?.fenix?.modelo || "-"}</td>
                <td data-label="Configuracion">{result.response?.source_details?.fenix?.configuracion || "-"}</td>
                <td data-label="Año">{result.response?.ano_modelo || "-"}</td>
                <td data-label="Combustible">{result.response?.tipo_combustible || "-"}</td>
                <td data-label="VIN">{result.response?.vin || "-"}</td>
                <td data-label="Copiar">
                  <button type="button" className="button-secondary button-sm" onClick={() => handleCopyVin(result.response?.vin)} disabled={!result.response?.vin}>
                    Copiar VIN
                  </button>
                </td>
                <td data-label="ESN">{result.response?.engine_number || "-"}</td>
                <td data-label="TEC#">{result.response?.technical_engine_configuration || "-"}</td>
                <td data-label="CPL">{result.response?.cpl || "-"}</td>
                <td data-label="Motor">{result.response?.registered_motor?.engine_name || "-"}</td>
                <td data-label="Cliente">{result.response?.assigned_database?.client_name || "-"}</td>
                <td data-label="Geotab Navi">{result.response?.geotab_status || "-"}</td>
                <td data-label="Geotab Cliente">{result.response?.geotab_customer_status || "-"}</td>
                <td data-label="Estado">
                  <span className={`status ${getStatusClass(result.status)}`}>{result.status}</span>
                </td>
                <td data-label="Mensaje">
                  <span className="bulk-message-cell" title={getMessage(result)}>{getMessage(result)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
