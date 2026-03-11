import { useEffect, useState } from "react";

import {
  createCustomer,
  createCustomerDatabase,
  listCustomers
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

  return {
    loading,
    customers,
    error,
    loadCustomers,
    registerCustomer,
    registerCustomerDatabase
  };
}
