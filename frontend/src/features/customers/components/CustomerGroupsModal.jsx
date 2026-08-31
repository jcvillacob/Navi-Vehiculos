import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createCustomerGroup,
  deleteCustomerGroup,
  listCustomerGroups,
  updateCustomerGroup,
} from "../../../api/vehicleApi";

/**
 * Gestion del arbol de grupos internos de vehiculos de un cliente
 * (categorias y subcategorias, ej. Regional -> CEDI). El arbol viaja plano
 * (parent_id) y aqui se arma por niveles con sangria.
 */

function buildTree(groups) {
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
    const children = byParent.get(parentKey) || [];
    children.forEach((group) => {
      ordered.push({ ...group, depth });
      walk(group.id, depth + 1);
    });
  };
  walk(0, 0);
  return ordered;
}

export default function CustomerGroupsModal({ open, customer, canEdit = true, onClose }) {
  const [groups, setGroups] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [newParentId, setNewParentId] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editingName, setEditingName] = useState("");

  const loadGroups = useCallback(async () => {
    if (!customer?.id) {
      return;
    }
    setLoading(true);
    setError("");
    try {
      const records = await listCustomerGroups(customer.id);
      setGroups(records);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible cargar los grupos");
    } finally {
      setLoading(false);
    }
  }, [customer?.id]);

  useEffect(() => {
    if (open) {
      setNewName("");
      setNewParentId("");
      setEditingId(null);
      setError("");
      loadGroups();
    }
  }, [open, loadGroups]);

  const orderedGroups = useMemo(() => buildTree(groups), [groups]);

  const runAction = async (action) => {
    setSaving(true);
    setError("");
    try {
      await action();
      await loadGroups();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible guardar el cambio");
    } finally {
      setSaving(false);
    }
  };

  const handleCreate = async (event) => {
    event.preventDefault();
    const name = newName.trim();
    if (!name) {
      return;
    }
    await runAction(async () => {
      await createCustomerGroup(customer.id, {
        name,
        parent_id: newParentId ? Number(newParentId) : null,
      });
      setNewName("");
    });
  };

  const handleRename = async (group) => {
    const name = editingName.trim();
    if (!name || name === group.name) {
      setEditingId(null);
      return;
    }
    await runAction(async () => {
      await updateCustomerGroup(group.id, { name });
      setEditingId(null);
    });
  };

  const handleToggleActive = async (group) => {
    await runAction(() => updateCustomerGroup(group.id, { is_active: !group.is_active }));
  };

  const handleDelete = async (group) => {
    const confirmed = window.confirm(
      `¿Eliminar el grupo "${group.name}"? Solo se puede si no tiene subgrupos ni vehiculos.`
    );
    if (!confirmed) {
      return;
    }
    await runAction(() => deleteCustomerGroup(group.id));
  };

  if (!open || !customer) {
    return null;
  }

  return (
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="card modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={`Grupos de ${customer.name}`}
      >
        <header className="modal-header">
          <div className="modal-heading">
            <span className="eyebrow">Organizacion interna</span>
            <h3>Grupos de {customer.name}</h3>
          </div>
          <button type="button" className="icon-button modal-close-button" onClick={onClose}>
            Cerrar
          </button>
        </header>

        <p className="support-copy">
          Categorias y subcategorias propias del cliente (ej. Regional, CEDI). Cada vehiculo
          se asigna a un grupo desde la pagina de Vehiculos y Portal Clientes filtra por ellos.
        </p>

        {error ? <div className="notice-banner notice-error">{error}</div> : null}

        {loading ? (
          <p className="support-copy">Cargando grupos...</p>
        ) : orderedGroups.length ? (
          <div className="group-tree-list">
            {orderedGroups.map((group) => (
              <div
                key={group.id}
                className="group-tree-row"
                style={{ paddingLeft: `${group.depth * 22}px` }}
              >
                {editingId === group.id ? (
                  <input
                    className="control-sm"
                    value={editingName}
                    autoFocus
                    onChange={(event) => setEditingName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        handleRename(group);
                      }
                      if (event.key === "Escape") {
                        setEditingId(null);
                      }
                    }}
                    onBlur={() => handleRename(group)}
                    disabled={saving}
                  />
                ) : (
                  <span className={`group-tree-name${group.is_active ? "" : " is-inactive"}`}>
                    {group.depth > 0 ? "└ " : ""}
                    {group.name}
                  </span>
                )}
                <span className="group-tree-meta">
                  {group.vehicle_count > 0 ? `${group.vehicle_count} veh.` : ""}
                  {!group.is_active ? " · inactivo" : ""}
                </span>
                {canEdit ? (
                  <span className="group-tree-actions">
                    <button
                      type="button"
                      className="button-secondary button-sm"
                      onClick={() => {
                        setEditingId(group.id);
                        setEditingName(group.name);
                      }}
                      disabled={saving}
                    >
                      Renombrar
                    </button>
                    <button
                      type="button"
                      className="button-secondary button-sm"
                      onClick={() => handleToggleActive(group)}
                      disabled={saving}
                    >
                      {group.is_active ? "Desactivar" : "Activar"}
                    </button>
                    <button
                      type="button"
                      className="button-secondary button-sm"
                      onClick={() => handleDelete(group)}
                      disabled={saving || group.vehicle_count > 0}
                      title={
                        group.vehicle_count > 0
                          ? "Tiene vehiculos asignados; reasignelos primero"
                          : "Eliminar grupo vacio"
                      }
                    >
                      Eliminar
                    </button>
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="support-copy">Este cliente aun no tiene grupos definidos.</p>
        )}

        {canEdit ? (
          <form className="register-form" onSubmit={handleCreate}>
            <div className="form-field">
              <label htmlFor="new-group-name">Nuevo grupo</label>
              <input
                id="new-group-name"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Ej: Regional Antioquia"
                maxLength={120}
                disabled={saving}
              />
            </div>
            <div className="form-field">
              <label htmlFor="new-group-parent">Dentro de</label>
              <select
                id="new-group-parent"
                value={newParentId}
                onChange={(event) => setNewParentId(event.target.value)}
                disabled={saving}
              >
                <option value="">Nivel raiz (categoria)</option>
                {orderedGroups
                  .filter((group) => group.is_active)
                  .map((group) => (
                    <option key={group.id} value={group.id}>
                      {`${"— ".repeat(group.depth)}${group.name}`}
                    </option>
                  ))}
              </select>
            </div>
            <div className="actions-row modal-actions">
              <button type="submit" disabled={saving || !newName.trim()}>
                {saving ? "Guardando..." : "Agregar grupo"}
              </button>
            </div>
          </form>
        ) : null}
      </section>
    </div>
  );
}
