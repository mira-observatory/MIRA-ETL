# Preparacion y recuperacion de PostgreSQL

MIRA-ETL mantiene la estructura (`sql/`) y la documentacion de roles. Esta guia
cubre la base; no instala los servidores ni programa el ETL. Ver los despliegues de
[MIRA-API](https://github.com/mira-observatory/MIRA-API/blob/main/docs/digitalocean.md) y
[MIRA-WEB](https://github.com/mira-observatory/MIRA-WEB/blob/main/docs/operations-and-recovery.md).

## Ambiente actual

PostgreSQL 18 administrado por DigitalOcean: cluster `mira-db-prod`, base `mira`,
puerto `25060`, con TLS. Obtener host y CA desde Connection Details del panel.

## Si solo cambian los servidores de aplicacion

Conservar la BD, agregar el nuevo backend a Trusted Sources y configurar los DSN.
No recrear esquemas, roles ni contrasenas. Retirar accesos anteriores despues de
validar el traslado.

## Si la base es nueva y esta vacia

1. Crear PostgreSQL administrado y la base `mira`. Autorizar temporalmente la
   maquina administradora y autorizar el backend en Trusted Sources.
2. En una maquina con Python 3.11+ y soporte para entornos virtuales, clonar este
   repo y elegir el commit aprobado. Desde el repo en Linux:

   ```bash
   bash scripts/install.sh
   ```

3. El instalador crea `.env` desde la unica plantilla `.env.example` si falta.
   Completar `SUPABASE_DB_URL` con un usuario autorizado a crear los objetos.
   El nombre es historico: acepta PostgreSQL de DigitalOcean. La CLI carga
   `.env`, no `.env.digitalocean`. Protegerlo con `chmod 600 .env`.
4. Crear estructura y semillas con el comando existente:

   ```bash
   bash scripts/init_db.sh
   ```

5. Aplicar roles y permisos siguiendo **unicamente**
   [database_security.md](database_security.md), como propietario de los objetos.
   Alli estan los pasos de `mira_query`, `mira_web` y `mira_logger`; no se mantiene
   un segundo SQL con esos mismos permisos.
6. Guardar contrasenas distintas por rol en la boveda y entregar los DSN al
   administrador de la API. Verificar permisos segun esa guia y conexiones de API.

Instalar el ETL, crear estructura y asignar permisos/secretos son operaciones
separadas: los roles no se crean en cada carga. `init-db` no recupera las
contrataciones ni los registros de auditoria de una base perdida.

## Si tambien se trasladan los datos

Se necesita backup y restauracion, no solo `init-db`:

1. Acordar responsables, frecuencia, retencion y ubicacion del backup administrado
   o exportacion con `pg_dump`. Usar herramientas compatibles con PostgreSQL 18.
2. Definir el corte de escrituras de API/ETL para evitar perder cambios posteriores
   al backup. Preparar la base de destino y su conectividad.
3. Restaurar primero en una base separada. Verificar extensiones, propietarios,
   permisos, conteos, cobertura y auditoria. Un dump de la base no incluye por si
   solo todos los roles del cluster: revisar la guia existente. Los privilegios
   por defecto dependen del usuario que crea los objetos.
4. Probar los tres usuarios desde la API y una carga controlada del ETL. Tras el
   corte acordado, actualizar DSN y validar. Conservar el origen durante el plazo
   de recuperacion acordado. No restaurar sobre produccion como prueba.

La politica de backup del cluster y una restauracion real estan pendientes de
verificacion. Esta guia no acredita un respaldo externo probado; el comando
exacto de restauracion depende del mecanismo de backup elegido.

## Custodia y limites

El equipo tiene acceso a Git y DigitalOcean; falta elegir boveda y al menos dos
custodios. Guardar acceso administrativo/ETL, credenciales de API, identificador
del cluster, ubicacion de backups y claves de cifrado si corresponden. No guardar
contrasenas ni dumps de produccion en Git.

Los SQL versionados recrean estructura/semillas. El ETL se ejecuta con Docker y
timers de Ubuntu en el servidor de MIRA-API; ver [operacion](digitalocean.md).
Publicar los cambios en Git es necesario para que el equipo disponga de las guias.
