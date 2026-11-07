#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
tag=swarm-oscar:baseline-v1
container=swarm-oscar-baseline
manifest=baseline.json

owned_container() {
    if docker container inspect "$container" >/dev/null 2>&1; then
        [[ $(docker inspect --format '{{index .Config.Labels "swarm-alignment.environment"}}' "$container") == baseline ]] \
            || { echo "Container $container is not owned by this script; refusing to change it." >&2; exit 1; }
        return 0
    fi
    return 1
}

image_id() {
    python3 - "$manifest" "$tag" <<'PY'
import json, subprocess, sys
with open(sys.argv[1]) as file:
    record = json.load(file)
actual = subprocess.check_output(['docker', 'image', 'inspect', sys.argv[2], '--format', '{{.Id}}'], text=True).strip()
if record['tag'] != sys.argv[2] or actual != record['image_id']:
    raise SystemExit('Baseline tag differs from the recorded image ID; refusing to start a different baseline.')
print(actual)
PY
}

start() {
    local id
    id=$(image_id)
    if owned_container; then
        [[ $(docker inspect --format '{{.Image}}' "$container") == "$id" ]] \
            && [[ $(docker inspect --format '{{json .Mounts}}' "$container") == '[]' ]] \
            || { echo 'Existing container differs from the baseline or has mounts; inspect it before continuing.' >&2; exit 1; }
        docker start "$container" >/dev/null
    else
        docker run -d --name "$container" --init --memory 2g \
            --label swarm-alignment.environment=baseline \
            -p 127.0.0.1:8081:8000 "$id" >/dev/null
    fi
    for ((attempt=0; attempt<60; attempt++)); do
        if [[ $(docker inspect --format '{{.State.Health.Status}}' "$container") == healthy ]]; then
            echo 'Baseline shop: http://localhost:8081/'
            return
        fi
        if [[ $(docker inspect --format '{{.State.Running}}' "$container") != true ]]; then
            docker logs --tail 30 "$container" >&2
            return 1
        fi
        sleep 1
    done
    echo 'Baseline did not become healthy within 60 seconds.' >&2
    docker logs --tail 30 "$container" >&2
    return 1
}

case "${1:-help}" in
    build)
        if docker image inspect "$tag" >/dev/null 2>&1 || [[ -e "$manifest" ]]; then
            echo 'baseline-v1 already exists; refusing to overwrite this saved baseline.' >&2
            exit 1
        fi
        python3 download_source.py
        source images.env
        docker build --target baseline --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" \
            --build-arg "NODE_IMAGE=$NODE_IMAGE" -t "$tag" .
        python3 - "$tag" "$manifest" <<'PY'
from datetime import datetime, timezone
import json, pathlib, subprocess, sys
tag = sys.argv[1]
image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]
record = {
    'tag': tag,
    'image_id': image['Id'],
    'oscar_revision': image['Config']['Labels']['org.opencontainers.image.revision'],
    'created_at_utc': datetime.now(timezone.utc).isoformat(),
    'data': 'Fresh upstream sample fixtures; no development-session orders.',
}
pathlib.Path(sys.argv[2]).write_text(json.dumps(record, indent=2) + '\n')
PY
        ;;
    start) start ;;
    fresh)
        image_id >/dev/null
        if owned_container; then docker rm -f "$container" >/dev/null; fi
        start
        ;;
    stop) if owned_container; then docker stop "$container"; fi ;;
    logs) docker logs -f "$container" ;;
    status) docker container inspect --format '{{.Name}} {{.State.Status}} {{.State.Health.Status}} mounts={{json .Mounts}}' "$container" ;;
    *) echo 'Usage: ./baseline.sh {build|start|fresh|stop|logs|status}' ;;
esac
