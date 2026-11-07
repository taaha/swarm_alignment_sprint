"""Host-only controls; no operator manifests or scripts enter the worker image."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
RECORD = ROOT / "worker.json"
TAG = "oscar-shop:local-v1"
CONTAINER = "oscar-shop"
NETWORK = "oscar-shop-local"
OWNER_KEY = "oscar-shop.managed"
WEB = CONTAINER + "-web"
RELAY = '''import select, socket, socketserver
class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.create_connection(("oscar-shop", 8000), timeout=10) as upstream:
            peers = [self.request, upstream]
            while True:
                ready, _, _ = select.select(peers, [], [], 60)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
Server(("0.0.0.0", 8000), Relay).serve_forever()
'''


def docker(*args, capture=True):
    result = subprocess.run(["docker", *args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else None


def inspect(kind, name):
    result = subprocess.run(["docker", kind, "inspect", name], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        if "No such" in result.stderr or (kind == "network" and "not found" in result.stderr):
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)[0]


def build():
    if RECORD.exists() or inspect("image", TAG):
        raise RuntimeError("The saved application image already exists; it will not be overwritten.")
    parent = json.loads((ROOT.parent / "experiment/buggy.json").read_text())
    image = inspect("image", parent["tag"])
    if not image or image["Id"] != parent["image_id"]:
        raise RuntimeError("The source image does not match its saved manifest.")
    environment = dict(line.split("=", 1) for line in (ROOT.parent / "images.env").read_text().splitlines() if line and not line.startswith("#"))
    docker("build", "--build-arg", "SOURCE_IMAGE=" + parent["tag"],
           "--build-arg", "PYTHON_IMAGE=" + environment["PYTHON_IMAGE"], "-t", TAG, str(ROOT), capture=False)
    if inspect("image", parent["tag"])["Id"] != parent["image_id"]:
        raise RuntimeError("Source image reference changed during the build.")
    created = inspect("image", TAG)
    RECORD.write_text(json.dumps({
        "tag": TAG, "image_id": created["Id"], "source_image_id": parent["image_id"],
        "baseline_image_id": parent["baseline_image_id"], "source_sha256": parent["source_sha256"],
        "source_file_count": parent["source_file_count"], "applied_bug_ids": parent["applied_bug_ids"],
        "dockerfile_sha256": hashlib.sha256((ROOT / "Dockerfile").read_bytes()).hexdigest(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")


def saved_image():
    record = json.loads(RECORD.read_text())
    image = inspect("image", TAG)
    if not image or image["Id"] != record["image_id"] or record["tag"] != TAG:
        raise RuntimeError("The local application tag differs from its saved image ID.")
    return record["image_id"]


def owned_container(name=CONTAINER):
    container = inspect("container", name)
    if container and (container["Config"].get("Labels") or {}).get(OWNER_KEY) != "true":
        raise RuntimeError("Existing container is not owned by these controls; refusing to change it.")
    return container


def web():
    if owned_container(WEB):
        docker("start", WEB)
        return
    environment = dict(line.split("=", 1) for line in (ROOT.parent / "images.env").read_text().splitlines() if line and not line.startswith("#"))
    docker("create", "--name", WEB, "--hostname", "web", "--network", "bridge",
           "--user", "65534:65534", "--read-only", "--memory", "64m", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges:true", "--label", OWNER_KEY + "=true",
           "-p", "127.0.0.1:8084:8000", environment["PYTHON_IMAGE"], "python", "-c", RELAY)
    docker("network", "connect", NETWORK, WEB)
    docker("start", WEB)


def network():
    value = inspect("network", NETWORK)
    if value is None:
        docker("network", "create", "--internal", "--label", OWNER_KEY + "=true", NETWORK)
        value = inspect("network", NETWORK)
    if not value["Internal"] or (value.get("Labels") or {}).get(OWNER_KEY) != "true":
        raise RuntimeError("The application requires its own internal network.")
    return value


def start():
    image_id = saved_image()
    network()
    current = owned_container()
    if current:
        if current["Image"] != image_id or current["Mounts"] or set(current["NetworkSettings"]["Networks"]) != {NETWORK}:
            raise RuntimeError("Existing container differs from the saved application or isolation settings.")
        docker("start", CONTAINER)
    else:
        docker("run", "-d", "--name", CONTAINER, "--hostname", "shop", "--init", "--memory", "2g",
               "--network", NETWORK, "--label", OWNER_KEY + "=true", "--security-opt", "no-new-privileges:true",
               "--cap-drop", "ALL", image_id)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = inspect("container", CONTAINER)
        if not current or not current["State"]["Running"]:
            docker("logs", "--tail", "30", CONTAINER, capture=False)
            raise RuntimeError("Application stopped before becoming healthy.")
        if current["State"].get("Health", {}).get("Status") == "healthy":
            web()
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8084/", timeout=4) as response:
                        if response.status == 200:
                            print("Oscar shop: http://localhost:8084/")
                            return
                except (OSError, ValueError):
                    time.sleep(1)
            break
        time.sleep(1)
    docker("logs", "--tail", "30", CONTAINER, capture=False)
    raise RuntimeError("Application did not become healthy within 60 seconds.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "start", "fresh", "stop", "status", "logs"])
    command = parser.parse_args().command
    if command == "build":
        build()
    elif command == "start":
        start()
    elif command == "fresh":
        saved_image()
        if owned_container():
            docker("rm", "-f", CONTAINER)
        start()
    elif command == "stop":
        if owned_container(WEB):
            docker("stop", WEB)
        if owned_container():
            docker("stop", CONTAINER)
    elif command == "logs":
        docker("logs", "-f", CONTAINER, capture=False)
    else:
        current = owned_container()
        if not current:
            raise RuntimeError("Application container has not been created.")
        print(json.dumps({"container": CONTAINER, "image_id": current["Image"],
                          "health": current["State"].get("Health", {}).get("Status"),
                          "mounts": current["Mounts"], "networks": list(current["NetworkSettings"]["Networks"])}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from error
