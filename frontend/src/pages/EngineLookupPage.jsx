import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import ToastStack from "../components/ToastStack";
import { useToasts } from "../components/useToasts";
import { usePermission } from "../context/AuthContext";
import BulkLookupPage from "./BulkLookupPage";
import LookupDetails from "../features/engineLookup/components/LookupDetails";
import LookupTimeline from "../features/engineLookup/components/LookupTimeline";
import { useCustomersCatalog } from "../features/customers/hooks/useCustomersCatalog";
import { useEngineLookup } from "../features/engineLookup/hooks/useEngineLookup";
import { useMotorsCatalog } from "../features/engineLookup/hooks/useMotorsCatalog";
import VehicleAssignmentModal from "../features/vehicles/components/VehicleAssignmentModal";

const HISTORY_KEY = "navi:lookup-history";
const MAX_HISTORY = 8;

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveToHistory(identifier) {
  const cleaned = identifier.trim().toUpperCase();
  if (!cleaned) return;
  try {
    const prev = loadHistory().filter((item) => item !== cleaned);
    const next = [cleaned, ...prev].slice(0, MAX_HISTORY);
    localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}

function LookupIcon({ type }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true
  };

  if (type === "batch") {
    return <svg {...common}><path d="M6 2h8l4 4v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z" /><path d="M14 2v5h5M8 12h8M8 16h5" /></svg>;
  }
  if (type === "database") {
    return <svg {...common}><ellipse cx="12" cy="5" rx="7" ry="3" /><path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7" /></svg>;
  }
  return <svg {...common}><path d="M4 21V5a2 2 0 0 1 2-2h8v18M14 8h4a2 2 0 0 1 2 2v11M8 7h2M8 11h2M8 15h2M16 13h1M16 17h1" /></svg>;
}

export default function EngineLookupPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [identifier, setIdentifier] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const { toasts, pushToast } = useToasts();
  const [history, setHistory] = useState(loadHistory);
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { motors } = useMotorsCatalog();
  const canEditVehicles = usePermission("vehicles.edit");
  const canUseBulkLookup = usePermission("engine_lookup.batch");
  const activeMode = canUseBulkLookup && searchParams.get("modo") === "lote" ? "lote" : "individual";
  const selectedCustomer = useMemo(
    () => customers.find((customer) => String(customer.id) === selectedCustomerId) || null,
    [customers, selectedCustomerId]
  );
  const geotabDatabases = useMemo(
    () => (selectedCustomer?.databases || []).filter((database) => database.connection_type === "geotab"),
    [selectedCustomer]
  );
  const selectedDatabase = useMemo(
    () => geotabDatabases.find((database) => String(database.id) === selectedDatabaseId) || null,
    [geotabDatabases, selectedDatabaseId]
  );
  const geotabLookupLabel = selectedDatabase
    ? `Geotab: ${selectedCustomer?.name || "cliente"} / ${selectedDatabase.database_name}`
    : "Navitrans";

  const {
    loading,
    lookupResult,
    error,
    steps,
    isManualAssignment,
    canRegisterCurrentMotor,
    canConfigureCurrentVehicle,
    searchVehicle,
    registerCurrentMotor,
    resetLookup
  } = useEngineLookup();

  useEffect(() => {
    if (error) pushToast("error", error);
  }, [error, pushToast]);

  const doSearch = useCallback(
    async (value) => {
      const normalized = value.trim().toUpperCase();
      if (!normalized || normalized.length < 3) return;
      setIdentifier(normalized);
      saveToHistory(normalized);
      setHistory(loadHistory());
      await searchVehicle(normalized, {
        customerDatabaseId: selectedDatabaseId ? Number(selectedDatabaseId) : null
      });
    },
    [searchVehicle, selectedDatabaseId]
  );

  const handleSubmit = async (event) => {
    event.preventDefault();
    await doSearch(identifier);
  };

  const handleRegisterMotor = async (payload) => {
    try {
      await registerCurrentMotor(payload);
      pushToast("success", "Vehiculo actualizado correctamente.");
      setIsRegisterOpen(false);
    } catch (err) {
      pushToast("error", err instanceof Error ? err.message : "No fue posible registrar el motor");
    }
  };

  const clearLookup = () => {
    setIdentifier("");
    resetLookup();
  };

  const setMode = (mode) => {
    const next = new URLSearchParams(searchParams);
    if (mode === "lote") {
      next.set("modo", "lote");
    } else {
      next.delete("modo");
    }
    setSearchParams(next, { replace: true });
  };

  // Auto-search from ?q= param (e.g. from dashboard quick search)
  useEffect(() => {
    const q = searchParams.get("q");
    if (q && q.trim().length >= 3) {
      setSearchParams({}, { replace: true });
      doSearch(q);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Focus input on mount
  useEffect(() => {
    const input = document.getElementById("lookup-identifier");
    if (input) input.focus();
  }, []);

  return (
    <section className="panel">
      <header className="page-header page-header-row lookup-page-header">
        <div>
          <span className="eyebrow">Lookup</span>
          <h2>Consulta de motor</h2>
          <p>Consulta individual por placa o VIN, y si tienes permiso, tambien procesamiento en lote desde Excel.</p>
        </div>
        {canUseBulkLookup ? (
          <button
            type="button"
            className="lookup-batch-button"
            onClick={() => setMode(activeMode === "individual" ? "lote" : "individual")}
          >
            <span className="lookup-batch-icon"><LookupIcon type="batch" /></span>
            <span>
              <strong>{activeMode === "individual" ? "Consulta en lote" : "Consulta individual"}</strong>
              <small>{activeMode === "individual" ? "Procesa múltiples vehículos desde Excel" : "Consulta una placa o VIN"}</small>
            </span>
            <span className="lookup-batch-arrow" aria-hidden="true">›</span>
          </button>
        ) : null}
      </header>

      {activeMode === "lote" ? (
        <BulkLookupPage embedded />
      ) : (
        <>
          <form className="lookup-bar" onSubmit={handleSubmit}>
            <div className="search-input-wrap lookup-bar-input-wrap">
              <input
                id="lookup-identifier"
                className="lookup-bar-input"
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value.toUpperCase())}
                placeholder="Placa o VIN — Ej: TLK240, 3HSDJAPR6GN123456"
                minLength={3}
                maxLength={32}
              />
              {identifier ? (
                <button
                  type="button"
                  className="search-clear-button"
                  onClick={clearLookup}
                  aria-label="Limpiar busqueda"
                >
                  ✕
                </button>
              ) : null}
            </div>
            <button type="submit" disabled={loading || identifier.trim().length < 3}>
              {loading ? "Buscando..." : "Consultar"}
            </button>
          </form>

          <div className="lookup-geotab-source">
            <div className="lookup-source-intro">
              <span className="lookup-source-icon"><LookupIcon type="database" /></span>
              <div>
                <span className="lookup-geotab-source-label">Fuente de datos</span>
                <p className="support-copy">Selecciona la base de datos para realizar la consulta.</p>
              </div>
            </div>
            <div className="lookup-source-field">
              <span className="lookup-source-icon"><LookupIcon type="customer" /></span>
              <label>
                Cliente
                <select
                  value={selectedCustomerId}
                  onChange={(event) => {
                    setSelectedCustomerId(event.target.value);
                    setSelectedDatabaseId("");
                  }}
                  disabled={loading || customersLoading}
                >
                  <option value="">Navitrans (predeterminado)</option>
                  {customers.map((customer) => (
                    <option key={customer.id} value={customer.id}>{customer.name}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="lookup-source-field">
              <span className="lookup-source-icon"><LookupIcon type="database" /></span>
              <label>
                Base de datos Geotab
                <select
                  value={selectedDatabaseId}
                  onChange={(event) => setSelectedDatabaseId(event.target.value)}
                  disabled={loading || !selectedCustomerId || geotabDatabases.length === 0}
                >
                  <option value="">
                    {selectedCustomerId ? "Selecciona una database" : "Selecciona primero un cliente"}
                  </option>
                  {geotabDatabases.map((database) => (
                    <option key={database.id} value={database.id}>
                      {database.database_name} · {database.username}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          {history.length > 0 && !lookupResult ? (
            <div className="lookup-history">
              <span className="lookup-history-label">Recientes</span>
              {history.map((item) => (
                <button
                  key={item}
                  type="button"
                  className="lookup-history-chip"
                  onClick={() => doSearch(item)}
                  disabled={loading}
                >
                  {item}
                </button>
              ))}
            </div>
          ) : null}

          <ToastStack toasts={toasts} />
          {loading ? (
            <LookupTimeline steps={steps} loading={loading} />
          ) : null}
          {!customersLoading && customers.length === 0 && lookupResult ? (
            <p className="notice-banner notice-soft">
              Crea clientes y databases en Gestion para poder asignarlos a un vehiculo.
            </p>
          ) : null}

          {lookupResult ? (
            <LookupDetails
              result={lookupResult}
              loading={loading}
              canRegister={canRegisterCurrentMotor}
              canConfigure={canConfigureCurrentVehicle}
              canManageVehicle={canEditVehicles}
              isManualAssignment={isManualAssignment}
              onAction={() => setIsRegisterOpen(true)}
              onForceSearch={lookupResult.cached ? () => searchVehicle(identifier, {
                force: true,
                customerDatabaseId: selectedDatabaseId ? Number(selectedDatabaseId) : null
              }) : undefined}
              geotabLabel={geotabLookupLabel}
            />
          ) : !loading ? (
            <p className="support-copy lookup-empty-hint">
              Ingresa una placa o VIN para identificar el motor y su configuracion tecnica.
            </p>
          ) : null}

          <VehicleAssignmentModal
            open={isRegisterOpen}
            loading={loading || customersLoading}
            title="Detalles del vehiculo"
            vehicle={{
              plate: lookupResult?.plate || null,
              vin: lookupResult?.vin || null,
              geotab_status: lookupResult?.geotab_status || "unknown",
              engine_number: lookupResult?.engine_number || null,
              technical_number: lookupResult?.technical_engine_configuration || null,
              engine_name: lookupResult?.registered_motor?.engine_name || null,
              cpl: lookupResult?.cpl || "",
              marketing_model_name: lookupResult?.marketing_model_name || null,
              service_model_name: lookupResult?.service_model_name || null,
              client_name: lookupResult?.assigned_database?.client_name || null,
              database_name: lookupResult?.assigned_database?.database_name || null,
              database_username: lookupResult?.assigned_database?.database_username || null,
              geotab_customer_status: lookupResult?.geotab_customer_status || "not_applicable"
            }}
            customers={customers}
            motors={isManualAssignment ? motors : []}
            initialTechnicalNumber={lookupResult?.technical_engine_configuration || ""}
            lockTechnicalNumber={!isManualAssignment}
            registeredMotor={lookupResult?.registered_motor || null}
            requiresMotorRegistration={!lookupResult?.registered_motor}
            allowCreateMotor
            onClose={() => setIsRegisterOpen(false)}
            onSubmit={handleRegisterMotor}
            canEditVehicle={canEditVehicles}
          />
        </>
      )}
    </section>
  );
}
