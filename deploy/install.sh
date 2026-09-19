#!/usr/bin/env bash
# Instala sin habilitar horarios: primero verificar BD y retirar el scheduler anterior.
set -euo pipefail
test "$(id -u)" = 0
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test "$project_dir" = /opt/mira-etl
cd "$project_dir"
test -f .env
chmod 600 .env
docker compose version
docker compose --env-file .env config --quiet
install -d -m 0750 -o 10001 -g 10001 data/work
docker compose --env-file .env build
docker compose --env-file .env run --rm -T etl check-db </dev/null
chmod 755 deploy/mira-etl.sh
install -m 0644 deploy/mira-etl@.service deploy/mira-etl-monthly.timer deploy/mira-etl-daily.timer /etc/systemd/system/
ln -sfn /opt/mira-etl/deploy/mira-etl.sh /usr/local/bin/mira-etl
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/mira-etl@.service /etc/systemd/system/mira-etl-*.timer
echo 'Instalado. Tras retirar el scheduler de GitHub, ejecutar: mira-etl resume'
