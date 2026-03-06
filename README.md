# Navi Vehiculos

Estructura base dockerizada para una app modular con:
- Frontend: React + Vite
- Backend: FastAPI
- Base de datos: PostgreSQL
- Orquestación: Docker Compose

## Estructura

- `frontend/`: app React (feature `plateLookup`)
- `backend/`: API FastAPI modular (`api`, `services`, `clients`, `schemas`, `core`)
- `docker-compose.yml`: servicios frontend, backend y db
- Puerto expuesto al host: `8090` (entrada única por backend/proxy)

## Funcionalidad inicial

Consulta por placa desde frontend y retorno de datos en backend con flujo:

1. Placa
2. VIN (Geotab client)
3. Numero de motor / ESN (SQL client)
4. Technical Engine Configuration # (QuickServe client)

Rutas:

- API: `GET /api/v1/vehicle/lookup?plate=TLK240`
- App: `http://localhost:8090`

Nota: las integraciones de Geotab, SQL Server y QuickServe son reales y usan variables desde `.env`.

## Variables

Revisa `.env.example` y crea `.env` con tus credenciales reales.

## Uso

```bash
docker compose up --build
```
