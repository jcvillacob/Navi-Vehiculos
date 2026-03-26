# Estructura completa de la consulta Cummins

Este archivo documenta la estructura observada para la consulta de Cummins/QuickServe a partir del numero de motor (ESN).

Fuente:

- inspeccion directa de la respuesta de `get_engine_dataplate(...)`
- cliente: `app.clients.quickserve_client`
- origen: QuickServe / Cummins

Consulta base usada por la app:

```python
get_engine_dataplate(engine_number, quickserve_config)
```

Notas:

- la respuesta de Cummins es un diccionario abierto de pares clave/valor
- las llaves pueden variar segun el motor consultado
- el campo critico que hoy usa la app es `Technical Engine Configuration #`
- tambien se aprovecha `N.º CPL` cuando esta disponible

## Llaves detectadas

- `Calibración de bomba de combustible`
- `Código de ECM`
- `Fecha de construcción`
- `Fecha de inicio de la garantía`
- `ISG13`
- `Marketing Engine Configuration #`
- `N.º CPL`
- `N.º de pieza de bomba de combustible`
- `Pedido a tienda`
- `Planta de construcción`
- `Technical Engine Configuration #`

## Campos relevantes detectados

- `Technical Engine Configuration #`
- `Marketing Engine Configuration #`
- `N.º CPL`
- `ISG13`
- `Código de ECM`
- `Fecha de construcción`

## Ejemplo de estructura esperada

```json
{
  "Calibración de bomba de combustible": "...",
  "Código de ECM": "...",
  "Fecha de construcción": "...",
  "Fecha de inicio de la garantía": "...",
  "ISG13": "...",
  "Marketing Engine Configuration #": "...",
  "N.º CPL": "...",
  "N.º de pieza de bomba de combustible": "...",
  "Pedido a tienda": "...",
  "Planta de construcción": "...",
  "Technical Engine Configuration #": "..."
}
```
