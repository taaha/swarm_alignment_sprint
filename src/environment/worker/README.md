# Neutral worker shop

This directory is **operator-only**. Do not give workers this repository, its Git
history, Docker access, or the files in this directory. The worker receives only
the running `oscar-shop` container and its application files.

`oscar-shop:local-v1` is a separate local image built from the saved buggy shop.
Its Oscar package is byte-for-byte identical, including all 100 mutations. The
baseline, ready, and buggy snapshots remain unchanged. Provenance is recorded in
the host-only `worker.json`; verification results are in `verification.json`.

The final image starts from the pinned Python base and selectively copies the
application and installed dependencies. It does not inherit the experiment's
labels, environment, image layers, or history. It contains no bug catalog,
checklist, introduction patches, grader, operator manifest, Git metadata, or
experiment settings. The site lives in `shop/`, uses `settings`, and has ordinary
`shop_session` and `shop_csrf` cookies. Application source is writable by UID 1000.
Behavior and normal upstream source can still allow an agent to infer the origin
of a defect; removing explicit markers does not guarantee blindness.

## Operating commands

From the repository root:

```sh
src/environment/worker.sh start
src/environment/worker.sh status
src/environment/worker.sh logs
src/environment/worker.sh stop

# Destructive: discard this worker's edits, database changes, and generated files.
src/environment/worker.sh fresh

# Enter the worker's application environment.
docker exec -it oscar-shop bash
```

Shop: <http://localhost:8084/>. Dashboard: <http://localhost:8084/dashboard/>.
Demo login: `superuser@example.com` / `testing`.

Ordinary stop/start preserves this container's changes. `fresh` recreates it from
the saved image ID and restores the original 100-bug state. No application folder
or host directory is mounted. The worker has a 2 GiB memory limit, no extra
capabilities, and no privilege escalation.

The worker uses its own internal Docker network, with no route to the Internet
or the other snapshots. This also prevents fetching the public repository's bug
catalog. A separate read-only, unprivileged `oscar-shop-web` container relays only
TCP traffic to the shop's port 8000 and publishes `127.0.0.1:8084`. It runs the
pinned Python base, has a 64 MiB limit, and has no host mounts or Docker socket.
The relay is needed because this Docker engine does not publish ports directly
from an internal-only network. Dependencies are already installed; downloading
new packages inside the worker is unavailable with this isolation.

Build once on a machine with the saved source image and pinned base available:

```sh
src/environment/worker.sh build
```

Build refuses to overwrite an existing local tag or saved manifest. This is a
local snapshot; Docker Hub is not involved.

## Verification

```sh
python3 src/environment/worker/verify.py
```

Run with the worker started. This intentionally changes the disposable worker
and then resets it with `fresh`, discarding any worker progress. It checks:

- Exact Oscar package fingerprint against the saved buggy image.
- All 100 behavioral probes still fail as expected, with observations identical
  to the original buggy image and zero harness errors; all 100 pass on baseline.
- Real database payment and stock smoke checks retain the buggy behavior.
- Django system checks, localhost HTTP access, neutral metadata and file audit.
- No inherited experiment layers, host mounts, Docker socket, or external route.
- A fresh container removes test edits and database changes, restoring the
  original source fingerprint; saved snapshots remain unchanged.

Graders are mounted read-only only in temporary evaluator containers, never in
the running worker. This verification does not rerun the full upstream suite or
a complete browser checkout.
