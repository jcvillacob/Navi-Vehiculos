# Estructura de respuestas por fuente

Este documento resume la forma esperada de las tres consultas principales que usa el lookup de vehiculos.

Importante:

- el script exploratorio debe traer la respuesta cruda mas completa posible de cada fuente
- en Fenix eso implica consultar `SELECT TOP 1 *` sobre `dbo.T_DIM_VEHICULO_CONFIABILIDAD`
- adicionalmente el script genera una vista reducida llamada `fenix_selected`, que es la proyeccion minima que usa la app

- `Geotab`: valida si la placa existe y, cuando aplica, devuelve el dispositivo.
- `Fenix`: consulta SQL sobre `dbo.T_DIM_VEHICULO_CONFIABILIDAD`.
- `Cummins`: dataplate del motor a partir del ESN / numero de motor.

## Script exploratorio

Archivo: `backend/scripts/explore_vehicle_sources.py`

Uso desde la raiz del repo:

```bash
python3 backend/scripts/explore_vehicle_sources.py TLK240
python3 backend/scripts/explore_vehicle_sources.py LVBS7PEB9TT501398
```

Salida:

- imprime un JSON con las tres fuentes en stdout
- incluye `fenix` como fila completa cruda y `fenix_selected` como vista reducida usada por la app
- genera un markdown en `docs/source-response-structures.generated.md`

## Geotab

Forma general:

```json
{
  "device": {
    "id": "...",
    "name": "...",
    "licensePlate": "TLK240",
    "vehicleIdentificationNumber": "...",
    "...": "otros campos del tenant"
  },
  "vin_from_geotab": "..."
}
```

Notas:

- `device` puede ser `null`
- la estructura exacta depende del tenant y de los campos expuestos por Geotab

## Fenix (SQL)

Forma general de la respuesta cruda:

```json
{
  "...": "todas las columnas disponibles de dbo.T_DIM_VEHICULO_CONFIABILIDAD"
}
```

Notas:

- el script además genera `fenix_selected` con una vista reducida del tipo:

```json
{
  "VIN": "LVBS7PEB9TT501398",
  "plate": "TLK240",
  "numero_motor": "7789215"
}
```

- `plate` dentro de `fenix_selected` se resuelve dinámicamente buscando la columna de placa real en `dbo.T_DIM_VEHICULO_CONFIABILIDAD`
- si Fenix no encuentra el vehículo, la respuesta es `null`

## Cummins

Forma general:

```json
{
  "VIN": "LVBS7PEB9TT501398",
  "Engine Serial Number": "7789215",
  "Technical Engine Configuration #": "D1K3001BX03",
  "...": "otros pares clave/valor del dataplate"
}
```

Notas:

- el dataplate es un diccionario abierto; las llaves pueden variar por motor
- el campo crítico para la app es `Technical Engine Configuration #`
