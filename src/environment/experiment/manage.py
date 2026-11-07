#!/usr/bin/env python3
"""Prepare and run an unchanged experiment starting point from the local baseline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"
RECORD = ROOT / "ready.json"
TAG = "swarm-oscar:experiment-ready-v1"
CONTAINER = "swarm-oscar-experiment"
OWNER = "experiment-ready"


def docker(*args, capture=True):
    result = subprocess.run(
        ["docker", *args], check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else None


def inspect(kind, name):
    result = subprocess.run(
        ["docker", kind, "inspect", name], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        if "No such" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)[0]


def fingerprint(path):
    digest = hashlib.sha256()
    count = 0
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path).as_posix()
        if item.is_symlink():
            value = [relative, "symlink", item.readlink().as_posix()]
        elif item.is_file():
            count += 1
            value = [relative, "file", item.stat().st_mode & 0o777,
                     hashlib.sha256(item.read_bytes()).hexdigest()]
        else:
            value = [relative, "directory"]
        digest.update(json.dumps(value, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest(), count


def baseline():
    record = json.loads((ROOT.parent / "baseline.json").read_text())
    image = inspect("image", record["tag"])
    if image is None or image["Id"] != record["image_id"]:
        raise RuntimeError("The local baseline tag must match baseline.json.")
    if image["Config"].get("Volumes"):
        raise RuntimeError("The baseline image must not declare volumes.")
    return record


def prepare():
    parent = baseline()
    if WORKSPACE.exists():
        saved = json.loads((WORKSPACE / "origin.json").read_text())
        if saved["baseline_image_id"] != parent["image_id"]:
            raise RuntimeError("Existing workspace has a different baseline; it was not overwritten.")
        print("Keeping the existing workspace and any local edits.")
        return
    temporary_container = "swarm-oscar-prepare-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=ROOT) as temporary:
        staging = Path(temporary)
        source = staging / "source"
        source.mkdir()
        docker("create", "--name", temporary_container, parent["image_id"])
        try:
            docker("cp", temporary_container + ":/app/oscar/src/oscar/.", str(source))
        finally:
            docker("rm", temporary_container)
        checksum, count = fingerprint(source)
        (staging / "origin.json").write_text(json.dumps({
            "baseline_image_id": parent["image_id"],
            "source_sha256": checksum,
            "source_file_count": count,
        }, indent=2) + "\n")
        staging.rename(WORKSPACE)
    print("Prepared workspace/source from the saved baseline; applied bugs: 0.")


def build():
    if RECORD.exists() or inspect("image", TAG) is not None:
        raise RuntimeError("experiment-ready-v1 already exists; refusing to overwrite it.")
    prepare()
    parent = baseline()
    origin = json.loads((WORKSPACE / "origin.json").read_text())
    checksum, count = fingerprint(WORKSPACE / "source")
    if checksum != origin["source_sha256"]:
        raise RuntimeError("Workspace source was changed; refusing to build it as a zero-bug starting point.")
    docker("build", "--build-arg", "BASELINE_IMAGE=" + parent["tag"],
           "-t", TAG, str(ROOT), capture=False)
    if baseline()["image_id"] != parent["image_id"]:
        raise RuntimeError("The baseline reference changed during the build.")
    image = inspect("image", TAG)
    RECORD.write_text(json.dumps({
        "tag": TAG,
        "image_id": image["Id"],
        "baseline_image_id": parent["image_id"],
        "oscar_revision": parent["oscar_revision"],
        "source_sha256": checksum,
        "source_file_count": count,
        "stage": "ready",
        "applied_bug_ids": [],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


def saved_image():
    record = json.loads(RECORD.read_text())
    image = inspect("image", TAG)
    if image is None or image["Id"] != record["image_id"] or record["tag"] != TAG:
        raise RuntimeError("The ready image tag no longer matches its saved image ID.")
    if record["stage"] != "ready" or record["applied_bug_ids"]:
        raise RuntimeError("This command only runs the zero-bug starting point.")
    return record["image_id"]


def owned_container():
    container = inspect("container", CONTAINER)
    if container and (container["Config"].get("Labels") or {}).get("swarm-alignment.environment") != OWNER:
        raise RuntimeError("Existing container has a different owner; refusing to change it.")
    return container


def start():
    image_id = saved_image()
    container = owned_container()
    if container:
        if container["Image"] != image_id or container["Mounts"]:
            raise RuntimeError("Existing container has a different image or mounts; inspect it first.")
        docker("start", CONTAINER)
    else:
        docker("run", "-d", "--name", CONTAINER, "--init", "--memory", "2g",
               "--label", "swarm-alignment.environment=" + OWNER,
               "-p", "127.0.0.1:8082:8000", image_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        container = inspect("container", CONTAINER)
        if not container or not container["State"]["Running"]:
            raise RuntimeError("Experiment container stopped before becoming healthy.")
        if container["State"].get("Health", {}).get("Status") == "healthy":
            print("Experiment starting point: http://localhost:8082/ (applied bugs: 0; mounts: none)")
            return
        time.sleep(1)
    docker("logs", "--tail", "30", CONTAINER, capture=False)
    raise RuntimeError("Experiment container did not become healthy within 60 seconds.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "build", "start", "fresh", "stop", "status", "logs"])
    command = parser.parse_args().command
    if command == "prepare":
        prepare()
    elif command == "build":
        build()
    elif command == "start":
        start()
    elif command == "fresh":
        saved_image()
        if owned_container():
            docker("rm", "-f", CONTAINER)
        start()
    elif command == "stop":
        if owned_container():
            docker("stop", CONTAINER)
    elif command == "logs":
        docker("logs", "-f", CONTAINER, capture=False)
    else:
        container = owned_container()
        if container is None:
            raise RuntimeError("Experiment container has not been created.")
        print(json.dumps({"container": CONTAINER, "image_id": container["Image"],
                          "status": container["State"]["Status"],
                          "health": container["State"].get("Health", {}).get("Status"),
                          "mounts": container["Mounts"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
