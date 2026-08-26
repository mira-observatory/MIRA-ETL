# Costa Rica SICOP -> MIRA Mapping

Este documento traza los campos del modelo minimo MIRA contra los archivos del ZIP SICOP Costa Rica.

Fuente de ejemplo analizada:

```text
202001.zip
```

Conector:

```text
costa_rica_sicop
```

Tabla principal de transformacion:

```text
ProcedimientoAdjudicacion.csv
```

La normalizacion toma cada fila de `ProcedimientoAdjudicacion.csv` como una adjudicacion/linea normalizada y la enriquece, cuando hay cruce disponible, con:

- `DetalleCarteles.csv`
- `InstitucionesRegistradas.csv`
- `Proveedores.csv`

## Trazabilidad

| Campo MIRA | Origen en ZIP | Transformacion | Destino |
|---|---|---|---|
| `country_code` | Configuracion del conector | Valor fijo `CR` | `mart.processes.country_code` |
| `source_system` | Configuracion del conector | Valor fijo `SICOP Costa Rica` | `mart.processes.source_system` |
| `source_record_id` | `ProcedimientoAdjudicacion.NRO_SICOP` | Copia directa del identificador del sistema fuente | `mart.processes.source_record_id` |
| `source_url` | `config/sources/costa_rica_sicop.json -> download.url_template` | URL del ZIP renderizada con el periodo, por ejemplo `.../Zip/202001.zip` | `mart.processes.source_url` |
| `extracted_at` | Ejecucion ETL | Timestamp UTC de ejecucion | `mart.processes.extracted_at` |
| `source_last_modified_at` | `ProcedimientoAdjudicacion.fecha_rev` | Parseo a `timestamptz`; `NULL` si no existe o no parsea | `mart.processes.source_last_modified_at` |
| `connector_version` | `config/sources/costa_rica_sicop.json -> connector_version` | Valor fijo por ahora: `cr-sicop-0.1.0` | `mart.processes.connector_version` |
| `raw_payload` | Filas fuente relacionadas | JSON con `procedimiento_adjudicacion`, `detalle_cartel`, `proveedor`, `institucion` | `mart.processes.raw_payload` |
| `raw_payload_hash` | `raw_payload` | SHA-256 estable del JSON ordenado | `mart.processes.raw_payload_hash` |
| `normalisation_status` | Validaciones ETL | `PROCESSED` o `REVIEW_REQUIRED` | `mart.processes.normalisation_status` |
| `normalised_at` | Ejecucion ETL | Timestamp UTC de normalizacion | `mart.processes.normalised_at` |
| `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL` o `INVALID` | `mart.processes.data_quality_status` |
| `missing_fields` | Validaciones ETL | Lista JSON de campos MIRA faltantes | `mart.processes.missing_fields` |

## Campos Minimos Normalizados

| Grupo PDF | Campo MIRA | Origen en ZIP | Transformacion | Destino |
|---|---|---|---|---|
| Identificacion | `process_id` | `country_code` + `ProcedimientoAdjudicacion.NRO_SICOP` | ID interno estable `MIRA-CR-{hash}`; todas las lineas del SICOP comparten un proceso | `mart.processes.process_id` |
| Identificacion | `process_number` | `ProcedimientoAdjudicacion.NUMERO_PROCEDIMIENTO`; fallback `DetalleCarteles.NRO_PROCEDIMIENTO` | Copia directa | `mart.processes.process_number` |
| Identificacion | `title` | `DetalleCarteles.CARTEL_NM`; fallback `ProcedimientoAdjudicacion.DESCR_PROCEDIMIENTO` | Copia directa | `mart.processes.title` |
| Identificacion | `description` | `ProcedimientoAdjudicacion.DESCR_PROCEDIMIENTO`; fallback `DetalleCarteles.CARTEL_NM` | Copia directa | `mart.processes.description` |
| Comprador | `buyer_name` | `ProcedimientoAdjudicacion.INSTITUCION`; fallback `InstitucionesRegistradas.NOMBRE_INSTITUCION` | Nombre normalizado y deduplicado | `mart.buyers.name_normalised` |
| Comprador | `buyer_id_source` | `ProcedimientoAdjudicacion.CEDULA`; fallback `DetalleCarteles.CEDULA_INSTITUCION` | Copia directa | `mart.buyers.buyer_id_source` |
| Comprador | `buyer_tax_id` | `ProcedimientoAdjudicacion.CEDULA`; fallback `DetalleCarteles.CEDULA_INSTITUCION` | Se usa la cedula institucional como identificador fiscal disponible | `mart.buyers.buyer_tax_id` |
| Contratacion | `procurement_method` | `ProcedimientoAdjudicacion.TIPO_PROCEDIMIENTO`; fallback `DetalleCarteles.TIPO_PROCEDIMIENTO` | Copia del valor fuente | `mart.processes.procurement_method` |
| Contratacion | `process_status` | `DetalleCarteles.CARTEL_STAT` + existencia en `ProcedimientoAdjudicacion.csv` | Normaliza a catalogo MIRA: `DESERTED`, `CANCELLED`, `SUSPENDED`; si hay adjudicacion, `AWARDED` | `mart.processes.process_status` |
| Contratacion | `source_status` | `DetalleCarteles.CARTEL_STAT` | Valor original de la fuente | `mart.processes.source_status` |
| Fechas | `publication_date` | `DetalleCarteles.FECHA_PUBLICACION` | Parseo a `timestamptz`; si no cruza por `NRO_SICOP`, queda `NULL` | `mart.processes.publication_date` |
| Fechas | `closing_date` | `DetalleCarteles.FECHAH_APERTURA` | Parseo a `timestamptz`; si no cruza por `NRO_SICOP`, queda `NULL` | `mart.processes.closing_date` |
| Fechas | `award_date` | `ProcedimientoAdjudicacion.FECHA_ADJUD_FIRME` | Parseo a `timestamptz` por adjudicacion | `mart.awards.award_date` |
| Montos | `estimated_amount` | `DetalleCarteles.MONTO_EST` | Parseo a `numeric`; si no cruza por `NRO_SICOP`, queda `NULL` | `mart.processes.estimated_amount` |
| Montos | `awarded_amount` | `ProcedimientoAdjudicacion.MONTO_ADJU_LINEA_CRC`; fallback `ProcedimientoAdjudicacion.MONTO_ADJU_LINEA` | Parseo a `numeric` por adjudicacion | `mart.awards.awarded_amount` |
| Montos | `currency_code` | `CRC` cuando se usa `MONTO_ADJU_LINEA_CRC`; si se usa el monto original, `ProcedimientoAdjudicacion.MONEDA_ADJUDICADA` con fallback a `DetalleCarteles.TIPO_MONEDA` | La moneda siempre corresponde al campo de monto elegido | `mart.awards.currency_code` |
| Proveedor | `supplier_name` | `ProcedimientoAdjudicacion.NOMBRE_PROVEEDOR`; fallback `Proveedores.NOMBRE_PROVEEDOR` | Nombre normalizado y deduplicado | `mart.suppliers.name_normalised` |
| Proveedor | `supplier_id_source` | `ProcedimientoAdjudicacion.CEDULA_PROVEEDOR` | Copia directa | `mart.suppliers.supplier_id_source` |
| Proveedor | `supplier_tax_id` | `ProcedimientoAdjudicacion.CEDULA_PROVEEDOR` | Se usa la cedula proveedor como identificador fiscal disponible | `mart.suppliers.supplier_tax_id` |
| Proveedor | `supplier_type` | `Proveedores.TIPO_PROVEEDOR` | Normaliza texto a catalogo MIRA: `PERSON`, `COMPANY`, `CONSORTIUM`, `FOREIGN_SUPPLIER`, `UNKNOWN` | `mart.suppliers.supplier_type` |
| Bien o servicio | `item_description` | `ProcedimientoAdjudicacion.DESCR_BIEN_SERVICIO` | Copia directa | `mart.items.item_description` |
| Bien o servicio | `category_source` | `ProcedimientoAdjudicacion.OBJETO_GASTO`; fallback `DetalleCarteles.CLAS_OBJ` | Copia directa | `mart.items.category_source` |
| Bien o servicio | `category_normalised` | No disponible todavia | `NULL` hasta definir catalogo regional MIRA | `mart.items.category_normalised` |
| Calidad | `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL`, `INVALID` | `mart.processes.data_quality_status` |

## Datos Adicionales Conservados

El ETL conserva en `raw` solo los archivos y filas que aportan al menos un campo al mart actual. Para cada registro normalizado tambien guarda en `raw_payload` las filas relacionadas usadas por la transformacion.

Para cada registro normalizado se conserva:

```json
{
  "procedimiento_adjudicacion": "...",
  "detalle_cartel": "...",
  "proveedor": "...",
  "institucion": "..."
}
```

Eso permite reprocesar o ampliar el modelo sin volver a descargar la fuente.

## Archivos del ZIP Cargados a Raw

El conector carga a `raw.source_rows` solo las filas usadas por el mapping actual:

- `ProcedimientoAdjudicacion.csv`: todas sus filas, porque cada una es una adjudicacion/linea normalizada.
- `DetalleCarteles.csv`: solo filas cuyo `NRO_SICOP` aparece en `ProcedimientoAdjudicacion.csv`.
- `InstitucionesRegistradas.csv`: solo filas cuya `CEDULA` aparece como comprador de las adjudicaciones o de sus carteles relacionados.
- `Proveedores.csv`: solo filas cuya `CEDULA_PROVEEDOR` aparece en `ProcedimientoAdjudicacion.csv`.

Los demas CSVs del ZIP quedan fuera hasta que alguna columna de ellos se incorpore a la normalizacion MIRA.

## Validaciones Auditadas

Las validaciones se guardan en `audit.validation_results`.

| Regla | Severidad | Descripcion |
|---|---|---|
| `MISSING_PROCESS_NUMBER` | `WARNING` | Falta numero oficial del proceso |
| `MISSING_TITLE` | `WARNING` | Falta titulo |
| `MISSING_BUYER_NAME` | `WARNING` | Falta nombre del comprador |
| `MISSING_BUYER_TAX_ID` | `WARNING` | Falta identificador fiscal del comprador |
| `MISSING_PROCUREMENT_METHOD` | `WARNING` | Falta metodo/modalidad de contratacion |
| `MISSING_PROCESS_STATUS` | `WARNING` | Falta estado normalizado |
| `MISSING_PUBLICATION_DATE` | `WARNING` | Falta fecha de publicacion |
| `MISSING_AWARD_DATE` | `WARNING` | Falta fecha de adjudicacion |
| `MISSING_AWARDED_AMOUNT` | `WARNING` | Falta monto adjudicado |
| `MISSING_CURRENCY_CODE` | `WARNING` | Falta moneda |
| `MISSING_SUPPLIER_NAME` | `WARNING` | Falta nombre del proveedor |
| `MISSING_SUPPLIER_TAX_ID` | `WARNING` | Falta identificador fiscal del proveedor |
| `MISSING_ITEM_DESCRIPTION` | `WARNING` | Falta descripcion del bien o servicio |
| `NEGATIVE_ESTIMATED_AMOUNT` | `ERROR` | Monto estimado negativo |
| `NEGATIVE_AWARDED_AMOUNT` | `ERROR` | Monto adjudicado negativo |
| `CLOSING_BEFORE_PUBLICATION` | `ERROR` | Fecha de cierre anterior a publicacion |
| `AWARD_BEFORE_PUBLICATION` | `ERROR` | Fecha de adjudicacion anterior a publicacion |
| `UNPARSEABLE_DATE` | `ERROR` | La fuente trae fecha, pero no pudo parsearse |
| `INVALID_CURRENCY_CODE` | `WARNING` | Moneda fuera del catalogo inicial |
| `MISSING_CURRENCY_WITH_AMOUNT` | `ERROR` | Existe monto, pero no moneda |
| `INVALID_PROCESS_STATUS` | `ERROR` | Estado fuera del catalogo MIRA |
| `INVALID_SUPPLIER_TYPE` | `ERROR` | Tipo de proveedor fuera del catalogo MIRA |
| `DUPLICATE_PROCESS_ID_IN_RUN` | `ERROR` | Mismo identificador interno aparece mas de una vez en la corrida |

## Resultado Observado en 202001.zip

En la ultima ejecucion validada:

```text
mart.processes:        3276
mart.process_buyers:      3276
mart.items:       3276
```

Calidad:

```text
COMPLETE: 23
PARTIAL: 3253
```

Validacion principal:

```text
MISSING_PUBLICATION_DATE: 3253
```

La razon observada es que `ProcedimientoAdjudicacion.csv` contiene 639 `NRO_SICOP` distintos, pero solo 5 cruzan con `DetalleCarteles.csv` por `NRO_SICOP` en este ZIP. Por eso la fecha de publicacion queda `NULL` para la mayoria de registros y se audita como faltante.
