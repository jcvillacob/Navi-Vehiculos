# Plan — "Consulta de motores en lote por Excel"

## Decisiones arquitectónicas (fijas, no discutibles)

1. **El backend NO cambia su lógica de lookup.** Reutilizamos tal cual `GET /api/v1/vehicle/lookup?identifier=X` para cada dispositivo. Esto ya trae cache, Geotab, SQL y QuickServe.
2. **El frontend hace el loop** con `await` secuencial + `setTimeout` (throttle configurable). No usamos `Promise.all`. Secuencia estricta.
3. **El frontend parsea y exporta Excel** con la librería `xlsx` 0.18.5 (ya instalada en `frontend/package.json`). No se agrega dependencia nueva.
4. **Backend solo expone un permiso nuevo** (`engine_lookup.batch`) y un endpoint de salud opcional. Nada más.
5. **Ruta nueva:** `/consulta-lote`. Entrada en sidebar junto a "Consulta de motor".
6. **Estado se mantiene en memoria del hook** (no localStorage al principio). Si el usuario cierra la pestaña, se pierde. Queda documentado.

---

## FASE 1 — Esqueleto, permiso y subida/parseo del Excel

### Backend (mínimo)

**1.1. Nuevo permiso RBAC** — crear migración Alembic `20260417_0001_add_engine_lookup_batch_permission.py` en `backend/app/migrations/versions/`, siguiendo el patrón de `20260416_0004_seed_default_permissions.py`:
- `upgrade()`:
  - Insert en `permissions`: `('engine_lookup.batch', 'Consultar motores en lote')`
  - Insert en `role_permissions` para `admin` y `editor` (NO para `viewer`)
- `downgrade()`: borra ambos.
- `down_revision = "20260416_0006"` (la más reciente según `ls backend/app/migrations/versions/`).

### Frontend

**1.2. Registrar ruta y sidebar** en `frontend/src/App.jsx`:
- Import `BulkLookupPage` desde `./pages/BulkLookupPage`.
- Añadir `<Route path="/consulta-lote" element={<BulkLookupPage />} />`.
- Añadir `<NavLink to="/consulta-lote">Consulta en lote</NavLink>` en el sidebar, inmediatamente debajo de `/consulta-motor`. Mismo estilo que los existentes.
- El NavLink debe envolverse en un guard: si el usuario no tiene permiso `engine_lookup.batch`, ocultar. Usar `usePermission("engine_lookup.batch")` como lo hace `EngineLookupPage.jsx:45`.

**1.3. Crear la página** `frontend/src/pages/BulkLookupPage.jsx`:
- Layout estándar `<section className="panel">` con header idéntico a EngineLookupPage (`eyebrow` "Lookup en lote", `<h2>Consulta de motores por Excel</h2>`).
- Renderiza tres componentes en orden vertical (cada uno dentro de `.card` propio):
  1. `<BulkLookupUploader />` — paso 1.
  2. `<BulkLookupRunner />` — paso 2 (solo si hay items cargados).
  3. `<BulkLookupResults />` — paso 3 (solo si `status === "done"` o hay `results.length > 0`).
- Toda la lógica la delega al hook `useBulkLookup`.

**1.4. Crear la feature** `frontend/src/features/bulkLookup/` con subcarpetas `components/` y `hooks/`:
- `hooks/useBulkLookup.js` — hook central, por ahora solo expone `{ items, setItems, reset }` con `items: [{ identifier, rowNumber }]`. El resto llega en Fase 2.
- `components/BulkLookupUploader.jsx` — input file + parser del Excel.

**1.5. Componente `BulkLookupUploader`** — responsabilidades exactas:
- Acepta `onParsed(items)` como prop.
- Input `<input type="file" accept=".xlsx,.xls,.csv" />` oculto disparado por un botón visible `.button` con label "Seleccionar Excel".
- Al seleccionar archivo:
  - Leer con `xlsx` (`import * as XLSX from "xlsx"`): `const wb = XLSX.read(arrayBuffer, { type: "array" })`.
  - Tomar `wb.Sheets[wb.SheetNames[0]]`.
  - Convertir con `XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: "" })` → matriz.
  - **Validar que `matriz[0][0]` sea el string `"Dispositivo"`** (case-insensitive, trim). Si no: mostrar `.notice-banner.notice-error` "La columna A debe titularse 'Dispositivo'." y no seguir.
  - Recorrer `matriz.slice(1)`:
    - `raw = String(row[0] || "").trim().toUpperCase()`
    - `cleaned = raw.replace(/[^A-Z0-9]/g, "")`
    - Descartar vacíos.
    - Deduplicar manteniendo el orden.
  - Emitir `onParsed(items)` con `items = [{ identifier: cleaned, rowNumber: i+2 }]`.
- Muestra en pantalla resumen: "N dispositivos detectados", y lista scrollable (max-height 240px) con los primeros 50 + "…y N más" si excede.
- Si el user vuelve a subir archivo: llamar `reset()` del hook primero.

**1.6. Estilos** — añadir a `frontend/src/styles.css` al final (no crear archivos nuevos):
- `.bulk-upload-summary` — bloque con ítems detectados (usa tokens ya existentes: `var(--clear-gray)`, `var(--black)`).
- `.bulk-upload-list` — lista compacta scrollable.
- `.bulk-progress-track` y `.bulk-progress-fill` — barra de progreso (fill con `var(--red)`, track con `var(--clear-gray)`, radius 999px, height 8px).
- `.bulk-current-item` — línea "Consultando X de Y: PLACA".
- `.bulk-row-ok`, `.bulk-row-partial`, `.bulk-row-error`, `.bulk-row-not-found` — acento lateral de 3px por status.

### Criterio de aceptación Fase 1
- `/consulta-lote` carga sin error.
- Subir un `.xlsx` con A1="Dispositivo" y 3 placas en A2-A4 muestra "3 dispositivos detectados".
- Subir uno con A1 distinto muestra el banner de error y NO activa el runner.
- El NavLink aparece solo para roles con `engine_lookup.batch`.

---

## FASE 2 — Ejecución con throttle + barra de progreso

### Backend
**Sin cambios.** Se verifica solo que el endpoint existente `backend/app/api/routes/vehicle.py:22-28` responde ok con identifier placa y VIN. Ya cubre ambos casos.

### Frontend

**2.1. Ampliar `useBulkLookup`** — estado completo que debe exponer:
```
{
  items,              // array de { identifier, rowNumber } — set desde Uploader
  results,            // array de { identifier, rowNumber, status, response?, error? }
  status,             // "idle" | "running" | "paused" | "cancelled" | "done"
  processed,          // número de items ya procesados
  total,              // items.length
  currentIdentifier,  // placa en curso o null
  delayMs,            // default 1500
  setDelayMs,
  setItems,
  start,              // async — inicia/reanuda
  pause,              // detiene entre items (no interrumpe fetch en vuelo)
  cancel,             // marca cancelado; no recuperable
  reset,              // vuelve a idle
}
```

**2.2. Loop del hook** — reglas exactas:
- Flag de control `pauseRef` y `cancelRef` con `useRef` (para leer valores frescos dentro del bucle).
- Bucle `for (let i = processed; i < items.length; i++)`:
  - Si `cancelRef.current` → break y set status `"cancelled"`.
  - Si `pauseRef.current` → break y set status `"paused"` (reanuda desde `processed` actual).
  - Set `currentIdentifier = items[i].identifier`.
  - Llama `await lookupVehicle(items[i].identifier, { force: false })` desde `frontend/src/api/vehicleApi.js:223`.
  - Push a `results`: `{ identifier, rowNumber, status: response.status, response, error: null }`.
  - En catch: push `{ identifier, rowNumber, status: "error", response: null, error: err.message }`. **NO break** — continuar con la siguiente.
  - Incrementar `processed`.
  - Si no es la última iteración: `await new Promise(r => setTimeout(r, delayMs))`.
- Al final del bucle sin cancel/pause → status `"done"`.

**2.3. Optimización:** cuando una placa ya está en cache del backend (`cached === true` en respuesta), NO aplicar el delay siguiente (saltar el `setTimeout`). Esto acelera reintentos y no estresa QuickServe porque no se llamó. Documentar comportamiento en la UI.

**2.4. Componente `BulkLookupRunner`** — ubicación: `frontend/src/features/bulkLookup/components/BulkLookupRunner.jsx`:
- Props: todo lo que expone el hook (o recibir el hook completo).
- UI:
  - Header: "Ejecución" (eyebrow + h3).
  - Campo numérico "Delay entre consultas (ms)" con default 1500, min 500, max 5000. Deshabilitado durante `running`.
  - Botón principal:
    - `status === "idle"` → "Iniciar consulta" (primario rojo, `.button`).
    - `status === "running"` → "Pausar" (secundario).
    - `status === "paused"` → "Reanudar" (primario).
    - `status === "done" | "cancelled"` → "Reiniciar".
  - Botón secundario "Cancelar" solo visible en `running | paused`. Confirma con `window.confirm`.
- Progreso visual:
  - `.bulk-progress-track > .bulk-progress-fill` con `style={{ width: percent + "%" }}`.
  - Línea debajo: "Procesando X de Y — PLACA_ACTUAL" (usa `.bulk-current-item`).
  - Contadores agregados a la derecha: `OK: n · Parcial: n · No encontrado: n · Error: n` (derivados de `results`).
- Log en vivo: lista de los últimos 8 items procesados (con color de status), más reciente arriba.

**2.5. Telemetría mínima** — mostrar debajo del progreso: "Tiempo transcurrido" (calculado desde `startedAt` con `setInterval` de 1s) y "Tiempo estimado restante" = `(total-processed) * (delayMs + promedio_tiempo_respuesta)`. El promedio lo calcula el hook.

### Criterio de aceptación Fase 2
- Con 5 placas válidas conocidas, Start inicia, la barra crece del 0% al 100%, el contador muestra "5 de 5" al terminar.
- Pausar detiene el loop **entre llamadas**; reanudar continúa sin repetir.
- Cancelar detiene y bloquea reanudación (requiere Reiniciar).
- Si una placa falla (ej. 500 del backend), los contadores de Error suben pero el loop **no se detiene**.
- Cambiar el delay a 3000ms se nota en la cadencia.

---

## FASE 3 — Tabla de resultados + descarga Excel

### Backend
**Sin cambios.**

### Frontend

**3.1. Componente `BulkLookupResults`** — ubicación: `frontend/src/features/bulkLookup/components/BulkLookupResults.jsx`:
- Props: `results, items, status`.
- Header: eyebrow "Resultados" + h3 "Resumen del lote".
- Chips filtros (toggle múltiple): Todos · OK · Parcial · No encontrado · Error. Guardan estado local (useState).
- Tabla `<table className="data-table">` (estilo ya existente si lo hay; si no, crear `.data-table` con el look de `.card` + filas). Columnas visibles:
  1. #  (rowNumber)
  2. Dispositivo (identifier)
  3. Placa (response.plate)
  4. Marca (response.marca)
  5. Línea (response.linea)
  6. Modelo (response.source_details.fenix.modelo)
  7. Configuración (response.source_details.fenix.configuracion)
  8. Año (response.ano_modelo)
  9. Combustible (response.tipo_combustible)
  10. VIN (response.vin)
  11. ESN (response.engine_number)
  12. TEC# (response.technical_engine_configuration)
  13. CPL (response.cpl)
  14. Motor (response.registered_motor?.engine_name)
  15. Cliente (response.assigned_database?.client_name)
  16. Geotab Navi (response.geotab_status)
  17. Geotab Cliente (response.geotab_customer_status)
  18. Estado (response.status con pill `.status-ok/.partial/.error/.not_found`)
  19. Mensaje (response.message — truncado con title tooltip)
- Cuando `status === "error"` en un row: pintar `.bulk-row-error` y mostrar `result.error` en la columna Mensaje.

**3.2. Descarga Excel** — botón "Descargar Excel" en el header del componente Results:
- Deshabilitado si `results.length === 0`.
- Al click: llamar helper `buildBulkResultsWorkbook(results)` en nuevo archivo `frontend/src/features/bulkLookup/utils/exportExcel.js`:
  - Construye matriz con **los mismos 19 headers** de la tabla + dos columnas extra al final: `Warnings` (join de `response.warnings` con ` | `) y `Cached` (boolean).
  - `XLSX.utils.aoa_to_sheet(matriz)`.
  - Anchos de columna automáticos (calcular max length por columna, tope 40).
  - Congelar la primera fila: `sheet["!freeze"] = { ySplit: 1 }` y `sheet["!views"] = [{ state: "frozen", ySplit: 1 }]`.
  - `XLSX.utils.book_new()` → `book_append_sheet(wb, sheet, "Resultados")`.
  - `XLSX.writeFile(wb, nombre)` con nombre `"navi-consulta-lote-YYYYMMDD-HHmm.xlsx"` (fecha local).
- Además de "Descargar Excel", añadir botón secundario "Descargar solo errores" que filtra antes de exportar (rows con status error/not_found).

**3.3. Persistencia ligera (opcional pero recomendada)** — en `useBulkLookup`:
- Al terminar un item, hacer `localStorage.setItem("navi:bulk-lookup:last", JSON.stringify({ startedAt, items, results }))`.
- Al montar la página: si existe `last` y `results.length > 0` y es de las últimas 24h, mostrar banner `.notice-banner.notice-soft` "Se encontró un lote sin descargar del [fecha]. [Restaurar resultados]". Botón restaura estado `status = "done"`, `results` y `items`.
- Clear explícito al hacer "Reiniciar".

**3.4. UX final polishing**
- Warning si durante ejecución el usuario intenta cerrar tab: añadir `useEffect` con `beforeunload` listener **solo** cuando `status === "running" | "paused"`. Remover en cleanup.
- Accesibilidad: `role="progressbar"` + `aria-valuenow/valuemin/valuemax` en la barra.
- Copiar al portapapeles: en cada row de la tabla, icono copiar que copia el VIN.

### Criterio de aceptación Fase 3
- Con el lote completado: la tabla muestra todas las filas, los filtros funcionan, los colores de status son correctos.
- "Descargar Excel" genera un archivo con nombre `navi-consulta-lote-*.xlsx`, abriéndolo en Excel se ven 21 columnas con header congelado.
- "Descargar solo errores" no incluye filas OK.
- Cerrar tab durante ejecución muestra el prompt nativo del navegador.
- Restaurar desde localStorage funciona al recargar.

---

## Contratos clave (resumen para el modelo pequeño)

### Entrada (Excel del usuario)
- Celda A1 = `"Dispositivo"` (trim, case-insensitive).
- A2..An = identificadores. Puede ser placa (formato `AAA###`) o VIN (17 chars).
- Separadores, espacios y guiones se eliminan antes de enviar al backend.

### Llamada al backend (POR ÍTEM, secuencial)
```
GET /api/v1/vehicle/lookup?identifier=PLACA&force=false
```
- Requiere cookie de sesión (usa `fetchWithAuth` existente).
- Respuesta: schema `VehicleLookupResponse` ya definido en `backend/app/schemas/vehicle.py:56-93`.

### Throttle
- Default 1500 ms entre llamadas.
- Skip del delay cuando `response.cached === true`.
- Rango UI: 500–5000 ms.

### Permisos
- Ver página y usar: `engine_lookup.batch`.
- El NavLink se oculta sin permiso; la página también protege el contenido con `usePermission("engine_lookup.batch")` y muestra banner "No tienes permiso".

---

## Resumen para dar al modelo pequeño

- **Sin deps nuevas.** Python: solo migración Alembic. JS: `xlsx` ya está.
- **Archivos a tocar (fijos):**
  - `backend/app/migrations/versions/20260417_0001_add_engine_lookup_batch_permission.py` (nuevo)
  - `frontend/src/App.jsx` (editar: ruta + nav)
  - `frontend/src/pages/BulkLookupPage.jsx` (nuevo)
  - `frontend/src/features/bulkLookup/hooks/useBulkLookup.js` (nuevo)
  - `frontend/src/features/bulkLookup/components/BulkLookupUploader.jsx` (nuevo)
  - `frontend/src/features/bulkLookup/components/BulkLookupRunner.jsx` (nuevo)
  - `frontend/src/features/bulkLookup/components/BulkLookupResults.jsx` (nuevo)
  - `frontend/src/features/bulkLookup/utils/exportExcel.js` (nuevo)
  - `frontend/src/styles.css` (añadir clases al final)
- **No se inventan endpoints.** Todo reutiliza `/api/v1/vehicle/lookup`.
- **No se agrega documentación nueva.** Los comentarios en código van solo donde el "por qué" no sea evidente (casi ninguno).
- **El plan se puede entregar por fases al modelo pequeño**: cada fase es independiente, compilable y probable.
