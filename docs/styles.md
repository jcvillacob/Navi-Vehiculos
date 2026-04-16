# Sistema de Estilos - Navi Vehiculos

## 1) Identidad Visual
- Producto: `Navi Vehiculos`
- Estilo actual: operativo, corporativo y tecnico, con superficies claras, contraste funcional y acentos de marca muy contenidos.
- Principio visual: sidebar oscura + area de trabajo clara + tarjetas modulares + tipografia fuerte en encabezados.

## 2) Tipografia en Produccion
- Texto general: `Poppins`, fallback `sans-serif`
- Encabezados: `Montserrat`, fallback `sans-serif`
- Mono tecnico: `SFMono-Regular`, `Consolas`, `Liberation Mono`, `monospace`
- Pesos dominantes:
  - 400 para texto corrido
  - 500-600 para labels, tablas y acciones
  - 700-800 para encabezados, metricas y nombres tecnicos

## 3) Tokens Reales del Frontend

### 3.1 Fuentes
- `--font-body`: `"Poppins", sans-serif`
- `--font-heading`: `"Montserrat", sans-serif`
- `--font-mono`: `"SFMono-Regular", Consolas, "Liberation Mono", monospace`

### 3.2 Marca y acentos
- `--red`: `#EE2E2F`
- `--red-hover`: `#D42526`
- `--red-soft`: `rgba(238, 46, 47, 0.08)`
- `--black`: `#363534`
- `--clear-gray`: `#C3CAC8`
- `--gray`: `#354550`
- `--gray-deep`: `#253038`
- `--green`: `#B2E100`
- `--cream`: `#EFF0C8`
- `--yellow`: `#FFB301`
- `--blue`: `#185979`
- `--accent`: alias de `--red`
- `--amber`: `#D4A017`

### 3.3 Superficies y texto
- `--bg`: `#F1F3F5`
- `--bg-subtle`: `#E8EAED`
- `--surface`: `#FFFFFF`
- `--surface-2`: `#F8F9FA`
- `--text`: `#1E2528`
- `--text-primary`: alias de `--text`
- `--text-strong`: alias de `--text`
- `--text-secondary`: `#354550`
- `--text-muted`: `#6B7B85`
- `--text-subtle`: `#98AAB4`

### 3.4 Bordes, radius y sombras
- `--border`: `rgba(53, 69, 80, 0.10)`
- `--border-strong`: `rgba(53, 69, 80, 0.18)`
- `--border-focus`: `rgba(238, 46, 47, 0.5)`
- `--radius-sm`: `7px`
- `--radius-md`: `10px`
- `--radius-lg`: `14px`
- `--radius-xl`: `20px`
- `--radius-pill`: `999px`
- `--shadow-xs`: `0 1px 2px rgba(53, 69, 80, 0.07)`
- `--shadow-sm`: `0 1px 4px rgba(53, 69, 80, 0.08), 0 2px 8px rgba(53, 69, 80, 0.04)`
- `--shadow-md`: `0 4px 16px rgba(53, 69, 80, 0.1), 0 2px 4px rgba(53, 69, 80, 0.06)`
- `--shadow-lg`: `0 10px 32px rgba(53, 69, 80, 0.14), 0 4px 10px rgba(53, 69, 80, 0.06)`

## 4) Layout Actual
- `#root` y `.app-shell` usan `min-height: 100svh`
- `.app-grid` define desktop con `256px + 1fr`
- La navegacion lateral usa fondo `--gray-deep`
- El contenido principal vive en `.content-shell` con padding `28px 32px`
- En `max-width: 960px`, el sidebar colapsa a layout superior horizontal

## 5) Componentes Reales

### 5.1 Sidebar
- Bloque de marca con `brand-kicker`, `h1` fuerte y copy secundaria
- Navegacion con estado `hover` y `active` en rojo
- Bloque inferior de usuario con `sidebar-user`, nombre, rol y accion de logout

### 5.2 Encabezados y paneles
- `.panel` organiza vistas por secciones con `gap: 20px`
- `.page-header` usa `eyebrow`, `h2` grande y descripcion secundaria
- Las cards base usan `.card` con borde suave, fondo blanco y sombra ligera

### 5.3 Formularios
- Inputs y selects con altura `40px`
- Focus con borde `--border-focus` y halo `--red-soft`
- Botones primarios en rojo y secundarios sobre superficie blanca
- En mobile, acciones se apilan y los botones ocupan el ancho disponible

### 5.4 Estados y feedback
- Badges `.status-*` para `ok`, `partial`, `error`, `not_found`
- Banners `.notice-info`, `.notice-soft`, `.notice-error`
- Toasts flotantes en esquina superior derecha
- Spinner simple con clase `.spin`

### 5.5 Vistas especiales
- Dashboard con metricas, busqueda rapida e historico reciente
- Consulta de motor con `lookup-bar`, `lookup-history` y paneles de fuentes
- Modales amplios para asignacion de vehiculos, clientes, databases y adjuntos
- Tablas densas para vehiculos y rendimientos, con degradacion a tarjetas en mobile
- Login con card centrada y estilo alineado al sistema principal

## 6) Responsive Implementado
- `1180px`: metricas y resumentes pasan a una sola columna segun modulo
- `960px`: sidebar se vuelve horizontal y filtros complejos se apilan
- `720px`: tablas de vehiculos se convierten en cards, modales pasan a bottom sheet y botones ocupan todo el ancho
- `640px`: se simplifican headers y formularios del inspector de reglas

## 7) Estados Visuales del Dominio
- Geotab:
  - `found`: verde suave
  - `not_found`: rojo suave
  - `unknown` y `na`: neutro
- Rendimientos:
  - `is-calculated`: verde
  - `is-partial`, `is-no-data`, `is-unbound`: ambar
  - `is-error`: rojo
- Reglas:
  - `rule-badge`, `rule-resolution-dot`, `rules-dot` y chips de seleccion para inspeccion y asignacion

## 8) Convenciones Actualizadas
- La fuente oficial de implementacion es `frontend/src/styles.css`
- Los componentes deben reutilizar tokens CSS antes de introducir hex nuevos
- Para textos tecnicos, ids y numeros de regla se usa `--font-mono`
- Los acentos principales de interaccion siguen el rojo de marca, no azul
- El sistema actual es claro; no existe un tema dark operativo para contenido principal

## 9) Nota de Mantenimiento
- Existen componentes legacy en `frontend/src/features/plateLookup/components/` con clases como `lookup-form`, `lookup-row`, `result-card`, `data-row` y `message` que hoy no forman parte del flujo principal ni del sistema documentado.
