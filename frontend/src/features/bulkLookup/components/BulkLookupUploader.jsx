import { useId, useRef, useState } from "react";
import * as XLSX from "xlsx";

function normalizeHeader(value) {
  return String(value || "").trim().toLowerCase();
}

export default function BulkLookupUploader({ onParsed, onReset }) {
  const inputId = useId();
  const inputRef = useRef(null);
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [summaryItems, setSummaryItems] = useState([]);

  const handleOpenPicker = () => {
    inputRef.current?.click();
  };

  const handleChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    onReset?.();
    setError("");
    setSummaryItems([]);
    setFileName(file.name);

    try {
      const buffer = await file.arrayBuffer();
      const workbook = XLSX.read(buffer, { type: "array" });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      const matrix = XLSX.utils.sheet_to_json(sheet, {
        header: 1,
        raw: false,
        defval: "",
      });

      if (normalizeHeader(matrix?.[0]?.[0]) !== "dispositivo") {
        setError("La columna A debe titularse 'Dispositivo'.");
        onParsed([]);
        return;
      }

      const seen = new Set();
      const parsedItems = [];

      matrix.slice(1).forEach((row, index) => {
        const raw = String(row?.[0] || "").trim().toUpperCase();
        const cleaned = raw.replace(/[^A-Z0-9]/g, "");

        if (!cleaned || seen.has(cleaned)) return;

        seen.add(cleaned);
        parsedItems.push({
          identifier: cleaned,
          rowNumber: index + 2,
        });
      });

      setSummaryItems(parsedItems);
      onParsed(parsedItems);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No fue posible leer el archivo.");
      onParsed([]);
    } finally {
      event.target.value = "";
    }
  };

  const visibleItems = summaryItems.slice(0, 50);
  const remainingCount = Math.max(summaryItems.length - visibleItems.length, 0);

  return (
    <article className="card bulk-upload-card">
      <div className="dash-search-heading">
        <span className="eyebrow">Paso 1</span>
        <h3>Subir y validar archivo</h3>
        <p className="support-copy">Carga un Excel o CSV con la columna A titulada como Dispositivo.</p>
      </div>

      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept=".xlsx,.xls,.csv"
        hidden
        onChange={handleChange}
      />

      <div className="actions-row">
        <button type="button" onClick={handleOpenPicker}>
          Seleccionar Excel
        </button>
        {fileName ? <span className="support-copy">{fileName}</span> : null}
      </div>

      {error ? <div className="notice-banner notice-error">{error}</div> : null}

      {summaryItems.length > 0 ? (
        <div className="bulk-upload-summary">
          <strong>{summaryItems.length} dispositivos detectados</strong>
          <div className="bulk-upload-list" role="list" aria-label="Dispositivos detectados">
            {visibleItems.map((item) => (
              <div key={`${item.identifier}-${item.rowNumber}`} role="listitem" className="bulk-upload-list-item">
                <span>{item.identifier}</span>
                <small>Fila {item.rowNumber}</small>
              </div>
            ))}
            {remainingCount > 0 ? (
              <div className="bulk-upload-list-item bulk-upload-list-more">
                <span>...y {remainingCount} mas</span>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  );
}
