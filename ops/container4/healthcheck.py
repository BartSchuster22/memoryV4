#!/usr/bin/env python3
"""Fail-closed production invariant and health check for MemoryV4 Container 4."""

from __future__ import annotations

import json
import os
import subprocess
import sys

CONTAINER = os.environ.get("MEMORYV4_CONTAINER", "memory-v4-memory-v4-1")
PRIVATE_NETWORK = os.environ.get("MEMORYV4_PRIVATE_NETWORK", "unify_memory-private")
DATA_VOLUME = os.environ.get("MEMORYV4_DATA_VOLUME", "memory-v4-data-v1")
ALLOWED_NETWORK_CONTAINERS = {
    CONTAINER,
    os.environ.get("MEMORYV4_GATEWAY_CONTAINER", "unify-unify-core-1"),
}


def docker_json(*args: str) -> object:
    result = subprocess.run(
        ["docker", *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    container = docker_json("inspect", CONTAINER)[0]
    state = container["State"]
    config = container["Config"]
    host = container["HostConfig"]

    require(state["Running"] is True, "container is not running")
    require(state.get("Health", {}).get("Status") == "healthy", "container is not healthy")
    require(config["User"] == "65532:65532", "container user is not 65532:65532")
    require(host["ReadonlyRootfs"] is True, "root filesystem is writable")
    require(host["CapDrop"] == ["ALL"], "Linux capabilities are not fully dropped")
    require("no-new-privileges:true" in host["SecurityOpt"], "no-new-privileges is absent")
    require(host["RestartPolicy"]["Name"] == "unless-stopped", "restart policy is incorrect")
    require(config["Image"].startswith("sha256:"), "container image is not digest pinned")

    ports = container["NetworkSettings"].get("Ports") or {}
    require(all(bindings is None for bindings in ports.values()), "MemoryV4 publishes a host port")

    mounts = container.get("Mounts", [])
    persistent = [mount for mount in mounts if mount["Type"] == "volume"]
    require(
        [(mount["Name"], mount["Destination"], mount["RW"]) for mount in persistent]
        == [(DATA_VOLUME, "/data", True)],
        "application container must mount only the canonical data volume",
    )
    secrets = [mount for mount in mounts if mount["Destination"].startswith("/run/secrets/")]
    require(len(secrets) == 1 and secrets[0]["RW"] is False, "credential secret is not read-only")

    network = docker_json("network", "inspect", PRIVATE_NETWORK)[0]
    require(network["Internal"] is True, "MemoryV4 network is not internal")
    attached = {entry["Name"] for entry in network.get("Containers", {}).values()}
    require(
        attached == ALLOWED_NETWORK_CONTAINERS,
        f"unexpected private-network members: {attached}",
    )

    result = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "python",
            "-c",
            "import json,urllib.request; "
            "r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2); "
            "assert json.load(r)['status']=='ok'",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    require(result.returncode == 0, "in-container health endpoint failed")
    print(
        f"memoryv4_container4_monitor=PASS image={config['Image']} "
        f"health={state['Health']['Status']} network={PRIVATE_NETWORK}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"memoryv4_container4_monitor=FAIL error={error}", file=sys.stderr)
        raise SystemExit(1)
