import { useEffect, useMemo, useState } from "react";

import { listCustomerGroups } from "../../../api/vehicleApi";
import { getDatabaseTypeLabel } from "../../customers/providerCatalog";

function commonValue(items, getValue) {
  if (!items.length) return "";
  const first = getValue(items[0]) || "";
  return items.every((item) => (getValue(item) || "") === first) ? first : "";
}

// Aplana el arbol de grupos (parent_id) a opciones con sangria por nivel.
function flattenGroupTree(groups) {
  const byParent = new Map();
  groups.forEach((group) => {
    const key = group.parent_id ?? 0;
    if (!byParent.has(key)) {
      byParent.set(key, []);
    }
    byParent.get(key).push(group);
  });
  const ordered = [];
  const walk = (parentKey, depth) => {
    (byParent.get(parentKey) || []).forEach((group) => {
      ordered.push({ ...group, depth });
      walk(group.id, depth + 1);
    });
  };
  walk(0, 0);
  return ordered;
}

export default function BulkVehicleAssignmentModal({
  open,
  loading = false,
  customers = [],
  vehicles = [],
  onClose,
  onSubmit,
}) {
  const [selectedCustomerId, setSelectedCustomerId] = useState("");
  const [selectedDatabaseId, setSelectedDatabaseId] = useState("");
  // "" = no cambiar el grupo, "__none__" = quitar grupo, otro = id del grupo.
  const [selectedGroupValue, setSelectedGroupValue] = useState("");
  const [groupOptions, setGroupOptions] = useState([]);

  useEffect(() => {
    if (!open || !selectedCustomerId) {
      setGroupOptions([]);
      setSelectedGroupValue("");
      return;
    }
    let cancelled = false;
    listCustomerGroups(Number(selectedCustomerId))
      .then((records) => {
        if (!cancelled) {
          setGroupOptions(flattenGroupTree(records).filter((group) => group.is_active));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setGroupOptions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, selectedCustomerId]);

  useEffect(() => {
    if (!open) return;

    const commonClientName = commonValue(vehicles, (vehicle) => vehicle.client_name);
    const matchingCustomer = customers.find((customer) => customer.name === commonClientName);
    const customerId = matchingCustomer ? String(matchingCustomer.id) : "";
    setSelectedCustomerId(customerId);

    const commonDatabaseName = commonValue(vehicles, (vehicle) => vehicle.database_name);
    const commonDatabaseUsername = commonValue(vehicles, (vehicle) => vehicle.database_username);
    const matchingDatabase = matchingCustomer?.databases.find(
      (database) =>
        database.database_name === commonDatabaseName &&
        database.username === commonDatabaseUsername
    );
    setSelectedDatabaseId(matchingDatabase ? String(matchingDatabase.id) : "");
  }, [customers, open, vehicles]);

  const selectedCustomer = useMemo(
    () => customers.find((customer) => String(customer.id) === selectedCustomerId) || null,
    [customers, selectedCustomerId]
  );

  const availableDatabases = selectedCustomer?.databases || [];

  useEffect(() => {
    if (!selectedCustomerId) {
      setSelectedDatabaseId("");
      return;
    }
    const exists = availableDatabases.some((database) => String(database.id) === selectedDatabaseId);
    if (!exists) {
      setSelectedDatabaseId("");
    }
  }, [availableDatabases, selectedCustomerId, selectedDatabaseId]);

  if (!open) return null;

  const previewVehicles = vehicles.slice(0, 8);
  const remainingCount = Math.max(vehicles.length - previewVehicles.length, 0);

  const handleSubmit = async (event) => {
    event.preventDefault();
    const payload = {
      customer_database_id: selectedDatabaseId ? Number(selectedDatabaseId) : null,
    };
    if (selectedGroupValue !== "") {
      payload.customer_group_id =
        selectedGroupValue === "__none__" ? null : Number(selectedGroupValue);
    }
    await onSubmit(payload);
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="card modal-card" role="dialog" aria-modal="true" aria-label="Asignacion masiva de vehiculos">
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Masivo</span>
            <h3>Asignar cliente y database</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy modal-support-copy">
          Se actualizaran {vehicles.length} vehiculos al mismo tiempo.
        </p>

        <div className="bulk-selection-preview">
          {previewVehicles.map((vehicle) => (
            <span key={vehicle.plate} className="warning-chip">
              {vehicle.plate}
            </span>
          ))}
          {remainingCount > 0 ? (
            <span className="warning-chip">+{remainingCount} mas</span>
          ) : null}
        </div>

        <form className="register-form" onSubmit={handleSubmit}>
          <div className="form-field">
            <label htmlFor="bulk-assign-customer">Cliente <span className="form-optional">(opcional)</span></label>
            <select
              id="bulk-assign-customer"
              value={selectedCustomerId}
              onChange={(event) => setSelectedCustomerId(event.target.value)}
            >
              <option value="">Sin cliente</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="bulk-assign-database">Database <span className="form-optional">(opcional)</span></label>
            <select
              id="bulk-assign-database"
              value={selectedDatabaseId}
              onChange={(event) => setSelectedDatabaseId(event.target.value)}
              disabled={!selectedCustomerId}
            >
              <option value="">Sin database</option>
              {availableDatabases.map((database) => (
                <option key={database.id} value={database.id}>
                  {database.database_name} | {database.username}
                  {database.connection_type && database.connection_type !== "database"
                    ? ` [${getDatabaseTypeLabel(database.connection_type)}]`
                    : ""}
                </option>
              ))}
            </select>
          </div>

          {selectedCustomerId && groupOptions.length > 0 ? (
            <div className="form-field">
              <label htmlFor="bulk-assign-group">
                Grupo interno <span className="form-optional">(opcional)</span>
              </label>
              <select
                id="bulk-assign-group"
                value={selectedGroupValue}
                onChange={(event) => setSelectedGroupValue(event.target.value)}
              >
                <option value="">No cambiar</option>
                <option value="__none__">Quitar grupo</option>
                {groupOptions.map((group) => (
                  <option key={group.id} value={group.id}>
                    {`${"— ".repeat(group.depth)}${group.name}`}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          <small className="support-copy">
            Si dejas la database vacia, se quitara la asignacion actual de los vehiculos seleccionados.
          </small>

          <div className="actions-row modal-actions">
            <button type="submit" disabled={loading}>
              {loading ? "Asignando..." : `Aplicar a ${vehicles.length} vehiculos`}
            </button>
            <button type="button" className="button-secondary" onClick={onClose} disabled={loading}>
              Cancelar
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
