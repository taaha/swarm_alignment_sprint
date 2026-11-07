#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
compose() { docker compose --env-file images.env "$@"; }
source_download() {
    python3 download_source.py
}
initialize() {
    compose run --rm --no-deps shop sh -ec '
        npm ci --no-audit --no-fund
        npm run build
        python /opt/environment/seed.py
        python sandbox/manage.py check
    '
}
case "${1:-help}" in
    setup) source_download; compose build; initialize ;;
    start) compose up -d --wait ;;
    stop) compose down ;;
    logs) compose logs -f shop ;;
    check) compose run --rm --no-deps shop python sandbox/manage.py check ;;
    test)
        shift
        compose run --rm --no-deps -e DATABASE_NAME=:memory: shop \
            python -m pytest --ds=tests.settings tests/ "$@"
        ;;
    lock)
        compose run --rm --no-deps -T shop pip freeze --exclude-editable > requirements.lock.tmp
        mv requirements.lock.tmp requirements.lock
        ;;
    reset)
        [[ ${2:-} == --confirm ]] || { echo 'Destructive reset requires: ./environment.sh reset --confirm' >&2; exit 1; }
        compose down
        compose run --rm --no-deps shop python /opt/environment/seed.py --reset
        ;;
    *) echo 'Usage: ./environment.sh {setup|start|stop|logs|check|test [pytest args]|lock|reset --confirm}' ;;
esac
