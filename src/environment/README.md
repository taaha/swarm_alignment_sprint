# Local Oscar shop

Oscar is a Python/Django e-commerce application. This directory contains our
Docker development environment, not the swarm controller or bug scenarios.

## Requirements and layout

Use Docker Engine with Compose v2, Bash, Python 3, and Git on the host. Python and
Node application dependencies are installed only in Docker. Allow roughly 10–15 GB
of free disk space for images, caches, source, and generated data. The running
shop has a 2 GiB memory limit.

`oscar/` is an ordinary, Git-ignored source folder pinned to Oscar 4.2.1, commit
`d0a70850abf8e4229d4971aa7e8f4643326ec9f3`. The downloader uses a temporary checkout,
verifies the commit, and removes its Git metadata before installing the folder.
There is no submodule or fork. Existing source and edits are retained; an unknown
source folder is rejected rather than overwritten.

Only our configuration, scripts, and dependency pins belong in the parent repo.
Source, SQLite database, images, search index, and compiled assets live under the
ignored `oscar/` folder. Node modules use a Docker-managed volume. Images and
build caches also live in Docker's storage.

## Usage

From the repository root:

```bash
cd src/environment
./environment.sh setup
./environment.sh start
```

Open <http://localhost:8080/>. The shop redirects to its language-prefixed URL.
The Oscar dashboard is at <http://localhost:8080/en-gb/dashboard/>.

Demo administrator: **superuser**, password **testing**
(email: **superuser@example.com**). These are public sample credentials for a
local demo. Checkout creates sample orders without real payments; email goes to
container logs. The published port listens only on `127.0.0.1`.

```bash
./environment.sh check             # Django system checks
./environment.sh test              # Upstream unit, integration, functional tests
./environment.sh test -x           # Stop at the first test failure
./environment.sh logs              # Follow shop logs; Ctrl-C exits logs only
./environment.sh stop              # Stop containers; keep source and data
./environment.sh start             # Start again; preserve database and orders
```

The source is mounted into the container and installed in editable mode. Restart
the service after Python changes (automatic reloading is disabled):

```bash
docker compose --env-file images.env restart shop
```

Setup builds assets and seeds data only when no initialized database exists.
It never automatically resets an existing database. If initialization is
interrupted, inspect the data or explicitly reset it. Reset deletes the demo
database and rebuilds fixtures, images, and the search index; source edits remain:

```bash
./environment.sh reset --confirm
./environment.sh start
```

Tests use Oscar's separate test settings and an in-memory SQLite database, never
the shop database. They may create and clean test media/index files under
`oscar/tests/`. Run one test command at a time.

## Reproducibility

`images.env` pins the Python 3.12 and Node.js 22 base-image digests. Python
dependencies are pinned in `requirements.lock`, with Django restricted to 5.2;
JavaScript dependencies use Oscar's upstream `package-lock.json` via `npm ci`.

To intentionally update Python dependency pins after validating a changed image:

```bash
./environment.sh lock
```

Do not delete `oscar/` if it contains edits you want to keep: those edits are
ignored by the parent repository. Future bug patches should be saved separately
when that phase begins. No bugs are introduced by this setup.

## Verified baseline

Verified on 7 November 2026 with Python 3.12, Oscar 4.2.1, and Django 5.2.19:

- The browser displays styled catalogue and product pages with loaded cover images;
  the sample database contains 201 browsable products and 140 product images.
- Search, basket quantity updates, guest checkout, administrator login, and the
  dashboard work. Sample order **100001** contains two £7.99 items, totals £15.98,
  and remains visible after a container restart.
- Dependency checks pass. Django system checks report no errors and one upstream
  warning: both thumbnail libraries register a tag named `thumbnail`.
- The full upstream suite, in its standard collection order, reports **1,786
  passed, 4 failed, 2 skipped**, plus **104 passing subtests**. It is not fully green.

The remaining failures in the unchanged source are:

| Tests | Observed limitation |
| --- | --- |
| `TestCategoryFactory.test_updating_subtree_slugs_when_moving_category_to_new_parent` and `test_updating_subtree_when_moving_category_to_new_sibling` | Cached category slugs leak between tests that reuse database IDs, producing stale `books/factual/popular-science/...` paths. |
| `TestConcurrentOrderPlacement.test_single_usage` and `test_voucher_single_usage` | Concurrent writes encounter SQLite `database table is locked` errors. This SQLite environment does not validate production database concurrency behavior. |

Both category tests pass when run separately, confirming the shared-cache
isolation issue. The full-suite log is retained locally at
`oscar/.verification/upstream-tests.log` (ignored by Git and Docker builds).

Oscar's source files were compared byte-for-byte with the pinned checkout and
remain unchanged. Bug introduction and upstream fixes are deferred.

## Saved local baseline

`swarm-oscar:baseline-v1` is a self-contained local image containing the source,
Python dependencies, built assets, search index, images, and a freshly seeded
sample database. It has no bind mounts or volumes. Development-session orders
and edits made inside a running baseline container are not part of the saved image.
The upstream test limitations above still apply; no deliberate bugs were added.

`baseline.json` records the exact image ID and upstream revision. The script
checks the tag against that ID before starting a run and refuses to overwrite
an existing baseline during a build. Docker Hub is not used.

From `src/environment/`:

```bash
./baseline.sh start    # Start/resume on http://localhost:8081/
./baseline.sh status   # Show health and mounts (must be [])
./baseline.sh logs     # Follow logs; Ctrl-C leaves the container running
./baseline.sh stop    # Stop while preserving the current run's changes
./baseline.sh fresh   # Delete this run and create a fresh one from the saved image
```

**`fresh` discards all changes in `swarm-oscar-baseline`.** Export any agent
patches or results before using it. Recreating the container restores both code
and database from the image. A simple stop/start preserves the current run.
Neither action changes the mounted development shop on port 8080.

Reset verification passed: a temporary product-title change, source comment, and
new file were discarded when the container was recreated. Both runs used the
recorded image ID and had `Mounts: []`; the development shop's code, products,
and orders were unaffected. The report is saved locally at
`oscar/.verification/baseline-reset.json`.

The dashboard is at <http://localhost:8081/en-gb/dashboard/>, using
`superuser@example.com` / `testing`. Separate session and CSRF cookie names let
the baseline and development shop coexist in one browser.

To reproduce the image on a machine without the saved local tag, run
`./baseline.sh build` after downloading the source. If bringing the tracked
manifest to a different machine, archive that manifest first: a new build
generates its own image ID and manifest. Preserve the image itself with Docker's
`image save`/`image load` commands when exact image identity is required.
