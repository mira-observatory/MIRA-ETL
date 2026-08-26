# Nicaragua SISCAE -> MIRA Mapping

Este documento traza los campos del modelo minimo MIRA contra el listado de
"Procesos Vigentes" del portal SISCAE de Nicaragua.

Conector:

```text
nicaragua_siscae
```

## Diferencia de mecanismo frente a Costa Rica

A diferencia de `costa_rica_sicop` (descarga un ZIP periodico con CSVs),
`nicaragua_siscae` no tiene API ni archivo descargable: es un sistema HTML con
sesion (arquitectura de portlets Java/JSF, sin documentacion oficial). El
conector abre una sesion, sube el listado a 100 resultados por pagina, y seguido
la paginacion confirmada empiricamente para extraer el listado completo.

Por esto, `config/sources/nicaragua_siscae.json` usa `download.type =
"html_session_scrape"` en vez de `"http_zip"`. Los detalles del formulario y
la paginacion viven en el propio JSON de la fuente; el motor generico
(`HtmlSessionSpec`, reutilizable para otra plataforma con la misma estructura
JSF/portlet) esta documentado en `docs/html_session_sources.md` y vive en
`src/mira_etl/extract_html.py`, que reemplaza a `extract.py` (exclusivo de
fuentes ZIP) para este tipo de fuente. El resto del pipeline
(raw -> staging -> mart -> audit) no cambia.

Nicaragua es la unica fuente `html_session_scrape` que necesita mas que ese
motor generico: adjudicados y cerrados no se pueden traer paginando un solo
listado (ver "Adjudicaciones" mas abajo), asi que tiene su propio adaptador
de transformacion (`transform.adapter: "nicaragua_siscae"`, en
`src/mira_etl/transform_ni.py`) en vez del generico `"active_procedures"`.

El parametro `--period` no selecciona un archivo historico como en Costa Rica
-- SISCAE solo expone el estado *actual* de los procesos vigentes. `period` se
guarda como etiqueta de la corrida en `audit.etl_runs`, no como filtro de
datos.

## Alcance de esta version del conector

Solo se extrae el listado de **Procesos Vigentes** (activos, sin adjudicar
todavia). La vista de detalle de adjudicacion (proveedor, RUC, monto
adjudicado) requiere una navegacion adicional (Mas Datos -> Adjudicacion ->
Volver) que **no es confiable todavia** -- SISCAE renderiza el boton
"Adjudicacion" de forma intermitente incluso para el mismo proceso en
corridas consecutivas. Se deja fuera de este conector a proposito hasta
resolverlo; ver la seccion "Pendiente" al final.

Como consecuencia, todos los campos de Montos y Proveedor quedan `NULL` en
esta version -- son procesos que, por definicion, aun no tienen adjudicacion.

## Trazabilidad

| Campo MIRA | Origen en SISCAE | Transformacion | Destino |
|---|---|---|---|
| `country_code` | Configuracion del conector | Valor fijo `NI` | `mart.processes.country_code` |
| `source_system` | Configuracion del conector | Valor fijo `SISCAE Nicaragua` | `mart.processes.source_system` |
| `source_record_id` | Codigo SIGAF si existe; si no, `tipo_procedimiento-numero_proceso-institucion` | Codigo SIGAF suele venir vacio (`#`, normalizado a `NULL`), por eso el respaldo compuesto | `mart.processes.source_record_id` |
| `source_url` | `config/sources/nicaragua_siscae.json -> download.base_url` | URL base del listado de vigentes (no existe URL individual por proceso) | `mart.processes.source_url` |
| `extracted_at` | Ejecucion ETL | Timestamp UTC de ejecucion | `mart.processes.extracted_at` |
| `source_last_modified_at` | Bloque de detalle, etiqueta "Ultima Actualizacion:" | Parseo a `timestamptz` | `mart.processes.source_last_modified_at` |
| `connector_version` | `config/sources/nicaragua_siscae.json -> connector_version` | Valor fijo por ahora: `ni-siscae-0.1.0` | `mart.processes.connector_version` |
| `raw_payload` | Fila parseada del listado | JSON con `proceso` | `mart.processes.raw_payload` |
| `raw_payload_hash` | `raw_payload` | SHA-256 estable del JSON ordenado | `mart.processes.raw_payload_hash` |
| `normalisation_status` | Validaciones ETL | `PROCESSED` o `REVIEW_REQUIRED` | `mart.processes.normalisation_status` |
| `normalised_at` | Ejecucion ETL | Timestamp UTC de normalizacion | `mart.processes.normalised_at` |
| `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL` o `INVALID` | `mart.processes.data_quality_status` |
| `missing_fields` | Validaciones ETL | Lista JSON de campos MIRA faltantes | `mart.processes.missing_fields` |

## Campos Minimos Normalizados

| Grupo PDF | Campo MIRA | Origen en SISCAE | Transformacion | Destino |
|---|---|---|---|---|
| Identificacion | `process_id` | `country_code` + `source_record_id` | ID interno estable `MIRA-NI-{hash}` que une todas las tablas mart | `mart.processes.process_id` |
| Identificacion | `process_number` | Primera celda del listado, parte numerica (ej. `5/2026`) | Copia directa | `mart.processes.process_number` |
| Identificacion | `title` | No existe campo separado -> se usa `descripcion` | SISCAE no distingue titulo de descripcion | `mart.processes.title` |
| Identificacion | `description` | Texto libre tras el ultimo codigo de categoria en el bloque de detalle | Copia directa | `mart.processes.description` |
| Comprador | `buyer_name` | Bloque de detalle, texto entre "Ultima Actualizacion: \<fecha\>" y el separador " - " | Nombre normalizado y deduplicado | `mart.buyers.name_normalised` |
| Comprador | `buyer_id_source` | No expuesto en el registro individual | `NULL` | `mart.buyers.buyer_id_source` |
| Comprador | `buyer_tax_id` | No existe en la fuente | `NULL` | `mart.buyers.buyer_tax_id` |
| Contratacion | `procurement_method` | Primera celda del listado, parte de texto (ej. `LICITACION SELECTIVA`) | Copia directa | `mart.processes.procurement_method` |
| Contratacion | `process_status` | Bloque de detalle, etiqueta "Estado:" | Normaliza con `transform.status_map` de la configuracion de la fuente | `mart.processes.process_status` |
| Contratacion | `source_status` | Bloque de detalle, etiqueta "Estado:" | Valor original de la fuente, sin normalizar | `mart.processes.source_status` |
| Fechas | `publication_date` | Bloque de detalle, etiqueta "Publicacion:" | Parseo a `timestamptz` | `mart.processes.publication_date` |
| Fechas | `closing_date` | Bloque de detalle, etiqueta "Cierre:" | Parseo a `timestamptz` | `mart.processes.closing_date` |
| Fechas | `award_date` | No aplica (proceso vigente, aun sin adjudicar) | No crea adjudicacion | `mart.awards.award_date` |
| Montos | `estimated_amount` | No expuesto en ningun punto de la fuente revisado | `NULL` | `mart.processes.estimated_amount` |
| Montos | `awarded_amount` | No aplica en esta version del conector (ver "Alcance") | No crea adjudicacion | `mart.awards.awarded_amount` |
| Montos | `currency_code` | No aplica en esta version del conector | `NULL` | `mart.processes.currency_code` |
| Proveedor | `supplier_name` | No aplica en esta version del conector | `NULL` | `mart.suppliers.name_normalised` |
| Proveedor | `supplier_id_source` | No aplica en esta version del conector | `NULL` | `mart.suppliers.supplier_id_source` |
| Proveedor | `supplier_tax_id` | No aplica en esta version del conector | `NULL` | `mart.suppliers.supplier_tax_id` |
| Proveedor | `supplier_type` | No existe ningun campo nativo en la fuente | `NULL` | `mart.suppliers.supplier_type` |
| Bien o servicio | `item_description` | Igual que `description` -- no hay desglose de items individuales | Copia directa | `mart.items.item_description` |
| Bien o servicio | `category_source` | Bloque de detalle, fragmentos `"texto (codigo de 8 digitos)"`, tipo UNSPSC | Union de todos los codigos encontrados, separados por `; ` | `mart.items.category_source` |
| Bien o servicio | `category_normalised` | No disponible todavia | `NULL` hasta definir catalogo regional MIRA | `mart.items.category_normalised` |
| Calidad | `data_quality_status` | Validaciones ETL | `COMPLETE`, `PARTIAL`, `INVALID` | `mart.processes.data_quality_status` |

## Datos Adicionales Conservados

El registro fuente completo (todos los campos parseados del listado, incluso
los que no tienen columna normalizada propia) se conserva integro dentro de
`raw_payload`:

```json
{
  "proceso": {
    "tipo_procedimiento": "...",
    "numero_proceso": "...",
    "estado": "...",
    "codigo_sigaf": "...",
    "institucion": "...",
    "categoria": "...",
    "descripcion": "...",
    "fecha_publicacion": "...",
    "fecha_cierre": "...",
    "ultima_actualizacion": "..."
  }
}
```

## Metodologia y limitaciones conocidas

A diferencia del mapeo de Honduras (auditado sobre una descarga completa de
831,680 releases), los valores de ejemplo de este documento provienen de una
muestra manual de decenas de procesos revisados durante el desarrollo del
conector, no de una auditoria estadistica sobre el dataset completo (~422-430
procesos vigentes en un momento dado). No se han calculado porcentajes de
ausencia por campo para Nicaragua todavia.

## Adjudicaciones (resuelto en ni-siscae-0.2.0, 2026-08-26)

Nicaragua cargaba con cero adjudicaciones y cero proveedores. La causa no era
la navegacion "Mas Datos -> Adjudicacion" que se creia poco confiable: era el
**endpoint**. `busquedaProcedimientosVigentes?proc_estado=VIGENTE` solo puede
devolver procesos VIGENTES, que por definicion todavia no tienen adjudicacion.
Ninguna cantidad de reintentos sobre ese listado iba a producir un solo
proveedor.

El portal tiene un segundo buscador, "Todos los Procesos"
(`busqueda?accion=todos`), que expone el estado como checkbox: VIGENTE,
EJECUCION, CANCELADO, CERRADO, DESIERTO, **ADJUDICADO**, SUSPENDIDO. Las filas
salen con el mismo formato que el listado de vigentes, asi que
`parse_active_procedures_page` las parsea sin cambios.

Medido contra el portal real el 2026-08-26:

| estado del portal | dataset | procesos | estado MIRA |
|---|---|---|---|
| Vigente | `procesos_vigentes` | ~500 | OPEN |
| Adjudicado | `procesos_adjudicados` | ~1,300 | AWARDED |
| En Evaluacion (checkbox CERRADO) | `procesos_cerrados` | ~2,000 | EVALUATION |
| EJECUCION / DESIERTO / CANCELADO / SUSPENDIDO | -- | 0 | -- |

Unos 3,800 procesos en total, contra los 630 que se cargaban antes. Medido:
ADJUDICADO 1,129 de 1,129 unicos en 465 s; CERRADO 400 de 400 unicos en 327 s;
cero filas con codificacion rota en ambos.

Reparto por modalidad: 1,000 CONTRATACION MENOR, 77 LICITACION SELECTIVA,
21 CONTRATACION SIMPLIFICADA, 18 LICITACION PUBLICA, 7 CONCURSO PARA
CONSULTORES, 1 comparacion de calificaciones BCIE.

### Dos cosas que estaban rotas en silencio

**La paginacion se saltaba registros.** Sin un `ordenItems` explicito, SISCAE
pagina sobre un resultado sin orden estable y las filas se barajan entre
peticiones. Medido sobre 5 paginas de 10: sin orden, 32 procesos distintos de
50 recolectados (las paginas avanzaban 1, luego 10, luego 5). Con
`ordenItems=PorFechaPublicacion`, 48 de 50. Los duplicados son inofensivos
(el mart hace upsert por `source_record_id`); los registros saltados no lo
eran. El conector fija el orden y ademas deduplica.

**La codificacion.** SISCAE sirve Latin-1 sin declararlo de forma fiable. Sin
forzarlo, cada nombre de institucion acentuado entraba a la base como
mojibake -- el tipo de dano que nadie nota hasta que el dato ya esta
publicado.

### El detalle de adjudicacion

Proveedor, RUC, monto y moneda viven detras de
`listado -> [Mas Datos] -> [Adjudicacion] -> [Volver]`. El boton
"Adjudicacion" es un `<input type=submit>`, **no** un enlace con
`_link_hidden_` como el resto de la navegacion del portlet; buscarlo como
enlace es lo mas probable que hizo concluir que SISCAE lo renderiza "de forma
intermitente". Verificado: `Volver` si restaura el listado (misma pagina,
mismas 100 filas), y un proceso expone la pestana o no de forma consistente.

Ejemplos reales extraidos:

- `ENERGIA ELECTRICA SOL Y VIENTO SOCIEDAD ANONIMA - J0310000350337`,
  US$ 1,336,229.69 (ENATREL, licitacion BCIE)
- `NARVAEZ SANDOVAL, JUAN ANDRES - 0410401930001Y`, C$ 7,324,833.36
  (Alcaldia Potosi, licitacion selectiva)

La fuente escribe la moneda como simbolo (`US$` / `C$`), nunca como codigo;
el conector normaliza a USD / NIO. Leer `C$` como dolar inflaria un monto
nicaraguense unas 36 veces.

**Solo una minoria de los procesos adjudicados publica ese detalle.** El 89%
del corpus es CONTRATACION MENOR (compra menor), que no expone la pestana.
No se logro establecer la tasa exacta: las muestras tomadas son chicas y
pueden estar sesgadas, porque tras cada `Volver` el listado se vuelve a
consultar y los indices posicionales podrian no apuntar al mismo registro.
Queda como medicion pendiente, no como afirmacion.

Costo: 3 peticiones por proceso. Medido, con paginas de 10 filas, ~8 s por
proceso; con paginas de 100 filas, ~26 s (el formulario que hay que reenviar
pesa 728 KB en vez de 114 KB). Por eso `scrape_awarded_procedures` acepta
`award_detail_limit`: el corpus se recorre por lotes en vez de castigar un
portal que corre en una instancia unica y corta conexiones bajo carga
sostenida.

## Pendiente

- **Tasa real de publicacion del detalle de adjudicacion**, medida sobre una
  muestra aleatoria y no sobre indices consecutivos de la primera pagina.
- **Colisiones de `source_record_id`:** 3 de 1,129 procesos comparten
  (tipo, numero, institucion) y colapsan en un mismo id. Agregar la
  descripcion a la clave lo resolveria, pero **cambiaria todos los
  `process_id` de Nicaragua**, con el costo de migracion que eso implica
  sobre datos ya cargados. Es una decision del dueno del modelo, no del
  conector.
## Alcance temporal: solo 2026 (verificado, 2026-08-26)

**No hay historico disponible.** Se busco por tres caminos distintos y ninguno
devuelve un solo proceso anterior a 2026:

1. `ejercicioId` en el buscador simple. El desplegable solo ofrece 2026
   (`value=21`), pero se probaron a mano todos los valores del 10 al 25:
   unicamente el 21 devuelve filas. El barrido no estaba roto -- el 21
   respondio dentro de la misma corrida.
2. La **Busqueda Avanzada**, que si tiene rango de fechas
   (`fechaDesdeId`/`fechaHastaId`), selector de que fecha filtrar
   (creacion / publicacion / **adjudicacion** / cierre) y un checkbox
   **`historicosId`**. Con historicos activado y rango 2023-2025: cero filas,
   por fecha de publicacion, de adjudicacion y de creacion, repetido.
3. La prueba de control que lo cierra: rango **2020-2026** con historicos
   activado devuelve 100 filas, **todas de 2026**. La consulta funciona y el
   rango incluye 2020-2025; el portal simplemente no publica nada de esos
   anios.

### El registro OCP tampoco lo tiene (revisado 2026-08-26)

Se reviso https://data.open-contracting.org, que es de donde sale el dataset
de Honduras que ya usa MIRA. **Nicaragua no publica OCDS.** El registro tiene
134 datasets de 72 paises y la cadena "nicaragua" no aparece ni una vez en el
JSON completo del registro (`/en/publications.json`, 768 KB). Los paises con
N son Nepal, Netherlands, Nigeria, North Macedonia y Norway.

Centroamerica en el registro OCP:

| pais | publicador | rango |
|---|---|---|
| Honduras | ONCAE | 2005-2026 (el que usa MIRA) |
| Honduras | SEFIN | 2012-2026 |
| Honduras | IAIP | 2020-2023 |
| Honduras | SISOCS | 2018 |
| Guatemala | Ministerio de Finanzas Publicas | 2020-2026 |
| Costa Rica | Poder Judicial | 2018-2023 |
| Panama | DGCP | 2013-2024 |
| **Nicaragua** | **ninguno** | **--** |
| El Salvador | ninguno | -- |

Nicaragua figura en un diagnostico de la OEA sobre la *factibilidad* de
adoptar OCDS en la region, no como publicador. Su dato vive solo en el HTML
de SISCAE, y solo el ejercicio en curso.

### Fuentes descartadas (revisadas 2026-08-26)

| fuente | resultado |
|---|---|
| `ejercicioId` del buscador simple | Solo 2026. Probados a mano los valores 10..25 |
| Busqueda Avanzada + checkbox "historicos" | 2023-2025 da cero por fecha de publicacion, adjudicacion y creacion. Control 2020-2026: 100 filas, todas 2026 |
| Registro OCP (data.open-contracting.org) | Nicaragua no publica OCDS. Ausente de los 134 datasets |
| Portal nicaraguacompra.gob.ni | Solo normativa, guias y comunicados. No hay seccion de datos ni descargas |
| "Monitoreo y Evaluacion" del portal | Texto introductorio sobre compras publicas. Sin datasets ni reportes descargables |
| Internet Archive | Existe un snapshot de 2024-04-13, pero del *home* del portal. No se pudo descargar desde aqui para confirmar su contenido. En cualquier caso el Archive rastrea con GET y los listados de SISCAE son respuestas POST con estado de sesion, asi que en el mejor caso tendria la primera pagina de vigentes de ese dia, no un anio de adjudicaciones |
| Datasets de terceros / investigacion | No se encontro ninguno |

Si en algun momento se necesita 2023-2025 habra que pedirlo por gestion
(solicitud de acceso a la informacion a la DGCE, o un volcado directo). No es
un limite del conector, ni algo que se resuelva cambiando de fuente.

### Consecuencia operativa: lo que no se capture, se pierde

SISCAE solo expone el ejercicio en curso. Cuando cambie el anio, los ~3,800
procesos de 2026 dejaran de ser consultables por el portal igual que hoy no
lo son los de 2025 -- y no existiran en ninguna otra parte, porque Nicaragua
no publica OCDS ni volcados.

Eso invierte la prioridad de este conector: no es una carga que se pueda
posponer. Cada corrida es el unico registro que va a quedar de ese periodo.
Conviene correrlo con regularidad (y no solo una vez) para que MIRA acumule
el historico que la fuente no guarda.
- `buyer_tax_id`, `buyer_id_source`, `supplier_type`, `category_normalised`,
  `estimated_amount`: no expuestos por la fuente en ningun punto revisado.
- `award_date`: SISCAE no publica fecha de adjudicacion propia. "Ultima
  Actualizacion" es lo mas cercano, y llamarla fecha de adjudicacion seria
  inventar una precision que la fuente no da.
