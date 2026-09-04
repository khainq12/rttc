"""Prove duplicate fragments cannot postpone tail-index repair in Docker."""

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


SCHEMA_VERSION = "fleetrmw.fragment_tail_progress.v1"
RECEIVER_SCHEMA_VERSION = "fleetrmw.fragment_tail_progress_receiver.v1"
INJECTOR_SCHEMA_VERSION = "fleetrmw.fragment_tail_progress_injector.v1"
TAIL_GUARD_MS = 400
DUPLICATE_COUNT = 20


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
node = Node("fleetrmw_fragment_tail_progress_receiver")
subscription = node.create_subscription(
    String,
    "/fleetqox/fragment_tail_progress",
    lambda _message: None,
    QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
)
with open(os.environ["FLEETQOX_PROBE_READY_FILE"], "w", encoding="utf-8") as stream:
    stream.write("ready\n")

library = ctypes.CDLL("librmw_fleetqox_cpp.so")
names = (
    "fragment_active_assemblies",
    "fragment_active_missing_indexes",
    "fragment_nack_exhausted_assemblies",
    "fragment_nacks_sent",
    "fragment_nack_indexes_requested",
    "fragment_duplicate_no_progress_drops",
)
symbols = {}
for name in names:
    symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
    symbol.restype = ctypes.c_uint64
    symbols[name] = symbol

# The injector sends 20 duplicate fragments over real network round
# trips; under load that can take longer than a fixed short window, so
# poll for the drop counter to actually reach 20 instead of reading it
# once after an arbitrary delay and risking a stale, in-progress count.
deadline = time.monotonic() + 5.0
metrics = {}
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.02)
    metrics = {name: int(symbol()) for name, symbol in symbols.items()}
    if metrics["fragment_duplicate_no_progress_drops"] >= 20:
        break

expected = {
    "fragment_active_assemblies": 1,
    "fragment_active_missing_indexes": 2,
    "fragment_nack_exhausted_assemblies": 1,
    "fragment_nacks_sent": 1,
    "fragment_nack_indexes_requested": 2,
    "fragment_duplicate_no_progress_drops": 20,
}
status = "ok" if metrics == expected else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.fragment_tail_progress_receiver.v1",
    "status": status,
    "metrics": metrics,
    "expected": expected,
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
sock.settimeout(0.04)
prefix = "FLEETQOX_REPAIR_FRAGMENT_NACK_V1|"
sock.sendto(b"FLEETQOX_REPAIR_FRAGMENT_V1|tail-progress|0|4|8|ab", target)
sock.sendto(b"FLEETQOX_REPAIR_FRAGMENT_V1|tail-progress|1|4|8|cd", target)
started = time.monotonic()
requests = []
first_nack_elapsed_ms = None
duplicate_count = 20
for _index in range(duplicate_count):
    sock.sendto(b"FLEETQOX_REPAIR_FRAGMENT_V1|tail-progress|0|4|8|ab", target)
    iteration_deadline = time.monotonic() + 0.05
    while time.monotonic() < iteration_deadline:
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
        if first_nack_elapsed_ms is None:
            first_nack_elapsed_ms = (time.monotonic() - started) * 1000.0
sock.close()

duplicate_stream_elapsed_ms = (time.monotonic() - started) * 1000.0
nack_during_duplicate_stream = first_nack_elapsed_ms is not None
status = "ok" if (
    len(requests) == 1
    and requests[0] == {
        "fragment_id": "tail-progress",
        "fragment_count": 4,
        "indexes": "2-3",
    }
    and nack_during_duplicate_stream
    and first_nack_elapsed_ms >= 350.0
    and first_nack_elapsed_ms < 850.0
    and duplicate_stream_elapsed_ms >= 900.0
) else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.fragment_tail_progress_injector.v1",
    "status": status,
    "duplicate_count": duplicate_count,
    "duplicate_stream_elapsed_ms": duplicate_stream_elapsed_ms,
    "first_nack_elapsed_ms": first_nack_elapsed_ms,
    "nack_during_duplicate_stream": nack_during_duplicate_stream,
    "requests": requests,
}, sort_keys=True))
raise SystemExit(0 if status == "ok" else 1)
'''


def summarize_probe(
    receiver: dict[str, Any] | None,
    injector: dict[str, Any] | None,
    *,
    receiver_returncode: int,
    injector_returncode: int,
) -> dict[str, Any]:
    receiver_ok = (
        receiver_returncode == 0
        and isinstance(receiver, dict)
        and receiver.get("schema_version") == RECEIVER_SCHEMA_VERSION
        and receiver.get("status") == "ok"
        and receiver.get("metrics") == receiver.get("expected")
    )
    injector_ok = (
        injector_returncode == 0
        and isinstance(injector, dict)
        and injector.get("schema_version") == INJECTOR_SCHEMA_VERSION
        and injector.get("status") == "ok"
        and int(injector.get("duplicate_count", 0)) == DUPLICATE_COUNT
        and injector.get("nack_during_duplicate_stream") is True
        and 350.0 <= float(injector.get("first_nack_elapsed_ms", 0.0)) < 850.0
        and injector.get("requests") == [{
            "fragment_id": "tail-progress",
            "fragment_count": 4,
            "indexes": "2-3",
        }]
    )
    contract_ok = receiver_ok and injector_ok
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if contract_ok else "failed",
        "tail_guard_ms": TAIL_GUARD_MS,
        "duplicate_count": DUPLICATE_COUNT,
        "receiver_returncode": receiver_returncode,
        "injector_returncode": injector_returncode,
        "duplicate_fragment_no_progress_claim": contract_ok,
        "tail_repair_bounded_under_duplicate_pressure_claim": contract_ok,
        "production_large_sample_reliability_claim": False,
        "receiver": receiver,
        "injector": injector,
    }


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{time.time_ns() % 1_000_000_000}"
    network = f"fq-tail-net-{suffix}"
    receiver_name = f"fq-tail-r-{suffix}"
    injector_name = f"fq-tail-i-{suffix}"
    work_dir = root / f".tmp_fleetrmw_fragment_tail_progress_{suffix}"
    receiver_script = work_dir / "receiver.py"
    injector_script = work_dir / "injector.py"
    ready_path = "/tmp/fleetrmw_fragment_tail_progress_ready"
    receiver_returncode = injector_returncode = -1
    receiver_result: dict[str, Any] | None = None
    injector_result: dict[str, Any] | None = None
    receiver_logs_text = ""

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        receiver_script.write_text(RECEIVER_SCRIPT, encoding="utf-8")
        injector_script.write_text(INJECTOR_SCRIPT, encoding="utf-8")
        ensure_rmw_build(root=root, image=image)
        run(["docker", "network", "create", network])
        run([
            "docker", "run", "-d", "--name", injector_name,
            "--network", network, "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image,
            "-lc", "sleep 20",
        ])
        receiver_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49812 && "
            f"export FLEETQOX_RMW_PEERS={injector_name}:49811 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_INTERVAL_MS=100 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS=1 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST=8 && "
            f"export FLEETQOX_RMW_FRAGMENT_TAIL_GUARD_MS={TAIL_GUARD_MS} && "
            "export FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT=8 && "
            "export FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES=4096 && "
            f"export FLEETQOX_PROBE_READY_FILE={ready_path} && "
            f"python3 /work/{receiver_script.relative_to(root)}"
        )
        run([
            "docker", "run", "-d", "--name", receiver_name,
            "--network", network, "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image,
            "-lc", receiver_command,
        ])
        receiver_ready = True
        try:
            wait_for_container_path(receiver_name, ready_path, timeout_s=12.0)
        except Exception:
            receiver_ready = False
        if receiver_ready:
            injector = run([
                "docker", "exec", injector_name, "python3",
                f"/work/{injector_script.relative_to(root)}", receiver_name,
            ], check=False)
            injector_returncode = injector.returncode
            injector_result = parse_last_json(injector.stdout)
        receiver_returncode = int(run(["docker", "wait", receiver_name]).stdout.strip())
        receiver_logs = run(["docker", "logs", receiver_name], check=False)
        receiver_logs_text = receiver_logs.stdout + receiver_logs.stderr
        receiver_result = parse_last_json(receiver_logs.stdout)
    finally:
        for container in (receiver_name, injector_name):
            run(["docker", "rm", "-f", container], check=False)
        run(["docker", "network", "rm", network], check=False)
        shutil.rmtree(work_dir, ignore_errors=True)

    summary = summarize_probe(
        receiver_result,
        injector_result,
        receiver_returncode=receiver_returncode,
        injector_returncode=injector_returncode,
    )
    summary["receiver_logs"] = receiver_logs_text
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path(
            "results_rmw_socket/docker_fragment_tail_progress_probe_summary.json"
        ),
    )
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image)
    path = ROOT / args.summary_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"status={summary['status']} "
        f"duplicate_no_progress={summary['duplicate_fragment_no_progress_claim']}"
    )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
