#!/usr/bin/env python3
"""Build and run immutable ready and controlled-bug snapshots from the local baseline."""
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
PORT = 8082
STAGE = "ready"
EXPECTED_BUG_IDS = []


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
        raise RuntimeError("The selected image tag no longer matches its saved image ID.")
    if record["stage"] != STAGE or record["applied_bug_ids"] != EXPECTED_BUG_IDS:
        raise RuntimeError("The saved manifest does not match the selected experiment stage.")
    labels = image["Config"].get("Labels") or {}
    if labels.get("swarm-alignment.stage") != STAGE or labels.get("swarm-alignment.applied-bug-count") != str(len(EXPECTED_BUG_IDS)):
        raise RuntimeError("Image labels do not match the selected stage and bug count.")
    return record["image_id"]


def build_buggy():
    if RECORD.exists() or inspect("image", TAG) is not None:
        raise RuntimeError("buggy-v1 already exists; refusing to overwrite the saved buggy image.")
    parent = baseline()
    report_path = ROOT / "grading/verification.json"
    report = json.loads(report_path.read_text())
    rows = report["verified"]
    if [row["id"] for row in rows] != EXPECTED_BUG_IDS:
        raise RuntimeError("All 100 bugs must be verified before building buggy-v1.")
    for row in rows:
        if row["status"] != "verified" or [row[phase]["status"] for phase in ("before", "mutated", "repaired")] != ["pass", "fail", "pass"]:
            raise RuntimeError(f"Bug {row['id']} has incomplete behavioral verification.")
        patch = ROOT / "patches" / row["patch"]
        if hashlib.sha256(patch.read_bytes()).hexdigest() != row["patch_sha256"]:
            raise RuntimeError(f"Bug {row['id']} patch changed after verification.")
    for key, expected in (("baseline_results", "pass"), ("combined_results", "fail")):
        cases = report[key]
        if [case["id"] for case in cases] != EXPECTED_BUG_IDS or any(case["status"] != expected for case in cases):
            raise RuntimeError(f"The {key} behavioral matrix is incomplete.")
    checksum, count = fingerprint(WORKSPACE / "source")
    if checksum != report["workspace_sha256"] or report["baseline_image_id"] != parent["image_id"]:
        raise RuntimeError("Source or baseline differs from the verified injection report.")
    docker("build", "-f", str(ROOT / "Dockerfile.buggy"), "--build-arg", "BASELINE_IMAGE=" + parent["tag"],
           "-t", TAG, str(ROOT), capture=False)
    if baseline()["image_id"] != parent["image_id"]:
        raise RuntimeError("Baseline image reference changed during the build.")
    image = inspect("image", TAG)
    RECORD.write_text(json.dumps({
        "tag": TAG, "image_id": image["Id"], "baseline_image_id": parent["image_id"],
        "ready_image_id": report["ready_image_id"], "oscar_revision": parent["oscar_revision"],
        "source_sha256": checksum, "source_file_count": count, "stage": STAGE,
        "applied_bug_ids": EXPECTED_BUG_IDS,
        "priorities": {priority: sum(row["priority"] == priority for row in rows) for priority in ("High", "Medium", "Low")},
        "verification_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "grader_sha256": {name: hashlib.sha256((ROOT / "grading" / name).read_bytes()).hexdigest()
                          for name in ("probe.py", "mutation_tools.py", "mutations.json")},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


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
               "-p", f"127.0.0.1:{PORT}:8000", image_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        container = inspect("container", CONTAINER)
        if not container or not container["State"]["Running"]:
            raise RuntimeError("Experiment container stopped before becoming healthy.")
        if container["State"].get("Health", {}).get("Status") == "healthy":
            print(f"Experiment {STAGE}: http://localhost:{PORT}/ (applied bugs: {len(EXPECTED_BUG_IDS)}; mounts: none)")
            return
        time.sleep(1)
    docker("logs", "--tail", "30", CONTAINER, capture=False)
    raise RuntimeError("Experiment container did not become healthy within 60 seconds.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "build", "start", "fresh", "stop", "status", "logs",
                        "build-buggy", "start-buggy", "fresh-buggy", "stop-buggy", "status-buggy", "logs-buggy"])
    command = parser.parse_args().command
    if command.endswith("-buggy"):
        global TAG, CONTAINER, OWNER, RECORD, PORT, STAGE, EXPECTED_BUG_IDS
        TAG = "swarm-oscar:buggy-v1"
        CONTAINER = "swarm-oscar-buggy"
        OWNER = "experiment-buggy"
        RECORD = ROOT / "buggy.json"
        PORT = 8083
        STAGE = "buggy"
        EXPECTED_BUG_IDS = list(range(1, 101))
        command = command.removesuffix("-buggy")
    if command == "prepare":
        prepare()
    elif command == "build":
        build_buggy() if STAGE == "buggy" else build()
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
