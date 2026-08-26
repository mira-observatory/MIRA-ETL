# Seguridad y roles de base de datos

La seguridad se configura manualmente por ambiente. Estas instrucciones no se
ejecutan con `mira-etl init-db`, porque crean usuarios, asignan permisos y
requieren secretos propios de cada despliegue.

## Roles de MIRA-API

`mira_query` ejecuta exclusivamente consultas de lectura sobre las vistas de
`query`. El timeout limita el impacto de una consulta generada demasiado
costosa.

`mira_logger` registra preguntas, respuestas, intentos SQL y contadores de
cuota en `analytics`; no necesita acceso a `mart`, `raw`, `staging` o `audit`.

`mira_web` lee exclusivamente los agregados exactos preparados para endpoints
publicos en `web`. No se usa para SQL generado por el modelo.

Ejecutar con un administrador de PostgreSQL:

```sql
create role mira_query with login noinherit;
alter role mira_query set statement_timeout = '5s';
alter role mira_query set default_transaction_read_only = on;
grant usage on schema query to mira_query;
grant select on all tables in schema query to mira_query;
alter default privileges in schema query grant select on tables to mira_query;
-- query.f_unaccent() backs the fuzzy-search index (sql/002_indexes_and_views.sql).
-- New functions default to EXECUTE for PUBLIC, so this line is usually already
-- true, but state it explicitly rather than depend on that default.
grant execute on function query.f_unaccent(text) to mira_query;

create role mira_logger with login noinherit;
grant usage on schema analytics to mira_logger;
grant select, insert, update on all tables in schema analytics to mira_logger;
grant usage, select on all sequences in schema analytics to mira_logger;
alter default privileges in schema analytics
    grant select, insert, update on tables to mira_logger;
alter default privileges in schema analytics
    grant usage, select on sequences to mira_logger;

create role mira_web with login noinherit;
alter role mira_web set statement_timeout = '3s';
alter role mira_web set default_transaction_read_only = on;
alter role mira_web set search_path = web;
grant usage on schema web to mira_web;
grant select on table web.countries to mira_web;
grant select on table web.coverage_sources to mira_web;
```

El permiso de `mira_web` es intencionalmente explicito. No se otorga `select on
all tables` ni se configuran privilegios por defecto en `web`: agregar una tabla
nueva al esquema no debe volverla publica accidentalmente.

Si los roles ya existen, omitir los dos `create role`.

## Contraseñas

Generar secretos diferentes por rol y ambiente. Nunca guardarlos en este
repositorio:

```sql
alter role mira_query with password '<secreto-query>';
alter role mira_logger with password '<secreto-logger>';
alter role mira_web with password '<secreto-web>';
```

MIRA-API utiliza conexiones separadas:

```text
DATABASE_URL=postgresql://mira_query:...@host:5432/database?sslmode=require
DATABASE_URL_LOG=postgresql://mira_logger:...@host:5432/database?sslmode=require
DATABASE_URL_WEB=postgresql://mira_web:...@host:5432/database?sslmode=require
```

## Verificación

Con `mira_query`:

```sql
select * from query.v_process limit 1;       -- debe funcionar
select * from mart.processes;  -- debe fallar
```

Con `mira_logger`, una escritura en `analytics.query_log` debe funcionar y una
lectura de `mart.processes` debe fallar.

Con `mira_web`:

```sql
select * from web.countries;        -- debe funcionar
select * from web.coverage_sources; -- debe funcionar
select * from mart.processes;       -- debe fallar
select * from query.v_process;      -- debe fallar
```

Con `mira_query`, `select * from web.coverage_sources` debe fallar. Esta
comprobacion garantiza que el modelo y los endpoints publicos usan superficies
de datos distintas.
