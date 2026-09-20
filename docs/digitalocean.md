# Operar MIRA-ETL desde el servidor

El ETL esta instalado en **mira-app-prod**, el mismo Droplet que MIRA-API:
`104.131.184.162`, directorio `/opt/mira-etl`. Entrar por SSH con una llave propia
o por Web Console de DigitalOcean. Los ejemplos asumen usuario `root`; con otro
administrador, usar `sudo`.

Docker ejecuta cada carga y termina. Ubuntu/systemd mantiene los horarios y
supervisa las ejecuciones. No hay una API HTTP ni una interfaz web del ETL.
No usar `docker compose up` para dejarlo corriendo permanentemente.

## Comandos cotidianos

El comando `mira-etl` funciona desde cualquier directorio del servidor:

```bash
mira-etl sources       # Fuentes configuradas en la imagen desplegada
mira-etl status        # Horarios, trabajos activos/fallidos y espacio disponible
mira-etl history 20    # Ultimas 20 ejecuciones registradas en PostgreSQL
mira-etl logs          # Ultimas 100 lineas de todos los trabajos
mira-etl check         # Conexion y esquema: solo lectura, no aplica SQL
```

`history` consulta `audit.etl_runs`, con fuente, periodo, estado e inicio/fin.
Las fechas de auditoria se muestran con su zona horaria (actualmente UTC).
Un periodo omitido por estar completo aparece en logs como `SKIPPED` y no crea
otra fila de auditoria. Un fallo anterior a conectar con PostgreSQL solo tendra logs.

## Cargas manuales

Un mes de Guatemala, Costa Rica o Honduras:

```bash
mira-etl run --source guatemala_guatecompras --period 202608
mira-etl run --source costa_rica_sicop --period 202608
mira-etl run --source honduras_oncae --period 202608
```

Carga historica, incluyendo ambos extremos:

```bash
mira-etl run --source honduras_oncae --period 202401-202412
```

Nicaragua consulta el estado actual, sin periodo historico:

```bash
mira-etl run --source nicaragua_siscae
```

Cada orden devuelve el nombre de una unidad como
`mira-etl-manual-20260918230607-638886.service`. **Significa que se envio el trabajo,
no que termino correctamente.** El servidor continua aunque se cierre la consola.
Conservar el nombre para consultar o detener ese trabajo:

```bash
# Sustituir UNIDAD por el nombre completo devuelto.
journalctl -u UNIDAD -f
systemctl status UNIDAD --no-pager
mira-etl logs UNIDAD
mira-etl stop UNIDAD
```

`Ctrl+C` al seguir logs solo cierra la visualizacion. `stop` cancela el trabajo.
Una unidad manual terminada correctamente puede desaparecer de `systemctl`;
sus logs y la auditoria siguen disponibles.

Guatemala, Costa Rica y Honduras omiten periodos que ya tengan `SUCCESS`. Para
actualizar deliberadamente uno ya cargado:

```bash
mira-etl run --source costa_rica_sicop --period 202608 --force-reprocess
```

Nicaragua siempre vuelve a consultar el estado actual. Su enriquecimiento de
adjudicaciones esta acotado por defecto; se puede ajustar, por ejemplo:

```bash
mira-etl run --source nicaragua_siscae --award-detail-limit 50
```

Para pruebas pequenas existe `--limit N`, pero **no usarlo en cargas historicas
normales**: el ETL actual registra `SUCCESS` tambien para una carga limitada y
despues omite ese mes; haria falta repetirlo sin limite y con `--force-reprocess`.
Los datos se guardan por lotes: cancelar o fallar no revierte toda la carga.
Reprocesar puede agregar filas de auditoria/raw aunque los datos de negocio se
actualicen mediante sus identificadores.

## Automatizacion instalada

Se conservaron los horarios y periodos del workflow anterior:

| Trabajo | Hora de Guatemala | UTC | Datos |
| --- | --- | --- | --- |
| `mira-etl-monthly.timer` | Dia 3 de cada mes, 01:20 | Dia 3, 07:20 | Mes anterior: Guatemala, Costa Rica, Honduras, en ese orden |
| `mira-etl-daily.timer` | Todos los dias, 02:20 | Todos los dias, 08:20 | Estado actual de Nicaragua |

Los horarios estan definidos en UTC; el servidor puede mostrar UTC en `status`.
Ejemplo: el 3 de octubre de 2026 el trabajo mensual selecciona `202609`.
No se fuerza el reprocesamiento automatico de meses ya completos.

Ejecutar ahora la misma operacion programada:

```bash
mira-etl daily
mira-etl monthly
journalctl -u mira-etl@daily.service -f
journalctl -u mira-etl@monthly.service -f
```

Pausar o volver a habilitar ambos horarios:

```bash
mira-etl pause
mira-etl resume
```

Pausar no detiene trabajos activos ni manuales que esten esperando. Para detener
uno usar su unidad con `mira-etl stop`, por ejemplo:

```bash
mira-etl stop mira-etl@monthly.service
```

Los timers usan `Persistent=true`: tras una pausa/apagado pueden disparar una
ejecucion pendiente al reactivarse. No reproducen cada fecha perdida ni reconstruyen
todos los meses omitidos. El mensual calcula el mes anterior a la fecha real de
inicio; un atraso mayor requiere indicar los meses faltantes manualmente.
Una carga interrumpida por reinicio requiere revisar auditoria/logs y relanzarla.

El workflow GitHub `MIRA ETL` (ID `327200993`) se deshabilito al migrar. Ademas,
se retiraron sus eventos `schedule` del archivo versionado; se conserva como
referencia del flujo anterior. No habilitar dos programadores para la misma BD.
Sus secretos anteriores no se modificaron ni se usan en el servidor.

## Concurrencia, limites y errores

- Todas las cargas administradas con `mira-etl` comparten un bloqueo: solo una
  trabaja a la vez, incluidas las manuales. Una nueva espera hasta 6 horas;
  si no obtiene turno, falla con codigo 75. No es una cola durable ni garantiza FIFO.
- El trabajo mensual intenta las tres fuentes aunque una falle; al final queda
  fallido si alguna no termino. Las que tuvieron exito se omiten al repetirlo.
- Cada invocacion de una fuente/rango tiene hasta 6 horas por intento. Un error
  permite un segundo intento tras 60 segundos, salvo errores de argumentos,
  agotamiento de memoria, timeout o cancelacion. Un rango grande puede requerir
  dividirlo en grupos de meses. La unidad completa tiene un limite de 30 horas.
- El worker usa como maximo 1 CPU y 1536 MiB, sin swap, sin puertos publicados
  y con usuario de contenedor sin privilegios. No elimina la competencia de E/S
  o de consultas con la API; revisar recursos al aumentar volumen.
- Se exige al menos 8 GiB libres al iniciar cada intento. Las descargas se crean
  bajo `data/work/run.*` y se eliminan al finalizar; no son backups de los datos.
- `SIGTERM` permite registrar la cancelacion. Un corte electrico, OOM o SIGKILL
  puede dejar auditoria `RUNNING`: contrastarla con los servicios y logs. No
  asumir que una fila `RUNNING` prueba que el proceso siga vivo.
- Los logs quedan en el journal persistente de Ubuntu, sujeto a su retencion.
  El historial de PostgreSQL permanece en la BD. No hay alertas por correo/Slack
  configuradas: el administrador debe revisar los fallos.

Diagnostico:

```bash
mira-etl status
mira-etl history 30
mira-etl logs
docker stats --no-stream
curl --fail http://127.0.0.1:8080/healthz   # Salud de MIRA-API
```

Si una fuente no publico el archivo, tiene problemas de red o cambio su formato,
el scheduler no lo corrige: revisar el error y repetir el periodo cuando proceda.
No saltarse el bloqueo usando `docker compose run ... run` para cargas ordinarias.
Una caida abrupta puede dejar carpetas `data/work/run.*`; retirarlas solo tras
confirmar que no hay servicios ni worker activos.

## Cambiar horarios o fuentes

Los horarios se mantienen en `deploy/mira-etl-daily.timer` y
`deploy/mira-etl-monthly.timer`, dentro de este repositorio. Tras modificar y
publicar la version aprobada, instalar en el servidor:

```bash
cd /opt/mira-etl
install -m 0644 deploy/mira-etl-*.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/mira-etl-*.timer
systemctl daemon-reload
systemctl restart mira-etl-daily.timer mira-etl-monthly.timer
mira-etl status
```

Esto reactiva timers; si deben quedar pausados, no ejecutar `restart`.
Las fuentes estan en `config/sources/*.json`, incluidas en la imagen. Cambiarlas
requiere reconstruirla. Agregar una fuente tampoco la agenda automaticamente:
actualizar la lista mensual o la operacion diaria en `deploy/mira-etl.sh`.

## Reproducir o actualizar el despliegue

Todo lo necesario esta en MIRA-ETL. El host necesita Ubuntu 24.04, Docker Engine,
Compose y systemd; en el servidor actual Docker ya pertenece a la instalacion
de MIRA-API. No se instala Nginx ni otro certificado para el ETL.

1. Colocar la version aprobada del repo en `/opt/mira-etl`. Para un servidor nuevo,
   permitirlo en Trusted Sources de PostgreSQL.
2. Crear `/opt/mira-etl/.env` desde **la unica** `.env.example`, con DSN de
   `mira_etl` y una etiqueta `MIRA_ETL_IMAGE_TAG` propia de esa version.
   La variable historica `SUPABASE_DB_URL` apunta aqui a DigitalOcean.
   Recuperar la credencial de la boveda/servidor, no de una PC especifica.
3. Preparar BD/roles segun [database_recovery.md](database_recovery.md) y
   [database_security.md](database_security.md) si todavia no existen. No repetirlos
   en cada despliegue. El usuario `mira_etl` no sirve para aplicar `init-db`.
4. Como root, ejecutar el unico instalador del servicio:

   ```bash
   cd /opt/mira-etl
   bash deploy/install.sh
   ```

5. Verificar `mira-etl check`, `sources` y una carga controlada. Tras retirar
   el programador anterior, ejecutar `mira-etl resume` y revisar `status`.

El instalador construye y valida la imagen, instala la unidad/timers y el comando
`/usr/local/bin/mira-etl`; en una instalacion nueva no activa horarios por si solo.
En una actualizacion conserva su estado previo: pausar primero y esperar a que
no haya trabajos activos o esperando antes de reemplazar archivos.

No sobrescribir `.env` al actualizar. Mantener la imagen anterior hasta validar;
para volver atras, restaurar fuentes/configuracion y etiqueta compatibles y
reaplicar las unidades. Las imagenes fijan base y dependencias; mantenerlas
actualizadas con pruebas. Nunca pasar secretos como argumentos de build.

La credencial de produccion esta en `.env`, permisos 600. El usuario de ejecucion
es `mira_etl`, no `doadmin`. Su provision y permisos se documentan solo en la guia
de seguridad existente. La boveda compartida del equipo sigue pendiente.

## Verificacion del despliegue inicial

El 18/09/2026 se construyo `mira-etl:a2302da-do1`, se verifico el esquema sin
aplicar migraciones y paso una carga limitada de Nicaragua: auditoria ID 1,
`SUCCESS`, cinco procesos normalizados. El servicio continuo sin una sesion SSH
abierta y termino limpiando su contenedor y descargas; MIRA-API siguio saludable.
Tambien pasaron las 75 pruebas del proyecto. Esta verificacion no equivale a
una carga historica completa de los cuatro paises.

Los timers quedaron habilitados el 18/09/2026, hora de Guatemala. Primera
ejecucion diaria prevista: 19/09/2026 a las 02:20; mensual: 03/10/2026 a las
01:20. Se verifico tambien que un trabajo manual espera el bloqueo y solo
crea su worker despues de liberarse, sin ejecutar cargas concurrentes.

## Correccion de rendimiento y caracteres de origen (19/09/2026)

Imagen: `mira-etl:20260919-fix1`. Se agrego `idx_award_items_item` sobre
`mart.award_items(item_id)` al SQL de inicializacion. El indice evita recorrer
la tabla de relaciones completa al comprobar la clave foranea durante el
reemplazo de articulos. En una BD existente se aplica como propietario, fuera
de una transaccion, para permitir que continuen las escrituras:

```sql
create index concurrently if not exists idx_award_items_item
    on mart.award_items (item_id);
```

Verificar que el indice este valido; `IF NOT EXISTS` no repara uno invalido ni
garantiza que su definicion coincida. No volver a ejecutar todos los SQL de
inicializacion para aplicar este cambio puntual.

El ETL reemplaza el caracter NUL real (U+0000) por U+FFFD (`�`) en los valores
guardados en columnas de texto y JSONB. No modifica la secuencia literal de
seis caracteres `\u0000`, ni altera el archivo descargado o el hash original.
Los registros afectados generan una advertencia `SOURCE_NUL_CHARACTER`, con
las rutas afectadas y el JSON original serializado como texto en
`audit.validation_results.payload.original_payload_json`. Ese texto permite
recuperar el original mediante `json.loads`; no es una eliminacion silenciosa
del caracter ni de la contratacion. En RAW/STAGING/MART se guarda la representacion
compatible con PostgreSQL; el hash sigue identificando el contenido original.

Si reemplazar caracteres haria colisionar dos claves distintas de un objeto JSON,
la carga falla expresamente en lugar de descartar una de las claves.

Pasaron 81 pruebas, incluida una carga y su reprocesamiento contra PostgreSQL 18
efimero, sin acceso a produccion. La prueba confirma que texto/JSONB se guardan,
que la evidencia original puede recuperarse y que el indice existe y es valido.

Tras actualizar se relanzaron los rangos solicitados: Guatemala `202401-202609`
y Honduras `202201-202609`, sin forzar los periodos ya completados. Un periodo
incompleto vuelve a comenzar; no se reanuda desde la ultima fila. El cambio de
imagen no modifica los horarios ni los limites documentados arriba.

Validacion en produccion: auditoria 101, Guatemala `202412`, termino `SUCCESS`
con 14,045 registros RAW/STAGING en 4 minutos y 18 segundos (tiempo de auditoria).
El intento anterior, auditoria 98, habia terminado con error tras casi 39 minutos.
Se verifico el uso del indice con `EXPLAIN` y se recupero el NUL original desde
la evidencia de `SOURCE_NUL_CHARACTER`. Esta comprobacion valida ese periodo;
los rangos historicos restantes continuan su ejecucion por separado.

## Indices del catalogo publico (19/09/2026)

El catalogo de MIRA-API necesita estos indices de `mart.processes`, definidos
en `sql/002_indexes_and_views.sql`:

- `idx_processes_publication_page`: orden por fecha descendente, nulos al final
  y desempate por `process_id`.
- `idx_processes_status`: conteo y filtro por estado normalizado.
- `idx_processes_number_trgm`, `idx_processes_title_trgm` y
  `idx_processes_description_trgm`: busquedas literales con `ILIKE` en los tres
  campos. Usan la extension `pg_trgm`, ya incluida en la inicializacion.

En una base existente, ejecutar solo las cinco definiciones nuevas como
propietario, cambiando `CREATE INDEX` por `CREATE INDEX CONCURRENTLY`, fuera
de una transaccion. Verificar `pg_index.indisvalid` y la definicion de cada
indice antes de dar la instalacion por terminada. No recrear tablas ni roles.
Estos indices se mantienen automaticamente con las siguientes cargas; no
requieren reconstruccion despues de cada ETL. Ocupan espacio y agregan trabajo
a las escrituras. Las busquedas muy cortas o que coinciden con gran parte del
catalogo todavia pueden resultar costosas.

El ajuste de consultas pertenece a MIRA-API (`db/procedures.py`): pagina y conteo
separados dentro de una sentencia y agrupacion directa de estados. Ambas partes
son necesarias; aumentar el timeout no fue la correccion aplicada.
