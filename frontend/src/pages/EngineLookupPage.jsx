import { useCallback, useEffect, useState } from "react";
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

export default function EngineLookupPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [identifier, setIdentifier] = useState("");
  const [isRegisterOpen, setIsRegisterOpen] = useState(false);
  const { toasts, pushToast } = useToasts();
  const [history, setHistory] = useState(loadHistory);
  const { customers, loading: customersLoading } = useCustomersCatalog();
  const { motors } = useMotorsCatalog();
  const canEditVehicles = usePermission("vehicles.edit");
  const canUseBulkLookup = usePermission("engine_lookup.batch");
  const activeMode = canUseBulkLookup && searchParams.get("modo") === "lote" ? "lote" : "individual";

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
      await searchVehicle(normalized);
    },
    [searchVehicle]
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
      <header className="page-header">
        <span className="eyebrow">Lookup</span>
        <h2>Consulta de motor</h2>
        <p>Consulta individual por placa o VIN, y si tienes permiso, tambien procesamiento en lote desde Excel.</p>
      </header>

      {canUseBulkLookup ? (
        <div className="lookup-tabs" role="tablist" aria-label="Modo de consulta de motor">
          <button
            type="button"
            className={activeMode === "individual" ? "lookup-tab is-active" : "lookup-tab"}
            aria-pressed={activeMode === "individual"}
            onClick={() => setMode("individual")}
          >
            Consulta individual
          </button>
          <button
            type="button"
            className={activeMode === "lote" ? "lookup-tab is-active" : "lookup-tab"}
            aria-pressed={activeMode === "lote"}
            onClick={() => setMode("lote")}
          >
            Consulta en lote
          </button>
        </div>
      ) : null}

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
              onForceSearch={lookupResult.cached ? () => searchVehicle(identifier, { force: true }) : undefined}
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
