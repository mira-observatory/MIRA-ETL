# Honduras ONCAE (HonduCompras) -> MIRA Mapping

Este documento traza los campos del modelo minimo MIRA contra los releases
compilados OCDS que la Oficina Normativa de Contratacion y Adquisiciones del
Estado (ONCAE) publica en el OCP Data Registry.

Conector:

```text
honduras_oncae
```

Fuente (publicacion 122 del OCP Data Registry):

```text
https://data.open-contracting.org/en/publication/122
https://data.open-contracting.org/en/publication/122/download?name={AAAA}.jsonl.gz
```

Cobertura de la fuente: noviembre 2005 a la fecha, actualizacion diaria.

## Diferencia de mecanismo frente a Guatemala

Guatemala (`guatemala_guatecompras`) descarga un ZIP mensual que contiene un
unico JSON con un arreglo `records`, y se recorre con `ijson`. Honduras publica
**un archivo por a├▒o** en formato JSON Lines comprimido (`{AAAA}.jsonl.gz`):
cada linea es un release compilado (`tag: ["compiled"]`) completo, sin envoltura
`compiledRelease` y sin arreglo contenedor.

Por eso `config/sources/honduras_oncae.json` usa `download.type =
"http_jsonl_gz"`, con dos piezas nuevas:

- `extract.obtain_jsonl_gz` descarga y valida el gzip del a├▒o del periodo.
- `pipeline.process_jsonl_records` lo lee linea por linea sobre el gzip (sin
  descomprimirlo a disco) y carga en lotes de `MIRA_JSON_BATCH_SIZE`.

El resto del pipeline (raw -> staging -> mart -> audit) no cambia.

### Periodo mensual sobre un archivo anual

El repositorio trabaja con `--period AAAAMM` y se mantiene asi: el conector
descarga el archivo del **a├▒o** del periodo y conserva unicamente los releases
cuyo mes coincide. El filtro (`pipeline.period_of`) usa el campo `date` del
release compilado, **en la hora local del publicador** (los valores traen
desplazamiento `-06:00`), sin convertir a UTC, para que un proceso pertenezca
al mes que reporta su propio sistema.

Cada release tiene exactamente un `date`, asi que los doce periodos de un a├▒o
particionan el archivo sin solapes ni huecos. En el archivo de 2024 (78,177
releases) todas las lineas traen `date` y todas caen dentro de 2024. Una linea
sin `date` utilizable se descarta y se reporta en el resumen de la corrida.

Consecuencia operativa: una corrida mensual descarga el archivo anual completo
(~29 MB comprimidos para 2024). Con `--local-zip` se puede reutilizar un
`.jsonl.gz` ya descargado y evitar doce descargas del mismo archivo.

## Alcance: tres sistemas en un mismo dataset

El dataset mezcla tres sistemas de ONCAE, distinguibles por `sources`. Reparto
en el archivo de 2024:

| Sistema | `sources.id` | Releases 2024 |
|---|---|---|
| HonduCompras 1.0 (Modulo de Difusion de Compras y Contrataciones) | `honducompras-1` | 63,714 |
| Catalogo Electronico (convenios marco) | `catalogo-electronico` | 14,421 |
| Modulo de Difusion Directa de Contratos | `difusion-directa-contrato` | 42 |

Los tres se cargan sin filtrar. No comparten la misma estructura: el Catalogo
Electronico no publica `tender.title`, `tender.status`, `tender.statusDetails`
ni fechas de licitacion, pero si publica `awards[].value` con moneda. El
transformador cubre ambas formas y deja en `NULL` lo que la fuente no expone.

## Trazabilidad

| Campo MIRA | Origen en HonduCompras | Transformacion | Destino |
|---|---|---|---|
| `country_code` | Configuracion del conector | Valor fijo `HN` | `mart.procurement_record_core.country_code` |
| `source_system` | Configuracion del conector | Valor fijo `HonduCompras ONCAE OCDS` | `mart.procurement_record_core.source_system` |
| `source_record_id` | `ocid` | Copia directa (ej. `ocds-lcuori-0rw29R-CM-SDE-142-2024-1`) | `mart.procurement_record_core.source_record_id` |
| `source_url` | `sources[].url` | Primera URL declarada; si no hay, la URL de descarga del a├▒o | `mart.procurement_record_core.source_url` |
| `extracted_at` | Ejecucion ETL | Timestamp UTC de ejecucion | `mart.procurement_record_core.extracted_at` |
| `source_last_modified_at` | `date` del release compilado | Parseo ISO-8601 a `timestamptz` | `mart.procurement_record_core.source_last_modified_at` |
| `connector_version` | `config/sources/honduras_oncae.json -> connector_version` | Valor fijo por ahora: `hn-oncae-0.1.0` | `mart.procurement_record_core.connector_version` |
| `raw_payload` | Linea JSONL completa | Se guarda integra, sin recortar | `mart.procurement_record_core.raw_payload` |
| `raw_payload_hash` | `raw_payload` | SHA-256 estable del JSON ordenado | `mart.procurement_record_core.raw_payload_hash` |
| `normalisation_status` | Validaciones ETL | `PROCESSED` o `REVIEW_REQUIRED` | `mart.procurement_record_core.normalisation_status` |
| `normalised_at` | Ejecucion ETL | Timestamp UTC de normalizacion | `mart.procurement_record_core.normalised_at` |
| `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL` o `INVALID` | `mart.procurement_record_core.data_quality_status` |
| `missing_fields` | Validaciones ETL | Lista JSON de campos MIRA faltantes | `mart.procurement_record_core.missing_fields` |

## Campos Minimos Normalizados

| Grupo PDF | Campo MIRA | Origen en HonduCompras | Transformacion | Destino |
|---|---|---|---|---|
| Identificacion | `process_id` | `country_code` + `source_record_id` | ID interno estable `MIRA-HN-{hash}` que une todas las tablas mart | `mart.procurement_record_core.process_id` |
| Identificacion | `process_number` | `tender.title` (respaldo `tender.id`) | En HonduCompras `tender.title` es el numero de proceso (ej. `CM-SDE-142-2024`); `tender.id` es un consecutivo interno | `mart.procurement_process_details.process_number` |
| Identificacion | `title` | `tender.description`, si no `tender.title`, si no `description` | Solo 6.5% de los releases traen descripcion propia; el resto queda con el numero de proceso, y el Catalogo Electronico (que no tiene ninguno de los dos) con el texto del item | `mart.procurement_process_details.title` |
| Identificacion | `description` | `tender.description`, si no la descripcion del item, si no `awards[].description` | Primer valor disponible | `mart.procurement_process_details.description` |
| Comprador | `buyer_name` | Raiz de la cadena `parties[].memberOf` del `buyer` | ONCAE apunta `buyer` a la unidad ejecutora (ej. "Unidad Central"); MIRA guarda la institucion raiz (ej. "Secretaria de Estado en el Despacho de Desarrollo Economico"). La unidad queda en `raw_payload` | `mart.buyers.name_normalised` |
| Comprador | `buyer_id_source` | `parties[].identifier.id` de esa institucion | Codigo interno de ONCAE, esquema `X-HN-ONCAE-CE` | `mart.buyers.buyer_id_source` |
| Comprador | `buyer_tax_id` | No expuesto | `NULL`: el unico identificador del comprador es el codigo `X-HN-ONCAE-CE`, que no es RTN. No se guarda como identificador fiscal para no mezclar semanticas; la deduplicacion de compradores usa `buyer_id_source` | `mart.buyers.buyer_tax_id` |
| Contratacion | `procurement_method` | `tender.procurementMethodDetails` (respaldo `tender.procurementMethod`) | Valor local sin normalizar (`Compra Menor`, `Licitaci├│n p├║blica nacional`, `Convenio Marco`, ...) | `mart.procurement_process_details.procurement_method` |
| Contratacion | `process_status` | Contrato firmado, `tender.statusDetails`, adjudicacion o `tender.status`, en ese orden | Ver "Normalizacion de estado" | `mart.procurement_process_details.process_status` |
| Contratacion | `source_status` | `tender.statusDetails` (respaldo estados OCDS) | Valor original de la fuente, sin normalizar | `mart.procurement_process_details.source_status` |
| Fechas | `publication_date` | `tender.datePublished`, si no `tender.tenderPeriod.startDate`, si no `date` del release | El Catalogo Electronico no publica fechas de licitacion; su unica fecha es la del release | `mart.procurement_process_details.publication_date` |
| Fechas | `closing_date` | `tender.tenderPeriod.endDate` | Parseo ISO-8601 a `timestamptz` | `mart.procurement_process_details.closing_date` |
| Fechas | `award_date` | `contracts[].dateSigned` | Los `awards[]` de HonduCompras no traen `date`; la unica fecha de adjudicacion es la firma del contrato | `mart.procurement_process_details.award_date` |
| Montos | `estimated_amount` | `tender.value`, si no `planning.budget.amount` | Ninguno de los dos aparece poblado en el dataset revisado -> en la practica `NULL` (ver "Limitaciones") | `mart.procurement_process_details.estimated_amount` |
| Montos | `awarded_amount` | `contracts[].value.amount`, si no `awards[].value.amount` | Solo se toman objetos Value completos (monto + moneda) | `mart.procurement_process_details.awarded_amount` |
| Montos | `currency_code` | `currency` del mismo objeto Value | Siempre `HNL` en el dataset revisado | `mart.procurement_process_details.currency_code` |
| Proveedor | `supplier_name` | `awards[].suppliers[0].name` (respaldo `contracts[].suppliers[0]`) | Nombre normalizado y deduplicado | `mart.suppliers.name_normalised` |
| Proveedor | `supplier_id_source` | `parties[].identifier.id` o `id` del proveedor | Copia directa | `mart.suppliers.supplier_id_source` |
| Proveedor | `supplier_tax_id` | `parties[].identifier` con `scheme = HN-RTN`; respaldo: prefijo `HN-RTN-` del `id` | RTN hondureno de 14 digitos | `mart.suppliers.supplier_tax_id` |
| Proveedor | `supplier_type` | No existe campo nativo | `UNKNOWN` cuando hay proveedor, `NULL` cuando no lo hay | `mart.suppliers.supplier_type` |
| Bien o servicio | `item_description` | Primer item de `awards[]`, `contracts[]` o `tender[]`; respaldo `awards[].description` | Copia directa | `mart.procurement_item_details.item_description` |
| Bien o servicio | `category_source` | `classification.id` de ese item | Codigo UNSPSC de 8 digitos | `mart.procurement_item_details.category_source` |
| Bien o servicio | `category_normalised` | No disponible todavia | `NULL` hasta definir catalogo regional MIRA | `mart.procurement_item_details.category_normalised` |
| Calidad | `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL`, `INVALID` | `mart.procurement_record_core.data_quality_status` |

## Normalizacion de estado

HonduCompras expone dos estados: el generico de OCDS (`tender.status`) y uno
propio en espa├▒ol (`tender.statusDetails`), mas especifico. `transform_hn.py`
resuelve `process_status` en este orden:

1. `contracts[].status` `complete`/`terminated` -> `COMPLETED`; `cancelled` ->
   `CANCELLED` (no aparece poblado en el dataset revisado).
2. Hay `contracts[].dateSigned` -> `CONTRACTED`.
3. `tender.statusDetails` segun `STATUS_DETAILS_MAP` (se ignoran acentos y
   mayusculas):

   | `statusDetails` | MIRA |
   |---|---|
   | Elaboraci├│n | `PLANNED` |
   | Revisado / Publicado | `PUBLISHED` |
   | Recepci├│n de Ofertas | `OPEN` |
   | Evaluaci├│n | `EVALUATION` |
   | Adjudicado | `AWARDED` |
   | Fracasado(s) / Desierto | `DESERTED` |
   | Cancelado | `CANCELLED` |
   | Suspendido | `SUSPENDED` |
   | Finalizado | `COMPLETED` |

4. Hay `awards[]` -> `AWARDED`.
5. `tender.status` (`planned`, `active`, `complete`, `cancelled`,
   `unsuccessful`, `withdrawn`); por defecto `PUBLISHED`.

## Datos Adicionales Conservados

La linea JSONL completa se guarda integra en `raw_payload`, incluyendo lo que
todavia no tiene columna normalizada propia: `parties[]` con direcciones y
contactos, `documents[]` (pliegos, adendas, contratos en PDF), `planning.budget`
con las fuentes de financiamiento, `tender.legalBasis`, `tender.tenderers[]`,
`contracts[].guarantees[]`, `initiationType` y `publisher`.

## Metodologia y resultados observados

Las cifras siguientes salen de correr el conector completo sobre el archivo
real de 2024 (78,177 releases) para el periodo `202410`, sobre los primeros
4,000 registros del mes:

| Campo MIRA | Cobertura |
|---|---|
| `process_number`, `title`, `buyer_name`, `buyer_id_source`, `procurement_method`, `process_status`, `source_status`, `publication_date`, `source_url` | 100% |
| `description` | 98.9% |
| `item_description` | 98.2% |
| `category_source` (UNSPSC) | 95.7% |
| `closing_date` | 78.7% |
| `supplier_name` y `supplier_type` | 63.5% |
| `supplier_tax_id` (RTN) | 61.6% |
| `awarded_amount` y `currency_code` | 46.6% |
| `award_date` | 27.9% |
| `buyer_tax_id`, `estimated_amount` | 0% |

Reparto de `process_status` en esa muestra: `AWARDED` 1,381, `CONTRACTED`
1,116, `OPEN` 840, `PLANNED` 354, `DESERTED` 151, `CANCELLED` 144,
`EVALUATION` 11, `PUBLISHED` 3. La unica moneda observada es `HNL`.

Calidad resultante: 3,800 registros `PARTIAL` y 200 `INVALID`. Los 200
`INVALID` provienen de una sola regla, `CLOSING_BEFORE_PUBLICATION` (5% de la
muestra): son procesos que la fuente publica *despues* de cerrar la recepcion
de ofertas, sobre todo Compras Menores registradas de forma retroactiva. No es
un defecto del mapeo sino una inconsistencia real de la fuente, y por eso se
deja que la validacion la marque en vez de silenciarla.

Limitaciones:

- **`estimated_amount` sin poblar.** La fuente no publica `tender.value` ni
  `planning.budget.amount`. Los items si traen `unit.value.amount`, pero **sin
  moneda**, y el ETL no inventa una: cargar un monto sin `currency_code` viola
  la regla `MISSING_CURRENCY_WITH_AMOUNT` y marcaria el registro como
  `INVALID`. Queda pendiente decidir si se asume `HNL` para esos montos.
- **`buyer_tax_id` sin poblar.** Los compradores solo tienen el codigo interno
  de ONCAE; los RTN institucionales no estan en el dataset.
- **`award_date` solo con contrato firmado.** Un proceso `Adjudicado` sin
  contrato publicado no tiene fecha de adjudicacion en la fuente.
- **`supplier_type` sin clasificar.** `parties[]` de Honduras no trae `details`
  ni tipo de persona juridica, a diferencia de Guatemala.
- **Un mismo `ocid` puede reaparecer en meses distintos** si el proceso cambia:
  el release compilado se re-publica con un `date` nuevo. El `process_id` es
  estable, asi que la carga del mes siguiente actualiza el mismo registro en
  `mart` (upsert por `process_id`) en vez de duplicarlo.
