# GitHub Actions y despliegue de MIRA-ETL

## Pipeline automatico de GitHub Actions

El workflow `.github/workflows/ci.yml` verifica cada pull request y cada push a
`main`. Solo `main` publica una imagen en `ghcr.io/mira-observatory/mira-etl`,
con etiqueta `sha-COMMIT`; el servidor descarga el digest exacto generado.
Actions construye la imagen: el Droplet no compila ni necesita clonar GitHub.
Ademas de la imagen se transfiere un paquete pequeno con Compose y `deploy/`.
Las fuentes que pudiera haber en `/opt/mira-etl` no representan necesariamente
la imagen activa: consultar `/var/lib/mira-deploy/mira-etl/current.txt`.

El job de deploy usa el environment `digitalocean-production`, restringido a
`main`, sin aprobacion manual. La variable **del repositorio**
`DEPLOY_ENABLED=true` habilita despliegues; ponerla en `false` conserva CI y
publicacion de imagenes pero omite el cambio del servidor.

Configuracion en GitHub (Settings):
- Environment `digitalocean-production`: variable `DEPLOY_HOST=104.131.184.162`.
- En ese environment: secretos `DEPLOY_SSH_KEY` y `DEPLOY_KNOWN_HOSTS`.
- `DEPLOY_SSH_KEY` es una llave Ed25519 exclusiva de este repositorio. Su parte
  publica va en `/root/.ssh/authorized_keys` del destino con prefijo `restrict`.
  No es la llave personal de ningun integrante.
- `DEPLOY_KNOWN_HOSTS` contiene la clave publica del host SSH, verificada contra
  la huella obtenida desde la consola de DigitalOcean. No usar
  `StrictHostKeyChecking=no` ni confiar ciegamente en `ssh-keyscan`.
- GitHub genera `GITHUB_TOKEN` por job: `packages:write` para publicar,
  `packages:read` para descargar. El servidor recibe ese token por stdin y
  elimina el directorio temporal de autenticacion al terminar. No necesita PAT
  permanente para el registro.
- Los secretos de la aplicacion siguen en `.env`
  del servidor; nunca se incluyen en la imagen o el paquete de despliegue.

Quien pueda modificar `main` o los secretos de deploy puede ejecutar un
despliegue con acceso administrativo al servidor. Limitar esos permisos al equipo
responsable; las llaves separadas permiten revocar un repositorio sin compartir
la llave personal. Un administrador de GitHub/DigitalOcean puede reemplazarlas:
generar otra llave, autorizar la publica, actualizar el secreto y retirar la
publica anterior tras validar. No depende de esta computadora.

`deploy/release.sh` descarga la imagen, conserva Compose/configuracion anterior
y los campos de version (sin copiar contrasenas), aplica la nueva version y
comprueba el contrato de la base con `check-db`, sin cargar datos.
Si falla despues de cambiar la configuracion, intenta restaurar automaticamente
la version anterior y el job queda fallido. Revisar los logs si tambien falla
la restauracion. Conserva los timers habilitados o deshabilitados como estaban. No habilita horarios nuevos, no ejecuta migraciones ni inicia una carga. Si hay cargas activas o esperando, termina con codigo 75 y mantiene la version actual.

Operacion:
1. Publicar cambios mediante push/merge a `main`; seguir **Actions → CI and DigitalOcean**.
2. Para reintentar, usar **Run workflow** sobre `main`. Una ejecucion de un
   commit antiguo no puede desplazar una version mas reciente de `main`.
3. Para volver a la version anterior, desde la consola del servidor:
   `bash /opt/mira-etl/deploy/release.sh rollback`.
   Conserva las imagenes actual y anterior: no ejecutar un prune que las elimine.
4. Para detener temporalmente el deploy automatico, cambiar `DEPLOY_ENABLED`
   a `false`. Esto no pausa las cargas programadas; para eso existe `mira-etl pause`.

Al trasladar el servicio, preparar el nuevo host con la guia de este repositorio
y sus secretos, autorizar una llave nueva, verificar su huella y actualizar
`DEPLOY_HOST`/`DEPLOY_KNOWN_HOSTS`. Ejecutar el workflow sobre `main`.
No copiar llaves privadas de host ni carpetas temporales de la PC.
El rollback de aplicacion no revierte cambios de esquema: estos se revisan y
aplican por separado desde MIRA-ETL. El workflow antiguo de cargas del ETL
permanece deshabilitado; los horarios siguen en systemd.
