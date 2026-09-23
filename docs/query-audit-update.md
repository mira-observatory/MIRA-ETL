# Actualizar consultas y auditoria en DigitalOcean

Estos cambios estan en los tres archivos SQL existentes. No hay un `004` y
desplegar la API o ejecutar el ETL no aplica automaticamente el esquema.

## Aplicar el esquema

Desde una maquina con acceso a **DigitalOcean**, usar un checkout actualizado
de MIRA-ETL y una conexion del propietario de las tablas (administrador).
`MIRA_DB_ADMIN_URL` debe contener esa conexion con TLS; no usar una conexion
historica de Supabase ni las credenciales restringidas de la API/ETL.

Desde la raiz de MIRA-ETL:

```bash
psql "$MIRA_DB_ADMIN_URL" -X -v ON_ERROR_STOP=1 --single-transaction \
  -f sql/001_init.sql \
  -f sql/002_indexes_and_views.sql \
  -f sql/003_seed_base_data.sql
```

El comando confirma todos los cambios juntos o los revierte si falla. El
backfill de estados recorre los payloads guardados y puede tardar en una base
grande. Los registros historicos de auditoria conservan sus datos; los campos
nuevos `query_id`, `error_stage` y `error_type` quedan NULL para ese historial.

Verificar los permisos segun [database_security.md](database_security.md).
En particular, la nueva vista requiere SELECT para `mira_query`; si los
privilegios por defecto no estaban configurados para el propietario que
ejecuto los SQL, concederlo como administrador:

```sql
grant select on query.v_awards_all to mira_query;
```

Despues desplegar las versiones actualizadas de ETL, API y WEB. Reiniciar la
API para cargar el diccionario semantico. El esquema debe actualizarse **antes**
de desplegar la API: su escritor necesita las nuevas columnas y los CHECK de
resultados actualizados. Seguir el procedimiento de imagenes documentado en
[github-actions.md](github-actions.md); los archivos antiguos del servidor no
necesariamente corresponden a la imagen desplegada.

## Comprobar el registro de errores

La API espera el COMMIT antes de terminar una consulta. Guarda el registro y
sus intentos en una transaccion y reintenta fallos transitorios hasta tres
veces sin duplicarlos. `query_id` coincide con el identificador que devuelve
la respuesta JSON o el evento SSE `done`.

| Resultado | Significado |
| --- | --- |
| `REJECTED_QUESTION_TOO_BROAD` | Hace falta precisar el resultado buscado |
| `REJECTED_INTENT_UNCLEAR` | No se pudo determinar la intencion |
| `REJECTED_SQL_*` | El SQL generado fallo una regla de validacion |
| `FAILED_DB_TIMEOUT` | Tiempo de espera agotado en la BD o su pool |
| `FAILED_DB_ERROR` | Otro error de PostgreSQL |
| `FAILED_LLM_ERROR` | Fallo o rechazo del proveedor del modelo |
| `FAILED_INTERNAL_ERROR` | Fallo inesperado del servicio |

`error_stage` indica la etapa y `error_type` la clase de excepcion o regla de
rechazo. Una aclaracion no tiene excepcion tecnica: se diferencia por outcome.
Los SQL generados y sus rechazos quedan en `analytics.query_attempt`, incluso
si el modelo falla al reintentar despues de un rechazo.

Con un rol autorizado a leer analytics, ejecutar:

```sql
select created_at, query_id, outcome, error_stage, error_type, attempt_count
from analytics.query_log
where outcome like 'FAILED_%' or outcome like 'REJECTED_%'
order by created_at desc
limit 50;

-- Debe devolver cero filas: detecta consultas con intentos incompletos.
select l.query_id, l.attempt_count, count(a.id) as saved_attempts
from analytics.query_log l
left join analytics.query_attempt a on a.query_log_id = l.id
where l.query_id is not null
group by l.id
having count(a.id) <> l.attempt_count;
```

Despues de una consulta real, buscar el `query_id` recibido en esta tabla para
comprobar el circuito desplegado. La ausencia de errores recientes no demuestra
por si sola que el guardado funcione.

La prueba `MIRA-API/tests/test_audit_postgres_integration.py` fuerza siete tipos
de resultado en el pipeline y verifica COMMIT, rollback e idempotencia contra
PostgreSQL. Solo crea tablas temporales de sesion; no escribe registros ni
cuotas de produccion. Desde MIRA-API, con dependencias de pruebas instaladas y
el checkout MIRA-ETL como hermano:

```bash
# Usar una conexion de prueba con permiso TEMP.
MIRA_TEST_AUDIT_DB_URL="$MIRA_TEST_DB_URL" \
  python -m pytest tests/test_audit_postgres_integration.py -q
```

Si la propia base de auditoria no esta disponible o rechaza la escritura,
ningun COMMIT es posible: la API deja `audit_write_failed` con el `query_id`
en los logs del servidor. No hay una cola duradera que recupere esos registros
posteriormente. Las pruebas locales no acreditan el esquema, los permisos ni
el despliegue actual de DigitalOcean.

## Comprobar el estado original de adjudicaciones

`query.v_awards` incluye estados de adjudicacion `active`/`complete` publicados
por la fuente y excluye procedimientos cancelados, desiertos o suspendidos.
Cuando no hay estado individual, usa el estado de negocio del procedimiento
(`AWARDED`, `CONTRACTED`, `COMPLETED`). La calidad y normalizacion del ETL no
intervienen. Este filtro se aplica antes de ORDER BY, LIMIT y agregaciones.
Solo las peticiones explicitas sobre otros estados usan `query.v_awards_all`.

El SQL recupera estados OCDS de los payloads originales ya guardados cuando
puede emparejarlos por identificador; las siguientes cargas tambien conservan
ese estado. No inventa un estado activo para datos desconocidos. Una fuente
que no publique ese dato solo permite usar la evidencia del procedimiento.

```sql
-- Inspeccionar el estado original y el resultado de la regla.
select p.process_number, p.source_status, a.source_award_id,
       a.award_status, a.process_status, a.is_valid_award, a.awarded_amount
from query.v_awards_all a
join query.v_process p on p.process_id = a.process_id
where p.country_code = 'GT'
order by a.awarded_amount desc nulls last
limit 20;

-- Ranking por moneda, excluyendo estados no validos desde la vista.
select p.process_number, a.source_award_id, a.award_status,
       a.process_status, a.awarded_amount, a.currency_code
from query.v_awards a
join query.v_process p on p.process_id = a.process_id
where p.country_code = 'GT' and a.currency_code = 'GTQ'
order by a.awarded_amount desc nulls last
limit 1;
```

Una adjudicacion vigente no demuestra por si sola pago o ejecucion fisica
completa; eso requiere informacion adicional de la fuente.
