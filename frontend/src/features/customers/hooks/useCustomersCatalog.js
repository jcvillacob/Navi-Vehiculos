import { useEffect, useState } from "react";

import {
  createCustomer,
  createCustomerDatabase,
  createGeotabRule,
  createGeotabRuleGroup,
  deleteGeotabRule,
  deleteGeotabRuleGroup,
  listCustomers,
  updateCustomer,
  updateCustomerDatabase
} from "../../../api/vehicleApi";

export function useCustomersCatalog() {
  const [loading, setLoading] = useState(false);
  const [customers, setCustomers] = useState([]);
  const [error, setError] = useState("");

  const loadCustomers = async () => {
    setLoading(true);
    setError("");
    try {
      const records = await listCustomers();
      setCustomers(records);
      return records;
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible cargar los clientes");
      throw err;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCustomers().catch(() => {});
  }, []);

  const registerCustomer = async (payload) => {
    setLoading(true);
    setError("");
    try {
      const created = await createCustomer(payload);
      setCustomers((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      return created;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible crear el cliente";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const editCustomer = async (customerId, payload) => {
    setLoading(true);
    setError("");
    try {
      const updated = await updateCustomer(customerId, payload);
      setCustomers((prev) =>
        prev.map((c) => (c.id === customerId ? updated : c)).sort((a, b) => a.name.localeCompare(b.name))
      );
      return updated;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible actualizar el cliente";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const registerCustomerDatabase = async (customerId, payload) => {
    setLoading(true);
    setError("");
    try {
      const created = await createCustomerDatabase(customerId, payload);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                database_count: (customer.database_count || 0) + 1,
                databases: [...customer.databases, created].sort((a, b) =>
                  a.database_name.localeCompare(b.database_name)
                )
              }
            : customer
        )
      );
      return created;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "No fue posible crear la database del cliente";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const editCustomerDatabase = async (databaseId, customerId, payload) => {
    setLoading(true);
    setError("");
    try {
      const updated = await updateCustomerDatabase(databaseId, payload);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                databases: customer.databases
                  .map((db) => (db.id === databaseId ? updated : db))
                  .sort((a, b) => a.database_name.localeCompare(b.database_name))
              }
            : customer
        )
      );
      return updated;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "No fue posible actualizar la database";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const addGeotabRule = async (databaseId, customerId, payload) => {
    setLoading(true);
    setError("");
    try {
      const created = await createGeotabRule(databaseId, payload);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                databases: customer.databases.map((db) =>
                  db.id === databaseId
                    ? { ...db, rules: [...db.rules, created].sort((a, b) => a.name.localeCompare(b.name)) }
                    : db
                )
              }
            : customer
        )
      );
      return created;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible crear la regla";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const addGeotabRuleGroup = async (databaseId, customerId, payload) => {
    setLoading(true);
    setError("");
    try {
      const created = await createGeotabRuleGroup(databaseId, payload);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                databases: customer.databases.map((db) =>
                  db.id === databaseId
                    ? {
                        ...db,
                        rule_groups: [...(db.rule_groups || []), created].sort((a, b) =>
                          a.motor_name.localeCompare(b.motor_name) || a.name.localeCompare(b.name)
                        )
                      }
                    : db
                )
              }
            : customer
        )
      );
      return created;
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible crear el grupo de reglas";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const removeGeotabRule = async (ruleId, databaseId, customerId) => {
    setLoading(true);
    setError("");
    try {
      await deleteGeotabRule(ruleId);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                databases: customer.databases.map((db) =>
                  db.id === databaseId
                    ? {
                        ...db,
                        rules: db.rules.filter((r) => r.id !== ruleId),
                        rule_groups: (db.rule_groups || [])
                          .map((group) => ({
                            ...group,
                            rules: (group.rules || []).filter((rule) => rule.rule_record_id !== ruleId)
                          }))
                          .filter((group) => (group.rules || []).length > 0)
                      }
                    : db
                )
              }
            : customer
        )
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible eliminar la regla";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  const removeGeotabRuleGroup = async (groupId, databaseId, customerId) => {
    setLoading(true);
    setError("");
    try {
      await deleteGeotabRuleGroup(groupId);
      setCustomers((prev) =>
        prev.map((customer) =>
          customer.id === customerId
            ? {
                ...customer,
                databases: customer.databases.map((db) =>
                  db.id === databaseId
                    ? {
                        ...db,
                        rule_groups: (db.rule_groups || []).filter((group) => group.id !== groupId)
                      }
                    : db
                )
              }
            : customer
        )
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "No fue posible eliminar el grupo de reglas";
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return {
    loading,
    customers,
    error,
    loadCustomers,
    registerCustomer,
    editCustomer,
    registerCustomerDatabase,
    editCustomerDatabase,
    addGeotabRule,
    addGeotabRuleGroup,
    removeGeotabRule,
    removeGeotabRuleGroup
  };
}
