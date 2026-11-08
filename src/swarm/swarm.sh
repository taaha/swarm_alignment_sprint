#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export SWARM_OUTPUT_UID="${SWARM_OUTPUT_UID:-$(id -u)}"
export SWARM_OUTPUT_GID="${SWARM_OUTPUT_GID:-$(id -g)}"
action="${1:-status}"
if [ "$#" -gt 0 ]; then shift; fi
case "$action" in
  build) exec docker compose build controller ;;
  up) exec docker compose up -d mlflow ;;
  verify) exec docker compose run --rm --no-deps --entrypoint python controller -m unittest discover -s tests -v ;;
  run|resume-election|status|export|cleanup) exec docker compose run --rm controller "$action" "$@" ;;
  *) echo "Usage: $0 {build|up|verify|run|resume-election|status|export|cleanup} [options]" >&2; exit 2 ;;
esac
