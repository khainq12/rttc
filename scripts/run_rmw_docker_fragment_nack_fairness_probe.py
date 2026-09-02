"""Prove fleet-aware per-assembly fragment-NACK index budgeting in Docker."""

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


SCHEMA_VERSION = "fleetrmw.fragment_nack_fairness.v1"
RECEIVER_SCHEMA_VERSION = "fleetrmw.fragment_nack_fairness_receiver.v1"
INJECTOR_SCHEMA_VERSION = "fleetrmw.fragment_nack_fairness_injector.v1"
ASSEMBLY_COUNT = 513
FRAGMENT_COUNT = 16
CONFIGURED_INDEX_LIMIT = 8
FLEET_INDEX_BUDGET = 512
EXPECTED_ACTIVE_MISSING_INDEXES = ASSEMBLY_COUNT * (FRAGMENT_COUNT - 1)
# The fleet-wide NACK-index budget is consumed incrementally, one sweep per
# received datagram (refilled once per fragment_nack_interval_ms window),
# rather than in a single pass after every assembly is already known. So
# exactly how many assemblies get their full CONFIGURED_INDEX_LIMIT before
# the shared budget runs dry -- and thus the exact totals below -- depends
# on real UDP arrival timing and varies run to run. What IS structurally
# guaranteed regardless of timing: a single budget window can hand out at
# most floor(budget / limit) unreduced (full) grants before it's exhausted,
# so at least (count - that many) assemblies must end up reduced.
MAX_UNREDUCED_ASSEMBLIES = FLEET_INDEX_BUDGET // CONFIGURED_INDEX_LIMIT
MIN_BUDGET_REDUCTIONS = ASSEMBLY_COUNT - MAX_UNREDUCED_ASSEMBLIES
MIN_INDEXES_REQUESTED = ASSEMBLY_COUNT  # every assembly gets at least 1 index
MAX_INDEXES_REQUESTED = ASSEMBLY_COUNT * CONFIGURED_INDEX_LIMIT

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
node = Node("fleetrmw_fragment_nack_fairness_receiver")
subscription = node.create_subscription(
    String,
    "/fleetqox/fragment_nack_fairness",
    lambda _message: None,
    QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
)
with open(os.environ["FLEETQOX_PROBE_READY_FILE"], "w", encoding="utf-8") as stream:
    stream.write("ready\n")

deadline = time.monotonic() + 3.0
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)

library = ctypes.CDLL("librmw_fleetqox_cpp.so")
names = (
    "fragment_active_assemblies",
    "fragment_active_missing_indexes",
    "fragment_nack_exhausted_assemblies",
    "fragment_nacks_sent",
    "fragment_nack_indexes_requested",
    "fragment_nack_index_budget_reductions",
    "fragment_nack_max_sweep_indexes_requested",
    "fragment_nack_sweep_budget_exhaustions",
)
metrics = {}
for name in names:
    symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
    symbol.restype = ctypes.c_uint64
    metrics[name] = int(symbol())

ASSEMBLY_COUNT = 513
FLEET_INDEX_BUDGET = 512
CONFIGURED_INDEX_LIMIT = 8
MAX_UNREDUCED_ASSEMBLIES = FLEET_INDEX_BUDGET // CONFIGURED_INDEX_LIMIT
MIN_BUDGET_REDUCTIONS = ASSEMBLY_COUNT - MAX_UNREDUCED_ASSEMBLIES
# Exact, timing-independent invariants plus the fleet-budget bounds that
# hold regardless of real UDP arrival ordering -- see the matching
# constants/comment in run_rmw_docker_fragment_nack_fairness_probe.py.
checks = {
    "fragment_active_assemblies": metrics["fragment_active_assemblies"] == 513,
    "fragment_active_missing_indexes":
        metrics["fragment_active_missing_indexes"] == 7695,
    "fragment_nack_exhausted_assemblies":
        metrics["fragment_nack_exhausted_assemblies"] == 513,
    "fragment_nacks_sent": metrics["fragment_nacks_sent"] == 513,
    "fragment_nack_index_budget_reductions":
        MIN_BUDGET_REDUCTIONS
        <= metrics["fragment_nack_index_budget_reductions"]
        <= ASSEMBLY_COUNT,
    "fragment_nack_indexes_requested":
        ASSEMBLY_COUNT
        <= metrics["fragment_nack_indexes_requested"]
        <= ASSEMBLY_COUNT * CONFIGURED_INDEX_LIMIT,
    "fragment_nack_max_sweep_indexes_requested":
        0
        < metrics["fragment_nack_max_sweep_indexes_requested"]
        <= FLEET_INDEX_BUDGET,
    "fragment_nack_sweep_budget_exhaustions":
        metrics["fragment_nack_sweep_budget_exhaustions"] >= 1,
}
status = "ok" if all(checks.values()) else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.fragment_nack_fairness_receiver.v1",
    "status": status,
    "metrics": metrics,
    "checks": checks,
}, sort_keys=True))
node.destroy_subscription(subscription)
node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if status == "ok" else 1)
'''

INJECTOR_SCRIPT = r'''
import json
import socket
import sys
import time


target = (sys.argv[1], 49812)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 49811))
sock.settimeout(0.1)
for index in range(513):
    payload = (
        f"FLEETQOX_REPAIR_FRAGMENT_V1|fair-{index}|15|16|16|x"
    ).encode()
    sock.sendto(payload, target)

requests = []
deadline = time.monotonic() + 2.5
prefix = "FLEETQOX_REPAIR_FRAGMENT_NACK_V1|"
while time.monotonic() < deadline:
    try:
        payload, _source = sock.recvfrom(65535)
    except socket.timeout:
        continue
    text = payload.decode("utf-8", errors="replace")
    if not text.startswith(prefix):
        continue
    fields = text[len(prefix):].split("|", 2)
    if len(fields) != 3:
        continue
    requests.append({
        "fragment_id": fields[0],
        "fragment_count": int(fields[1]),
        "indexes": fields[2],
    })
sock.close()

def valid_index_range(text):
    # Every NACK always starts at index 0 (only index 15/16 was ever sent),
    # and can carry anywhere from 1 to CONFIGURED_INDEX_LIMIT (8) indexes --
    # the exact count depends on the fleet-wide budget's fair share at the
    # moment this particular assembly's request happened to be built, which
    # varies with real UDP arrival timing (see run_probe's MIN/MAX bounds
    # comment). "0" means 1 index; "0-K" means K+1 indexes for K in 1..7.
    if text == "0":
        return True
    if "-" not in text:
        return False
    low, _, high = text.partition("-")
    if not (low.isdigit() and high.isdigit()):
        return False
    return int(low) == 0 and 1 <= int(high) <= 7


ids = {row["fragment_id"] for row in requests}
status = "ok" if (
    len(requests) == 513
    and len(ids) == 513
    and all(row["fragment_count"] == 16 for row in requests)
    and all(valid_index_range(row["indexes"]) for row in requests)
) else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.fragment_nack_fairness_injector.v1",
    "status": status,
    "request_count": len(requests),
    "unique_fragment_id_count": len(ids),
    "index_ranges": sorted({row["indexes"] for row in requests}),
}, sort_keys=True))
raise SystemExit(0 if status == "ok" else 1)
'''


def _valid_index_range(text: str) -> bool:
    # Mirrors INJECTOR_SCRIPT's valid_index_range: every NACK starts at
    # index 0 and carries 1 to CONFIGURED_INDEX_LIMIT (8) indexes depending
    # on the fleet budget's fair share at request-build time.
    if text == "0":
        return True
    low, sep, high = text.partition("-")
    if not sep or not low.isdigit() or not high.isdigit():
        return False
    return int(low) == 0 and 1 <= int(high) <= 7


def summarize_probe(
    receiver: dict[str, Any] | None,
    injector: dict[str, Any] | None,
    *,
    receiver_returncode: int,
    injector_returncode: int,
) -> dict[str, Any]:
    metrics = receiver.get("metrics") if isinstance(receiver, dict) else None
    receiver_ok = (
        receiver_returncode == 0
        and isinstance(receiver, dict)
        and receiver.get("schema_version") == RECEIVER_SCHEMA_VERSION
        and receiver.get("status") == "ok"
        and isinstance(metrics, dict)
        and int(metrics.get("fragment_active_assemblies", -1))
        == ASSEMBLY_COUNT
        and int(metrics.get("fragment_active_missing_indexes", -1))
        == EXPECTED_ACTIVE_MISSING_INDEXES
        and int(metrics.get("fragment_nack_exhausted_assemblies", -1))
        == ASSEMBLY_COUNT
        and int(metrics.get("fragment_nacks_sent", -1)) == ASSEMBLY_COUNT
        and MIN_INDEXES_REQUESTED
        <= int(metrics.get("fragment_nack_indexes_requested", -1))
        <= MAX_INDEXES_REQUESTED
        and MIN_BUDGET_REDUCTIONS
        <= int(metrics.get("fragment_nack_index_budget_reductions", -1))
        <= ASSEMBLY_COUNT
        and 0
        < int(metrics.get("fragment_nack_max_sweep_indexes_requested", -1))
        <= FLEET_INDEX_BUDGET
        and int(metrics.get("fragment_nack_sweep_budget_exhaustions", -1))
        >= 1
    )
    index_ranges = (
        injector.get("index_ranges", []) if isinstance(injector, dict) else []
    )
    injector_ok = (
        injector_returncode == 0
        and isinstance(injector, dict)
        and injector.get("schema_version") == INJECTOR_SCHEMA_VERSION
        and injector.get("status") == "ok"
        and int(injector.get("request_count", -1)) == ASSEMBLY_COUNT
        and int(injector.get("unique_fragment_id_count", -1))
        == ASSEMBLY_COUNT
        and bool(index_ranges)
        and all(_valid_index_range(value) for value in index_ranges)
    )
    contract_ok = receiver_ok and injector_ok
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if contract_ok else "failed",
        "assembly_count": ASSEMBLY_COUNT,
        "fragment_count": FRAGMENT_COUNT,
        "configured_index_limit": CONFIGURED_INDEX_LIMIT,
        "fleet_index_budget": FLEET_INDEX_BUDGET,
        "min_budget_reductions": MIN_BUDGET_REDUCTIONS,
        "min_indexes_requested": MIN_INDEXES_REQUESTED,
        "max_indexes_requested": MAX_INDEXES_REQUESTED,
        "receiver_returncode": receiver_returncode,
        "injector_returncode": injector_returncode,
        "fleet_aware_fragment_nack_fairness_claim": contract_ok,
        "bounded_fragment_repair_burst_claim": contract_ok,
        "production_large_sample_reliability_claim": False,
        "receiver": receiver,
        "injector": injector,
    }


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{time.time_ns()}"
    network = f"fleetrmw-fragment-fairness-net-{suffix}"
    receiver_name = f"fleetrmw-fragment-fairness-receiver-{suffix}"
    injector_name = f"fleetrmw-fragment-fairness-injector-{suffix}"
    work_dir = root / f".tmp_fleetrmw_fragment_fairness_{suffix}"
    receiver_script = work_dir / "receiver.py"
    injector_script = work_dir / "injector.py"
    ready_path = "/tmp/fleetrmw_fragment_fairness_ready"
    receiver_returncode = injector_returncode = -1
    receiver_result: dict[str, Any] | None = None
    injector_result: dict[str, Any] | None = None

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
            "sleep 20",
        ])
        receiver_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49812 && "
            f"export FLEETQOX_RMW_PEERS={injector_name}:49811 && "
            "export FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES=1024 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_INTERVAL_MS=200 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS=1 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST=8 && "
            "export FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT=600 && "
            "export FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES=4096 && "
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
        wait_for_container_path(receiver_name, ready_path, timeout_s=12.0)
        injector = run([
            "docker",
            "exec",
            injector_name,
            "python3",
            f"/work/{injector_script.relative_to(root)}",
            receiver_name,
        ], check=False)
        injector_returncode = injector.returncode
        injector_result = parse_last_json(injector.stdout)
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
        injector_result,
        receiver_returncode=receiver_returncode,
        injector_returncode=injector_returncode,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path(
            "results_rmw_socket/"
            "docker_fragment_nack_fairness_probe_summary.json"
        ),
    )
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image)
    path = ROOT / args.summary_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"status={summary['status']} "
        f"fairness={summary['fleet_aware_fragment_nack_fairness_claim']}"
    )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
