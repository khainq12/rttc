"""Prove source-scoped fragment repair isolation for multiple UDP readers."""

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


SCHEMA_VERSION = "fleetrmw.multireader_fragment_repair.v1"
PUBLISHER_SCHEMA_VERSION = "fleetrmw.multireader_fragment_repair_publisher.v1"
INJECTOR_SCHEMA_VERSION = "fleetrmw.multireader_fragment_repair_injector.v1"

PUBLISHER_SCRIPT = r'''
import ctypes
import json
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


rclpy.init()
node = Node("fleetrmw_multireader_fragment_repair_publisher")
publisher = node.create_publisher(
    String,
    "/fleetqox/multireader_fragment_repair",
    QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
)
deadline = time.monotonic() + 1.0
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)
message = String()
message.data = "m" * 32768
publisher.publish(message)

deadline = time.monotonic() + 6.0
while time.monotonic() < deadline:
    rclpy.spin_once(node, timeout_sec=0.05)

library = ctypes.CDLL("librmw_fleetqox_cpp.so")
names = (
    "test_dropped_fragments",
    "fragment_nacks_received",
    "fragments_selectively_retransmitted",
    "fragment_repair_requests_coalesced",
    "fragment_repair_source_denials",
    "fragment_repair_reader_budget_exhausted",
    "fragment_history_request_exhausted",
    "fragment_send_failures",
    "fragment_send_queue_rejections",
)
metrics = {}
for name in names:
    symbol = getattr(library, f"rmw_fleetqox_cpp_socket_{name}")
    symbol.restype = ctypes.c_uint64
    metrics[name] = int(symbol())

expected = {
    "test_dropped_fragments": 1,
    "fragment_nacks_received": 2,
    "fragments_selectively_retransmitted": 2,
    "fragment_repair_requests_coalesced": 0,
    "fragment_repair_source_denials": 1,
    "fragment_repair_reader_budget_exhausted": 0,
    "fragment_history_request_exhausted": 1,
    "fragment_send_failures": 0,
    "fragment_send_queue_rejections": 0,
}
status = "ok" if metrics == expected else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.multireader_fragment_repair_publisher.v1",
    "status": status,
    "metrics": metrics,
    "expected": expected,
}, sort_keys=True))
node.destroy_publisher(publisher)
node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if status == "ok" else 1)
'''

INJECTOR_SCRIPT = r'''
import json
import os
import select
import socket
import time


ports = (49821, 49822, 49823)
sockets = {}
for port in ports:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    sock.bind(("0.0.0.0", port))
    sock.setblocking(False)
    sockets[port] = sock
with open(os.environ["FLEETQOX_PROBE_READY_FILE"], "w", encoding="utf-8") as stream:
    stream.write("ready\n")

# The publisher container does not exist yet when this script starts (the
# injector is started first so it can begin listening before any fragments
# are sent), so its hostname is not yet resolvable via Docker's embedded
# DNS and a direct getaddrinfo()-based sendto() to it intermittently fails
# with "Name or service not known". The orchestrator resolves the
# publisher's real IP via `docker inspect` once that container exists and
# writes it to this file on the shared /work bind mount instead.
addr_file = os.environ["FLEETQOX_PUBLISHER_ADDR_FILE"]
publisher_ip = None
addr_deadline = time.monotonic() + 8.0
while time.monotonic() < addr_deadline:
    if os.path.exists(addr_file):
        with open(addr_file, "r", encoding="utf-8") as stream:
            publisher_ip = stream.read().strip()
        if publisher_ip:
            break
    time.sleep(0.05)
publisher = (publisher_ip, 49812)

prefix = "FLEETQOX_REPAIR_FRAGMENT_V1|"
seen = {49821: {}, 49822: {}}
counts = {49821: {}, 49822: {}}
repair_seen = {49821: 0, 49822: 0, 49823: 0}
requested = False
selected_id = None
selected_count = None
deadline = time.monotonic() + 10.0
while time.monotonic() < deadline:
    readable, _, _ = select.select(list(sockets.values()), [], [], 0.05)
    for sock in readable:
        payload, _source = sock.recvfrom(65535)
        text = payload.decode("utf-8", errors="replace")
        if not text.startswith(prefix):
            continue
        fields = text[len(prefix):].split("|", 4)
        if len(fields) != 5:
            continue
        fragment_id = fields[0]
        fragment_index = int(fields[1])
        fragment_count = int(fields[2])
        port = sock.getsockname()[1]
        if port in seen:
            indexes = seen[port].setdefault(fragment_id, set())
            counts[port][fragment_id] = fragment_count
            if requested and fragment_id == selected_id and fragment_index == 2:
                repair_seen[port] += 1
            indexes.add(fragment_index)

    if not requested:
        shared_ids = set(seen[49821]).intersection(seen[49822])
        candidates = [
            fragment_id
            for fragment_id in shared_ids
            if 0 in seen[49821][fragment_id]
            and 0 in seen[49822][fragment_id]
            and 2 not in seen[49821][fragment_id]
            and 2 not in seen[49822][fragment_id]
            and counts[49821].get(fragment_id)
            == counts[49822].get(fragment_id)
        ]
        if candidates:
            selected_id = sorted(candidates)[0]
            selected_count = counts[49821][selected_id]
            request = (
                f"FLEETQOX_REPAIR_FRAGMENT_NACK_V1|"
                f"{selected_id}|{selected_count}|2"
            ).encode()
            sockets[49823].sendto(request, publisher)
            sockets[49821].sendto(request, publisher)
            sockets[49822].sendto(request, publisher)
            requested = True
    if requested and repair_seen[49821] >= 1 and repair_seen[49822] >= 1:
        break

for sock in sockets.values():
    sock.close()
status = "ok" if (
    requested
    and repair_seen[49821] == 1
    and repair_seen[49822] == 1
    and repair_seen[49823] == 0
) else "failed"
print(json.dumps({
    "schema_version": "fleetrmw.multireader_fragment_repair_injector.v1",
    "status": status,
    "requested": requested,
    "fragment_id": selected_id,
    "fragment_count": selected_count,
    "repair_seen_by_port": {
        str(port): repair_seen[port] for port in ports
    },
}, sort_keys=True))
raise SystemExit(0 if status == "ok" else 1)
'''


def summarize_probe(
    publisher: dict[str, Any] | None,
    injector: dict[str, Any] | None,
    *,
    publisher_returncode: int,
    injector_returncode: int,
) -> dict[str, Any]:
    publisher_ok = (
        publisher_returncode == 0
        and isinstance(publisher, dict)
        and publisher.get("schema_version") == PUBLISHER_SCHEMA_VERSION
        and publisher.get("status") == "ok"
        and publisher.get("metrics") == publisher.get("expected")
    )
    injector_ok = (
        injector_returncode == 0
        and isinstance(injector, dict)
        and injector.get("schema_version") == INJECTOR_SCHEMA_VERSION
        and injector.get("status") == "ok"
        and injector.get("requested") is True
        and injector.get("repair_seen_by_port")
        == {"49821": 1, "49822": 1, "49823": 0}
    )
    contract_ok = publisher_ok and injector_ok
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if contract_ok else "failed",
        "publisher_returncode": publisher_returncode,
        "injector_returncode": injector_returncode,
        "source_scoped_fragment_repair_admission_claim": contract_ok,
        "multi_reader_fragment_repair_isolation_claim": contract_ok,
        "unauthorized_fragment_repair_source_fail_closed_claim": contract_ok,
        "production_large_sample_reliability_claim": False,
        "publisher": publisher,
        "injector": injector,
    }


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = f"{os.getpid()}-{time.time_ns()}"
    network = f"fleetrmw-multireader-repair-net-{suffix}"
    publisher_name = f"fleetrmw-multireader-repair-publisher-{suffix}"
    injector_name = f"fleetrmw-multireader-repair-injector-{suffix}"
    work_dir = root / f".tmp_fleetrmw_multireader_repair_{suffix}"
    publisher_script = work_dir / "publisher.py"
    injector_script = work_dir / "injector.py"
    publisher_addr_path = work_dir / "publisher_addr.txt"
    ready_path = "/tmp/fleetrmw_multireader_repair_ready"
    publisher_returncode = injector_returncode = -1
    publisher_result: dict[str, Any] | None = None
    injector_result: dict[str, Any] | None = None

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        publisher_script.write_text(PUBLISHER_SCRIPT, encoding="utf-8")
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
            (
                f"export FLEETQOX_PROBE_READY_FILE={ready_path} && "
                f"export FLEETQOX_PUBLISHER_ADDR_FILE="
                f"/work/{publisher_addr_path.relative_to(root)} && "
                f"python3 /work/{injector_script.relative_to(root)}"
            ),
        ])
        wait_for_container_path(injector_name, ready_path, timeout_s=12.0)
        # FLEETQOX_RMW_PEERS is parsed as a literal IP:port by the RMW (it
        # does not resolve hostnames), so the injector container's Docker
        # network hostname can't be used directly here even though the
        # injector's own raw socket usage of that hostname works fine
        # (Python's socket module resolves it via getaddrinfo).
        injector_ip = run([
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            injector_name,
        ]).stdout.strip()
        publisher_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49812 && "
            f"export FLEETQOX_RMW_PEERS={injector_ip}:49821,"
            f"{injector_ip}:49822 && "
            "export FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES=1024 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS=1 && "
            "export FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST=8 && "
            "export FLEETQOX_RMW_FRAGMENT_ASYNC_SEND=1 && "
            "export FLEETQOX_RMW_FRAGMENT_SEND_QUEUE_LIMIT=4096 && "
            "export FLEETQOX_RMW_FRAGMENT_REPAIR_COOLDOWN_MS=1000 && "
            "export FLEETQOX_RMW_TEST_DROP_FRAGMENT_INDEXES=2 && "
            f"python3 /work/{publisher_script.relative_to(root)}"
        )
        run([
            "docker",
            "run",
            "-d",
            "--name",
            publisher_name,
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
            publisher_command,
        ])
        publisher_ip = run([
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            publisher_name,
        ]).stdout.strip()
        publisher_addr_path.write_text(publisher_ip, encoding="utf-8")
        injector_returncode = int(
            run(["docker", "wait", injector_name]).stdout.strip()
        )
        injector_logs = run(["docker", "logs", injector_name], check=False)
        injector_result = parse_last_json(injector_logs.stdout)
        publisher_returncode = int(
            run(["docker", "wait", publisher_name]).stdout.strip()
        )
        publisher_logs = run(["docker", "logs", publisher_name], check=False)
        publisher_result = parse_last_json(publisher_logs.stdout)
    finally:
        for container in (publisher_name, injector_name):
            run(["docker", "rm", "-f", container], check=False)
        run(["docker", "network", "rm", network], check=False)
        shutil.rmtree(work_dir, ignore_errors=True)

    return summarize_probe(
        publisher_result,
        injector_result,
        publisher_returncode=publisher_returncode,
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
            "docker_multireader_fragment_repair_probe_summary.json"
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
        f"multireader={summary['multi_reader_fragment_repair_isolation_claim']}"
    )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
