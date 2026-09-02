import { useEffect, useRef, useState } from "react";

import { MultiSelectFilter } from "../../../components/MultiSelectFilter";
import { fetchAdhocFilterOptions, listCustomers, listVehicleAssignments } from "../../../api/vehicleApi";
import { DATABASE_PROVIDERS } from "../../customers/providerCatalog";
import { getCurrentMonth } from "../../../utils/formatters";

const PERFORMANCE_PROVIDER_KEYS = new Set(
  DATABASE_PROVIDERS.filter((provider) => provider.supportsMonthlyPerformance).map((provider) => provider.key),
);

function buildClientSelectionLabel(eligibleClients, selectedCustomerIds) {
  if (!eligibleClients.length) return "Sin clientes";
  if (!selectedCustomerIds.length) return "Todos los clientes";
  const selected = eligibleClients.filter((client) => selectedCustomerIds.includes(client.id));
  if (selected.length === 1) return selected[0].name;
  if (selected.length === 2) return `${selected[0].name} y ${selected[1].name}`;
  return `${selected.length} clientes seleccionados`;
}

function buildEligibleClients(customers, vehicles) {
  const readyByCustomerId = new Map();
  for (const vehicle of vehicles) {
    if (!PERFORMANCE_PROVIDER_KEYS.has(vehicle.database_connection_type) || !vehicle.customer_id || !vehicle.plate) {
      continue;
    }
    if (!readyByCustomerId.has(vehicle.customer_id)) readyByCustomerId.set(vehicle.customer_id, new Set());
    readyByCustomerId.get(vehicle.customer_id).add(vehicle.plate);
  }
  return customers
    .map((customer) => ({
      id: customer.id,
      name: customer.name,
      readyVehicles: readyByCustomerId.get(customer.id)?.size || 0,
      hasPerformanceDatabase: (customer.databases || []).some((db) => PERFORMANCE_PROVIDER_KEYS.has(db.connection_type)),
    }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function parsePlates(text) {
  return text
    .split(/[,\n\s]+/)
    .map((p) => p.trim().toUpperCase())
    .filter(Boolean);
}

function AdhocFilter({ label, options, selected, onChange, openKey, setOpenKey }) {
  return (
    <div className="form-field">
      <span className="rendimientos-filter-label">{label}</span>
      <div role="group" aria-label={label} className="rendimientos-multi-filter rendimientos-multi-filter-boxed">
        <MultiSelectFilter
          label={selected.length ? `${label} (${selected.length})` : label}
          options={options}
          selected={selected}
          onChange={onChange}
          open={openKey === label}
          onOpenChange={(isOpen) => setOpenKey(isOpen ? label : null)}
        />
      </div>
    </div>
  );
}

/**
 * Modal "Consultar": rango de meses, clientes, disponibilidad y alcance
 * ad-hoc. Se mantiene montado (renderiza null si !open) para conservar la
 * seleccion entre aperturas, igual que antes del refactor.
 *
 * onSubmit({ monthFrom, monthTo, buildPayload }) — buildPayload(month)
 * devuelve el body de POST /rendimientos/calculate para ese mes.
 */
export default function CalculateModal({ open, onClose, onSubmit, calculating, pushToast }) {
  const [calcMonthFrom, setCalcMonthFrom] = useState(getCurrentMonth);
  const [calcMonthTo, setCalcMonthTo] = useState(getCurrentMonth);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [eligibleClients, setEligibleClients] = useState([]);
  const [selectedCustomerIds, setSelectedCustomerIds] = useState([]);
  const [calcAvailability, setCalcAvailability] = useState(false);
  const [includeAdhoc, setIncludeAdhoc] = useState(false);
  const [adhocOnly, setAdhocOnly] = useState(false);
  const [adhocFilterOptions, setAdhocFilterOptions] = useState(null);
  const [adhocLoadingFilters, setAdhocLoadingFilters] = useState(false);
  const [adhocSelectedMarcas, setAdhocSelectedMarcas] = useState([]);
  const [adhocSelectedLineas, setAdhocSelectedLineas] = useState([]);
  const [adhocSelectedNombres, setAdhocSelectedNombres] = useState([]);
  const [adhocPlatesText, setAdhocPlatesText] = useState("");
  const [openAdhocKey, setOpenAdhocKey] = useState(null);
  const pickerRef = useRef(null);

  // Clientes elegibles (con database que soporta rendimientos).
  useEffect(() => {
    let cancelled = false;
    setCatalogLoading(true);
    Promise.all([listCustomers(), listVehicleAssignments()])
      .then(([customers, vehicles]) => {
        if (!cancelled) setEligibleClients(buildEligibleClients(customers, vehicles));
      })
      .catch((err) => {
        if (!cancelled) pushToast("error", err instanceof Error ? err.message : "No fue posible cargar clientes");
      })
      .finally(() => {
        if (!cancelled) setCatalogLoading(false);
      });
    return () => { cancelled = true; };
  }, [pushToast]);

  // Limpieza: quita ids seleccionados que ya no existen.
  useEffect(() => {
    setSelectedCustomerIds((current) => {
      const next = current.filter((id) => eligibleClients.some((client) => client.id === id));
      return next.length === current.length ? current : next;
    });
  }, [eligibleClients]);

  // Opciones ad-hoc (lazy, al activar el check).
  useEffect(() => {
    if (!includeAdhoc || adhocFilterOptions) return undefined;
    let cancelled = false;
    setAdhocLoadingFilters(true);
    fetchAdhocFilterOptions()
      .then((data) => { if (!cancelled) setAdhocFilterOptions(data); })
      .catch((err) => { if (!cancelled) pushToast("error", err instanceof Error ? err.message : "Error cargando filtros ad-hoc"); })
      .finally(() => { if (!cancelled) setAdhocLoadingFilters(false); });
    return () => { cancelled = true; };
  }, [includeAdhoc, adhocFilterOptions, pushToast]);

  // Cierra el picker de clientes (details) al hacer clic fuera.
  useEffect(() => {
    if (!open) return undefined;
    function handleClickOutside(event) {
      if (pickerRef.current && !pickerRef.current.contains(event.target)) {
        pickerRef.current.removeAttribute("open");
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  if (!open) return null;

  const adhocHasScope =
    adhocSelectedMarcas.length > 0 ||
    adhocSelectedLineas.length > 0 ||
    adhocSelectedNombres.length > 0 ||
    Boolean(adhocPlatesText.trim());

  const handleSubmit = (event) => {
    event.preventDefault();
    const adhocPlates = parsePlates(adhocPlatesText);
    const adhocFilters = {};
    if (adhocSelectedMarcas.length) adhocFilters.marca = adhocSelectedMarcas;
    if (adhocSelectedLineas.length) adhocFilters.linea = adhocSelectedLineas;
    if (adhocSelectedNombres.length) adhocFilters.nombre_vehiculo = adhocSelectedNombres;

    const buildPayload = (month) => ({
      month,
      customer_ids: adhocOnly ? [] : selectedCustomerIds,
      force_recalculate: true,
      compute_availability: calcAvailability,
      include_adhoc: includeAdhoc,
      adhoc_only: adhocOnly,
      ...(includeAdhoc && { adhoc_plates: adhocPlates, adhoc_filters: adhocFilters }),
    });
    onSubmit({ monthFrom: calcMonthFrom, monthTo: calcMonthTo, buildPayload });
  };

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="card modal-card modal-card--popover" role="dialog" aria-modal="true" aria-label="Consultar rendimientos">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Calcular rendimientos</span>
            <h3>Consultar</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          Selecciona el rango de meses y los clientes a procesar. El cálculo correrá en segundo plano y verás el progreso en la parte superior.
        </p>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="rendimientos-month-actions">
            <div className="form-field">
              <label htmlFor="rendimientos-calc-month-from">Desde</label>
              <input
                id="rendimientos-calc-month-from"
                type="month"
                value={calcMonthFrom}
                onChange={(event) => setCalcMonthFrom(event.target.value)}
                disabled={calculating}
              />
            </div>
            <div className="form-field">
              <label htmlFor="rendimientos-calc-month-to">Hasta</label>
              <input
                id="rendimientos-calc-month-to"
                type="month"
                value={calcMonthTo}
                onChange={(event) => setCalcMonthTo(event.target.value)}
                disabled={calculating}
              />
            </div>

            <details className="client-picker" ref={pickerRef}>
              <summary className="client-picker-summary">
                <span className="client-picker-label">Clientes</span>
                <span className="client-picker-value">{buildClientSelectionLabel(eligibleClients, selectedCustomerIds)}</span>
              </summary>

              <div className="client-picker-panel">
                <label className="client-picker-option" key="all-clients">
                  <input
                    type="checkbox"
                    checked={selectedCustomerIds.length === 0}
                    onChange={() => setSelectedCustomerIds([])}
                  />
                  <span>
                    Todos los clientes
                    <small>{eligibleClients.reduce((total, client) => total + client.readyVehicles, 0)} placas listas</small>
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
                                : [...current, client.id].sort((a, b) => a - b),
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
          </div>

          <label className="client-picker-option" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={calcAvailability}
              onChange={(event) => setCalcAvailability(event.target.checked)}
            />
            <span>
              Calcular Disponibilidad
              <small>Incluye el cálculo de disponibilidad de la flota (órdenes de taller CloudFleet) para el rango.</small>
            </span>
          </label>

          <label className="client-picker-option" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={includeAdhoc}
              onChange={(event) => {
                setIncludeAdhoc(event.target.checked);
                if (!event.target.checked) setAdhocOnly(false);
              }}
            />
            <span>
              Incluir vehículos sin cliente (Navitrans Geotab)
              <small>Calcula rendimientos con credenciales globales de Navitrans para vehículos sin asignar.</small>
            </span>
          </label>

          {includeAdhoc && (
            <div className="adhoc-scope-radios" style={{ marginLeft: "1.75rem", marginBottom: "0.5rem" }}>
              <label className="client-picker-option" style={{ margin: 0 }}>
                <input type="radio" name="adhoc-scope" checked={!adhocOnly} onChange={() => setAdhocOnly(false)} />
                <span>
                  Clientes + vehículos ad-hoc
                  <small>Calcula los clientes seleccionados y además los vehículos ad-hoc.</small>
                </span>
              </label>
              <label className="client-picker-option" style={{ margin: 0 }}>
                <input type="radio" name="adhoc-scope" checked={adhocOnly} onChange={() => setAdhocOnly(true)} />
                <span>
                  Solo vehículos ad-hoc
                  <small>Calcula únicamente los vehículos filtrados abajo, sin incluir clientes.</small>
                </span>
              </label>
            </div>
          )}

          {includeAdhoc && (
            <div className="adhoc-filters-section">
              <span className="eyebrow" style={{ marginBottom: "0.5rem", display: "block" }}>Filtros avanzados</span>

              {adhocLoadingFilters ? (
                <p className="support-copy">Cargando filtros...</p>
              ) : !adhocFilterOptions ? (
                <p className="support-copy">No se pudieron cargar los filtros.</p>
              ) : adhocFilterOptions.total === 0 ? (
                <p className="support-copy">No hay vehículos sin cliente en el sistema.</p>
              ) : (
                <>
                  <p className="support-copy" style={{ marginBottom: "0.5rem" }}>
                    {adhocFilterOptions.total} vehículos sin cliente disponibles. Selecciona al menos un filtro.
                  </p>
                  <div className="adhoc-filters-grid">
                    {adhocFilterOptions.marcas.length > 0 && (
                      <AdhocFilter
                        label="Marca"
                        options={adhocFilterOptions.marcas}
                        selected={adhocSelectedMarcas}
                        onChange={setAdhocSelectedMarcas}
                        openKey={openAdhocKey}
                        setOpenKey={setOpenAdhocKey}
                      />
                    )}
                    {adhocFilterOptions.lineas.length > 0 && (
                      <AdhocFilter
                        label="Línea"
                        options={adhocFilterOptions.lineas}
                        selected={adhocSelectedLineas}
                        onChange={setAdhocSelectedLineas}
                        openKey={openAdhocKey}
                        setOpenKey={setOpenAdhocKey}
                      />
                    )}
                    {adhocFilterOptions.nombres.length > 0 && (
                      <AdhocFilter
                        label="Nombre"
                        options={adhocFilterOptions.nombres}
                        selected={adhocSelectedNombres}
                        onChange={setAdhocSelectedNombres}
                        openKey={openAdhocKey}
                        setOpenKey={setOpenAdhocKey}
                      />
                    )}
                  </div>

                  <div className="form-field" style={{ marginTop: "0.75rem" }}>
                    <label htmlFor="adhoc-plates-input">Placas específicas</label>
                    <textarea
                      id="adhoc-plates-input"
                      className="form-textarea"
                      rows={3}
                      placeholder="Pega placas separadas por coma, espacio o salto de línea"
                      value={adhocPlatesText}
                      onChange={(event) => setAdhocPlatesText(event.target.value)}
                    />
                  </div>

                  {!adhocHasScope && (
                    <p className="notice-banner notice-soft" style={{ marginTop: "0.5rem" }}>
                      Selecciona al menos un filtro o ingresa placas para el cálculo ad-hoc.
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          <div className="actions-row modal-actions">
            <button type="submit" disabled={calculating || !calcMonthFrom || !calcMonthTo}>
              {calculating ? "Calculando..." : "Calcular"}
            </button>
            <button type="button" className="button-secondary" onClick={onClose}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
