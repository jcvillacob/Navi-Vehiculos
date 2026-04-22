import { useEffect } from "react";

import { usePermission } from "../context/AuthContext";
import BulkLookupResults from "../features/bulkLookup/components/BulkLookupResults";
import BulkLookupRunner from "../features/bulkLookup/components/BulkLookupRunner";
import BulkLookupUploader from "../features/bulkLookup/components/BulkLookupUploader";
import { useBulkLookup } from "../features/bulkLookup/hooks/useBulkLookup";

export default function BulkLookupPage({ embedded = false }) {
  const canUseBulkLookup = usePermission("engine_lookup.batch");
  const bulkLookup = useBulkLookup();
  const { items, results, status, setItems, reset, restoreCandidate, restoreLastBatch } = bulkLookup;

  useEffect(() => {
    if (status !== "running" && status !== "paused") return undefined;

    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [status]);

  if (!canUseBulkLookup) {
    return (
      <section className="panel">
        {!embedded ? (
          <header className="page-header">
            <span className="eyebrow">Lookup en lote</span>
            <h2>Consulta de motores por Excel</h2>
          </header>
        ) : null}
        <div className="notice-banner notice-error">No tienes permiso.</div>
      </section>
    );
  }

  return (
    <section className="panel">
      {!embedded ? (
        <header className="page-header">
          <span className="eyebrow">Lookup en lote</span>
          <h2>Consulta de motores por Excel</h2>
        </header>
      ) : null}

      {restoreCandidate?.results?.length ? (
        <div className="notice-banner notice-soft">
          Se encontro un lote sin descargar del {new Date(restoreCandidate.startedAt).toLocaleString("es-CO")}.
          <button type="button" className="button-secondary button-sm" onClick={restoreLastBatch}>
            Restaurar resultados
          </button>
        </div>
      ) : null}

      <BulkLookupUploader onParsed={setItems} onReset={reset} />
      {items.length > 0 ? <BulkLookupRunner {...bulkLookup} /> : null}
      {status === "done" || results.length > 0 ? <BulkLookupResults results={results} items={items} status={status} /> : null}
    </section>
  );
}
