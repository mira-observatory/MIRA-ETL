# Resolucion de entidades: proveedores y compradores

`mart.process_buyers` relaciona procesos con compradores.
`mart.award_suppliers` relaciona adjudicaciones con proveedores.
Sus llaves compuestas permiten múltiples entidades sin repetir nombre,
identificador fiscal ni identificador fuente.

`sql/001_init.sql` incluye dos tablas de dimension
(`mart.suppliers`, `mart.buyers`) con un ID propio. Las tablas de detalle
referencian esos IDs; puede haber varios compradores por proceso y varios
proveedores por adjudicacion.
El nombre existe una sola vez en `name_normalised`. Conserva la grafia
publicada; solo se normaliza la representacion Unicode y se colapsan espacios.

## Estrategia de match: tres niveles, en orden de confianza

Implementada en `Database.get_or_create_supplier` / `get_or_create_buyer`
(`src/mira_etl/db.py`). Para cada registro, se intenta un nivel a la vez;
el primero que tenga un valor disponible decide como se busca (o se crea) la
fila de dimension:

1. **`TAX_ID`** -- identificador fiscal (RUC/RTN/Cedula/NIT, segun el pais)
   normalizado por `country_code`. El mas confiable: es un numero oficial,
   unico por ley.
2. **`SOURCE_ID`** -- ID interno que la propia fuente le asigna al proveedor/
   comprador en su catalogo (`supplier_id_source` / `buyer_id_source`), sin
   ser necesariamente un identificador fiscal. Se usa solo si no hay
   identificador fiscal disponible.
3. **`NAME_EXACT_NORMALISED`** -- nombre con Unicode canonico y espacios
   normalizados. Conserva mayusculas, acentos, puntuacion y sufijos legales.
   Solo se usa si no hay ninguno de los dos identificadores anteriores.
El orden anterior se usa durante la carga, pero no se guarda como una columna:
la tabla final conserva solamente los datos necesarios para identificar y
relacionar la entidad.

## Riesgo aceptado explicitamente

La normalizacion de nombre solo captura diferencias de representacion Unicode
y espacios. Por lo tanto, **no** captura:

- Diferencias de mayusculas, acentos, puntuacion o sufijos legales.
- Abreviaturas ("Const." vs "Constructora").
- Errores de tipeo.
- Orden de palabras distinto.

Estos casos **no se fusionan** -- cada uno recibe su propio `supplier_id`/
`buyer_id` nuevo, como si fueran entidades distintas. Es una decision de
diseno deliberada: se prefiere *no* agrupar (perder algo de deduplicacion)
antes que fusionar dos empresas que en realidad son distintas por un parecido
de nombre. Coincide con el principio que ya sigue el resto de MIRA: nunca
adivinar en silencio, documentar la limitacion.

## Cuando queda `NULL`

Si un registro no trae ni tax_id, ni source_id, ni nombre de proveedor/
comprador, no se crea ninguna fila de dimension. Para compradores, la
referencia queda `NULL`; para proveedores, no se crea una fila en la tabla de
relacion -- no se genera una entidad "vacia" solo para tener algo que enlazar.

## Concurrencia

La busqueda-o-creacion (`get_or_create_*`) hace un `SELECT` seguido de un
`INSERT` en pasos separados, sin bloqueo explicito. Esto es seguro para como
corre el ETL hoy (un solo proceso, secuencial), pero **no** es seguro si dos
corridas del mismo pipeline llegaran a ejecutarse en paralelo sobre el mismo
pais/fuente -- podrian crear dos filas de dimension para la misma entidad en
una condicion de carrera. Si en el futuro se paraleliza la ejecucion, esto
necesitaria revisarse (por ejemplo, con un indice `UNIQUE` parcial por nivel
+ `ON CONFLICT ... RETURNING`, o un `advisory lock` por pais).

## Verificado

Probado de punta a punta contra una instancia local de PostgreSQL (no contra
Supabase real, por restricciones de red del entorno de desarrollo):

- Dos procesos con el mismo `supplier_tax_id` colapsan al mismo `supplier_id`.
- Un tercer proceso con el mismo proveedor, sin tax_id, escrito con un sufijo
  legal distinto, se agrupa correctamente por nombre normalizado con el
  mismo `supplier_id` que ya existia por `TAX_ID`.
- Un proveedor sin ningun identificador en comun con los anteriores recibe un
  `supplier_id` nuevo, propio.
- Un registro sin tax_id, source_id, ni nombre no crea una relacion de
  proveedor ni una fila de dimension.
- Volver a correr el mismo lote de registros no duplica filas de dimension
  (idempotente).
