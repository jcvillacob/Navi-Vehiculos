import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as XLSX from "xlsx";

import Can from "../components/Can";
import ColumnSelectorDrawer from "../components/ColumnSelectorDrawer";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { usePermission } from "../context/AuthContext";
import { useBulkRefresh } from "../context/BulkRefreshContext";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";
import BulkVehicleAssignmentModal from "../features/vehicles/components/BulkVehicleAssignmentModal";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";
import { assignVehicleDatabase, checkVehicleConnections, manualAssignVehicle, refreshVehicle, revalidateCustomerGeotab } from "../api/vehicleApi";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

const VEHICLE_COLUMNS = [
  { key: "plate", label: "Placa", getValue: (v) => v.plate },
  { key: "nombre_vehiculo", label: "Nombre", getValue: (v) => v.nombre_vehiculo || "-" },
  { key: "marca", label: "Marca", getValue: (v) => v.marca || "-" },
  { key: "linea", label: "Linea", getValue: (v) => v.linea || "-" },
  { key: "ano_modelo", label: "Año", getValue: (v) => v.ano_modelo || "-" },
  { key: "tipo_combustible", label: "Combustible", getValue: (v) => v.tipo_combustible || "-" },
  { key: "vin", label: "VIN", getValue: (v) => v.vin || "Sin VIN" },
  { key: "cpl", label: "CPL", getValue: (v) => v.cpl || "Sin CPL" },
  { key: "db_connection", label: "DB", getExportValue: (v) => v.database_connection_type || "-" },
  { key: "engine_name", label: "Motor", getValue: (v) => v.engine_name || "Sin catalogar" },
  { key: "technical_number", label: "TEC#", getValue: (v) => v.technical_number },
  { key: "client_name", label: "Cliente", getValue: (v) => v.client_name || "Sin cliente" },
  { key: "database_name", label: "Database", getValue: (v) => v.database_name || "Sin database" },
  { key: "has_motor_rules", label: "Reglas", getExportValue: (v) => (v.has_motor_rules ? "Si" : "No") },
  { key: "attachments", label: "Adjuntos", getExportValue: (v) => v.attachments?.length || 0 },
];

function AttachmentIcon({ contentType }) {
  const isPdf = contentType === "application/pdf";

  return (
    <span className="attachment-icon" aria-hidden="true">
      {isPdf ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M8 13h8" />
          <path d="M8 17h5" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.2" />
          <path d="m21 15-4.5-4.5L8 19" />
        </svg>
      )}
    </span>
  );
}

function DbConnectionBadge({ vehicle, result, checking }) {
  const dbType = vehicle.database_connection_type;
  const eligible = dbType === "geotab" || !vehicle.customer_database_id;
  const dbTypeKey = dbType || (vehicle.customer_database_id ? "unknown" : "none");
  const dbLabel = dbType ? dbType.toUpperCase() : vehicle.customer_database_id ? "?" : "GEOTAB";

  if (!eligible) {
    return (
      <span
        className={`status geotab-badge db-type-badge db-type-${dbTypeKey}`}
        title={`Tipo: ${dbType || "desconocido"} (no aplica revision Geotab)`}
      >
        {dbLabel}
      </span>
    );
  }

  const status = result?.status;
  const statusToClass = {
    connected: "db-status-connected",
    disconnected: "db-status-disconnected",
    not_found: "db-status-not-found",
    error: "db-status-error",
  };
  const statusToTitle = {
    connected: "Conectado (comunicacion en las ultimas 24h)",
    disconnected: "Encontrado en Geotab pero sin comunicacion reciente",
    not_found: "No encontrado en Geotab",
    error: "Error revisando la conexion",
  };
  const statusToDot = {
    connected: "db-status-dot-ok",
    disconnected: "db-status-dot-warn",
    not_found: "db-status-dot-error",
    error: "db-status-dot-error",
  };

  const baseClass = status ? statusToClass[status] : `db-type-${dbTypeKey}`;
  const title = status
    ? statusToTitle[status]
    : checking
      ? "Revisando conexion..."
      : `${dbType ? "Geotab del cliente" : "Geotab global"} (sin revisar)`;
  const dotClass = status ? statusToDot[status] : null;

  return (
    <span
      className={`status geotab-badge db-type-badge ${baseClass}`}
      title={title}
    >
      {dotClass ? <span className={`db-status-dot ${dotClass}`} aria-hidden /> : null}
      {dbLabel}
    </span>
  );
}

function MultiSelectFilter({ label, options, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handleClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const items = options.map((opt) =>
    typeof opt === "string" ? { value: opt, label: opt } : opt
  );

  const toggle = (value) => {
    onChange(
      selected.includes(value)
        ? selected.filter((v) => v !== value)
        : [...selected, value]
    );
  };

  return (
    <div className={`th-multifilter ${selected.length ? "is-active" : ""}`} ref={ref}>
      <button type="button" className="th-multifilter-trigger" onClick={() => setOpen(!open)}>
        <span className="th-multifilter-text">{label}</span>
        <span className="th-multifilter-arrow">▼</span>
      </button>
      {open && (
        <div className="th-multifilter-dropdown">
          {items.map((item) => (
            <label key={item.value} className="th-multifilter-option">
              <input
                type="checkbox"
                checked={selected.includes(item.value)}
                onChange={() => toggle(item.value)}
              />
              <span>{item.label}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export default function VehiclesPage() {
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [bulkAssignOpen, setBulkAssignOpen] = useState(false);
  const [bulkAssigning, setBulkAssigning] = useState(false);
  const [selectedPlates, setSelectedPlates] = useState(() => new Set());
  const [refreshingPlates, setRefreshingPlates] = useState(new Set());
  const [checkingConnections, setCheckingConnections] = useState(false);
  const [connectionResults, setConnectionResults] = useState({});
  const [filterClient, setFilterClient] = useState([]);
  const [filterMotor, setFilterMotor] = useState([]);
  const [filterDatabase, setFilterDatabase] = useState([]);
  const [filterConnection, setFilterConnection] = useState([]);
  const [reprocessPromptPlates, setReprocessPromptPlates] = useState(null);
  const [reprocessSkipGeotab, setReprocessSkipGeotab] = useState(false);
  const selectAllRef = useRef(null);
  const { loading, vehicles, error, search, setSearch, loadVehicles } = useVehicleAssignments();
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { motors, loading: motorsLoading } = useMotorsCatalog();
  const { toasts, pushToast } = useToasts();
  const { bulkRefresh, startBulkRefresh, cancelBulkRefresh, acknowledgeBulkRefresh } = useBulkRefresh();
  const canEditVehicles = usePermission("vehicles.edit");
  const canRefreshVehicles = usePermission("vehicles.refresh");

  const [visibleColumns, setVisibleColumns] = useState(() => new Set(VEHICLE_COLUMNS.map((c) => c.key)));
  const [columnSelectorOpen, setColumnSelectorOpen] = useState(false);

  const handleApplyColumns = useCallback((nextKeys) => {
    setVisibleColumns(new Set(nextKeys));
  }, []);

  // React to bulk refresh finishing (works even if user navigated away and came back)
  useEffect(() => {
    if (bulkRefresh?.status !== "finished") return;
    const { wasCancelled, errors, total } = bulkRefresh;

    loadVehicles(search).then(() => {
      if (wasCancelled) {
        pushToast("error", "Reprocesamiento cancelado.");
      } else if (errors.length) {
        pushToast("error", `Completado con ${errors.length} error(es): ${errors.join(", ")}`);
      } else {
        pushToast("success", `${total} vehiculos reprocesados correctamente.`);
      }
      acknowledgeBulkRefresh();
    });
  }, [bulkRefresh?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const clientOptions = useMemo(() => {
    let subset = vehicles;
    if (filterMotor.length) subset = subset.filter((v) => filterMotor.includes(v.engine_name));
    if (filterDatabase.length) subset = subset.filter((v) => filterDatabase.includes(v.database_name));
    const names = new Set();
    for (const v of subset) {
      if (v.client_name) names.add(v.client_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterMotor, filterDatabase]);

  const motorOptions = useMemo(() => {
    let subset = vehicles;
    if (filterClient.length) subset = subset.filter((v) => filterClient.includes(v.client_name));
    if (filterDatabase.length) subset = subset.filter((v) => filterDatabase.includes(v.database_name));
    const names = new Set();
    for (const v of subset) {
      if (v.engine_name) names.add(v.engine_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterClient, filterDatabase]);

  const databaseOptions = useMemo(() => {
    let subset = vehicles;
    if (filterClient.length) subset = subset.filter((v) => filterClient.includes(v.client_name));
    if (filterMotor.length) subset = subset.filter((v) => filterMotor.includes(v.engine_name));
    const names = new Set();
    for (const v of subset) {
      if (v.database_name) names.add(v.database_name);
    }
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [vehicles, filterClient, filterMotor]);

  useEffect(() => {
    setFilterClient((current) => current.filter((name) => clientOptions.includes(name)));
  }, [clientOptions]);

  useEffect(() => {
    setFilterMotor((current) => current.filter((name) => motorOptions.includes(name)));
  }, [motorOptions]);

  useEffect(() => {
    setFilterDatabase((current) => current.filter((name) => databaseOptions.includes(name)));
  }, [databaseOptions]);

  const filteredVehicles = useMemo(() => {
    let result = vehicles;
    if (filterClient.length) {
      result = result.filter((v) => filterClient.includes(v.client_name));
    }
    if (filterMotor.length) {
      result = result.filter((v) => filterMotor.includes(v.engine_name));
    }
    if (filterDatabase.length) {
      result = result.filter((v) => filterDatabase.includes(v.database_name));
    }
    if (filterConnection.length) {
      result = result.filter((v) => {
        const eligible = v.database_connection_type === "geotab" || !v.customer_database_id;
        const status = eligible ? connectionResults[v.plate]?.status : "not_applicable";
        if (filterConnection.includes("active") && status === "connected") return true;
        if (filterConnection.includes("inactive") && (status === "disconnected" || status === "not_found")) return true;
        if (filterConnection.includes("unchecked") && eligible && !status) return true;
        if (filterConnection.includes("not_applicable") && !eligible) return true;
        return false;
      });
    }
    return result;
  }, [vehicles, filterClient, filterMotor, filterDatabase, filterConnection, connectionResults]);

  const activeColumns = useMemo(
    () => VEHICLE_COLUMNS.filter((col) => visibleColumns.has(col.key)),
    [visibleColumns]
  );

  const handleExportExcel = useCallback(() => {
    try {
      const headers = activeColumns.map((col) => col.label);
      const rows = filteredVehicles.map((vehicle) =>
        activeColumns.map((col) => {
          const fn = col.getExportValue || col.getValue;
          const value = fn ? fn(vehicle) : "";
          if (value === null || value === undefined) return "";
          return String(value);
        })
      );

      const matrix = [headers, ...rows];
      const ws = XLSX.utils.aoa_to_sheet(matrix);
      ws["!freeze"] = { xSplit: 0, ySplit: 1 };
      ws["!cols"] = headers.map((_, colIndex) => {
        const maxLen = matrix.reduce((max, row) => {
          const val = row[colIndex] == null ? "" : String(row[colIndex]);
          return Math.max(max, val.length);
        }, String(headers[colIndex] || "").length);
        return { wch: Math.min(60, Math.max(10, maxLen + 2)) };
      });

      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Vehiculos");

      const now = new Date();
      const datePart = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
      const timePart = `${String(now.getHours()).padStart(2, "0")}${String(now.getMinutes()).padStart(2, "0")}`;
      XLSX.writeFile(wb, `navi-vehiculos-${datePart}-${timePart}.xlsx`);
    } catch (err) {
      console.error("Export error:", err);
      pushToast("error", "No fue posible exportar a Excel.");
    }
  }, [activeColumns, filteredVehicles, pushToast]);

  useEffect(() => {
    const visiblePlates = new Set(filteredVehicles.map((vehicle) => vehicle.plate));
    setSelectedPlates((current) => {
      const next = new Set([...current].filter((plate) => visiblePlates.has(plate)));
      if (
        next.size === current.size &&
        [...next].every((plate) => current.has(plate))
      ) {
        return current;
      }
      return next;
    });
  }, [filteredVehicles]);

  const selectedVehicles = useMemo(
    () => filteredVehicles.filter((vehicle) => selectedPlates.has(vehicle.plate)),
    [filteredVehicles, selectedPlates]
  );

  const allVisibleSelected =
    filteredVehicles.length > 0 && filteredVehicles.every((vehicle) => selectedPlates.has(vehicle.plate));
  const someVisibleSelected =
    !allVisibleSelected && filteredVehicles.some((vehicle) => selectedPlates.has(vehicle.plate));

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = someVisibleSelected;
    }
  }, [someVisibleSelected]);

  useEffect(() => {
    if (bulkAssignOpen && selectedVehicles.length === 0) {
      setBulkAssignOpen(false);
    }
  }, [bulkAssignOpen, selectedVehicles.length]);

  const summary = useMemo(() => {
    const registered = filteredVehicles.filter((vehicle) => vehicle.engine_name).length;
    const withRules = filteredVehicles.filter((vehicle) => vehicle.has_motor_rules).length;
    return {
      total: filteredVehicles.length,
      registered,
      withRules
    };
  }, [filteredVehicles]);

  const handleClear = () => {
    setSearch("");
    setFilterClient([]);
    setFilterMotor([]);
    setFilterDatabase([]);
    setFilterConnection([]);
    setSelectedPlates(new Set());
    setBulkAssignOpen(false);
  };

  const handleToggleVehicleSelection = (plate) => {
    setSelectedPlates((current) => {
      const next = new Set(current);
      if (next.has(plate)) {
        next.delete(plate);
      } else {
        next.add(plate);
      }
      return next;
    });
  };

  const handleToggleVisibleSelection = () => {
    setSelectedPlates((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        filteredVehicles.forEach((vehicle) => next.delete(vehicle.plate));
      } else {
        filteredVehicles.forEach((vehicle) => next.add(vehicle.plate));
      }
      return next;
    });
  };

  const handleUpdateVehicle = async (payload) => {
    if (!selectedVehicle) {
      return;
    }

    try {
      const motorChanged =
        payload.technical_number &&
        payload.technical_number !== selectedVehicle.technical_number;

      if (motorChanged) {
        await manualAssignVehicle(selectedVehicle.plate, {
          technical_number: payload.technical_number,
          cpl: selectedVehicle.cpl || null,
          vin: selectedVehicle.vin || null,
          engine_number: selectedVehicle.engine_number || null,
          marca: selectedVehicle.marca || null,
          linea: selectedVehicle.linea || null,
          ano_modelo: selectedVehicle.ano_modelo || null,
          tipo_combustible: selectedVehicle.tipo_combustible || null,
          geotab_status: selectedVehicle.geotab_status || "unknown",
        });
      }

      await assignVehicleDatabase(selectedVehicle.plate, {
        customer_database_id: payload.customer_database_id,
        ...(Object.prototype.hasOwnProperty.call(payload, "provider_vehicle_id")
          ? { provider_vehicle_id: payload.provider_vehicle_id }
          : {}),
      });
      pushToast("success", `Vehiculo ${selectedVehicle.plate} actualizado.`);
      setSelectedVehicle(null);
      await loadVehicles(search);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "Error actualizando vehiculo");
    }
  };

  const handleRevalidateCustomerGeotab = async (plate) => {
    try {
      const result = await revalidateCustomerGeotab(plate);
      await loadVehicles(search);
      pushToast("success", result.message || `Geotab cliente revalidado para ${plate}.`);
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible revalidar Geotab del cliente"
      );
    }
  };

  const handleRefreshVehicle = async (plate) => {
    setRefreshingPlates((prev) => new Set(prev).add(plate));

    try {
      await refreshVehicle(plate);
      await loadVehicles(search);
      pushToast("success", `Vehiculo ${plate} actualizado.`);
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible actualizar el vehiculo"
      );
    } finally {
      setRefreshingPlates((prev) => {
        const next = new Set(prev);
        next.delete(plate);
        return next;
      });
    }
  };

  const handleCheckConnections = async () => {
    setCheckingConnections(true);
    try {
      const result = await checkVehicleConnections();
      setConnectionResults(result.results || {});
      const parts = [
        `${result.connected || 0} conectado(s)`,
        `${result.disconnected || 0} desconectado(s)`,
        `${result.not_found || 0} no encontrado(s)`,
      ];
      if (result.errors) parts.push(`${result.errors} error(es)`);
      pushToast(result.errors > 0 ? "warning" : "success", `Conexiones revisadas: ${parts.join(", ")}.`);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible revisar conexiones");
    } finally {
      setCheckingConnections(false);
    }
  };

  const handleBulkAssignVehicles = async (payload) => {
    const plates = selectedVehicles.map((vehicle) => vehicle.plate);
    if (!plates.length) return;

    setBulkAssigning(true);
    try {
      const failedPlates = [];
      for (const plate of plates) {
        try {
          await assignVehicleDatabase(plate, payload);
        } catch {
          failedPlates.push(plate);
        }
      }

      await loadVehicles(search);

      if (failedPlates.length) {
        setSelectedPlates(new Set(failedPlates));
        const sample = failedPlates.slice(0, 5).join(", ");
        pushToast(
          "error",
          `Se actualizaron ${plates.length - failedPlates.length} vehiculos y fallaron ${failedPlates.length}: ${sample}${failedPlates.length > 5 ? "..." : ""}`
        );
      } else {
        setSelectedPlates(new Set());
        pushToast("success", `${plates.length} vehiculos actualizados correctamente.`);
      }

      setBulkAssignOpen(false);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible aplicar la asignacion masiva");
    } finally {
      setBulkAssigning(false);
    }
  };

  return (
    <section className="panel">
      <ToastStack toasts={toasts} />

      <header className="page-header">
        <span className="eyebrow">Relacion vehiculo-motor</span>
        <h2>Vehiculos asociados</h2>
        <p>
          Aqui puedes ver las placas guardadas, su VIN, el TEC# detectado, el motor registrado, sus
          adjuntos tecnicos por CPL y el cliente/database asociados a cada vehiculo.
        </p>
      </header>

      <section className="vehicles-summary-grid">
        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Placas unicas</span>
          <strong>{summary.total}</strong>
        </article>

        <article className="card metric-card metric-card-compact feature-card-accent">
          <span className="eyebrow">Catalogadas</span>
          <strong>{summary.registered}</strong>
        </article>

        <article className="card metric-card metric-card-compact">
          <span className="eyebrow">Con reglas</span>
          <strong>{summary.withRules}</strong>
        </article>
      </section>

      <section className="card vehicles-panel">
        <header className="section-heading">
          <div>
            <span className="eyebrow">Explorar</span>
            <h3>Base de vehiculos asociados</h3>
          </div>

          <div className="actions-row section-heading-actions">
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={() => setColumnSelectorOpen(true)}
              aria-haspopup="dialog"
              aria-expanded={columnSelectorOpen}
            >
              Columnas ({visibleColumns.size}/{VEHICLE_COLUMNS.length})
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={handleExportExcel}
              disabled={filteredVehicles.length === 0}
            >
              Exportar Excel
            </button>
            <Can permission="vehicles.edit">
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={() => setBulkAssignOpen(true)}
                disabled={loading || bulkAssigning || selectedVehicles.length === 0}
              >
                Asignar seleccionados ({selectedVehicles.length})
              </button>
            </Can>
            <Can permission="vehicles.refresh">
              <button
                type="button"
                className="button button-sm"
                onClick={handleCheckConnections}
                disabled={loading || checkingConnections}
              >
                {checkingConnections ? "Revisando..." : "Revisar conexión"}
              </button>
            </Can>
            <Can permission="vehicles.refresh">
              <button
                type="button"
                className="button button-sm"
                onClick={() =>
                  setReprocessPromptPlates(
                    selectedVehicles.length > 0
                      ? selectedVehicles.map((v) => v.plate)
                      : filteredVehicles.map((v) => v.plate)
                  )
                }
                disabled={loading || Boolean(bulkRefresh) || filteredVehicles.length === 0}
              >
                Reprocesar {selectedVehicles.length > 0 ? `(${selectedVehicles.length})` : `todos (${filteredVehicles.length})`}
              </button>
            </Can>
          </div>
        </header>

        <div className="vehicles-filter-bar">
          <div className="form-field vehicles-search-field">
            <label htmlFor="vehicles-search">Buscar</label>
            <div className="search-input-wrap">
              <input
                id="vehicles-search"
                value={search}
                onChange={(event) => setSearch(event.target.value.toUpperCase())}
                placeholder="Placa, VIN, TEC#, CPL, motor, cliente, database, nombre, linea o marca"
              />
              {search && (
                <button
                  type="button"
                  className="search-clear-button"
                  onClick={() => setSearch("")}
                  aria-label="Limpiar busqueda"
                >
                  ✕
                </button>
              )}
            </div>
          </div>
        </div>



        {/* ── Bulk refresh progress ── */}
        {bulkRefresh?.status === "running" ? (
          <div className="bulk-progress-bar-container">
            <div className="bulk-progress-header">
              <span className="bulk-progress-label">
                Reprocesando {bulkRefresh.done}/{bulkRefresh.total}
                {bulkRefresh.currentPlate ? ` — ${bulkRefresh.currentPlate}` : ""}
              </span>
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={cancelBulkRefresh}
              >
                Cancelar
              </button>
            </div>
            <div className="bulk-progress-track">
              <div
                className="bulk-progress-fill"
                style={{ width: `${(bulkRefresh.done / bulkRefresh.total) * 100}%` }}
              />
            </div>
            <span className="bulk-progress-percent">
              {Math.round((bulkRefresh.done / bulkRefresh.total) * 100)}%
            </span>
          </div>
        ) : null}

        {!customersLoading && customers.length === 0 ? (
          <p className="notice-banner notice-soft">
            No hay clientes ni databases creados. Usa la vista de Clientes para poblar los selectores.
          </p>
        ) : null}

        <div className="vehicles-table-shell">
          <table className="vehicles-table">
            <thead>
              <tr>
                <th>
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={handleToggleVisibleSelection}
                    aria-label="Seleccionar vehiculos visibles"
                  />
                </th>
                {activeColumns.map((col) => (
                  <th key={col.key}>
                    {col.key === "client_name" && (
                      <MultiSelectFilter
                        label={col.label}
                        options={clientOptions}
                        selected={filterClient}
                        onChange={setFilterClient}
                      />
                    )}
                    {col.key === "engine_name" && (
                      <MultiSelectFilter
                        label={col.label}
                        options={motorOptions}
                        selected={filterMotor}
                        onChange={setFilterMotor}
                      />
                    )}
                    {col.key === "database_name" && (
                      <MultiSelectFilter
                        label={col.label}
                        options={databaseOptions}
                        selected={filterDatabase}
                        onChange={setFilterDatabase}
                      />
                    )}
                    {col.key === "db_connection" && (
                      <MultiSelectFilter
                        label={col.label}
                        options={[
                          { value: "active", label: "Activos" },
                          { value: "inactive", label: "Inactivos" },
                          { value: "unchecked", label: "Sin revisar" },
                          { value: "not_applicable", label: "No aplica" }
                        ]}
                        selected={filterConnection}
                        onChange={setFilterConnection}
                      />
                    )}
                    {![ "client_name", "engine_name", "database_name", "db_connection" ].includes(col.key) && (
                      <span>{col.label}</span>
                    )}
                  </th>
                ))}
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {filteredVehicles.length === 0 ? (
                <tr>
                  <td colSpan={activeColumns.length + 2} className="table-empty-row">
                    {loading ? "Cargando..." : "No hay vehiculos que coincidan con los filtros actuales."}
                  </td>
                </tr>
              ) : (
                filteredVehicles.map((vehicle) => (
                  <tr key={vehicle.plate} className={selectedPlates.has(vehicle.plate) ? "is-selected" : ""}>
                    <td data-label="Seleccion">
                      <input
                        type="checkbox"
                        checked={selectedPlates.has(vehicle.plate)}
                        onChange={() => handleToggleVehicleSelection(vehicle.plate)}
                        aria-label={`Seleccionar ${vehicle.plate}`}
                      />
                    </td>
                    {activeColumns.map((col) => {
                      if (col.key === "plate") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <strong>{vehicle.plate}</strong>
                          </td>
                        );
                      }
                      if (col.key === "db_connection") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <DbConnectionBadge
                              vehicle={vehicle}
                              result={connectionResults[vehicle.plate]}
                              checking={checkingConnections}
                            />
                          </td>
                        );
                      }
                      if (col.key === "has_motor_rules") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <span
                              className={`rules-dot ${vehicle.has_motor_rules ? "rules-dot-active" : "rules-dot-inactive"}`}
                              title={vehicle.has_motor_rules ? "Motor con reglas configuradas" : "Sin reglas"}
                            />
                          </td>
                        );
                      }
                      if (col.key === "attachments") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            {vehicle.attachments?.length ? (
                              <div className="attachment-list attachment-list-compact">
                                {vehicle.attachments.map((attachment) => (
                                  <a
                                    key={attachment.id}
                                    className="attachment-chip"
                                    href={`${API_BASE}${attachment.download_url}`}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={`${attachment.original_filename} | CPL ${attachment.cpl || "Sin CPL"}`}
                                    aria-label={`Abrir adjunto ${attachment.original_filename} del CPL ${attachment.cpl || "sin cpl"} en otra pestana`}
                                  >
                                    <AttachmentIcon contentType={attachment.content_type} />
                                  </a>
                                ))}
                              </div>
                            ) : (
                              "Sin adjuntos"
                            )}
                          </td>
                        );
                      }
                      return (
                        <td key={col.key} data-label={col.label}>
                          {col.getValue(vehicle)}
                        </td>
                      );
                    })}
                    <td data-label="Acciones">
                      <div className="actions-row vehicles-row-actions">
                        <button
                          type="button"
                          className="button-secondary button-sm"
                          onClick={() => setSelectedVehicle(vehicle)}
                        >
                          Detalles
                        </button>
                        <Can permission="vehicles.refresh">
                          <button
                            type="button"
                            className="icon-button"
                            title="Actualizar datos del vehiculo"
                            onClick={() => handleRefreshVehicle(vehicle.plate)}
                            disabled={refreshingPlates.has(vehicle.plate)}
                          >
                            <svg
                              className={refreshingPlates.has(vehicle.plate) ? "spin" : ""}
                              width="16"
                              height="16"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M21 2v6h-6" />
                              <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                              <path d="M21 12a9 9 0 0 1-9 9c-4.2 0-7.7-2.8-8.8-6.7" />
                            </svg>
                          </button>
                        </Can>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {filteredVehicles.length > 0 ? (
          <div className="vehicles-table-shell">
            <table className="vehicles-table">
              <thead>
                <tr>
                  <th>
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={handleToggleVisibleSelection}
                      aria-label="Seleccionar vehiculos visibles"
                    />
                  </th>
                  {activeColumns.map((col) => (
                    <th key={col.key}>
                      {col.key === "client_name" && (
                        <MultiSelectFilter
                          label={col.label}
                          options={clientOptions}
                          selected={filterClient}
                          onChange={setFilterClient}
                        />
                      )}
                      {col.key === "engine_name" && (
                        <MultiSelectFilter
                          label={col.label}
                          options={motorOptions}
                          selected={filterMotor}
                          onChange={setFilterMotor}
                        />
                      )}
                      {col.key === "database_name" && (
                        <MultiSelectFilter
                          label={col.label}
                          options={databaseOptions}
                          selected={filterDatabase}
                          onChange={setFilterDatabase}
                        />
                      )}
                      {col.key === "db_connection" && (
                        <MultiSelectFilter
                          label={col.label}
                          options={[
                            { value: "active", label: "Activos" },
                            { value: "inactive", label: "Inactivos" },
                            { value: "unchecked", label: "Sin revisar" },
                            { value: "not_applicable", label: "No aplica" }
                          ]}
                          selected={filterConnection}
                          onChange={setFilterConnection}
                        />
                      )}
                      {![ "client_name", "engine_name", "database_name", "db_connection" ].includes(col.key) && (
                        <span>{col.label}</span>
                      )}
                    </th>
                  ))}
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filteredVehicles.map((vehicle) => (
                  <tr key={vehicle.plate} className={selectedPlates.has(vehicle.plate) ? "is-selected" : ""}>
                    <td data-label="Seleccion">
                      <input
                        type="checkbox"
                        checked={selectedPlates.has(vehicle.plate)}
                        onChange={() => handleToggleVehicleSelection(vehicle.plate)}
                        aria-label={`Seleccionar ${vehicle.plate}`}
                      />
                    </td>
                    {activeColumns.map((col) => {
                      if (col.key === "plate") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <strong>{vehicle.plate}</strong>
                          </td>
                        );
                      }
                      if (col.key === "db_connection") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <DbConnectionBadge
                              vehicle={vehicle}
                              result={connectionResults[vehicle.plate]}
                              checking={checkingConnections}
                            />
                          </td>
                        );
                      }
                      if (col.key === "has_motor_rules") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            <span
                              className={`rules-dot ${vehicle.has_motor_rules ? "rules-dot-active" : "rules-dot-inactive"}`}
                              title={vehicle.has_motor_rules ? "Motor con reglas configuradas" : "Sin reglas"}
                            />
                          </td>
                        );
                      }
                      if (col.key === "attachments") {
                        return (
                          <td key={col.key} data-label={col.label}>
                            {vehicle.attachments?.length ? (
                              <div className="attachment-list attachment-list-compact">
                                {vehicle.attachments.map((attachment) => (
                                  <a
                                    key={attachment.id}
                                    className="attachment-chip"
                                    href={`${API_BASE}${attachment.download_url}`}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={`${attachment.original_filename} | CPL ${attachment.cpl || "Sin CPL"}`}
                                    aria-label={`Abrir adjunto ${attachment.original_filename} del CPL ${attachment.cpl || "sin cpl"} en otra pestana`}
                                  >
                                    <AttachmentIcon contentType={attachment.content_type} />
                                  </a>
                                ))}
                              </div>
                            ) : (
                              "Sin adjuntos"
                            )}
                          </td>
                        );
                      }
                      return (
                        <td key={col.key} data-label={col.label}>
                          {col.getValue(vehicle)}
                        </td>
                      );
                    })}
                    <td data-label="Acciones">
                      <div className="actions-row vehicles-row-actions">
                        <button
                          type="button"
                          className="button-secondary button-sm"
                          onClick={() => setSelectedVehicle(vehicle)}
                        >
                          Detalles
                        </button>
                        <Can permission="vehicles.refresh">
                          <button
                            type="button"
                            className="icon-button"
                            title="Actualizar datos del vehiculo"
                            onClick={() => handleRefreshVehicle(vehicle.plate)}
                            disabled={refreshingPlates.has(vehicle.plate)}
                          >
                            <svg
                              className={refreshingPlates.has(vehicle.plate) ? "spin" : ""}
                              width="16"
                              height="16"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M21 2v6h-6" />
                              <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
                              <path d="M3 22v-6h6" />
                              <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
                            </svg>
                          </button>
                        </Can>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <VehicleAssignmentModal
        open={Boolean(selectedVehicle)}
        loading={loading || customersLoading || motorsLoading}
        title={selectedVehicle ? `Detalles ${selectedVehicle.plate}` : "Detalles del vehiculo"}
        vehicle={selectedVehicle}
        customers={customers}
        motors={motors}
        requiresMotorRegistration
        initialTechnicalNumber={selectedVehicle?.technical_number || ""}
        onClose={() => setSelectedVehicle(null)}
        onSubmit={handleUpdateVehicle}
        onRevalidateCustomerGeotab={handleRevalidateCustomerGeotab}
        canEditVehicle={canEditVehicles}
        canRevalidateCustomerGeotab={canRefreshVehicles}
      />

      <BulkVehicleAssignmentModal
        open={bulkAssignOpen}
        loading={bulkAssigning || loading || customersLoading}
        customers={customers}
        vehicles={selectedVehicles}
        onClose={() => setBulkAssignOpen(false)}
        onSubmit={handleBulkAssignVehicles}
      />

      <ColumnSelectorDrawer
        open={columnSelectorOpen}
        title="Columnas de vehiculos"
        description="Elige las columnas que quieres ver en la tabla y aplica los cambios cuando estes listo."
        columns={VEHICLE_COLUMNS}
        visibleKeys={visibleColumns}
        onApply={handleApplyColumns}
        onClose={() => setColumnSelectorOpen(false)}
      />

      {reprocessPromptPlates && (
        <div className="modal-overlay" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) setReprocessPromptPlates(null); }}>
          <section className="card modal-card reprocess-scope-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <div className="modal-heading">
                <span className="eyebrow">Reprocesar</span>
                <h3>{reprocessPromptPlates.length} vehiculos</h3>
              </div>
              <button type="button" className="icon-button" onClick={() => setReprocessPromptPlates(null)}>
                &times;
              </button>
            </header>
            <p className="reprocess-scope-description">
              Selecciona que datos quieres actualizar:
            </p>
            <div className="reprocess-scope-options">
              <button
                type="button"
                className="button-secondary reprocess-scope-button"
                onClick={() => {
                  startBulkRefresh(reprocessPromptPlates, { scope: "fenix", skipGeotab: reprocessSkipGeotab });
                  setReprocessPromptPlates(null);
                  setReprocessSkipGeotab(false);
                }}
              >
                <strong>Solo Fenix</strong>
                <span>Actualizar datos del vehiculo (marca, linea, nombre, VIN) desde SQL Server</span>
              </button>
              <button
                type="button"
                className="button-secondary reprocess-scope-button"
                onClick={() => {
                  startBulkRefresh(reprocessPromptPlates, { scope: "cummins" });
                  setReprocessPromptPlates(null);
                  setReprocessSkipGeotab(false);
                }}
              >
                <strong>Solo Cummins</strong>
                <span>Re-consultar QuickServe para actualizar TEC# y CPL usando el numero de motor almacenado</span>
              </button>
              <button
                type="button"
                className="button reprocess-scope-button"
                onClick={() => {
                  startBulkRefresh(reprocessPromptPlates, { scope: "all", skipGeotab: reprocessSkipGeotab });
                  setReprocessPromptPlates(null);
                  setReprocessSkipGeotab(false);
                }}
              >
                <strong>Ambos</strong>
                <span>Reprocesar completamente: Fenix y Cummins</span>
              </button>
            </div>
            <label className="reprocess-geotab-toggle">
              <input
                type="checkbox"
                checked={!reprocessSkipGeotab}
                onChange={(e) => setReprocessSkipGeotab(!e.target.checked)}
              />
              <span>Consultar Geotab</span>
              <span className="reprocess-geotab-hint">Verificar existencia del vehiculo en Geotab (mas lento)</span>
            </label>
          </section>
        </div>
      )}
    </section>
  );
}
