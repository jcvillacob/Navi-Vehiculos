import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as XLSX from "xlsx";

import Can from "../components/Can";
import ColumnSelectorDrawer from "../components/ColumnSelectorDrawer";
import { MultiSelectFilter } from "../components/MultiSelectFilter";
import { SortButton } from "../components/SortButton";
import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { usePermission } from "../context/AuthContext";
import { useBulkRefresh } from "../context/BulkRefreshContext";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useVehicleAssignments } from "../features/engineLookup/hooks/useVehicleAssignments";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";
import { useUserPreference } from "../hooks/useUserPreference";
import BulkVehicleAssignmentModal from "../features/vehicles/components/BulkVehicleAssignmentModal";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";
import { assignVehicleDatabase, checkVehicleConnections, fetchVehicleDetail, manualAssignVehicle, refreshVehicle, revalidateCustomerGeotab, setVehicleCategory, setVehicleVocacional } from "../api/vehicleApi";
import { CUSTOMER_CATEGORIES, categoryBadgeClass } from "../features/categories";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

// width fija por columna: la tabla usa table-layout fixed para que los anchos
// no salten al filtrar (si dependen del contenido, cambian con cada filtro).
const VEHICLE_COLUMNS = [
  { key: "plate", label: "Placa", width: 86, getValue: (v) => v.plate },
  { key: "nombre_vehiculo", label: "Nombre", width: 150, getValue: (v) => v.nombre_vehiculo || "-" },
  { key: "marca", label: "Marca", width: 110, getValue: (v) => v.marca || "-" },
  { key: "linea", label: "Linea", width: 110, getValue: (v) => v.linea || "-" },
  { key: "ano_modelo", label: "Año", width: 64, getValue: (v) => v.ano_modelo || "-" },
  { key: "tipo_combustible", label: "Combustible", width: 110, getValue: (v) => v.tipo_combustible || "-" },
  { key: "vin", label: "VIN", width: 170, getValue: (v) => v.vin || "Sin VIN" },
  { key: "cpl", label: "CPL", width: 80, getValue: (v) => v.cpl || "Sin CPL" },
  {
    key: "db_connection",
    label: "DB",
    width: 96,
    getSortValue: (v) => v.database_connection_type || "",
    getExportValue: (v) => v.database_connection_type || "-",
  },
  { key: "engine_name", label: "Motor", width: 140, getValue: (v) => v.engine_name || "Sin catalogar" },
  { key: "technical_number", label: "TEC#", width: 110, getValue: (v) => v.technical_number },
  { key: "client_name", label: "Cliente", width: 140, getValue: (v) => v.client_name || "Sin cliente" },
  {
    key: "category",
    label: "Categoria",
    width: 160,
    getSortValue: (v) => v.category || "Ninguna",
    getExportValue: (v) => v.category || "Ninguna",
  },
  {
    key: "vocacional",
    label: "Uso",
    width: 110,
    getSortValue: (v) => (v.vocacional ? 1 : 0),
    getExportValue: (v) => (v.vocacional ? "Vocacional" : "Transporte"),
  },
  { key: "database_name", label: "Database", width: 130, getValue: (v) => v.database_name || "Sin database" },
  {
    key: "has_motor_rules",
    label: "Reglas",
    width: 70,
    getSortValue: (v) => (v.has_motor_rules ? 1 : 0),
    getExportValue: (v) => (v.has_motor_rules ? "Si" : "No"),
  },
  {
    key: "attachments",
    label: "Adjuntos",
    width: 90,
    getSortValue: (v) => v.attachments_count ?? v.attachments?.length ?? 0,
    getExportValue: (v) => v.attachments_count ?? v.attachments?.length ?? 0,
  },
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
          <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
        </svg>
      )}
    </span>
  );
}

export default function VehiclesPage() {
  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadingAttachmentPlates, setLoadingAttachmentPlates] = useState(() => new Set());
  const [bulkAssignOpen, setBulkAssignOpen] = useState(false);
  const [bulkAssigning, setBulkAssigning] = useState(false);
  const [selectedPlates, setSelectedPlates] = useState(() => new Set());
  const [refreshingPlates, setRefreshingPlates] = useState(new Set());
  const [checkingConnections, setCheckingConnections] = useState(false);
  const [connectionResults, setConnectionResults] = useState({});
  const [filterClient, setFilterClient] = useState([]);
  const [filterCategory, setFilterCategory] = useState([]);
  const [filterMotor, setFilterMotor] = useState([]);
  const [filterDatabase, setFilterDatabase] = useState([]);
  const [filterConnection, setFilterConnection] = useState([]);
  const [savingCategoryPlates, setSavingCategoryPlates] = useState(() => new Set());
  const [savingVocacionalPlates, setSavingVocacionalPlates] = useState(() => new Set());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sort, setSort] = useState({ key: null, dir: null });
  const [openFilterKey, setOpenFilterKey] = useState(null);
  // Solo la fila en edicion monta un <select> nativo; las demas muestran el badge.
  // Asi evitamos cientos de selects nativos en el DOM (coste de paint + hit-testing).
  const [editingCategoryPlate, setEditingCategoryPlate] = useState(null);
  const [reprocessPromptPlates, setReprocessPromptPlates] = useState(null);
  const [reprocessSkipGeotab, setReprocessSkipGeotab] = useState(false);
  const selectAllRef = useRef(null);
  const detailRequestRef = useRef(0);
  const { loading, vehicles, error, search, setSearch, loadVehicles, patchVehicle } = useVehicleAssignments();
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { motors, loading: motorsLoading } = useMotorsCatalog();
  const { toasts, pushToast } = useToasts();
  const { bulkRefresh, startBulkRefresh, cancelBulkRefresh, acknowledgeBulkRefresh } = useBulkRefresh();
  const canEditVehicles = usePermission("vehicles.edit");
  const canRefreshVehicles = usePermission("vehicles.refresh");

  const [columnSelectorOpen, setColumnSelectorOpen] = useState(false);
  const defaultVisibleColumns = useMemo(() => VEHICLE_COLUMNS.map((c) => c.key), []);
  const validColumnKeys = useMemo(() => new Set(VEHICLE_COLUMNS.map((c) => c.key)), []);
  const validateVisibleColumns = useCallback(
    (raw) => {
      if (!Array.isArray(raw)) return null;
      const filtered = raw.filter((k) => typeof k === "string" && validColumnKeys.has(k));
      return filtered.length > 0 ? filtered : null;
    },
    [validColumnKeys]
  );
  const { value: savedColumns, setValue: persistColumns } = useUserPreference(
    "vehicles.visible_columns",
    null,
    { validator: validateVisibleColumns }
  );

  const visibleColumns = Array.isArray(savedColumns) && savedColumns.length > 0
    ? savedColumns
    : defaultVisibleColumns;

  const handleApplyColumns = useCallback(
    (nextKeys) => {
      persistColumns(nextKeys);
    },
    [persistColumns]
  );

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

  useEffect(() => {
    setPage(1);
  }, [sort.key, sort.dir]);

  const filteredVehicles = useMemo(() => {
    let result = vehicles;
    if (filterClient.length) {
      result = result.filter((v) => filterClient.includes(v.client_name));
    }
    if (filterCategory.length) {
      result = result.filter((v) => filterCategory.includes(v.category || "Ninguna"));
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
  }, [vehicles, filterClient, filterCategory, filterMotor, filterDatabase, filterConnection, connectionResults]);

  const activeColumns = useMemo(() => {
    const byKey = new Map(VEHICLE_COLUMNS.map((col) => [col.key, col]));
    const ordered = visibleColumns
      .map((key) => byKey.get(key))
      .filter((col) => Boolean(col));
    const known = new Set(visibleColumns);
    const missing = VEHICLE_COLUMNS.filter((col) => !known.has(col.key));
    return [...ordered, ...missing];
  }, [visibleColumns]);

  const sortedVehicles = useMemo(() => {
    if (!sort.key || !sort.dir) return filteredVehicles;
    const col = VEHICLE_COLUMNS.find((c) => c.key === sort.key);
    if (!col) return filteredVehicles;
    const accessor = (v) => col.getSortValue?.(v) ?? col.getValue?.(v);
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...filteredVehicles].sort((a, b) => {
      const va = accessor(a);
      const vb = accessor(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") {
        return (va - vb) * factor;
      }
      return (
        String(va)
          .localeCompare(String(vb), "es", { numeric: true, sensitivity: "base" }) *
        factor
      );
    });
  }, [filteredVehicles, sort]);

  const totalVehicles = filteredVehicles.length;
  const totalPages = Math.max(1, Math.ceil(totalVehicles / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * pageSize;
  const pagedVehicles = sortedVehicles.slice(pageStart, pageStart + pageSize);
  const fromRow = totalVehicles === 0 ? 0 : pageStart + 1;
  const toRow = Math.min(pageStart + pageSize, totalVehicles);

  const resetPage = useCallback(() => setPage(1), []);
  const handleFilterClient = useCallback((next) => { setFilterClient(next); resetPage(); }, [resetPage]);
  const handleFilterCategory = useCallback((next) => { setFilterCategory(next); resetPage(); }, [resetPage]);
  const handleFilterMotor = useCallback((next) => { setFilterMotor(next); resetPage(); }, [resetPage]);
  const handleFilterDatabase = useCallback((next) => { setFilterDatabase(next); resetPage(); }, [resetPage]);
  const handleFilterConnection = useCallback((next) => { setFilterConnection(next); resetPage(); }, [resetPage]);
  const handleSearchChange = useCallback((event) => { setSearch(event.target.value.toUpperCase()); resetPage(); }, [resetPage, setSearch]);
  const handleSearchClear = useCallback(() => { setSearch(""); resetPage(); }, [resetPage, setSearch]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const handleOpenDetails = useCallback((vehicle) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    setSelectedVehicle(vehicle);
    setDetailLoading(true);
    fetchVehicleDetail(vehicle.plate)
      .then((detail) => {
        if (detailRequestRef.current === requestId) setSelectedVehicle(detail);
      })
      .catch((err) => {
        if (detailRequestRef.current === requestId) {
          pushToast("error", err instanceof Error ? err.message : "No fue posible cargar el detalle del vehiculo");
        }
      })
      .finally(() => {
        if (detailRequestRef.current === requestId) setDetailLoading(false);
      });
  }, [pushToast]);

  const handleCloseDetails = useCallback(() => {
    detailRequestRef.current += 1;
    setDetailLoading(false);
    setSelectedVehicle(null);
  }, []);

  const handleLoadAttachments = useCallback(async (vehicle) => {
    if (Array.isArray(vehicle.attachments)) {
      return;
    }

    const count = vehicle.attachments_count ?? 0;
    const pendingWindow = count === 1 ? window.open("", "_blank") : null;
    setLoadingAttachmentPlates((current) => new Set(current).add(vehicle.plate));

    try {
      const detail = await fetchVehicleDetail(vehicle.plate);
      patchVehicle(vehicle.plate, detail);
      setSelectedVehicle((current) =>
        current?.plate === vehicle.plate ? detail : current
      );

      const attachments = Array.isArray(detail.attachments) ? detail.attachments : [];
      if (pendingWindow && attachments.length === 1) {
        pendingWindow.opener = null;
        pendingWindow.location.href = `${API_BASE}${attachments[0].download_url}`;
      } else {
        pendingWindow?.close();
      }

      if (!attachments.length) {
        pushToast("error", `No se encontraron adjuntos para ${vehicle.plate}.`);
      }
    } catch (err) {
      pendingWindow?.close();
      pushToast("error", err instanceof Error ? err.message : "No fue posible cargar los adjuntos");
    } finally {
      setLoadingAttachmentPlates((current) => {
        const next = new Set(current);
        next.delete(vehicle.plate);
        return next;
      });
    }
  }, [patchVehicle, pushToast]);

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
    setFilterCategory([]);
    setFilterMotor([]);
    setFilterDatabase([]);
    setFilterConnection([]);
    setSelectedPlates(new Set());
    setBulkAssignOpen(false);
    resetPage();
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
          marketing_model_name: selectedVehicle.marketing_model_name || null,
          service_model_name: selectedVehicle.service_model_name || null,
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

  const handleChangeCategory = async (vehicle, rawValue) => {
    // El valor "__inherit__" limpia el override y vuelve a heredar la del cliente.
    const override = rawValue === "__inherit__" ? null : rawValue;
    setSavingCategoryPlates((prev) => new Set(prev).add(vehicle.plate));
    try {
      const result = await setVehicleCategory(vehicle.plate, override);
      patchVehicle(vehicle.plate, {
        category: result.category,
        category_is_inherited: result.category_is_inherited,
        customer_category: result.customer_category
      });
      setSelectedVehicle((prev) =>
        prev && prev.plate === vehicle.plate
          ? {
              ...prev,
              category: result.category,
              category_is_inherited: result.category_is_inherited,
              customer_category: result.customer_category
            }
          : prev
      );
      setEditingCategoryPlate(null);
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible actualizar la categoria"
      );
    } finally {
      setSavingCategoryPlates((prev) => {
        const next = new Set(prev);
        next.delete(vehicle.plate);
        return next;
      });
    }
  };

  const handleChangeVocacional = async (vehicle, nextValue) => {
    setSavingVocacionalPlates((prev) => new Set(prev).add(vehicle.plate));
    try {
      const result = await setVehicleVocacional(vehicle.plate, nextValue);
      patchVehicle(vehicle.plate, { vocacional: result.vocacional });
      setSelectedVehicle((prev) =>
        prev && prev.plate === vehicle.plate
          ? { ...prev, vocacional: result.vocacional }
          : prev
      );
    } catch (err) {
      pushToast(
        "error",
        err instanceof Error ? err.message : "No fue posible actualizar el flag vocacional"
      );
    } finally {
      setSavingVocacionalPlates((prev) => {
        const next = new Set(prev);
        next.delete(vehicle.plate);
        return next;
      });
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
              Columnas ({visibleColumns.length}/{VEHICLE_COLUMNS.length})
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              onClick={handleExportExcel}
              disabled={filteredVehicles.length === 0}
            >
              Exportar
            </button>
            <Can permission="vehicles.edit">
              <button
                type="button"
                className="button-secondary button-sm"
                onClick={() => setBulkAssignOpen(true)}
                disabled={loading || bulkAssigning || selectedVehicles.length === 0}
              >
                Asignar ({selectedVehicles.length})
              </button>
            </Can>
            <Can permission="vehicles.refresh">
              <button
                type="button"
                className="button button-sm"
                onClick={handleCheckConnections}
                disabled={loading || checkingConnections}
              >
                {checkingConnections ? "Revisando..." : "Conexión"}
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
                Reprocesar ({selectedVehicles.length > 0 ? selectedVehicles.length : filteredVehicles.length})
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
                onChange={handleSearchChange}
                placeholder="Placa, VIN, TEC#, CPL, motor, cliente, database, nombre, linea o marca"
              />
              {search && (
                <button
                  type="button"
                  className="search-clear-button"
                  onClick={handleSearchClear}
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
                <th style={{ width: 40 }}>
                  <input
                    ref={selectAllRef}
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={handleToggleVisibleSelection}
                    aria-label="Seleccionar vehiculos visibles"
                  />
                </th>
                {activeColumns.map((col) => {
                  const isFilterColumn = [ "client_name", "category", "engine_name", "database_name", "db_connection" ].includes(col.key);
                  return (
                    <th key={col.key} style={col.width ? { width: col.width } : undefined}>
                      <div
                        className={`th-content${isFilterColumn ? " has-filter" : ""}`}
                        onClick={isFilterColumn ? () => setOpenFilterKey(col.key) : undefined}
                      >
                        {col.key === "client_name" && (
                          <MultiSelectFilter
                            label={col.label}
                            options={clientOptions}
                            selected={filterClient}
                            onChange={handleFilterClient}
                            open={openFilterKey === col.key}
                            onOpenChange={(isOpen) => setOpenFilterKey(isOpen ? col.key : null)}
                          />
                        )}
                        {col.key === "category" && (
                          <MultiSelectFilter
                            label={col.label}
                            options={CUSTOMER_CATEGORIES}
                            selected={filterCategory}
                            onChange={handleFilterCategory}
                            open={openFilterKey === col.key}
                            onOpenChange={(isOpen) => setOpenFilterKey(isOpen ? col.key : null)}
                          />
                        )}
                        {col.key === "engine_name" && (
                          <MultiSelectFilter
                            label={col.label}
                            options={motorOptions}
                            selected={filterMotor}
                            onChange={handleFilterMotor}
                            open={openFilterKey === col.key}
                            onOpenChange={(isOpen) => setOpenFilterKey(isOpen ? col.key : null)}
                          />
                        )}
                        {col.key === "database_name" && (
                          <MultiSelectFilter
                            label={col.label}
                            options={databaseOptions}
                            selected={filterDatabase}
                            onChange={handleFilterDatabase}
                            open={openFilterKey === col.key}
                            onOpenChange={(isOpen) => setOpenFilterKey(isOpen ? col.key : null)}
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
                            onChange={handleFilterConnection}
                            open={openFilterKey === col.key}
                            onOpenChange={(isOpen) => setOpenFilterKey(isOpen ? col.key : null)}
                          />
                        )}
                        {!isFilterColumn && <span>{col.label}</span>}
                        <SortButton
                          columnKey={col.key}
                          currentSort={sort}
                          onSortChange={setSort}
                        />
                      </div>
                    </th>
                  );
                })}
                <th style={{ width: 80 }}>Detalles</th>
              </tr>
            </thead>
            <tbody>
              {pagedVehicles.length === 0 ? (
                <tr>
                  <td colSpan={activeColumns.length + 2} className="table-empty-row">
                    {loading ? "Cargando..." : "No hay vehiculos que coincidan con los filtros actuales."}
                  </td>
                </tr>
              ) : (
                pagedVehicles.map((vehicle) => (
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
                      if (col.key === "category") {
                        const effective = vehicle.category || "Ninguna";
                        const inherited = vehicle.category_is_inherited;
                        const selectValue = inherited ? "__inherit__" : effective;
                        const badge = (
                          <span
                            className={`${categoryBadgeClass(effective)} ${inherited ? "is-inherited" : ""}`}
                            title={inherited ? "Heredada del cliente" : "Categoria propia del vehiculo"}
                          >
                            {effective}
                          </span>
                        );
                        if (!canEditVehicles) {
                          return (
                            <td key={col.key} data-label={col.label}>
                              {badge}
                            </td>
                          );
                        }
                        const isEditingCategory = editingCategoryPlate === vehicle.plate;
                        return (
                          <td key={col.key} data-label={col.label}>
                            {isEditingCategory ? (
                              <select
                                className="category-cell-select"
                                value={selectValue}
                                autoFocus
                                disabled={savingCategoryPlates.has(vehicle.plate)}
                                onChange={(event) => handleChangeCategory(vehicle, event.target.value)}
                                onFocus={(event) => {
                                  // Abre el desplegable en el mismo clic (donde el navegador lo soporta).
                                  try {
                                    event.target.showPicker?.();
                                  } catch {
                                    /* sin activacion suficiente: el usuario lo abre con otro clic */
                                  }
                                }}
                                onBlur={() => setEditingCategoryPlate(null)}
                                aria-label={`Categoria de ${vehicle.plate}`}
                              >
                                <option value="__inherit__">
                                  Heredar del cliente ({vehicle.customer_category || "Ninguna"})
                                </option>
                                {CUSTOMER_CATEGORIES.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <button
                                type="button"
                                className="category-cell-trigger"
                                onClick={() => setEditingCategoryPlate(vehicle.plate)}
                                disabled={savingCategoryPlates.has(vehicle.plate)}
                                title="Cambiar categoria"
                              >
                                {badge}
                              </button>
                            )}
                          </td>
                        );
                      }
                      if (col.key === "vocacional") {
                        const isVocacional = Boolean(vehicle.vocacional);
                        return (
                          <td key={col.key} data-label={col.label}>
                            <span
                              className={`status vocacional-badge ${isVocacional ? "is-true" : "is-false"}`}
                              title={isVocacional ? "Vehiculo de uso vocacional" : "Vehiculo de transporte"}
                            >
                              {isVocacional ? "Vocacional" : "Transporte"}
                            </span>
                          </td>
                        );
                      }
                      if (col.key === "attachments") {
                        const attachments = Array.isArray(vehicle.attachments) ? vehicle.attachments : null;
                        const count = vehicle.attachments_count ?? vehicle.attachments?.length ?? 0;
                        return (
                          <td key={col.key} data-label={col.label}>
                            {attachments?.length ? (
                              <div className="attachment-list attachment-list-compact">
                                {attachments.map((attachment) => (
                                  <a
                                    key={attachment.id}
                                    className="attachment-chip"
                                    href={`${API_BASE}${attachment.download_url}`}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={`${attachment.original_filename} | CPL ${attachment.cpl || "Sin CPL"}`}
                                    aria-label={`Abrir adjunto ${attachment.original_filename}`}
                                  >
                                    <AttachmentIcon contentType={attachment.content_type} />
                                  </a>
                                ))}
                              </div>
                            ) : count ? (
                              <button
                                type="button"
                                className="attachment-count-chip"
                                onClick={() => handleLoadAttachments(vehicle)}
                                disabled={loadingAttachmentPlates.has(vehicle.plate)}
                                title={`${count} adjunto(s) — cargar adjuntos`}
                                aria-label={`${count} adjuntos; cargar adjuntos`}
                              >
                                <AttachmentIcon contentType={null} />
                                <span>{loadingAttachmentPlates.has(vehicle.plate) ? "..." : count}</span>
                              </button>
                            ) : (
                              "Sin adjuntos"
                            )}
                          </td>
                        );
                      }
                      return (
                        <td key={col.key} data-label={col.label}>
                          <span className="cell-truncate" title={typeof col.getValue(vehicle) === "string" ? col.getValue(vehicle) : undefined}>
                            {col.getValue(vehicle)}
                          </span>
                        </td>
                      );
                    })}
                    <td data-label="Detalles">
                      <div className="actions-row vehicles-row-actions">
                        <button
                          type="button"
                          className="icon-button"
                          title="Ver detalles del vehiculo"
                          aria-label="Ver detalles del vehiculo"
                          onClick={() => handleOpenDetails(vehicle)}
                        >
                          <svg
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <circle cx="12" cy="12" r="9" />
                            <line x1="12" y1="8" x2="12" y2="12" />
                            <line x1="12" y1="16" x2="12.01" y2="16" />
                          </svg>
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="vehicles-pagination">
          <span className="vehicles-pagination-info">
            {totalVehicles === 0
              ? "0 vehiculos"
              : `${fromRow}–${toRow} de ${totalVehicles}`}
          </span>
          <div className="vehicles-pagination-controls">
            <button
              type="button"
              className="icon-button"
              onClick={() => setPage(Math.max(1, safePage - 1))}
              disabled={safePage <= 1}
              aria-label="Pagina anterior"
              title="Pagina anterior"
            >
              ‹
            </button>
            <span className="vehicles-pagination-pages">
              Pagina {safePage} de {totalPages}
            </span>
            <button
              type="button"
              className="icon-button"
              onClick={() => setPage(Math.min(totalPages, safePage + 1))}
              disabled={safePage >= totalPages}
              aria-label="Pagina siguiente"
              title="Pagina siguiente"
            >
              ›
            </button>
          </div>
          <label className="vehicles-pagination-size">
            Filas
            <select
              value={pageSize}
              onChange={(event) => setPageSize(Number(event.target.value))}
              aria-label="Filas por pagina"
            >
              {[10, 25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
        </div>

      </section>

      <VehicleAssignmentModal
        open={Boolean(selectedVehicle)}
        loading={loading || customersLoading || motorsLoading || detailLoading}
        title={selectedVehicle ? `Detalles ${selectedVehicle.plate}` : "Detalles del vehiculo"}
        vehicle={selectedVehicle}
        customers={customers}
        motors={motors}
        requiresMotorRegistration
        initialTechnicalNumber={selectedVehicle?.technical_number || ""}
        onClose={handleCloseDetails}
        onSubmit={handleUpdateVehicle}
        onRevalidateCustomerGeotab={handleRevalidateCustomerGeotab}
        canEditVehicle={canEditVehicles}
        canRevalidateCustomerGeotab={canRefreshVehicles}
        onChangeCategory={handleChangeCategory}
        savingCategory={selectedVehicle ? savingCategoryPlates.has(selectedVehicle.plate) : false}
        onChangeVocacional={handleChangeVocacional}
        savingVocacional={selectedVehicle ? savingVocacionalPlates.has(selectedVehicle.plate) : false}
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
