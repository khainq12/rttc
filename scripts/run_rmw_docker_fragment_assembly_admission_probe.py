"""Prove bounded FleetRMW fragment-assembly admission under raw UDP input."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_authenticated_fragment_assembly_probe import (  # noqa: E402
    ensure_rmw_build,
)
from scripts.run_ros2_direct_rmw_netem_probe import (  # noqa: E402
    parse_last_json,
    run,
    wait_for_container_path,
)
from scripts.run_ros2_relay_rmw_netem_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    SERIALIZED_RELAY_INSTALL,
)


SCHEMA_VERSION = "fleetrmw.fragment_assembly_admission.v1"
RECEIVER_SCRIPT = r'''
import ctypes
import json
import os
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


rclpy.init()
node = Node("fleetrmw_fragment_assembly_admission_receiver")
node.create_subscription(
    String,
    "/fleetqox/fragment_assembly_admission",
    lambda message: None,
    QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
)
ready_path = os.environ["FLEETQOX_PROBE_READY_FILE"]
with open(ready_path, "w", encoding="utf-8") as stream:
    stream.write("ready\n")

deadline = time.monotonic() + 0.65
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)

library = ctypes.CDLL("librmw_fleetqox_cpp.so")
names = (
    "fragment_active_assemblies",
    "fragment_active_missing_indexes",
    "fragment_assembly_evictions",
    "fragment_assembly_oversize_drops",
    "fragment_assembly_metadata_mismatch_drops",
)
metrics = {}
for name in names:
    symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
    symbol.restype = ctypes.c_uint64
    metrics[name] = int(symbol())

expected = {
    "fragment_active_assemblies": 4,
    "fragment_active_missing_indexes": 4,
    "fragment_assembly_evictions": 2,
    "fragment_assembly_oversize_drops": 1,
    "fragment_assembly_metadata_mismatch_drops": 1,
}
status = "ok" if metrics == expected else "failed"
deadline = time.monotonic() + 1.4
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)
expiry_names = (
    "fragment_active_assemblies",
    "fragment_active_missing_indexes",
    "fragment_assembly_ttl_expirations",
    "fragment_assembly_ttl_expired_missing_indexes",
)
expiry_metrics = {}
for name in expiry_names:
    symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
    symbol.restype = ctypes.c_uint64
    expiry_metrics[name] = int(symbol())
expiry_expected = {
    "fragment_active_assemblies": 0,
    "fragment_active_missing_indexes": 0,
    "fragment_assembly_ttl_expirations": 4,
    "fragment_assembly_ttl_expired_missing_indexes": 4,
}
status = "ok" if status == "ok" and expiry_metrics == expiry_expected else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.fragment_assembly_admission_receiver.v1",
    "status": status,
    "metrics": metrics,
    "expected": expected,
    "expiry_metrics": expiry_metrics,
    "expiry_expected": expiry_expected,
}, sort_keys=True))
node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if status == "ok" else 1)
'''

INJECTOR_SCRIPT = r'''
import socket
import sys
import time


target = (sys.argv[1], 49812)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 49811))
for index in range(6):
    payload = (
        f"FLEETQOX_REPAIR_FRAGMENT_V1|flood-{index}|0|2|8|abcd"
    ).encode()
    sock.sendto(payload, target)
    time.sleep(0.02)
sock.sendto(
    b"FLEETQOX_REPAIR_FRAGMENT_V1|flood-5|1|3|8|efgh",
    target,
)
sock.sendto(
    b"FLEETQOX_REPAIR_FRAGMENT_V1|oversize|0|2|4097|x",
    target,
)
sock.close()
'''


def summarize_probe(
    receiver: dict[str, Any] | None,
    *,
    receiver_returncode: int,
    injector_returncode: int,
    assembly_limit: int,
    max_assembly_bytes: int,
    assembly_ttl_ms: int = 1000,
) -> dict[str, Any]:
    metrics = receiver.get("metrics") if isinstance(receiver, dict) else None
    expiry_metrics = (
        receiver.get("expiry_metrics") if isinstance(receiver, dict) else None
    )
    contract_ok = (
        receiver_returncode == 0
        and injector_returncode == 0
        and isinstance(receiver, dict)
        and receiver.get("status") == "ok"
        and receiver.get("schema_version")
        == "fleetrmw.fragment_assembly_admission_receiver.v1"
        and isinstance(metrics, dict)
        and int(metrics.get("fragment_active_assemblies", -1))
        == assembly_limit
        and int(metrics.get("fragment_active_missing_indexes", -1))
        == assembly_limit
        and int(metrics.get("fragment_assembly_evictions", -1)) == 2
        and int(metrics.get("fragment_assembly_oversize_drops", -1)) == 1
        and int(
            metrics.get("fragment_assembly_metadata_mismatch_drops", -1)
        ) == 1
        and isinstance(expiry_metrics, dict)
        and int(expiry_metrics.get("fragment_active_assemblies", -1)) == 0
        and int(expiry_metrics.get("fragment_active_missing_indexes", -1))
        == 0
        and int(expiry_metrics.get("fragment_assembly_ttl_expirations", -1))
        == assembly_limit
        and int(
            expiry_metrics.get(
                "fragment_assembly_ttl_expired_missing_indexes", -1
            )
        )
        == assembly_limit
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if contract_ok else "failed",
        "assembly_limit": assembly_limit,
        "max_assembly_bytes": max_assembly_bytes,
        "assembly_ttl_ms": assembly_ttl_ms,
        "raw_partial_assembly_count": 6,
        "receiver_returncode": receiver_returncode,
        "injector_returncode": injector_returncode,
        "bounded_fragment_assembly_admission_claim": contract_ok,
        "fragment_assembly_oversize_fail_closed_claim": contract_ok,
        "fragment_metadata_mismatch_isolation_claim": contract_ok,
        "bounded_fragment_assembly_ttl_claim": contract_ok,
        "production_fragment_security_claim": False,
        "receiver": receiver,
    }


def run_probe(
    *,
    root: Path,
    image: str,
    assembly_limit: int,
    max_assembly_bytes: int,
    assembly_ttl_ms: int,
) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{time.time_ns()}"
    network = f"fleetrmw-fragment-admission-net-{suffix}"
    receiver_name = f"fleetrmw-fragment-admission-receiver-{suffix}"
    injector_name = f"fleetrmw-fragment-admission-injector-{suffix}"
    work_dir = root / f".tmp_fleetrmw_fragment_admission_{suffix}"
    receiver_script = work_dir / "receiver.py"
    injector_script = work_dir / "injector.py"
    ready_path = "/tmp/fleetrmw_fragment_admission_ready"
    receiver_returncode = -1
    injector_returncode = -1
    receiver_result: dict[str, Any] | None = None

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        receiver_script.write_text(RECEIVER_SCRIPT, encoding="utf-8")
        injector_script.write_text(INJECTOR_SCRIPT, encoding="utf-8")
        ensure_rmw_build(root=root, image=image)
        run(["docker", "network", "create", network])
        run([
            "docker",
            "run",
            "-d",
            "--name",
            injector_name,
            "--network",
            network,
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            "sleep 30",
        ])
        receiver_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49812 && "
            f"export FLEETQOX_RMW_PEERS={injector_name}:49811 && "
            "export FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES=1024 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS=0 && "
            f"export FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT={assembly_limit} && "
            "export FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES="
            f"{max_assembly_bytes} && "
            "export FLEETQOX_RMW_FRAGMENT_ASSEMBLY_TTL_MS="
            f"{assembly_ttl_ms} && "
            f"export FLEETQOX_PROBE_READY_FILE={ready_path} && "
            f"python3 /work/{receiver_script.relative_to(root)}"
        )
        run([
            "docker",
            "run",
            "-d",
            "--name",
            receiver_name,
            "--network",
            network,
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            receiver_command,
        ])
        wait_for_container_path(receiver_name, ready_path, timeout_s=30.0)
        injector = run([
            "docker",
            "exec",
            injector_name,
            "python3",
            f"/work/{injector_script.relative_to(root)}",
            receiver_name,
        ], check=False)
        injector_returncode = injector.returncode
        receiver_returncode = int(
            run(["docker", "wait", receiver_name]).stdout.strip()
        )
        receiver_logs = run(["docker", "logs", receiver_name], check=False)
        receiver_result = parse_last_json(receiver_logs.stdout)
    finally:
        for container in (receiver_name, injector_name):
            run(["docker", "rm", "-f", container], check=False)
        run(["docker", "network", "rm", network], check=False)
        shutil.rmtree(work_dir, ignore_errors=True)

    return summarize_probe(
        receiver_result,
        receiver_returncode=receiver_returncode,
        injector_returncode=injector_returncode,
        assembly_limit=assembly_limit,
        max_assembly_bytes=max_assembly_bytes,
        assembly_ttl_ms=assembly_ttl_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--assembly-limit", type=int, default=4)
    parser.add_argument("--max-assembly-bytes", type=int, default=4096)
    parser.add_argument("--assembly-ttl-ms", type=int, default=1000)
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path(
            "results_rmw_socket/"
            "docker_fragment_assembly_admission_probe_summary.json"
        ),
    )
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        assembly_limit=max(min(args.assembly_limit, 16384), 1),
        max_assembly_bytes=max(
            min(args.max_assembly_bytes, 256 * 1024 * 1024),
            1,
        ),
        assembly_ttl_ms=max(min(args.assembly_ttl_ms, 600000), 1000),
    )
    path = ROOT / args.summary_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"status={summary['status']} "
        f"bounded={summary['bounded_fragment_assembly_admission_claim']}"
    )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
