# Plan: Roles dinámicos, permisos por módulo (Lectura/Escritura) y control de sesiones

> Continuación de [`plan-sistema-usuarios.md`](./plan-sistema-usuarios.md), que ya está **implementado** (RBAC en DB, JWT + refresh tokens, lockout, rate limit, política de contraseñas, sesiones, auditoría y tests).
> Este documento cubre la **siguiente evolución**: convertir los roles fijos en roles **gestionables desde la app**, reorganizar los permisos como una matriz **Módulo × {Lectura, Escritura}**, exponer una **sección de Roles** en el frontend, reforzar el **control de sesiones** y ampliar la **cobertura de tests**.
> Cada fase es independiente y secuencial. Un agente puede ejecutar una fase a la vez; las verificaciones confirman completitud.

---

## Estado (junio 2026)

**Plan ejecutado completo** — todas las fases (0-6) están implementadas en el repo.

### Cambios entregados

| Fase | Entregable | Archivos clave |
|---|---|---|
| 0 | `PERMISSIONS_BY_ROLE` eliminado, `module_registry.py` creado, placeholder corregido | `backend/app/services/module_registry.py` (nuevo), `auth_service.py`, `frontend/src/pages/UsersPage.jsx` |
| 1 | Tabla `roles`, FKs, drop del CHECK, CRUD en `auth_service`, `users.py` valida contra `list_valid_role_keys()` | `backend/app/migrations/versions/20260601_0001_create_roles_table.py` |
| 2-3 | Router `roles.py` con `/modules`, CRUD, `/{key}/permissions` (GET/PUT) | `backend/app/api/routes/roles.py`, `router.py` |
| 4 | `RolesPage.jsx` con matriz, sidebar, refactor `UsersPage` a roles dinámicos | `frontend/src/pages/RolesPage.jsx` (nuevo), `App.jsx`, `UsersPage.jsx` |
| 5 | `user_agent` en `refresh_tokens`, `MAX_CONCURRENT_SESSIONS`, job de limpieza agendado, `last_login_at` en UI | `backend/app/migrations/versions/20260601_0002_add_refresh_token_user_agent.py`, `auth_service.py`, `auth.py`, `scheduler.py` |
| 6 | `test_roles.py` (25 tests), ampliación de `test_permissions.py` (3 tests) | `backend/tests/test_roles.py` (nuevo) |

### Modelo de módulos (referencia rápida)

```
módulo            nivel        codenames
───────────────  ───────────  ───────────────────────────────
dashboard         lectura      dashboard.view
consulta_motor    lectura      engine_lookup.use, engine_lookup.batch
rendimientos      lectura      rendimientos.view
rendimientos      escritura    rendimientos.refresh
vehiculos         lectura      vehicles.list
vehiculos         escritura    vehicles.list, vehicles.edit, vehicles.refresh
motores           lectura      motors.list
motores           escritura    motors.list, motors.create, motors.edit, motors.delete, motors.attachments
clientes          lectura      customers.list
clientes          escritura    customers.list, customers.create, customers.edit
usuarios          lectura      users.list
usuarios          escritura    users.list, users.create, users.edit
roles             escritura    roles.manage
auditoria         lectura      audit.view
```

`engine_lookup.use` y `engine_lookup.batch` están agrupados en `consulta_motor` (lectura).
El módulo `roles` solo expone nivel "escritura" (gestión de la matriz = permiso `roles.manage`).
La traducción matriz↔codenames vive **exclusivamente** en `module_registry.py`.

### Variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `MAX_CONCURRENT_SESSIONS` | `0` (sin límite) | Al exceder, revoca la sesión más antigua del usuario |

### Endpoints nuevos

| Método | Path | Permiso | Descripción |
|---|---|---|---|
| `GET`  | `/api/v1/roles` | `users.list` | Lista roles con `user_count` |
| `GET`  | `/api/v1/roles/modules` | `users.list` | Catálogo de módulos y niveles |
| `GET`  | `/api/v1/roles/{key}` | `users.list` | Detalle de un rol |
| `POST` | `/api/v1/roles` | `roles.manage` | Crear rol (key slugified si vacía) |
| `PUT`  | `/api/v1/roles/{key}` | `roles.manage` | Editar label/description |
| `DELETE` | `/api/v1/roles/{key}` | `roles.manage` | Borrar (rechaza system/con-usuarios) |
| `GET`  | `/api/v1/roles/{key}/permissions` | `users.list` | Matriz actual del rol |
| `PUT`  | `/api/v1/roles/{key}/permissions` | `roles.manage` | Reescribe la matriz (invalida cache) |

`/api/v1/users` ahora devuelve `last_login_at` y valida el rol contra la tabla `roles`.


---

## Estado actual (auditoría del código, junio 2026)

### Lo que YA existe y funciona
- **Auth completo**: login con JWT en cookie httpOnly, refresh tokens rotatorios (`refresh_tokens`), logout con blacklist en Redis, `/auth/me`, `/auth/permissions`.
- **Seguridad de login**: rate limit `5/minute` (`slowapi`), lockout tras 5 intentos (15 min), `failed_login_attempts` / `locked_until` / `last_login_at` en `users`.
- **Contraseñas**: `validate_password_strength` (10 chars, mayús/minús/número/especial, ≠ username), cambio propio (`PUT /auth/password`) y reset por admin (`POST /users/{id}/reset-password`); ambos revocan refresh tokens.
- **RBAC en DB**: tablas `permissions` y `role_permissions` (migración `…0003`), seed de permisos (`…0004`), `get_user_permissions(role)` con cache Redis (TTL 5 min), dependency `require_permission(*perms)`.
- **Sesiones**: `GET /auth/sessions` (propias o de otro usuario si `users.edit`), `DELETE /auth/sessions/{id}`, `DELETE /auth/sessions` (cerrar las demás). UI en `App.jsx` (`SessionsModal`) y `UsersPage` (`UserSessionsModal`).
- **Auditoría**: `AuditMiddleware`, tabla `audit_logs`, `AuditPage`, logs de seguridad estructurados (`security_logging.py`).
- **Frontend RBAC**: `AuthContext` con `permissions[]`, `usePermission`, `<Can permission>`, `ProtectedRoute` por permiso.
- **Migraciones Alembic** versionadas y **tests** (`test_auth.py` 9, `test_permissions.py` 4, `test_passwords.py` 3).

### Brechas y deuda técnica detectadas (lo que este plan resuelve)
1. **Roles hardcodeados en 4 lugares** — no se pueden crear/editar roles desde la app:
   - `PERMISSIONS_BY_ROLE` dict en [`auth_service.py`](../backend/app/services/auth_service.py) (**código muerto**: `get_user_permissions` ya lee de la DB, este dict no se usa).
   - `valid_roles = {"admin", "editor", "viewer"}` repetido **3 veces** en [`users.py`](../backend/app/api/routes/users.py) (`create`, `update` ×2).
   - `CHECK` constraint del campo `role` en la migración inicial.
   - `ROLES` / `ROLE_LABELS` hardcodeados en [`UsersPage.jsx`](../frontend/src/pages/UsersPage.jsx).
2. **No hay gestión de roles**: la tabla `role_permissions` solo se edita por SQL/migración. No hay endpoints CRUD ni UI.
3. **Permisos por acción, no por módulo**: el usuario quiere un modelo **Módulo → Lectura o Escritura**. Hoy son codenames sueltos (`motors.create`, `motors.edit`, `motors.delete`…) sin agrupación ni concepto de nivel.
4. **Doble fuente de verdad** de permisos (dict en código vs tabla en DB) — riesgo de divergencia.
5. **Cobertura de permisos por ruta incompleta**: módulos nuevos pueden no tener `require_permission` (consulta por lote, jobs de cálculo de rendimientos, bindings manuales de proveedor, `proxy.py`). Auditar.
6. **Detalles de UX**: placeholder "min 8 caracteres" en `CreateUserModal` contradice la validación real (10).

---

## Decisión de diseño: modelo de permisos por módulo

Se mantiene la tabla `permissions` (codenames) como **capa de bajo nivel** y se introduce una **capa de módulos** por encima, que es lo que ve el usuario administrador.

```
módulo            nivel        codenames implicados
────────────────  ───────────  ─────────────────────────────────────────
dashboard         lectura      dashboard.view
consulta_motor    lectura      engine_lookup.use, engine_lookup.batch
rendimientos      lectura      rendimientos.view
rendimientos      escritura    rendimientos.refresh
vehiculos         lectura      vehicles.list
vehiculos         escritura    vehicles.edit, vehicles.refresh
motores           lectura      motors.list
motores           escritura    motors.create, motors.edit, motors.delete, motors.attachments
clientes          lectura      customers.list
clientes          escritura    customers.create, customers.edit
usuarios          lectura      users.list
usuarios          escritura    users.create, users.edit
roles             escritura    roles.manage            ← NUEVO
auditoria         lectura      audit.view
```

Reglas:
- **Escritura implica lectura**: asignar "escritura" a un módulo otorga también los codenames de lectura.
- La matriz que edita el admin es **Módulo × {ninguno, lectura, escritura}** por rol. Al guardar, el backend **traduce** la matriz a filas `role_permissions` (codenames). Así los `require_permission(...)` existentes en las rutas **no cambian**.
- La definición de módulos → codenames vive en **un solo lugar** (`module_registry.py`), reutilizable por backend (traducción/validación) y expuesta al frontend vía endpoint (para construir la UI dinámicamente).

---

## FASE 0 — Reconciliación de deuda técnica
**Estado: PENDIENTE** · Prerrequisito de todo lo demás.

### Objetivo
Una sola fuente de verdad para permisos antes de construir encima.

### Tareas
1. Eliminar el dict `PERMISSIONS_BY_ROLE` de `auth_service.py` (código muerto) y cualquier import.
2. Crear `backend/app/services/module_registry.py` con:
   - `MODULES`: lista ordenada de módulos `{key, label, levels: [...] }`.
   - `MODULE_CODENAMES`: mapa `(module, level) -> tuple[codename, ...]`.
   - Helpers: `codenames_for(module, level)`, `level_for_role(role_permissions: set) -> dict[module, level]`, `permissions_for_matrix(matrix: dict) -> set[codename]`.
3. Mantener `PERMISSION_DESCRIPTIONS` (sigue siendo útil) pero moverlo junto al registry.
4. Corregir placeholder "min 8 caracteres" → "min 10 caracteres" en `CreateUserModal` y `register-form`.

### Verificación
- [ ] `grep -rn "PERMISSIONS_BY_ROLE" backend/` no devuelve nada.
- [ ] `module_registry.py` importa sin efectos secundarios y `permissions_for_matrix` cubre todos los codenames del seed.
- [ ] Tests existentes siguen pasando: `pytest backend/tests/ -v`.

---

## FASE 1 — Roles dinámicos (modelo de datos)
**Estado: PENDIENTE**

### Objetivo
Pasar de 3 roles fijos a roles almacenados en DB y editables.

### Modelo de datos (nueva migración Alembic)
```
roles
  id           SERIAL PK
  key          TEXT UNIQUE NOT NULL      -- slug estable: "admin", "editor", "viewer", "supervisor"…
  label        TEXT NOT NULL             -- nombre visible
  description  TEXT
  is_system    BOOLEAN NOT NULL DEFAULT FALSE   -- system roles no se pueden borrar
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

### Tareas
1. Migración que:
   - Crea `roles`, hace **seed** de `admin`/`editor`/`viewer` con `is_system = TRUE`.
   - Agrega FK `users.role → roles.key` (`ON UPDATE CASCADE`, `ON DELETE RESTRICT`).
   - **Elimina el `CHECK` constraint** de `users.role`.
   - Agrega FK `role_permissions.role → roles.key` (`ON DELETE CASCADE`).
   - Migración de seed de `roles.manage` en `permissions` + asignación a `admin`.
2. Backend: funciones en `auth_service.py` → `list_roles()`, `get_role(key)`, `create_role(...)`, `update_role(...)`, `delete_role(key)` (rechaza si `is_system` o si hay usuarios asignados).
3. Reemplazar los `valid_roles = {...}` hardcodeados en `users.py` por validación contra `list_roles()`.

### Verificación
- [ ] `SELECT * FROM roles` devuelve los 3 roles de sistema.
- [ ] Crear usuario con rol inexistente → 422; con rol válido → 201.
- [ ] No queda ningún `{"admin", "editor", "viewer"}` literal en el backend (`grep`).
- [ ] Borrar un rol de sistema → 409/422.

---

## FASE 2 — Permisos por módulo (Lectura/Escritura)
**Estado: PENDIENTE** · Depende de Fase 1.

### Objetivo
Exponer y persistir permisos como matriz Módulo × nivel.

### Tareas backend
1. Endpoint `GET /api/v1/roles/modules` → catálogo de módulos y niveles desde `module_registry` (para que el frontend dibuje la matriz).
2. Endpoint `GET /api/v1/roles/{key}/permissions` → nivel actual por módulo del rol (`level_for_role`).
3. Endpoint `PUT /api/v1/roles/{key}/permissions` (perm `roles.manage`):
   - Body: `{ "modules": { "vehiculos": "escritura", "motores": "lectura", ... } }`.
   - Valida módulos/niveles contra el registry.
   - Traduce a codenames (`permissions_for_matrix`) y reescribe `role_permissions` del rol en una transacción.
   - `clear_role_permissions_cache(key)` para invalidar el cache Redis.
   - Registra en `audit_log` (`action="ROLE_PERMISSIONS_UPDATE"`).
4. Guardas de seguridad: impedir que el rol `admin` (system) pierda `roles.manage`/`users.edit` (evitar lockout administrativo).

### Verificación
- [ ] `GET /roles/modules` lista todos los módulos del registry.
- [ ] Asignar `vehiculos=escritura` a un rol inserta `vehicles.list`, `vehicles.edit`, `vehicles.refresh` en `role_permissions`.
- [ ] Tras `PUT`, `GET /auth/permissions` del usuario afectado refleja el cambio (probar invalidación de cache).
- [ ] Un usuario sin `roles.manage` recibe 403 en `PUT /roles/{key}/permissions`.

---

## FASE 3 — API de gestión de roles (CRUD)
**Estado: PENDIENTE** · Depende de Fases 1–2.

### Objetivo
CRUD de roles consumible por el frontend.

### Tareas
1. Nuevo router `backend/app/api/routes/roles.py` e incluirlo en `router.py`:
   - `GET /api/v1/roles` (perm `users.list` o `roles.manage`) → lista con conteo de usuarios por rol.
   - `POST /api/v1/roles` (`roles.manage`) → crea rol (key slugificada y única).
   - `PUT /api/v1/roles/{key}` (`roles.manage`) → edita `label`/`description`.
   - `DELETE /api/v1/roles/{key}` (`roles.manage`) → bloquea `is_system` y roles con usuarios.
   - (Permisos del rol → endpoints de Fase 2.)
2. Auditar **todas** las rutas y agregar `require_permission(...)` donde falte (lote, jobs de rendimientos, bindings manuales, `proxy.py`).

### Verificación
- [ ] CRUD completo de un rol "supervisor" vía `curl`.
- [ ] `DELETE` de un rol con usuarios asignados → 409.
- [ ] `grep -L require_permission` sobre rutas de escritura no encuentra endpoints desprotegidos.

---

## FASE 4 — Frontend: sección de Roles y permisos
**Estado: PENDIENTE** · Depende de Fase 3.

### Objetivo
UI de administración de roles con la matriz Módulo × Lectura/Escritura.

### Tareas
1. Nueva página `RolesPage.jsx` + ruta `/roles` protegida con `permissions={["roles.manage"]}`; entrada en el sidebar bajo "Gestión" envuelta en `<Can permission="roles.manage">`.
2. API client en `vehicleApi.js`: `listRoles`, `createRole`, `updateRole`, `deleteRole`, `fetchModules`, `fetchRolePermissions`, `updateRolePermissions`.
3. Componente **matriz de permisos**: filas = módulos, columnas = `Ninguno / Lectura / Escritura` (radio por fila); deshabilitar edición de roles de sistema según política. Guardar → `PUT …/permissions`.
4. Refactor `UsersPage.jsx`: cargar roles dinámicamente (`listRoles`) en lugar de `ROLES`/`ROLE_LABELS` fijos; el `<select>` de rol se llena desde la API.
5. Estilos en `frontend/src/styles.css` siguiendo el design system (tabla/matriz con `.card`, `.status-*`, etc. — **sin** CSS por componente ni Tailwind).
6. Tras `updateRolePermissions`, refrescar `AuthContext` (`/auth/me`) si el rol editado es el del usuario actual.

### Verificación
- [ ] `/roles` visible solo para usuarios con `roles.manage`.
- [ ] Cambiar `motores` a "Escritura" para un rol y verificar que un usuario de ese rol ya puede crear/editar motores tras re-login/refresh.
- [ ] Crear/editar/eliminar roles desde la UI funciona; roles de sistema no se pueden borrar.
- [ ] El selector de rol en Usuarios refleja los roles creados.

---

## FASE 5 — Refuerzo del control de sesiones
**Estado: PENDIENTE (mejora; lo básico ya existe)**

### Objetivo
Más visibilidad y control sobre sesiones concurrentes.

### Tareas
1. Capturar `user_agent` al crear refresh token (agregar columna `user_agent` a `refresh_tokens`) y mostrarlo en los modales de sesiones ("dispositivo/navegador").
2. (Opcional, configurable) **límite de sesiones concurrentes** por usuario: al exceder N, revocar la más antigua. Variable `MAX_CONCURRENT_SESSIONS`.
3. Job de limpieza periódico de `refresh_tokens` expirados/revocados (`cleanup_expired_refresh_tokens` ya existe — agendarlo en el scheduler/APScheduler).
4. Mostrar `last_login_at` en `UsersPage`.

### Verificación
- [ ] Los modales de sesiones muestran IP + dispositivo + fecha.
- [ ] Con `MAX_CONCURRENT_SESSIONS=2`, un 3er login revoca la sesión más antigua.
- [ ] El job de limpieza corre y reduce filas obsoletas.

---

## FASE 6 — Tests y hardening final
**Estado: PENDIENTE**

### Objetivo
Cubrir lo nuevo y blindar contra regresiones.

### Tareas
1. `backend/tests/test_roles.py`:
   - CRUD de roles; no se borra rol de sistema; no se borra rol con usuarios.
   - `PUT /roles/{key}/permissions` traduce la matriz a codenames correctos.
   - Cambiar permisos de un rol invalida el cache y se refleja en `/auth/permissions`.
   - El rol `admin` no puede perder `roles.manage`/`users.edit`.
2. Ampliar `test_permissions.py`: un rol custom con `vehiculos=lectura` no puede escribir vehículos pero sí listarlos.
3. Test de regresión: ninguna ruta de escritura queda sin `require_permission`.
4. Tests de frontend opcionales (si hay runner): render de la matriz y guardado.
5. Actualizar `docs/` y `CLAUDE.md`/memoria con el modelo de módulos final.

### Verificación
- [ ] `pytest backend/tests/ -v` al 100%.
- [ ] `alembic upgrade head` desde DB limpia aplica todas las migraciones sin error.
- [ ] Lint/format del backend y frontend sin errores.

---

## Resumen de cambios por capa

| Capa | Cambios principales |
|---|---|
| **DB / migraciones** | tabla `roles`, FKs `users.role`/`role_permissions.role`, drop del `CHECK`, permiso `roles.manage`, columna `refresh_tokens.user_agent` |
| **Backend servicios** | `module_registry.py`, CRUD de roles, traducción matriz↔codenames, borrado de `PERMISSIONS_BY_ROLE` |
| **Backend API** | router `roles.py` (CRUD + `/modules` + `/{key}/permissions`), auditoría de `require_permission` en rutas |
| **Frontend** | `RolesPage.jsx` + matriz Módulo×R/W, roles dinámicos en `UsersPage`, sidebar, API client |
| **Tests** | `test_roles.py`, ampliación de `test_permissions.py` |

## Variables de entorno nuevas (opcionales)
| Variable | Uso | Fase |
|---|---|---|
| `MAX_CONCURRENT_SESSIONS` | Límite de sesiones por usuario | 5 |

## Orden de ejecución
```
FASE 0 (deuda técnica)
  └─→ FASE 1 (roles dinámicos)
        └─→ FASE 2 (permisos por módulo)
              └─→ FASE 3 (API roles)
                    └─→ FASE 4 (frontend Roles)
                          └─→ FASE 5 (sesiones)   ← puede paralelizarse
                                └─→ FASE 6 (tests + hardening)
```
Las fases 0–4 son la columna vertebral de lo solicitado (roles + permisos por módulo). La 5 es independiente y puede adelantarse.
