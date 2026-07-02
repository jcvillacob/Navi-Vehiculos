# Plan: Integrar LogiTracs Triton como proveedor de rendimientos

## 0. Contexto que debe leer primero el modelo ejecutor

El script de [`docs/apiLogitracsTriton.txt`](apiLogitracsTriton.txt) hace esto:
1. Login a la API de Triton (`https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login`) con `username`, `password`, `codigoEmpresa`.
2. Extrae `EmailUsuario` del JWT que regresa.
3. SSO a LogiVIM web con ese email.
4. Login Laravel al front LogiVIM con `email` + `password_web` + `_token` CSRF.
5. POST al reporte `informeOperacionalFlota` con rango `fechaInicioFiltro`/`fechaFinFiltro`.
6. Parsea la tabla `#order-listingFleet` (44 columnas) y arma un DataFrame.

El repo **ya tiene infraestructura** para proveedores mensuales (Artimo, Frotcom, Geotab). El patrón canónico está en:
- [backend/app/services/provider_registry.py](../backend/app/services/provider_registry.py) — catálogo de proveedores
- [backend/app/clients/artimo_client.py](../backend/app/clients/artimo_client.py) — cliente de un proveedor web (login + fetch + extractores)
- [backend/app/services/performance_providers.py](../backend/app/services/performance_providers.py) — clase provider con `calculate_database_rows`
- [backend/app/services/rendimientos.py:196](../backend/app/services/rendimientos.py#L196) — aquí se invoca `infer_provider_key` y se filtra por `supports_monthly_performance`
- [frontend/src/features/customers/providerCatalog.js](../frontend/src/features/customers/providerCatalog.js) — catálogo UI
- [frontend/src/pages/CustomersPage.jsx](../frontend/src/pages/CustomersPage.jsx) — formulario UI de databases

**La clave del provider es `logitracs_triton`** (snake_case, sin espacios ni mayúsculas). Se elige ese nombre porque hay variantes ("otros logitracs") que se agregarán después.

---

## 1. Reglas obligatorias a respetar

- **Prohibido escribir archivos** (no llamar a `Path.write_bytes`, `Path.write_text`, `df.to_csv`, `df.to_excel`, `.to_excel`, `pd.DataFrame.to_*`). Todo en memoria.
- **No usar pandas** si no hace falta. El resto de clientes (Artimo, Frotcom) no lo usan; parsea el HTML con `BeautifulSoup` y mapea a dicts.
- **No agregar dependencias nuevas en `requirements.txt`** sin justificación fuerte. `beautifulsoup4`, `lxml` y `requests` ya están (usados por `artimo_client`/scraping previo — verificar con `grep -E 'bs4|lxml|beautifulsoup' backend/requirements.txt`; si falta alguno, agrégalo).
- **No usar `requests_toolbelt.MultipartEncoder`** en el primer intento. El script lo usa solo como fallback cuando el POST urlencoded tira 419. Implementa el primer POST como `data=payload` (urlencoded) y **sólo si** detectas el fallback (status en `{400,415,419}` o `"TokenMismatch"` en el body o el HTML parece login), reintenta como multipart. Si `requests_toolbelt` no está instalado, déjalo como dependencia opcional y omite el fallback multipart (lanza el error original) — no introducir dependencias silenciosas.
- **Credenciales**: usar **solo** las que vienen de `customer_databases` (username/password/provider_config). **No** leer env vars tipo `LOGITRACS_*`. El patrón del repo es credenciales por DB (ver `FrotcomMonthlyPerformanceProvider._build_frotcom_config`).
- **Logs**: usar `logging.getLogger(__name__)` como en `vehicle_lookup.py`. No `print`.

---

## 2. Cambios por archivo

### 2.1 `backend/app/services/provider_registry.py`

**Qué hacer:**

- **Agregar una entrada** al dict `_PROVIDERS` (alrededor de la línea 17) con estas llaves exactamente:
  ```
  key="logitracs_triton"
  label="LogiTracs Triton"
  description="Telematica LogiTracs Triton (informe operacional de flota mensual)."
  supports_monthly_performance=True
  uses_access_url=False
  ```

- **Agregar función `_looks_like_logitracs_triton`** siguiendo el patrón de `_looks_like_artimo` / `_looks_like_frotcom` (líneas 94-128). Heurística:
  - `"logitracs"` o `"triton"` en `_normalize_token(database_name)` → True
  - `"logitracs"` o `"triton"` en `_normalize_token(access_url)` → True
  - En `provider_config`: si `codigo_empresa` está presente → True

- **Actualizar `infer_provider_key`** (línea 131) para llamar a `_looks_like_logitracs_triton(...)` **antes** del fallback a `database`, pero **después** de Artimo y Frotcom (el orden no debería importar porque los tokens son disjuntos, pero mantener la jerarquía existente).

- **Actualizar `normalize_provider_config`** (línea 152) — agregar rama para `logitracs_triton`. Campos a persistir:
  | Clave JSON | Tipo | Default | Obligatorio? | Notas |
  |---|---|---|---|---|
  | `codigo_empresa` | str | `""` | **Opcional** (per punto 3 del user) | Si viene vacío, el provider usará `None` y fallará al llamar a la API; dejar que el error del login suba — **no** poner default hardcoded tipo "GRUPOK" |
  | `password_web` | str | igual a `password` principal | Opcional | Si no lo dan, reusar `password` normal. El script trae esto como un campo distinto porque la password de LogiVIM puede diferir de la de Triton |
  | `triton_login_url` | str | `"https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login"` | No | Override para QA/staging |
  | `logivim_base_url` | str | `"https://triton.logitracs.com/LogiVIMwebTriton/public"` | No | URL base del Laravel |

  El `username` principal y la `password` principal ya vienen en `cd.username` / `cd.password`; **no** duplicarlos en `provider_config`.

  `normalize_provider_config` debe devolver `{}` para `database`/`geotab`/`frotcom` como hace ahora. Solo para `artimo` y `logitracs_triton` devolver el dict normalizado.

- **Actualizar `public_provider_config`** (línea 202) — agregar rama `logitracs_triton` que devuelve `codigo_empresa`, `triton_login_url`, `logivim_base_url` (pero **NO** `password_web` — es secreto, igual que no expone `password` Artimo).

### 2.2 `backend/app/clients/logitracs_triton_client.py` (archivo nuevo)

**Contrato general:** una sola clase `LogitracsTritonClient` con métodos `login()` y `get_fleet_operational_report(start_date, end_date)`. Sin escribir archivos. Caché de token a nivel de instancia (no global como Frotcom — esto ya es instancia por target).

**Imports permitidos:** `requests`, `urllib.parse`, `re`, `json`, `base64`, `logging`, `dataclasses`, `datetime`, `typing`, `bs4` (BeautifulSoup + lxml parser), y opcionalmente `requests_toolbelt.multipart.encoder.MultipartEncoder`.

**Dataclass `LogitracsTritonConfig`:**
```
username: str
password: str           # password de API Triton
password_web: str       # password de LogiVIM web (fallback: password)
codigo_empresa: str     # puede ser "" — si vacío, login fallará con mensaje claro
triton_base_url: str = "https://triton.logitracs.com/Logitracs.Triton"
logivim_base_url: str = "https://triton.logitracs.com/LogiVIMwebTriton/public"
```

**Excepción:** `LogitracsTritonAuthError(RuntimeError)` — alineado con `ArtimoAuthError`, `FrotcomAuthError`.

**Método `login(self)`:**
1. Crear `self.session = requests.Session()` con User-Agent realista (Mozilla/5.0...) y `Accept-Language: es-ES,es;q=0.9`.
2. **Paso 1** — `GET {triton_base_url}/Login` para obtener cookies (`XSRF-TOKEN`, etc.). `raise_for_status`.
3. Helper `_inject_xsrf()`: si hay cookie `XSRF-TOKEN`, setear `session.headers["X-XSRF-TOKEN"] = urllib.parse.unquote(cookie_value)`.
4. **Paso 2** — `POST {triton_base_url}/api/Usuarios/Login` con body JSON `{"username","password","codigoEmpresa"}`. Headers `Content-Type: application/json;charset=UTF-8`, `Accept: application/json, text/plain, */*`, `Origin: https://triton.logitracs.com`, `Referer: {triton_base_url}/Login`.
   - Si status ∈ {401,403}: `raise LogitracsTritonAuthError("Credenciales LogiTracs Triton invalidas. Revisa usuario, contraseña y codigoEmpresa.")`.
   - Guardar `self.jwt = resp.json()["token"]`.
   - Decodificar el segundo segmento del JWT (base64url + padding `=*(-len%4)`) y extraer `EmailUsuario` → `self.email_usuario`.
5. **Paso 3 — SSO**:
   - Quitar `Content-Type` de `session.headers` (pop con default `None`).
   - Setear headers HTML (`Accept: text/html,...`, `Referer: {triton_base_url}/LogiVim`, `Sec-Fetch-*` como en el script, `Upgrade-Insecure-Requests: 1`).
   - `sso_url = f"{logivim_base_url}/loginLogitracs/usuario/{urllib.parse.quote(self.email_usuario, safe='@')}"`.
   - `resp_sso = session.get(sso_url, allow_redirects=True)` y guardar `resp_sso.text`, `resp_sso.url`.
   - `session.get(f"{logivim_base_url}/ver-informacion-especifica")` para forzar la cookie de sesión Laravel. `_inject_xsrf()` después de cada GET.
6. **Paso 4 — Laravel login**:
   - Helper `_looks_like_login(html: str) -> bool`: `'name="password"' in html or 'type="password"' in html`.
   - Helper `_csrf_from_html(html)`: `BeautifulSoup(html, "lxml")` → primero `input[name=_token]`, si no `meta[name=csrf-token]`.
   - Si `resp_sso.text` es login, usarlo; si no, `GET {logivim_base_url}/login`.
   - `token_login = _csrf_from_html(login_html)`; si no hay token, `raise RuntimeError("LogiVIM login sin _token (layout inesperado).")` — **no** escribas `login_page.html`.
   - `POST {logivim_base_url}/login` con `data={"_token","email","password"}` donde `password = config.password_web or config.password` y `email = self.email_usuario`. Headers `Origin`, `Referer: login_page_url`.
   - Si el body final sigue siendo login: `raise LogitracsTritonAuthError("Login web LogiVIM fallo. Verifica password_web.")`.
7. Al final de `login()`: `self._logged_in = True`. Cualquier error cacheado se maneja en el provider, no aquí.

**Método `get_fleet_operational_report(self, start_date: str, end_date: str) -> list[dict]`:**
- `start_date`/`end_date` en formato `YYYY-MM-DD`.
- Si no está logueado, llamar `self.login()`.
- `informe_url = f"{logivim_base_url}/informeOperacionalFlota"`.
- **GET previo** para obtener `_token`: `r_informe = session.get(informe_url)`, `_inject_xsrf`, si `_looks_like_login(r_informe.text)` → raise `LogitracsTritonAuthError("Sesion LogiVIM expirada")`. Extraer `token_informe = _csrf_from_html(r_informe.text)`; si no hay, raise `RuntimeError`.
- **POST urlencoded** con payload:
  ```
  {"_token": token_informe,
   "fechaInicioFiltro": start_date,
   "fechaFinFiltro": end_date,
   "idCobertura": "0",
   "lgOperacion": "0",
   "lgFrente": "0",
   "id": ""}                         # ← intencionalmente vacio (punto 3 del requerimiento)
  ```
  Headers `Origin`, `Referer: informe_url`, `Accept: text/html,...`.
- **Fallback multipart**: si status ∈ {400,415,419} o `"TokenMismatch"` in body o `_looks_like_login(body)`, reintentar con `MultipartEncoder(fields=payload)` y `Content-Type: m.content_type`. Si `requests_toolbelt` no está disponible, `raise RuntimeError(f"Informe fallo HTTP {status} y no hay requests_toolbelt para fallback")`.
- Si status final ≠ 200 o el body parece login: `raise RuntimeError("POST del informe operacional fallo.")` — **no** escribir `error_response.html`.
- Parsear con BeautifulSoup y retornar `list[dict]`:
  - `table = soup.find("table", id="order-listingFleet") or soup.find("table")`. Si es `None`, raise `RuntimeError("Tabla del informe no encontrada")`.
  - Headers: `[normalize(th.get_text(" ", strip=True)) for th in table.find("thead").find_all("th")]` donde `normalize = lambda s: re.sub(r"\s+", " ", s).strip()`.
  - Rows: por cada `tr` en `tbody`, cells = `[td.get_text(" ", strip=True) for td in tr.find_all("td")]`, pad con `None` hasta largo headers, luego `dict(zip(headers, cells))`.
  - Devolver la lista de dicts **sin convertir a float** — los extractores lo harán.

**Helpers a exponer (a nivel módulo, para que el provider los use, igual que artimo_client):**
- `_normalize_key(value: str) -> str` — alfanumérico lowercase, sin tildes (usar `unicodedata.normalize("NFKD", ...)` + filtrar `combining`).
- `_build_field_index(row) -> dict` y `_first_value(row, *candidates)` — copiar de artimo_client.
- `_to_float(value) -> float | None` — igual que artimo_client, pero adicionalmente acepta `%` al final (para campos como `Relacion urea (%)`, aunque no lo usemos).
- `extract_plate(row)` — buscar keys `"Placa"`, `"placa"`, `"plate"`.
- `extract_odometer_end(row)` — `"Odometro final"`.
- `extract_kilometraje_vo(row)` — `"Kilometraje Vo"`.
- `extract_kms_period(row)` — `"Kilometraje"` (distancia del periodo, NO el total).
- `extract_fuel_liters(row)` — `"Combustible"` (galones? litros? ver §3).
- `extract_engine_hours(row)` — `"Tiempo Encendido(h)"`.

### 2.3 `backend/app/services/performance_providers.py`

**Qué hacer:**

- **Agregar imports** del nuevo cliente al inicio (junto a los de artimo/frotcom/geotab).
- **Crear `LogitracsTritonMonthlyPerformanceProvider`** siguiendo exactamente el esqueleto de `ArtimoMonthlyPerformanceProvider` (líneas 231-389):
  - `key = "logitracs_triton"`.
  - Método `_build_config(target)`:
    - Extraer `provider_config` (dict).
    - Requerir `target.username` y `target.password` (mensaje de error igual que Artimo).
    - `codigo_empresa = str(provider_config.get("codigo_empresa") or "").strip()` — si está vacío, raise `ValueError(f"La database {target.database_name or target.customer_database_id} no tiene codigo_empresa de LogiTracs Triton.")`.
    - `password_web = str(provider_config.get("password_web") or "").strip() or target.password` (fallback al password principal).
    - Crear `LogitracsTritonConfig(...)`.
  - Método `calculate_database_rows(...)`:
    - Si `not targets`: return `ProviderCalculationResult(records=[], binding_updates=[])`.
    - Instanciar `client = LogitracsTritonClient(self._build_config(targets[0]))`.
    - Calcular `start_date`/`end_date` del mes: primer día y último día del mes como `YYYY-MM-DD`. No usar `get_month_range` con timezones — el informe toma fechas planas.
    - Lo mismo para `previous_start_date`/`previous_end_date` (usar `previous_month`).
    - Try: `current_rows = client.get_fleet_operational_report(start_date, end_date)` (indexar por plate normalizada upper).
      - Y `previous_rows = client.get_fleet_operational_report(prev_start, prev_end)` indexar por plate.
    - Except `LogitracsTritonAuthError` (msg de error): para cada target, agregar `BindingUpsert(binding_status="error", last_error=msg)` y un `_build_status_record(status="error", warnings=[msg])`. Retornar inmediatamente igual que Artimo (lines 298-317).
    - Except `RuntimeError` genérico del client: **mismo tratamiento** (status=error en todos los targets), pero tracearlo con `logger.exception`.
    - Por cada `target`:
      - `current_row = current_rows.get(target.plate)`, `previous_row = previous_rows.get(target.plate)`.
      - Resolver `provider_vehicle_id`: usar helper existente `_select_binding(bindings, target)`. Si manual → usar ese; si no, usar `target.plate` como identificador natural (la placa es la clave única en el informe).
        - **Importante**: si no hay fila en `current_rows` ni binding manual, marcar `binding_status="unbound"` con mensaje `"La placa no aparece en el informe operacional de LogiTracs para el mes."` y status del record `"unbound"`.
      - Si hay fila: `binding_updates.append(BindingUpsert(target, provider_vehicle_id=target.plate, binding_status="resolved", last_error=None))`.
      - Llamar función de cálculo → siguiente bullet.

- **Función `_calculate_logitracs_vehicle_record(target, month, current_row, previous_row, previous_record)`:**
  - Si `current_row is None and previous_row is None`: retornar `_build_status_record(status="no_data", warnings=["No hay datos LogiTracs para el mes solicitado."], provider_vehicle_id=target.plate)`.
  - `warnings: list[str] = []`.
  - **Mapeo de campos → MonthlyPerformanceRecord** (ver tabla en §3):
    - `odo_end = extract_odometer_end(current_row)`.
    - `odo_start`:
      - Si `previous_record` y `previous_record.odo_end is not None` → `previous_record.odo_end` (preferente, encadena meses).
      - Sino si `previous_row`: `extract_odometer_end(previous_row)` + warning "Odometro inicial tomado del cierre LogiTracs del mes anterior.".
      - Sino: derivar como `odo_end - extract_kms_period(current_row)` si ambos no-None + warning "Odometro inicial estimado a partir del kilometraje del mes actual.".
      - Sino: `None`.
    - `kms_ecm = max(0.0, odo_end - odo_start)` si ambos no-None; si no, `extract_kms_period(current_row)` y warning.
    - `kms_gps = None` (el informe no separa ECM/GPS explícitamente — **decidir**: si no hay distinción, setear los dos iguales con warning "LogiTracs no separa kms ECM/GPS; se replica.", o dejar `kms_gps=None`). **Recomendación**: `kms_gps = None` para ser conservador y permitir comparaciones futuras.
    - `hours_gps = extract_engine_hours(current_row)` (es "Tiempo Encendido(h)", tiempo motor encendido).
    - `hours_ecm = None` (sin fuente clara ECM).
    - `horo_start = None`, `horo_end = None` (el informe no trae horómetro acumulado, solo duración del periodo).
    - `fuel_gallons`: el campo `"Combustible"` del informe. **Confirmar unidades**: el script muestra ~838 para kilometraje ~5173, dato que sugiere **galones ya** (rendimiento 6.17 km/gal es típico camión; si fueran litros daría ~1.6 km/L, bajo). **Mapeo final**: `fuel_gallons = extract_fuel_liters(current_row)` **tratándolo como galones directamente**, con warning "LogiTracs 'Combustible' interpretado como galones (unidad por confirmar)." hasta que se valide con cliente.
  - `status = "calculated"` si todos (`odo_start, odo_end, kms_ecm, hours_gps, fuel_gallons`) no-None, si no `"partial"`.
  - Retornar `MonthlyPerformanceRecord(..., source_provider=target.provider_key, provider_vehicle_id=target.plate, period_month=month, warnings=warnings, calculation_status=status)`.

- **Registrar** en `_MONTHLY_PERFORMANCE_PROVIDERS` (línea 930):
  ```
  "logitracs_triton": LogitracsTritonMonthlyPerformanceProvider(),
  ```

### 2.4 `backend/app/services/rendimientos.py`

**No requiere cambios funcionales.** El SQL en [rendimientos.py:166](../backend/app/services/rendimientos.py#L166) ya lee `cd.provider_config`, `cd.connection_type`, y `infer_provider_key` + `supports_monthly_performance` harán lo correcto cuando la database tenga `connection_type='logitracs_triton'`. Verificar al final que un target con ese provider llegue al nuevo proveedor sin dropearse.

### 2.5 Frontend — `frontend/src/features/customers/providerCatalog.js`

- **Agregar `LOGITRACS_TRITON_DEFAULTS`** arriba:
  ```
  codigoEmpresa: "", passwordWeb: "",
  tritonLoginUrl: "https://triton.logitracs.com/Logitracs.Triton/api/Usuarios/Login",
  logivimBaseUrl: "https://triton.logitracs.com/LogiVIMwebTriton/public"
  ```
- **Agregar al array `DATABASE_PROVIDERS`**:
  ```
  { key: "logitracs_triton", label: "LogiTracs Triton",
    description: "Informe operacional de flota mensual.",
    usesAccessUrl: false, supportsMonthlyPerformance: true }
  ```
- **Extender `getInitialProviderConfig`** (línea 53) con rama `connectionType === "logitracs_triton"` que lee `provider_config.codigo_empresa`, `provider_config.password_web` (si viene en edit), `triton_login_url`, `logivim_base_url`.
- **Extender `buildProviderConfigPayload`** (línea 68) con la rama correspondiente → `{ codigo_empresa, password_web, triton_login_url, logivim_base_url }`.
- **Extender `getProviderDetailRows`** (línea 82) para mostrar `codigo_empresa` y las URLs (no mostrar `password_web`).
- **Agregar `"logitracs_triton"`** al Set `PROVIDERS_WITH_MANUAL_ID` (línea 94) — la placa es el ID natural pero permitimos override manual.

### 2.6 Frontend — `frontend/src/pages/CustomersPage.jsx`

Buscar todas las ocurrencias de `showArtimoFields` / `connectionType === "artimo"` y duplicar la estructura para `connectionType === "logitracs_triton"`:

- **Create modal** (alrededor de [línea 236](../frontend/src/pages/CustomersPage.jsx#L236)): después del bloque Artimo, agregar un bloque condicional `showLogitracsFields` con inputs para:
  - `codigoEmpresa` (text, label "Código empresa LogiTracs", **no requerido** — placeholder "Opcional, ej. GRUPOK").
  - `passwordWeb` (password, label "Password web LogiVIM", helper "Si se omite, usa la password principal").
  - `tritonLoginUrl` (text, con default visible).
  - `logivimBaseUrl` (text, con default visible).
- **Edit modal** (alrededor de [línea 465](../frontend/src/pages/CustomersPage.jsx#L465)): espejar el bloque.
- Declarar `const showLogitracsFields = connectionType === "logitracs_triton";` junto a `showArtimoFields`.
- **No tocar** los estilos — usar `form-group`, `input`, clases existentes. Montserrat/Poppins ya aplican por herencia.

### 2.7 Tests (opcional pero recomendado)

- Añadir `backend/tests/test_logitracs_triton_provider.py` que monkeypatchee `LogitracsTritonClient.get_fleet_operational_report` para devolver filas fake y verifique:
  1. Una placa con fila en `current` y `previous` → `status=calculated`, `odo_start` encadenado.
  2. Placa sin fila → `status=unbound`, binding `unbound`.
  3. Placa con solo `current` (sin previous) → `status=partial`, warning de odómetro estimado.
- No hacer tests de integración real contra Triton en CI.

---

## 3. Tabla de mapeo del informe (44 columnas → MonthlyPerformanceRecord)

| Columna HTML (exacta) | Campo destino | Notas |
|---|---|---|
| `Placa` | `plate` | UPPER + strip |
| `Odometro final` | `odo_end` | float |
| `Kilometraje Vo` | (no usar directo) | Aparente lectura inicial del odómetro en el periodo; útil sólo como validación |
| `Kilometraje` | ayuda para `odo_start` | distancia del periodo, para derivar `odo_start = odo_end - Kilometraje` cuando falta el previo |
| `Tiempo Encendido(h)` | `hours_gps` | float horas |
| `Combustible` | `fuel_gallons` | **Asumir galones**; warning hasta confirmar con cliente |
| (nada) | `horo_start`/`horo_end` | `None` — el informe no trae horómetro acumulado |
| (nada) | `hours_ecm` | `None` |
| (nada) | `kms_gps` | `None` (conservador) |
| `Tipo motor`, `Combustible Ralenti`, `Rendimiento*`, `Tiempo Encendido` (string), `Consumo de urea`, `Relacion urea (%)`, `Tiempo taller(h)`, `Tiempo taller`, `Consumo Gas(kilos)`, `Rendimiento Gas`, `Veces frenada larga`, `Frenada larga`, `Detalle`, el resto... | (ignorar) | No los necesitamos para la fila de `monthly_vehicle_performance` actual |

`kms_ecm`: **calcular** como `odo_end - odo_start` (derivado), no leer de la columna.

**Nota crítica para el implementador**: el nombre `"Odometro final"` aparece sin tilde en la muestra. **Siempre** normaliza claves con `_normalize_key` (alfanumérico + quitar tildes) antes de comparar, para robustez ante cambios de tildes/espacios.

---

## 4. Datos de prueba (para la verificación manual)

Usuario de prueba que viene en el script (no commitearlo, es del operador):
- `USERNAME="JULIANU"`, `PASSWORD="JULI4NURR3A*"`, `CODIGO_EMPRESA="GRUPOK"`, `PASSWORD_WEB="123456"`.
- Rango de prueba: `2026-03-01` → `2026-03-31` (19 placas esperadas según la respuesta sample).

**Flujo de prueba manual (para el modelo al terminar la implementación):**
1. `cd /home/jvillacob/lab/apps/Navi-Vehiculos && docker-compose up` (o el comando que use el repo — `cat README.md` para confirmar).
2. En la app, `/clientes` → crear cliente "Grankarga" → crear DB con `connection_type="logitracs_triton"`, las credenciales de arriba.
3. Asignar al menos 1 placa (`LJX019`, por ejemplo) a esa DB en `/vehiculos` (requiere que esa placa exista previamente en `vehicle_motor_assignments` — si no, hay que pasar por el flujo de Consulta Motor primero).
4. `/rendimientos` → seleccionar cliente Grankarga, mes 2026-03, "Calcular". Verificar que la fila aparezca con `source_provider="logitracs_triton"`, `kms_ecm` ≈ 5173, `fuel_gallons` ≈ 838, `hours_gps` ≈ 193.34 para `LJX019`.
5. **Verificar que no se haya creado ningún archivo** `.html`, `.csv`, `.xlsx` en el working dir ni en `/tmp`: `ls | grep -E '(informe|error_response|login_)'` debe salir vacío.

---

## 5. Checklist final que el modelo ejecutor debe recorrer antes de declarar "listo"

- [ ] `provider_registry._PROVIDERS` tiene `logitracs_triton` con `supports_monthly_performance=True`, `uses_access_url=False`.
- [ ] `infer_provider_key` detecta por `codigo_empresa` en `provider_config` o por token en nombre/URL.
- [ ] `normalize_provider_config` y `public_provider_config` cubren la nueva llave sin filtrar `password_web`.
- [ ] `backend/app/clients/logitracs_triton_client.py` existe, **no** importa pandas, **no** llama a `Path.write_*` ni a `.to_csv/.to_excel`.
- [ ] El cliente lanza `LogitracsTritonAuthError` en 401/403 del paso 2 y en login web fallido del paso 4.
- [ ] `LogitracsTritonMonthlyPerformanceProvider` registrado en `_MONTHLY_PERFORMANCE_PROVIDERS`.
- [ ] `provider_vehicle_id` para LogiTracs = placa (o binding manual si existe).
- [ ] Target sin fila en el informe actual → `calculation_status="unbound"`.
- [ ] Encadenamiento mes anterior: si `previous_record.odo_end` existe, se prefiere como `odo_start`.
- [ ] Frontend `CustomersPage.jsx` muestra inputs de `codigoEmpresa` + `passwordWeb` + URLs overrideables cuando se elige LogiTracs Triton.
- [ ] `grep -rn "logitracs" frontend/src backend/app` muestra al menos los archivos tocados; nada de credenciales hardcoded ("GRUPOK", "JULIANU").
- [ ] `grep -rn "to_csv\|to_excel\|write_bytes\|write_text" backend/app/clients/logitracs_triton_client.py` retorna 0 líneas.
- [ ] Corrida de `pytest backend/tests -q` pasa.
- [ ] Prueba manual del §4 pasa con una placa real.

---

## 6. Cosas que el modelo ejecutor NO debe hacer

- No mover credenciales de `customer_databases` a env vars.
- No descargar archivos "de respaldo" ante errores. Si algo falla, levantar excepción con mensaje humano.
- No crear una tabla nueva ni migración nueva — `customer_databases.connection_type` es `TEXT` sin CHECK, y `provider_config` es `JSONB`. Ya caben.
- No tocar `vehicle_lookup.py` — la "búsqueda de placas" que pide el usuario es la del flujo de rendimientos (el informe contiene todas las placas), no la consulta por placa contra Geotab/SQL.
- No remover el fallback multipart sin antes intentar el POST urlencoded.
- No agregar mapeo de `Consumo de urea`, `Frenada larga`, etc. — están fuera de scope del schema actual de `MonthlyPerformanceRecord`.
- No incluir `pandas` ni `beautifulsoup4` si no están ya (verificar `requirements.txt` antes).

---

Este plan está dimensionado para que el modelo pequeño copie estructuras existentes (Artimo como template principal, Frotcom como template secundario) y solo adapte la capa de auth + parseo de HTML. El cambio no toca el SQL, el esquema, ni el flujo de plate-lookup — toda la integración vive en la capa de `providers` + `clients` + UI de clientes.
