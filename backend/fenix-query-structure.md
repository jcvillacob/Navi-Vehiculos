# Estructura completa de la consulta Fenix

Este archivo documenta la estructura completa observada para la consulta de Fenix sobre `dbo.T_DIM_VEHICULO_CONFIABILIDAD` en `DB_DynamicsBI_Prod`.

Fuente:

- inspeccion de metadatos de SQL Server sobre `sys.columns`
- tabla: `dbo.T_DIM_VEHICULO_CONFIABILIDAD`
- base de datos: `DB_DynamicsBI_Prod`

Consulta base usada por la app:

```sql
SELECT TOP 1 *
FROM dbo.T_DIM_VEHICULO_CONFIABILIDAD
```

Notas:

- la app hoy consulta la fila completa y luego normaliza principalmente `VIN`, `plate` y `numero_motor`
- en la estructura compartida no aparecen columnas llamadas exactamente `Número de clase` ni `Número de marca`
- si se necesitan esos datos, habría que confirmar si existen en otra tabla, vista o con otro nombre funcional

## Columnas de `dbo.T_DIM_VEHICULO_CONFIABILIDAD`

| column_id | name | data_type | max_length | is_nullable |
| --- | --- | --- | --- | --- |
| 194 | Id Vehiculo | nvarchar | 100 | True |
| 195 | Nombre Vehiculo | nvarchar | 500 | True |
| 196 | Marca | nvarchar | 100 | True |
| 197 | Linea | nvarchar | 100 | True |
| 198 | Modelo | nvarchar | 100 | True |
| 199 | Configuracion | nvarchar | 100 | True |
| 200 | AñoModelo | int | 4 | True |
| 201 | Importado | varchar | 12 | True |
| 202 | Tipo de vehiculo | nvarchar | 100 | True |
| 203 | Nuevo/Usado | varchar | 5 | True |
| 204 | MajorStatus | nvarchar | 100 | True |
| 205 | NumberPlaca | nvarchar | 100 | True |
| 206 | VIN | nvarchar | 100 | True |
| 207 | Empresa | varchar | 10 | True |
| 208 | NumeroEnvio | nvarchar | 40 | True |
| 209 | Número de motor | nvarchar | 100 | True |
| 210 | Vehiculo | nvarchar | 100 | True |
| 211 | MarcaMayor | nvarchar | 100 | True |
| 212 | Tipo de Combustible | nvarchar | 100 | True |
| 213 | Tipo del motor | nvarchar | 100 | True |
| 214 | En tránsito | varchar | 2 | True |
| 215 | Código de articulo | nvarchar | 100 | True |
| 216 | Estatus principal | nvarchar | 500 | True |
| 217 | Estado de ventas | nvarchar | 500 | True |
| 218 | Grupo de Vehículo | varchar | 10 | True |
| 219 | Ubicación de inventario | nvarchar | 100 | True |
| 220 | Factura preliminar | nvarchar | 100 | True |
| 221 | Fecha de llegada a puerto estimada | datetime | 8 | True |
| 222 | Fecha del envío | datetime | 8 | True |
| 223 | Precio de venta | decimal | 17 | True |
| 224 | Responsable de ventas | nvarchar | 600 | True |
| 225 | Nombre del cliente | nvarchar | 500 | True |
| 226 | Orden de compra vehiculos | nvarchar | 100 | True |
| 227 | Orden de venta vehiculos | nvarchar | 100 | True |
| 228 | Número de envío | nvarchar | 100 | True |
| 229 | Fecha de factura de venta | datetime | 8 | True |
| 230 | Año modelo | int | 4 | True |
| 231 | Fecha de producción estimada | datetime | 8 | True |
| 232 | Importación Directa | varchar | 2 | True |
| 233 | Número pedido | nvarchar | 100 | True |
| 234 | Estado de la compra | nvarchar | 500 | True |
| 235 | Importe de coste financiero | decimal | 17 | True |
| 236 | Paso actual | nvarchar | 100 | True |
| 237 | Factura de compra | nvarchar | 100 | True |
| 238 | Matrícula | nvarchar | 100 | True |
| 239 | N° de declaración de importación | nvarchar | 200 | True |
| 240 | Normativa de emisiones | nvarchar | 100 | True |
| 241 | POI | varchar | 10 | True |
| 242 | Fecha de la orden de ventas | datetime | 8 | True |
| 243 | Reservado gerencia | varchar | 2 | True |
| 244 | Canibalizado | varchar | 2 | True |
| 245 | Novedad | varchar | 2 | True |
| 246 | Demo | varchar | 2 | True |
| 247 | Back up | varchar | 2 | True |
| 248 | Prestamo | varchar | 2 | True |
| 249 | Legal | varchar | 2 | True |
| 250 | Evento | varchar | 2 | True |
| 251 | Vehículo Demo | varchar | 2 | True |
| 252 | Configuración | nvarchar | 100 | True |
| 253 | Ubicación geográfica | nvarchar | 100 | True |
| 254 | Color | nvarchar | 100 | True |
| 255 | Almacen linea | varchar | 10 | True |
| 256 | Tercero | nvarchar | 100 | True |
| 257 | Nombre Tercero | nvarchar | 500 | True |
| 258 | Tamano | varchar | 50 | True |
| 259 | Fecha de venta final | datetime | 8 | True |
| 260 | Fecha de Matricula | datetime | 8 | True |
| 261 | ID Responsable de ventas | int | 4 | True |
| 262 | Fecha Entrega | date | 3 | True |
| 263 | GEstandar | int | 4 | True |
| 264 | GMotor | int | 4 | True |

## Campos relevantes detectados

- `Marca`
- `MarcaMayor`
- `VIN`
- `Número de motor`
- `Vehiculo`
- `NumberPlaca`
- `Matrícula`
- `Tipo de vehiculo`
- `Tipo del motor`
- `Grupo de Vehículo`
