#!/usr/bin/env python3
"""Fetch an ordinary pinned source folder; no Git metadata is retained."""
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION = "d0a70850abf8e4229d4971aa7e8f4643326ec9f3"
ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT / "oscar"


def main():
    if DESTINATION.exists():
        marker = DESTINATION / ".source-revision"
        if not marker.is_file() or marker.read_text().strip() != REVISION:
            raise SystemExit("Existing Oscar folder has an unknown revision; refusing to overwrite it.")
        print("Keeping existing Oscar source and local edits.")
        return
    with tempfile.TemporaryDirectory(prefix=".download-", dir=ROOT) as staging:
        source = Path(staging) / "source"
        subprocess.run([
            "git", "clone", "--depth", "1", "--branch", "4.2.1",
            "https://github.com/django-oscar/django-oscar.git", str(source)
        ], check=True, timeout=300)
        revision = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        if revision != REVISION:
            raise SystemExit("Downloaded revision differs from the pinned commit; refusing to install it.")
        shutil.rmtree(source / ".git")
        (source / ".source-revision").write_text(REVISION + "\n")
        if DESTINATION.exists():
            raise SystemExit("Oscar folder appeared during download; refusing to overwrite it.")
        source.rename(DESTINATION)


if __name__ == "__main__":
    main()
