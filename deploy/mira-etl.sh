#!/usr/bin/env bash
# Consola unica; las cargas manuales y programadas pasan por el mismo bloqueo.
set -euo pipefail
project_dir="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "$project_dir"
compose=(docker compose --env-file .env)
usage() {
    echo 'Uso: mira-etl {sources|status|history [N]|check|logs [UNIDAD]|pause|resume}'
    echo '     mira-etl run --source FUENTE [--period YYYYMM-YYYYMM] [--force-reprocess]'
    echo '     mira-etl {monthly|daily|stop UNIDAD}'
}
[[ $# -gt 0 ]] || { usage; exit 2; }
action="$1"; shift
case "$action" in
    sources) exec "${compose[@]}" run --rm -T etl sources </dev/null ;;
    history) exec "${compose[@]}" run --rm -T etl history --limit "${1:-20}" </dev/null ;;
    check) exec "${compose[@]}" run --rm -T etl check-db </dev/null ;;
    status)
        systemctl list-timers --all 'mira-etl*' --no-pager
        systemctl list-units --all 'mira-etl*' --no-pager
        docker ps --filter name=mira-etl-worker --format '{{.Names}} {{.Status}}'
        df -h "$project_dir/data/work"
        exit ;;
    logs) exec journalctl -u "${1:-mira-etl*}" -n 100 --no-pager ;;
    pause) exec systemctl disable --now mira-etl-monthly.timer mira-etl-daily.timer ;;
    resume) exec systemctl enable --now mira-etl-monthly.timer mira-etl-daily.timer ;;
    stop)
        [[ ${1:-} =~ ^mira-etl(@(monthly|daily)|-manual-[0-9-]+)(\.service)?$ ]] || { usage; exit 2; }
        exec systemctl stop "$1" ;;
    monthly|daily) exec systemctl start --no-block "mira-etl@$action.service" ;;
    run)
        [[ $# -gt 0 ]] || { usage; exit 2; }
        unit="mira-etl-manual-$(date -u +%Y%m%d%H%M%S)-$$"
        systemd-run --unit="$unit" --description='MIRA ETL manual' \
            --property=Type=exec --property=RuntimeMaxSec=30h --property=TimeoutStopSec=90s \
            --property=UMask=0077 --property=Nice=10 \
            --working-directory="$project_dir" \
            "$project_dir/deploy/mira-etl.sh" _execute run "$@"
        echo "Trabajo enviado: $unit.service (no implica que ya termino)."
        echo "Ver avance: journalctl -u $unit.service -f"
        exit ;;
    _execute) ;;
    *) usage; exit 2 ;;
esac

mode="${1:?Falta modo interno}"; shift
# Calcular el mes antes de esperar por el bloqueo: no cambiarlo al cruzar de mes.
previous_month="$(date -u -d "$(date -u +%Y-%m-01) -1 month" +%Y%m)"
exec 9>/run/lock/mira-etl.lock
echo "Esperando turno: $mode (maximo 6 horas)."
flock -w 21600 9 || { echo 'ERROR: no se obtuvo turno en 6 horas.'; exit 75; }
run_dir="$(mktemp -d "$project_dir/data/work/run.XXXXXXXX")"
chown 10001:10001 "$run_dir"
chmod 750 "$run_dir"
stop_container() {
    docker stop -t 45 mira-etl-worker >/dev/null 2>&1 || true
    docker rm -f mira-etl-worker >/dev/null 2>&1 || true
}
cleanup() {
    stop_container
    # Solo la carpeta unica creada por esta ejecucion, bajo data/work.
    [[ "$run_dir" == "$project_dir/data/work/run."* ]] && rm -rf -- "$run_dir"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
# Una caida del host pudo dejar un contenedor detenido; nunca borrar otro activo.
if [[ "$(docker inspect -f '{{.State.Running}}' mira-etl-worker 2>/dev/null || true)" == true ]]; then
    trap - EXIT
    rmdir "$run_dir"
    echo 'ERROR: existe un worker activo fuera del bloqueo; revisar antes de continuar.'
    exit 75
fi
docker rm mira-etl-worker >/dev/null 2>&1 || true

run_source() {
    local attempt rc=0 child
    for attempt in 1 2; do
        if (( $(df -Pk "$project_dir/data/work" | awk 'NR==2 {print $4}') < 8388608 )); then
            echo 'ERROR: se requieren al menos 8 GiB libres para iniciar una carga.'
            return 1
        fi
        echo "Inicio intento $attempt: $*"
        timeout --signal=TERM --kill-after=60s 6h \
            "${compose[@]}" run --rm --name mira-etl-worker -T etl run "$@" \
            --work-dir "/work/${run_dir##*/}" </dev/null &
        child=$!
        if wait "$child"; then
            echo 'Carga terminada correctamente.'
            return 0
        else
            rc=$?
        fi
        stop_container
        echo "ERROR: intento $attempt termino con codigo $rc."
        # No repetir errores de argumentos, agotamiento de memoria o cancelaciones.
        case "$rc" in 2|124|137|143) return "$rc" ;; esac
        if [[ $attempt = 1 ]]; then sleep 60 & wait $!; fi
    done
    return "$rc"
}
result=0
case "$mode" in
    daily) run_source --source nicaragua_siscae || result=$? ;;
    monthly)
        for source in guatemala_guatecompras costa_rica_sicop honduras_oncae; do
            run_source --source "$source" --period "$previous_month" || result=1
        done ;;
    run) run_source "$@" || result=$? ;;
    *) echo 'Modo interno invalido.'; exit 2 ;;
esac
echo "Fin del trabajo $mode; codigo=$result"
exit "$result"
