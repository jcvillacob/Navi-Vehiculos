# Plan: Sistema de Usuarios, Permisos y Accesos

> Documento de referencia para implementar un sistema de auth robusto sobre la base existente.
> Cada fase es independiente y secuencial. Un modelo de IA ejecutará cada fase; las verificaciones permiten confirmar completitud.

---

## Estado actual (auditoría del código existente)

### Lo que ya existe
- Tabla `users` con campos: id, username, email, password_hash, role, is_active, created_at, updated_at
- Roles fijos en CHECK constraint: `admin`, `editor`, `viewer`
- Auth por JWT en cookie httpOnly (`access_token`), algoritmo HS256, expiración 480 min
- Blacklist de tokens en Redis (por `jti`)
- `get_current_user` dependency lee cookie → decodifica JWT → valida blacklist → retorna user
- `require_role(*roles)` factory dependency para proteger endpoints por rol
- `ProtectedRoute` en frontend filtra por autenticación y opcionalmente por `roles[]`
- Sidebar condicional: Usuarios y Auditoría solo visibles para `role === "admin"`
- AuditMiddleware registra POST/PUT/DELETE (excepto /auth) en tabla `audit_logs`
- Hashing con bcrypt (salt automático)
- CRUD de usuarios: solo admin puede listar, crear y editar usuarios
- Tabla `audit_logs` con índices en user_id y created_at DESC

### Problemas y carencias detectados
1. **Sin permisos granulares**: solo 3 roles hardcodeados. No hay tabla de permisos ni asignación flexible
2. **Sin refresh tokens**: si el JWT expira, el usuario pierde sesión sin opción de renovación silenciosa
3. **Sin rate limiting en login**: vulnerable a brute force
4. **Sin política de contraseñas**: solo min 8 chars en schema, sin validación de complejidad
5. **Sin bloqueo por intentos fallidos**: no hay lockout temporal
6. **Sin cambio de contraseña**: no existe endpoint para que el usuario cambie su propia password
7. **Sin reset de contraseña**: no hay flujo de recuperación
8. **Sin CSRF protection**: cookies sin token CSRF complementario
9. **Cookie `secure=False`**: hardcodeado, no condicional a entorno
10. **Sin sesiones concurrentes controladas**: un usuario puede tener N sesiones sin límite
11. **Sin expiración de contraseña**: no hay política de rotación
12. **Frontend no reacciona a 401/403**: no hay interceptor que redirija a login ante token expirado
13. **SQL directo con psycopg**: no usa SQLAlchemy ORM/migrations (Alembic), la tabla se crea con `CREATE IF NOT EXISTS` en cada query
14. **Sin tests de auth**

---

## FASE 1: Migraciones con Alembic
**Estado: PENDIENTE**

### Objetivo
Reemplazar los `CREATE TABLE IF NOT EXISTS` por migraciones versionadas.

### Tecnologías
- `alembic==1.15.1` (agregar a requirements.txt)
- SQLAlchemy ya está en requirements.txt (usarlo solo para metadata de Alembic, no como ORM completo)

### Tareas
1. `alembic init backend/app/migrations`
2. Configurar `alembic.ini` para leer `DATABASE_URL` desde env
3. Crear migración inicial que refleje las tablas `users` y `audit_logs` tal como están
4. Agregar campo `failed_login_attempts INTEGER DEFAULT 0` a tabla `users`
5. Agregar campo `locked_until TIMESTAMPTZ DEFAULT NULL` a tabla `users`
6. Agregar campo `password_changed_at TIMESTAMPTZ DEFAULT NOW()` a tabla `users`
7. Agregar campo `last_login_at TIMESTAMPTZ DEFAULT NULL` a tabla `users`
8. Eliminar los bloques `_ensure_auth_tables()` de `auth_service.py` y todas sus invocaciones
9. Agregar `alembic upgrade head` al startup del contenedor backend (en Dockerfile o entrypoint)

### Verificación
- [ ] `alembic current` muestra la revisión head
- [ ] `alembic history` lista las migraciones
- [ ] Las tablas `users` y `audit_logs` existen con los nuevos campos
- [ ] No quedan llamadas a `_ensure_auth_tables` en el código
- [ ] `docker compose up` ejecuta migraciones automáticamente

---

## FASE 2: Modelo de permisos granular (RBAC)
**Estado: PENDIENTE**

### Objetivo
Pasar de roles hardcodeados a un sistema RBAC con permisos asignables por rol.

### Modelo de datos (nueva migración Alembic)

```
permissions
  id          SERIAL PK
  codename    TEXT UNIQUE NOT NULL    -- ej: "motors.create", "users.list"
  description TEXT

role_permissions
  role        TEXT NOT NULL           -- "admin", "editor", "viewer" (o nuevos)
  permission  TEXT NOT NULL REFERENCES permissions(codename)
  PRIMARY KEY (role, permission)
```

### Permisos a definir (seed)

| Codename | Descripción | admin | editor | viewer |
|---|---|---|---|---|
| `dashboard.view` | Ver dashboard | x | x | x |
| `motors.list` | Listar motores | x | x | x |
| `motors.create` | Crear motor | x | x | |
| `motors.edit` | Editar motor | x | x | |
| `motors.delete` | Eliminar motor | x | | |
| `motors.attachments` | Gestionar adjuntos | x | x | |
| `vehicles.list` | Listar vehículos | x | x | x |
| `vehicles.edit` | Editar vehículo | x | x | |
| `vehicles.refresh` | Refrescar datos Geotab | x | x | |
| `customers.list` | Listar clientes | x | x | x |
| `customers.create` | Crear cliente | x | x | |
| `customers.edit` | Editar cliente | x | x | |
| `rendimientos.view` | Ver rendimientos | x | x | x |
| `rendimientos.refresh` | Refrescar rendimientos | x | x | |
| `users.list` | Listar usuarios | x | | |
| `users.create` | Crear usuario | x | | |
| `users.edit` | Editar usuario | x | | |
| `audit.view` | Ver auditoría | x | | |
| `engine_lookup.use` | Consultar motor por placa | x | x | x |

### Tareas backend
1. Crear migración con tablas `permissions` y `role_permissions`
2. Crear seed script (`backend/scripts/seed_permissions.py`) que inserte los permisos y asignaciones por defecto
3. Crear función `get_user_permissions(role: str) -> set[str]` en `auth_service.py` — consultar `role_permissions` con cache en Redis (TTL 5 min)
4. Crear dependency `require_permission(*perms: str)` en `dependencies.py` que:
   - Obtiene el user via `get_current_user`
   - Carga permisos del rol desde cache/DB
   - Verifica que al menos uno de los permisos requeridos esté presente
   - Lanza 403 si no
5. Reemplazar TODOS los `require_role(...)` por `require_permission(...)` en cada route file
6. Mantener `require_role` como wrapper legacy que consulta `role_permissions` (deprecar gradualmente)
7. Agregar endpoint `GET /api/v1/auth/permissions` que retorne los permisos del usuario autenticado

### Tareas frontend
1. Extender el objeto `user` en `AuthContext` para incluir `permissions: string[]` (obtenidos de `/auth/me` o `/auth/permissions`)
2. Crear hook `usePermission(codename)` → `boolean`
3. Crear componente `<Can permission="motors.create">` que renderiza children condicionalmente
4. Reemplazar las verificaciones `user?.role === "admin"` en `App.jsx` por `<Can permission="...">`
5. Proteger botones de acción (crear, editar, eliminar) con `<Can>`

### Verificación
- [ ] `GET /api/v1/auth/permissions` retorna array de codenames para el usuario autenticado
- [ ] Un viewer NO puede hacer POST a `/api/v1/motors` (403)
- [ ] Un editor SÍ puede hacer POST a `/api/v1/motors` (201)
- [ ] Un editor NO puede hacer GET a `/api/v1/users` (403)
- [ ] Los botones de acción se ocultan/deshabilitan según permisos en el frontend
- [ ] El sidebar muestra/oculta secciones según permisos, no roles
- [ ] Cache de permisos en Redis funciona (verificar con `redis-cli KEYS perm:*`)

---

## FASE 3: Seguridad del flujo de autenticación
**Estado: PENDIENTE**

### Objetivo
Hardening del login, tokens y cookies.

### Tecnologías
- `slowapi==0.1.9` (rate limiting basado en starlette, agregar a requirements.txt)

### Tareas

#### 3.1 Rate limiting en login
1. Agregar `slowapi` a requirements.txt
2. Configurar limiter global en `main.py` con key function `get_remote_address`
3. Aplicar `@limiter.limit("5/minute")` al endpoint `POST /auth/login`
4. Retornar 429 con header `Retry-After`

#### 3.2 Bloqueo por intentos fallidos
1. En `POST /auth/login`, si credenciales incorrectas:
   - Incrementar `failed_login_attempts` en DB
   - Si `failed_login_attempts >= 5`, setear `locked_until = NOW() + 15 min`
2. Antes de validar password, verificar si `locked_until > NOW()` → retornar 423 con mensaje y tiempo restante
3. En login exitoso: resetear `failed_login_attempts = 0`, `locked_until = NULL`, setear `last_login_at = NOW()`

#### 3.3 Refresh tokens
1. Crear tabla `refresh_tokens` (migración):
   ```
   refresh_tokens
     id          UUID PK DEFAULT gen_random_uuid()
     user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
     token_hash  TEXT NOT NULL
     expires_at  TIMESTAMPTZ NOT NULL
     created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
     revoked     BOOLEAN DEFAULT FALSE
   ```
2. En login exitoso: generar refresh token (UUID), hashear con SHA-256, guardar en DB, enviar como cookie httpOnly (`refresh_token`, path `/api/v1/auth/refresh`, max_age 7 días)
3. Crear endpoint `POST /api/v1/auth/refresh`:
   - Leer cookie `refresh_token`
   - Buscar en DB por hash, verificar no expirado, no revocado, usuario activo
   - Emitir nuevo access_token JWT (cookie)
   - Rotar: revocar refresh token actual, emitir nuevo refresh token
4. En logout: revocar todos los refresh tokens del usuario
5. Reducir expiración del access_token JWT de 480 min a 15 min
6. Crear cron job o task que limpie refresh_tokens expirados/revocados (ejecutar diario)

#### 3.4 Cookie segura condicional
1. Leer variable de entorno `ENVIRONMENT` (`development` | `production`)
2. En `set_cookie`: `secure=True` si `ENVIRONMENT == "production"`
3. Agregar `samesite="strict"` en producción, `"lax"` en desarrollo

#### 3.5 Interceptor frontend para token expirado
1. En `vehicleApi.js`, wrappear `parseJsonOrThrow`:
   - Si response.status === 401, intentar `POST /api/v1/auth/refresh` (una vez)
   - Si refresh exitoso, reintentar request original
   - Si refresh falla, limpiar estado de AuthContext y redirigir a `/login`
2. Alternativamente, crear un wrapper `fetchWithAuth(url, options)` que maneje esto

### Verificación
- [ ] 6to intento de login con password incorrecto retorna 423
- [ ] Después de 15 min el lock se libera
- [ ] Tras login exitoso, `failed_login_attempts = 0`
- [ ] `POST /auth/login` retorna 429 tras 5 requests en 1 minuto
- [ ] Access token expira en 15 min (verificar con jwt.io)
- [ ] Al expirar access token, el frontend hace refresh automático sin perder la sesión
- [ ] Si el refresh token está revocado, redirige a login
- [ ] Cookie `secure=true` cuando `ENVIRONMENT=production`
- [ ] Logout revoca todos los refresh tokens del usuario

---

## FASE 4: Política de contraseñas y gestión de credenciales
**Estado: PENDIENTE**

### Objetivo
Contraseñas seguras, cambio y recuperación.

### Tareas

#### 4.1 Validación de contraseñas
1. Crear función `validate_password_strength(password: str) -> list[str]` en `auth_service.py`:
   - Min 10 caracteres
   - Al menos 1 mayúscula
   - Al menos 1 minúscula
   - Al menos 1 número
   - Al menos 1 carácter especial (`!@#$%^&*()_+-=[]{}|;:,.<>?`)
   - No puede ser igual al username
   - Retorna lista de errores (vacía = válida)
2. Aplicar en `create_user` y en los endpoints de cambio de contraseña
3. Validar también en frontend antes de enviar (mismas reglas, para UX)

#### 4.2 Cambio de contraseña (usuario autenticado)
1. Crear endpoint `PUT /api/v1/auth/password`:
   - Body: `{ current_password, new_password }`
   - Verificar current_password contra hash en DB
   - Validar strength de new_password
   - Actualizar `password_hash` y `password_changed_at`
   - Revocar todos los refresh tokens del usuario (forzar re-login en otros dispositivos)
   - Registrar en audit_log
2. Crear página/modal en frontend accesible desde el sidebar-user

#### 4.3 Reset de contraseña (admin)
1. Crear endpoint `POST /api/v1/users/{user_id}/reset-password` (solo admin):
   - Body: `{ new_password }`
   - Validar strength
   - Actualizar hash, `password_changed_at`, revocar refresh tokens del target user
   - Registrar en audit_log
2. Agregar botón "Resetear contraseña" en la tabla de UsersPage (solo admin)

### Verificación
- [ ] Crear usuario con password "12345678" falla con errores descriptivos
- [ ] Crear usuario con password "MyStr0ng!Pass" funciona
- [ ] `PUT /auth/password` con current_password incorrecto retorna 401
- [ ] Tras cambiar password, las sesiones previas se invalidan (refresh tokens revocados)
- [ ] Admin puede resetear password de otro usuario
- [ ] Audit log registra cambios de contraseña

---

## FASE 5: Control de sesiones
**Estado: PENDIENTE**

### Objetivo
Visibilidad y control sobre sesiones activas.

### Tareas
1. Crear endpoint `GET /api/v1/auth/sessions` (usuario autenticado):
   - Retorna lista de refresh tokens activos (no revocados, no expirados) del usuario
   - Campos: id, created_at, last_ip (agregar campo `ip_address` a `refresh_tokens` en migración)
   - Marcar cuál es la sesión actual
2. Crear endpoint `DELETE /api/v1/auth/sessions/{session_id}`:
   - Revocar un refresh token específico (cerrar sesión remota)
   - Solo el propio usuario o un admin
3. Crear endpoint `DELETE /api/v1/auth/sessions` (revocar todas excepto la actual)
4. Crear sección "Sesiones activas" en el frontend (accesible desde perfil del usuario en sidebar)
5. Mostrar lista con IP, fecha de creación, y botón "Cerrar" por cada sesión

### Verificación
- [ ] `GET /auth/sessions` retorna sesiones activas del usuario
- [ ] Al cerrar una sesión remota, esa sesión pierde acceso (refresh falla)
- [ ] "Cerrar todas" revoca todo excepto la sesión actual
- [ ] Admin puede ver sesiones de cualquier usuario

---

## FASE 6: Hardening final y tests
**Estado: PENDIENTE**

### Objetivo
Tests automatizados, headers de seguridad, y preparación para producción.

### Tecnologías
- `pytest==8.3.5` y `pytest-asyncio==0.25.3` (agregar a requirements.txt)
- `httpx` (ya instalado) para TestClient async

### Tareas

#### 6.1 Tests
1. Crear `backend/tests/conftest.py` con:
   - Fixture de DB de test (usar variable `DATABASE_URL_TEST` o crear DB temporal)
   - Fixture de client (`httpx.AsyncClient` con app)
   - Fixture de usuario admin, editor, viewer pre-creados
2. Tests a escribir en `backend/tests/test_auth.py`:
   - Login exitoso retorna 200 + cookie
   - Login con credenciales incorrectas retorna 401
   - Login con usuario inactivo retorna 403
   - Lockout tras 5 intentos fallidos (423)
   - Rate limit en login (429)
   - `/auth/me` sin cookie retorna 401
   - `/auth/me` con cookie válida retorna usuario
   - `/auth/refresh` rota tokens correctamente
   - Logout revoca tokens
3. Tests en `backend/tests/test_permissions.py`:
   - Viewer no puede acceder a endpoints de escritura
   - Editor puede crear/editar pero no gestionar usuarios
   - Admin tiene acceso completo
   - Permisos se cachean en Redis
4. Tests en `backend/tests/test_passwords.py`:
   - Validación de strength rechaza passwords débiles
   - Cambio de password invalida sesiones previas
   - Admin puede resetear password de otro usuario

#### 6.2 Headers de seguridad
1. Agregar middleware o configurar en nginx:
   ```
   X-Content-Type-Options: nosniff
   X-Frame-Options: DENY
   X-XSS-Protection: 0
   Referrer-Policy: strict-origin-when-cross-origin
   Content-Security-Policy: default-src 'self'; style-src 'self' fonts.googleapis.com; font-src fonts.gstatic.com
   Strict-Transport-Security: max-age=63072000; includeSubDomains (solo producción)
   ```
2. Configurar en `infra/nginx/default.conf`

#### 6.3 Logging estructurado de eventos de seguridad
1. Agregar logs con `logging` (nivel WARNING) para:
   - Login fallido (username, IP)
   - Account locked (username, IP)
   - Token refresh fallido
   - Acceso denegado por permisos (user_id, endpoint, permission requerido)
2. Formato JSON para facilitar ingesta en sistemas de monitoreo futuros

### Verificación
- [ ] `pytest backend/tests/ -v` pasa al 100%
- [ ] Headers de seguridad presentes en responses (verificar con `curl -I`)
- [ ] Logs de seguridad aparecen en stdout del contenedor backend
- [ ] No hay llamadas directas a `_ensure_auth_tables` en el código
- [ ] `alembic check` no reporta discrepancias

---

## Resumen de dependencias a agregar

```
# requirements.txt (nuevas)
alembic==1.15.1
slowapi==0.1.9
pytest==8.3.5
pytest-asyncio==0.25.3
```

## Resumen de variables de entorno nuevas

| Variable | Valor ejemplo | Fase |
|---|---|---|
| `ENVIRONMENT` | `development` / `production` | 3 |
| `DATABASE_URL_TEST` | `postgresql://...navi_db_test` | 6 |

## Orden de ejecución

```
FASE 1 (Alembic)
  └─→ FASE 2 (RBAC)
        └─→ FASE 3 (Auth hardening)
              └─→ FASE 4 (Passwords)
                    └─→ FASE 5 (Sesiones)
                          └─→ FASE 6 (Tests + headers)
```

Cada fase depende de la anterior. No saltar fases.
