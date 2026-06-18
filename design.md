# Design System — Navi Vehiculos

> Guia de diseño reutilizable para llevar la identidad visual de **Navi Vehiculos** a otras aplicaciones de la familia Navitrans.
>
> Fuente canonica de implementacion: `frontend/src/styles.css` (~5 400 lineas, unico stylesheet global). Este documento es un resumen portable: tokens, patrones y convenciones listas para copiar.

---

## 1. Identidad visual

- **Producto:** Navi Vehiculos (familia **Navi Fleet Intelligence / Navitrans**).
- **Estilo:** operativo, corporativo y tecnico. Superficies claras, contraste funcional, acentos de marca muy contenidos.
- **Principio visual:** sidebar oscura + area de trabajo clara + tarjetas modulares + tipografia fuerte en encabezados.
- **Composicion de color:** rojo de marca dominante, grises neutros como base, y una paleta secundaria (verde, amarillo, azul) limitada a ~15 % del peso visual.
- **Localizacion:** UI en espanol, locale `es-CO`.
- **Modo:** solo claro. `html { color-scheme: light; }`. No existe tema dark operativo para el contenido principal.

**Tagline de marca (kicker):** `Navi Fleet Intelligence`.

---

## 2. Tipografia

### 2.1 Familias

| Rol | Familia | Pesos | Origen |
|---|---|---|---|
| Encabezados | `"Montserrat", sans-serif` | 600, 700, 800 | Google Fonts |
| Texto / UI | `"Poppins", sans-serif` | 400, 500, 600 | Google Fonts |
| Mono tecnico | `SFMono-Regular, Consolas, "Liberation Mono", monospace` | 400 | Sistema |

Import canonico (al inicio del CSS global):

```css
@import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700;800&family=Poppins:wght@400;500;600&display=swap");
```

### 2.2 Escala tipografica (valores reales en uso)

| Estilo | Tamano | Peso | Letter-spacing | Notas |
|---|---|---|---|---|
| Body | `14px` | 400 | normal | Base |
| Page header `h2` | `clamp(22px, 3vw, 32px)` | 800 | `-0.025em` | Titulo de seccion |
| Page header `p` | `13px` | 400 | normal | Descripcion |
| Modal header `h3` | `clamp(20px, 3vw, 28px)` | 800 | `-0.025em` | Titulo de modal |
| Metric card `strong` | `clamp(30px, 4vw, 44px)` | 800 | `-0.04em` | Numeros grandes |
| Metric card compact `strong` | `clamp(26px, 3vw, 38px)` | 800 | — | KPI compacto |
| Card `h3` | `17px` | 700 | `-0.015em` | Titulo de tarjeta |
| Section heading `h3` | `16px` | 700 | `-0.015em` | Seccion interna |
| Brand `h1` (sidebar) | `20px` | 800 | `-0.02em` | Titulo marca (blanco) |
| Sidebar nav | `13px` | 500 | normal | Label de nav |
| Eyebrow / kicker | `9px` | 600 | `0.10–0.18em` | UPPERCASE, color rojo |
| Section label | `9–10px` | 600/700 | `0.08–0.12em` | UPPERCASE |
| Data item label | `10px` | 600 | `0.09em` | UPPERCASE |
| Data item value | `13px` | 500 | — | Valor |
| Table th | `10px` | 600 | `0.09em` | UPPERCASE |
| Table body | `12px` | 400 | — | Tablas densas |
| Plate (tabla) | `14px` | 700 | `-0.01em` | Placa destacada |
| Button | `13px` | 600 | normal | Default |
| Button sm | `12px` | 600 | normal | |
| Button lg | `14px` | 600 | normal | |
| Status pill | `10px` | 600 | `0.04–0.06em` | UPPERCASE |
| Mono tecnico (TEC#, CPL) | `11px` | 400 | — | Identificadores |

**Reglas de uso:**
- Numeros, IDs tecnicos, numeros de regla y user-agents siempre en `--font-mono`.
- Encabezados siempre Montserrat 700/800.
- Labels y microcopy siempre UPPERCASE + tracking ancho + peso 600.

---

## 3. Tokens (copiar verbatim)

### 3.1 Familias de fuentes

```css
:root {
  --font-body:    "Poppins", sans-serif;
  --font-heading: "Montserrat", sans-serif;
  --font-mono:    SFMono-Regular, Consolas, "Liberation Mono", monospace;
}
```

### 3.2 Marca y acentos

```css
:root {
  --red:        #ee2e2f;   /* Brand primary */
  --red-hover:  #d42526;
  --red-soft:   rgba(238, 46, 47, 0.08);
  --red-darker: #c82526;   /* danger hover */

  --black:      #363534;   /* Brand "ink" */
  --clear-gray: #c3cac8;
  --gray:       #354550;
  --gray-deep:  #253038;   /* Sidebar bg */

  --green:      #b2e100;   /* Secundario (≤ 15 %) */
  --green-dark: #1f8f5f;   /* Estado calculado */
  --yellow:     #ffb301;   /* Warning */
  --amber:      #d4a017;   /* Loading */
  --blue:       #185979;   /* Experiencia / cron */
  --cream:      #eff0c8;   /* Acento opcional */

  --accent:     var(--red);
}
```

### 3.3 Superficies y texto

```css
:root {
  --bg:         #f1f3f5;   /* Fondo de pagina */
  --bg-subtle:  #e8eaed;
  --surface:    #ffffff;   /* Fondo de tarjeta */
  --surface-2:  #f8f9fa;   /* Hover / alt row */

  --text:           #1e2528;
  --text-primary:   var(--text);
  --text-strong:    var(--text);
  --text-secondary: #354550;
  --text-muted:     #6b7b85;
  --text-subtle:    #98aab4;
}
```

### 3.4 Bordes, radios y sombras

```css
:root {
  --border:         rgba(53, 69, 80, 0.10);
  --border-strong:  rgba(53, 69, 80, 0.18);
  --border-focus:   rgba(238, 46, 47, 0.50);

  --radius-sm:    7px;     /* Botones, chips pequenos */
  --radius-md:   10px;     /* Inputs, chips, dropzones */
  --radius-lg:   14px;     /* Cards */
  --radius-xl:   20px;     /* Modales */
  --radius-pill: 999px;    /* Status pills, avatares */

  --shadow-xs: 0 1px 2px rgba(53, 69, 80, 0.07);
  --shadow-sm: 0 1px 4px rgba(53, 69, 80, 0.08), 0 2px 8px rgba(53, 69, 80, 0.04);
  --shadow-md: 0 4px 16px rgba(53, 69, 80, 0.10), 0 2px 4px rgba(53, 69, 80, 0.06);
  --shadow-lg: 0 10px 32px rgba(53, 69, 80, 0.14), 0 4px 10px rgba(53, 69, 80, 0.06);
}
```

### 3.5 Paleta de estados (semantica)

```css
:root {
  --status-ok-bg:         #eef8d3;
  --status-ok-border:     rgba(178, 225, 0, 0.45);
  --status-ok-text:       #3a5800;

  --status-partial-bg:    #fff8e0;
  --status-partial-border: rgba(255, 179, 1, 0.45);
  --status-partial-text:  #7a5500;

  --status-error-bg:      #fef0f0;
  --status-error-border:  rgba(238, 46, 47, 0.20);
  --status-error-text:    #8b1515;

  --status-soft-bg:       #f4f6f8;
  --status-soft-border:   rgba(53, 69, 80, 0.14);
  --status-soft-text:     #4a5e6a;
}
```

### 3.6 Tokens especificos de sidebar oscuro

```css
:root {
  --sidebar-bg:           var(--gray-deep);
  --sidebar-border:       rgba(255, 255, 255, 0.07);
  --sidebar-muted:        rgba(195, 202, 200, 0.55);
  --sidebar-text:         #ffffff;
  --sidebar-hover-bg:     rgba(255, 255, 255, 0.06);
  --sidebar-active-bg:    var(--red);
}
```

---

## 4. Layout base

### 4.1 Grid principal (escritorio)

```css
.app-shell { min-height: 100svh; display: grid; }
.app-grid  { display: grid; grid-template-columns: 256px minmax(0, 1fr); }

/* ≥ 1180px  → sidebar 256px */
/* < 1180px  → sidebar 220px */
/* < 960px   → sidebar horizontal arriba (1 columna) */
```

### 4.2 Content shell

```css
.content-shell { padding: 28px 32px; }
/* ≤ 960px → 20px 20px 32px */
/* ≤ 720px → 16px 14px 28px */
```

### 4.3 Breakpoints

| Breakpoint | Comportamiento |
|---|---|
| `≤ 1180px` | Sidebar a 220px; metric cards a 1 columna donde aplique |
| `≤ 960px`  | Sidebar colapsa a nav horizontal arriba; filtros complejos se apilan |
| `≤ 720px`  | Tablas se vuelven cards (data-label); modales pasan a bottom sheet; botones full-width |
| `≤ 640px`  | Headers y formularios del inspector de reglas se simplifican |

### 4.4 Anchos maximos tipicos

- Modal estandar: `min(640px, calc(100vw - 40px))`
- Modal attachment manager: `min(980px, ...)`
- Modal reglas: `min(680px, ...)`
- Drawer lateral: `min(420px, 100vw)`
- Login card: `420px`
- Toast stack: `min(360px, calc(100vw - 32px))`
- Tabla vehiculos: `min-width: 1180px`, `table-layout: fixed`
- Tabla bulk results: `min-width: 1800px`

---

## 5. Componentes

### 5.1 Botones

Reset universal: **todo `<button>` se estiliza por defecto** (no requiere clase).

```css
button, .button {
  height: 36px;
  padding: 0 16px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background-color: var(--red);
  color: #fff;
  font-family: var(--font-body);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background-color 130ms ease,
    border-color 130ms ease,
    box-shadow 130ms ease,
    opacity 130ms ease;
}
button:hover, .button:hover {
  background-color: var(--red-hover);
  box-shadow: 0 3px 10px rgba(238, 46, 47, 0.28);
}
button:disabled, .button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
  pointer-events: none;
}
```

**Variantes:**

| Clase | Background | Color | Border | Uso |
|---|---|---|---|---|
| `button` (default) | `--red` | white | transparent | CTA primario |
| `.button-secondary` | `--surface` | `--text-secondary` | `--border-strong` + `--shadow-xs` | Cancelar / Limpiar |
| `.button-danger` | `--red` | `--surface` | `--red` | Confirmacion destructiva |
| `.button-danger-outline` | transparent | `--red` | `--red` | Eliminar |
| `.icon-button` | transparent | `--text-muted` | `--border-strong` | Icon-only (h 32px) |

**Tamanos:** `.button-sm` (h 30 / fs 12) · default (h 36 / fs 13) · `.button-lg` (h 42 / fs 14).

### 5.2 Cards

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 20px;
}
```

Variantes:
- `.metric-card` / `.metric-card-compact` — KPI con eyebrow + numero grande + caption.
- `.feature-card-accent` — `border-top: 3px solid var(--red);`
- `.detail-card` — contiene secciones anidadas.
- `.insight-card` — panel de estado.
- `.empty-state-card` / `.empty-prompt-card` — estados vacios.
- `.soft-note-card` — sin sombra, neutro.
- `.empty-prompt-card` — `border-left: 3px solid var(--gray);`
- `.modal-card` — envoltura de modal (ver 5.7).

### 5.3 Formularios

```css
input, select, textarea {
  width: 100%;
  padding: 0 13px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: var(--surface);
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--text);
  transition:
    border-color 130ms ease,
    box-shadow 130ms ease;
}
input, select { height: 40px; }
textarea { min-height: 72px; padding: 10px 13px; resize: vertical; line-height: 1.5; }

input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--border-focus);
  box-shadow: 0 0 0 3px var(--red-soft);
}
input::placeholder { color: var(--text-subtle); }
input[readonly]    { background: var(--bg); color: var(--text-muted); cursor: default; }
```

- `<select>` con caret SVG inline (`stroke='%23354550'`, `appearance: none`).
- Form label: 12px / 600 / 0.01em.
- Form hint: 11–12px, color `--text-muted`.

### 5.4 Status pills / badges

Pills con `border-radius: 999px`, padding `3px 9px`, fuente `10px / 600 / 0.04–0.06em` UPPERCASE.

| Clase | Background | Color | Uso |
|---|---|---|---|
| `.status-ok` | `--status-ok-bg` | `--status-ok-text` | Exitoso |
| `.status-partial` | `--status-partial-bg` | `--status-partial-text` | Parcial / warning |
| `.status-error` / `.status-not_found` | `--status-error-bg` | `--status-error-text` | Error |
| `.status-soft` | `--status-soft-bg` | `--status-soft-text` | Neutro |

Variantes por dominio:
- Geotab: `.geotab-found`, `.geotab-not_found`, `.geotab-unknown`, `.geotab-na`.
- Database: `.db-type-geotab` (verde), `.db-type-database` (rojo), `.db-type-artimo` (amber), `.db-type-unknown` (gris).
- Vehiculo connection dot: `≥ 80%` `#2F8C2F` · `50–79%` `#D18C00` · `< 50% / error` `#C52B2B`.

### 5.5 Category badges (clientes / vehiculos)

| Clase | Background | Color | Border |
|---|---|---|---|
| `.is-experiencia` | `rgba(24,89,121,.12)` | `--blue` | `rgba(24,89,121,.35)` |
| `.is-flota` | `rgba(255,179,1,.16)` | `#8a6100` | `rgba(255,179,1,.5)` |
| `.is-ninguna` | `rgba(53,69,80,.08)` | `--gray` | `rgba(53,69,80,.2)` |
| `.is-inherited` | (anade) `opacity: 0.75; border-style: dashed;` | — | — |

Categorias canónicas: `["Ninguna", "Experiencia Superior", "Flota Administrada"]`.

### 5.6 Status chips (Rendimientos) — click-to-filter

```css
.is-calculated { color: #1f8f5f; background: rgba(31,143,95,.12); }
.is-partial,
.is-no-data,
.is-unbound     { color: #8a6500; background: rgba(212,160,23,.12); }
.is-error       { color: var(--red); background: rgba(238,46,47,.1); }
```

Estado activo: `inset 0 0 0 1px currentColor`. Hover: ligero lift + focus ring rojo.

### 5.7 Modal

```css
.modal-overlay {
  position: fixed; inset: 0;
  display: grid; place-items: center;
  padding: 20px;
  background: rgba(30, 37, 40, 0.55);
  backdrop-filter: blur(3px);
  z-index: 30;
}
.modal-card {
  width: min(640px, calc(100vw - 40px));
  display: grid; gap: 18px;
  padding: 26px;
  background: var(--surface);
  border-radius: var(--radius-xl);
  box-shadow: 0 20px 60px rgba(30, 37, 40, 0.22);
}
/* ≤ 720px → bottom sheet: align-items: end; border-radius: 16px 16px 0 0; */
```

Header: eyebrow + `h3` Montserrat 800 + close icon button. Acciones: fila de botones con `min-width: 130px`.

### 5.8 Notice banners (inline messages)

3 px de `border-left` coloreado + glifo en `::before`.

| Clase | Bg | Border-left | Glyph |
|---|---|---|---|
| `.notice-info` | `--status-ok-bg` | `--green` | `✓` |
| `.notice-error` | `--status-error-bg` | `--red` | `✕` |
| `.notice-soft` | `--status-soft-bg` | `--clear-gray` | `·` (bullet grande) |

### 5.9 Toasts

```css
.toast-stack {
  position: fixed; top: 20px; right: 20px;
  z-index: 60;
  display: grid; gap: 10px;
  width: min(360px, calc(100vw - 32px));
}
.toast-banner {
  padding: 12px 14px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  box-shadow: 0 14px 40px rgba(30, 37, 40, 0.18);
  backdrop-filter: blur(8px);
  font-size: 13px; font-weight: 500; line-height: 1.5;
}
.toast-success { background: rgba(232,245,224,.96); border-color: var(--status-ok-border);    color: var(--status-ok-text); }
.toast-error   { background: rgba(252,235,231,.98); border-color: var(--status-error-border); color: var(--status-error-text); }
.toast-info    { background: rgba(245,242,235,.98); border-color: var(--status-soft-border);  color: var(--text); }
```

Duracion: error 5s, resto 3.2s. Stack en esquina superior derecha.

### 5.10 Tablas

```css
.table-shell { overflow: auto; border: 1px solid var(--border); border-radius: var(--radius-md); }
table { width: 100%; border-collapse: separate; border-spacing: 0; }

thead th {
  background: var(--bg);
  color: var(--text-subtle);
  font-family: var(--font-body);
  font-size: 10px; font-weight: 600;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  text-align: left;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-strong);
  position: sticky; top: 0;
}

tbody td {
  font-size: 12px; font-weight: 400;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
  line-height: 1.5;
  color: var(--text);
}
tbody tr:hover { background: var(--surface-2); }
tbody tr.selected { background: rgba(31,143,95,.07); }
```

Mobile (≤ 720px): ocultar `thead`, transformar `tbody tr` en grid de cards donde cada `td` se rotula con `data-label` (`::before` 9px UPPERCASE).

### 5.11 Eyebrow / kicker (universal)

```css
.eyebrow {
  font-family: var(--font-body);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--red);
}
```

Se usa en TODA seccion como label de capitulo.

### 5.12 Section heading

```css
.section-heading {
  display: flex; justify-content: space-between; align-items: center;
  gap: 16px; margin-bottom: 12px;
}
.section-heading h3 {
  font-family: var(--font-heading);
  font-size: 16px; font-weight: 700;
  letter-spacing: -0.015em;
  color: var(--text);
  margin: 0;
}
```

### 5.13 Data grid (2 columnas)

```css
.data-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.data-item { display: grid; gap: 4px; }
.data-item .label {
  font-size: 10px; font-weight: 600; letter-spacing: 0.09em;
  text-transform: uppercase; color: var(--text-muted);
}
.data-item .value { font-size: 13px; font-weight: 500; color: var(--text); }
```

### 5.14 Sidebar (panel oscuro)

- Background: `var(--gray-deep)`.
- Brand block padding: `24px 20px 20px`. Logo max-width 168px.
- Brand kicker: 9px / 0.18em UPPERCASE / `rgba(195,202,200,.55)`.
- Brand `h1`: 20px / 800 / `-0.02em` / blanco.
- Nav link: `padding: 9px 12px; border-radius: var(--radius-md);` transparent → `rgba(255,255,255,.06)` hover → `--red` activo (label blanco bold).
- Sub-nav "Gestion" con 22px de indent.
- User block inferior: nombre blanco 13px/600 + rol UPPERCASE 10px `--clear-gray`.
- ≤ 960px: pasa a nav horizontal arriba.

### 5.15 Column selector drawer (lateral derecho)

- Ancho 420px, slide-in 320ms `cubic-bezier(0.22, 0.61, 0.36, 1)`.
- Header con gradient top: `var(--red) → var(--red-hover) → var(--gray-deep)`.
- Counter + progress bar (4–8px track, fill con linear-gradient rojo).
- Buscador + acciones rapidas "Todas / Ninguna".
- Filas con checkbox custom: check rojo + barra izquierda 3px roja cuando esta seleccionado.
- Footer: Cancelar / Aplicar cambios.

### 5.16 Graficos (recharts)

- Tooltip cursor: `fill: rgba(53,69,80,.05)`.
- Grid: `stroke="rgba(53,69,80,.08)"`.
- Line color: `#ee2e2f`, stroke width 2.5.
- RadialBar (gauge): `cornerRadius: 12`, `innerRadius: 78%`, `outerRadius: 100%`, start 210 / end -30.

### 5.17 File dropzone

- Acepta `application/pdf, image/png, image/jpeg, image/webp`.
- Estado idle: borde `--border-strong`, fondo `--surface-2`, icono + label.
- Estado drag-over: borde `--red`, fondo `var(--red-soft)`, label "Suelta para subir".

---

## 6. Animaciones y movimiento

```css
@keyframes spin              { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes pulse-dot         { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
@keyframes conn-alert-pulse  { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }

.spin            { animation: spin 0.8s linear infinite; }
.rule-resolution-dot { animation: pulse-dot 1s ease-in-out infinite; }
.conn-pct-alert  { animation: conn-alert-pulse 1.5s ease-in-out infinite; }
```

**Easings canonicos:**
- Botones / inputs / nav: `130ms ease`.
- Cards (motor, rules): `140ms ease`.
- Drawer overlay: `280ms ease` (bg + backdrop-filter blur).
- Drawer panel: `320ms cubic-bezier(0.22, 0.61, 0.36, 1)` (signature easing).
- Opciones / dots: `160ms cubic-bezier(0.22, 0.61, 0.36, 1)`.
- Progress bar fill: `240ms cubic-bezier(0.22, 0.61, 0.36, 1)`.
- Hover lift en cards: `transform: translateY(-2px)` + `--shadow-md`.

Ripple en el boton "Aplicar cambios" del drawer: `::after` circular que se expande a 220px / 360ms en `:active`.

---

## 7. Iconografia

**No se usa ninguna libreria de iconos.** Todos los iconos son **SVG inline dibujados a mano**:

- PDF, imagen, file icons en chips de adjuntos.
- Ojo / ojo-off en inputs de password.
- Refresh spinner (usa `.spin`).
- Close X, search magnifier, chevrons, arrow-up-down.
- Caret custom del `<select>` (SVG data-URL en `background-image`).

**Glifos CSS** (renderizados con `::before`):
- `✓` para `.notice-info`
- `✕` para `.notice-error`
- `·` (bullet grande) para `.notice-soft`
- `▲ ▼ ↕` para indicadores de sort en tablas

**Entidades UTF-8 frecuentes:** `&#10005;` (✕) · `&#9998;` (✎) · `&#8943;` (⋮) · `&#8592;` (←).

---

## 8. Branding

### 8.1 Logo

- Archivo: `Logo navitrans.png` (raiz del repo) y copia en `frontend/public/logo-navitrans.png`.
- Uso en sidebar: `<img className="sidebar-logo" src="/logo-navitrans.png" alt="Navitrans" />`.
- Tamano: `width: min(100%, 168px); height: auto; object-fit: contain;`.

### 8.2 Naming

- Titulo de documento: `Navi Vehiculos`.
- Login H1: `Navi Vehiculos` con kicker `Navi Fleet Intelligence`.
- Alias corto: **Navitrans** (alt text, color de kicker, badges tipo "Geotab Navi").
- Eyebrows de seccion: nombre del modulo (`Lookup`, `Relacion vehiculo-motor`, `Analitica operativa`).

### 8.3 Reglas de marca

- Sidebar con fondo `--gray-deep` y logo en espacio blanco/transparente.
- Item de nav activo se rellena con rojo de marca.
- Eyebrows de seccion siempre en `--red`.
- Composicion bi-tono: rojo dominante + grises neutros.
- Paleta secundaria (verde / amarillo / azul) limitada a ~15 % del peso visual.

---

## 9. Convenciones de implementacion

1. **Fuente oficial:** `frontend/src/styles.css` (~5 400 lineas, unico stylesheet global).
2. **Reutilizar tokens antes de introducir hex nuevos.** Cualquier valor nuevo debe ir a `:root`.
3. **Mono obligatorio** para IDs tecnicos, numeros de regla, TEC#, CPL y user-agents.
4. **Acentos de interaccion rojos**, no azules. Los acentos azules solo se reservan para la categoria "Experiencia Superior" y elementos read-only.
5. **No existe tema dark** operativo para el contenido principal; `html { color-scheme: light; }`.
6. **Todo `<button>` lleva estilos por defecto** — no requiere clase para ser visualmente valido.
7. **Eyebrow (9px UPPERCASE rojo)** precede SIEMPRE a cualquier `h2`/`h3` de seccion.
8. **Modales y drawers** usan siempre las mismas sombras y radios (`--radius-xl` modales, `var(--radius-md)` drawers), con el easing `cubic-bezier(0.22, 0.61, 0.36, 1)`.
9. **Tablas densas** con `table-layout: fixed`, columnas pineadas, y degradacion a cards en mobile via `data-label`.
10. **Legacy excluido del sistema:** clases `lookup-form`, `lookup-row`, `result-card`, `data-row`, `message` en `frontend/src/features/plateLookup/components/` no forman parte del sistema documentado y deben migrarse o eliminarse.

---

## 10. Mapa de aplicacion (rutas)

| Path | Pagina | Proposito |
|---|---|---|
| `/login` | LoginPage | Login centrado con brand-kicker + H1 |
| `/` | HomePage | Dashboard: 4 KPIs + busqueda rapida + tablas recientes |
| `/consulta-motor` | EngineLookupPage | Lookup individual / por lote (modo `?modo=lote`) |
| `/rendimientos` | RendimientosPage | KPIs + chips de estado + tabla densa + export Excel |
| `/disponibilidad` | DisponibilidadPage | Toolbar (mes + flota) + gauge + barras + line + ranking |
| `/vehiculos` | VehiclesPage | Tabla densa + drawer de columnas + filtros + acciones masivas |
| `/motores` | MotorsPage | Grid de motor cards con adjuntos |
| `/clientes` | CustomersPage | Clientes + databases + reglas Geotab + pool de credenciales |
| `/usuarios` | UsersPage | Gestion de usuarios (permiso `users.list`) |
| `/roles` | RolesPage | Matriz de permisos master/detail (`roles.manage`) |
| `/auditoria` | AuditPage | Log paginado con badges de accion (`audit.view`) |

**Redirects:** `/consulta-lote` → `/consulta-motor?modo=lote` · unknown → `/` · sin permiso → `/`.

**Auth:** sesion persistida por cookie httpOnly; `AuthContext` expone `login()`, `logout()`, `hasPermission(perm)`, `useAuth()`, `usePermission()`. Componentes `<Can permission="…">` y `<ProtectedRoute permissions={[...]}/>` controlan acceso.

---

## 11. Inventario de componentes reutilizables

`frontend/src/components/`:
- `Can.jsx` — gate por permiso (`<Can permission="…">{children}</Can>`).
- `ProtectedRoute.jsx` — guard de ruta con `permissions` y/o `roles` opcionales.
- `PasswordInput.jsx` — input con toggle show/hide (SVG inline).
- `ToastStack.jsx` — renderer de toasts (top-right fixed).
- `useToasts.js` — hook de toasts.
- `ColumnSelectorDrawer.jsx` — picker de columnas lateral derecho.
- `FileDropzone.jsx` — dropzone PDF/imagen con drag-over.

`frontend/src/context/`:
- `AuthContext.jsx` — autenticacion y permisos.
- `BulkRefreshContext.jsx` — estado de operaciones masivas de larga duracion.

`frontend/src/utils/`:
- `passwordValidation.js` — 10+ chars, upper, lower, digito, especial, ≠ username.

---

## 12. Checklist para portar el diseno a otra app

1. Importar Montserrat + Poppins desde Google Fonts con `display=swap`.
2. Copiar el bloque `:root` completo (fuentes, marca, superficies, texto, bordes, radios, sombras, estados, sidebar).
3. Aplicar `button { ... }` como reset global para que todo boton herede el estilo primario.
4. Aplicar `input, select, textarea { ... }` como reset global con focus ring rojo.
5. Implementar sidebar oscuro (`--gray-deep`) con brand block + nav + user block.
6. Implementar `.eyebrow` (9px UPPERCASE rojo) como label de toda seccion.
7. Usar `.card` con `--radius-lg` + `--shadow-sm` como contenedor base.
8. Implementar `.modal-card` con `--radius-xl` y overlay `rgba(30,37,40,0.55)` + `backdrop-filter: blur(3px)`.
9. Implementar `.notice-banner` (border-left 3px + glifo) y `.toast-banner` (top-right fixed).
10. Usar `cubic-bezier(0.22, 0.61, 0.36, 1)` para todos los slides/scales.
11. Limitar la paleta secundaria (verde/amarillo/azul) a ~15 % del peso visual.
12. No usar librerias de iconos: SVGs inline o glifos CSS.
13. Mantener mono (`--font-mono`) para cualquier identificador tecnico.
14. Respetar los breakpoints `1180 / 960 / 720 / 640`.
15. Degradar tablas a cards en `≤ 720px` via `data-label` + `::before`.
