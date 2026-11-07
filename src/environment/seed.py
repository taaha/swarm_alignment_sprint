"""Initialize the demo using the same management commands as Oscar's Makefile."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path("/app/oscar")
SANDBOX = ROOT / "sandbox"
MARKER = SANDBOX / ".initialized"
DATABASE = SANDBOX / "db.sqlite"


def manage(*arguments):
    subprocess.run([sys.executable, "sandbox/manage.py", *arguments], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    if not (SANDBOX / "manage.py").is_file():
        raise SystemExit("Run this script inside the Oscar container.")
    if args.reset:
        MARKER.unlink(missing_ok=True)
        for filename in ("db.sqlite", "db.sqlite-journal", "db.sqlite-wal", "db.sqlite-shm"):
            (SANDBOX / filename).unlink(missing_ok=True)
        for directory in ("public", "whoosh_index"):
            path = SANDBOX / directory
            if path.exists():
                shutil.rmtree(path)
    elif DATABASE.exists() and not MARKER.exists():
        raise SystemExit("Database exists without completed initialization. Inspect it or use reset --confirm.")
    elif MARKER.exists():
        if not DATABASE.exists():
            raise SystemExit("Initialization marker exists but database is missing. Use reset --confirm.")
        print("Existing database retained; fixtures were not reloaded.")
        return

    manage("migrate", "--noinput")
    manage("loaddata", "sandbox/fixtures/auth.json")
    manage("loaddata", "sandbox/fixtures/child_products.json")
    manage("oscar_import_catalogue", *[str(p.relative_to(ROOT)) for p in sorted((SANDBOX / "fixtures").glob("*.csv"))])
    manage("oscar_import_catalogue_images", "sandbox/fixtures/images.tar.gz")
    manage("oscar_populate_countries", "--initial-only")
    manage("loaddata", "sandbox/fixtures/pages.json", "sandbox/fixtures/ranges.json", "sandbox/fixtures/offers.json")
    manage("loaddata", "sandbox/fixtures/orders.json")
    manage("clear_index", "--noinput")
    manage("update_index", "catalogue")
    manage("thumbnail", "cleanup")
    manage("collectstatic", "--noinput")
    MARKER.touch()


if __name__ == "__main__":
    main()
