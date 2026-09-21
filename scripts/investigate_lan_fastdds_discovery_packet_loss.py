"""LAN Phase 1/2 packet-level diagnostic for Fast DDS LAN Table V
readiness failures (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN
DISCOVERY-CONVERGENCE ROOT-CAUSE INVESTIGATION"). N=4 (per this
investigation's own instruction: start small, not N=16).

Captures UDP traffic on EVERY endpoint's own eth0 (portable tcpdump,
same mechanism already used and documented for the Table VI N=8
network-loss investigation) on the Fast DDS discovery-server port
(11811), for the full discovery-server lifetime, then correlates
against the harness's own readiness outcome to answer directly: for
whichever endpoint fails, did its discovery packets ever leave its own
interface, and did the corresponding packets ever arrive at
control_station's interface (or vice versa)?

Read-only diagnostic -- does NOT change run_lan_probe()/launch_endpoints()
or any production code path.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    FASTDDS_DISCOVERY_SERVER_PORT,
    ReadinessFailure,
    ReferenceTopologyProbe,
    build_static_subscriptions,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
TCPDUMP_PORTABLE_HOST_DIR = ROOT / ".tcpdump_portable"
DISCOVERY_PORT = FASTDDS_DISCOVERY_SERVER_PORT
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_fastdds_discovery_packet_loss_n4"


def deploy_portable_tcpdump(container_name: str) -> None:
    docker("cp", str(TCPDUMP_PORTABLE_HOST_DIR), f"{container_name}:/tmp/tcpdump_portable")


def start_capture(container_name: str, pcap_path_container: str, pid_file_container: str) -> None:
    cmd = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i eth0 -n -U "
        f"udp -w /work/{pcap_path_container} "
        f"> /work/{pcap_path_container}.log 2>&1 & "
        f"echo $! > /work/{pid_file_container}"
    )
    docker("exec", "-d", container_name, "bash", "-lc", cmd)


def stop_capture(container_name: str, pid_file_host: Path) -> None:
    if not pid_file_host.exists():
        return
    pid = pid_file_host.read_text().strip()
    if not pid:
        return
    docker("exec", container_name, "bash", "-lc", f"kill -INT {pid} 2>/dev/null || true")


def read_pcap_summary(pcap_path: Path) -> list[str]:
    if not pcap_path.exists() or pcap_path.stat().st_size == 0:
        return []
    result = subprocess.run(
        ["tcpdump", "-r", str(pcap_path), "-tt", "-n", "-q"],
        capture_output=True, text=True,
    )
    return result.stdout.splitlines()


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_fastdds_pkt_loss_n4"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_fastdds_pkt_loss_{NUM_ROBOTS}robot",
        robots=NUM_ROBOTS,
        seconds=3,
        seed=SEED,
        capacity_bytes_per_second=max(200_000, NUM_ROBOTS * 6_000),
        capacity_packets_per_second=None,
        capacity_airtime_ns_per_second=None,
        policies=("fifo",),
        include_non_sent=False,
        merge_control_station=True,
    )
    write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"
    endpoints = endpoint_list(NUM_ROBOTS)
    required_peer_ids_by_endpoint = required_peers_from_trace(trace_path, "fifo", endpoints)
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    pcap_paths_container = {}
    pid_paths_container = {}
    findings: dict[str, Any] = {}
    try:
        probe.start_containers()
        probe.wire_network_lan()

        print("Deploying portable tcpdump to every endpoint...", flush=True)
        for name in probe.endpoint_container_names:
            deploy_portable_tcpdump(name)

        print("Starting captures BEFORE the discovery server (catch earliest packets)...", flush=True)
        for i, name in enumerate(probe.endpoint_container_names):
            pcap_c = f"{results_dir_container}/discovery_{i}.pcap"
            pid_c = f"{results_dir_container}/discovery_{i}.pid"
            pcap_paths_container[i] = pcap_c
            pid_paths_container[i] = pid_c
            start_capture(name, pcap_c, pid_c)
        time.sleep(4.0)

        t_start_server = time.monotonic()
        probe.start_fastdds_discovery_server()
        t_server_alive = time.monotonic()
        print(f"Discovery server alive after {t_server_alive - t_start_server:.2f}s", flush=True)

        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy="fifo",
            start_offset_ms=2000.0,
            drain_s=10.0,
            discovery_timeout_s=15.0,
            static_mode=False,
            static_subscriptions=None,
            extra_rmw_env=None,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=45.0,
            rmw_implementation="rmw_fastrtps_cpp",
            discovery_mode="discovery_server",
            required_peer_ids_by_endpoint=required_peer_ids_by_endpoint,
        )
        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            findings["readiness"] = "ALL_READY"
        except ReadinessFailure as exc:
            findings["readiness"] = f"INVALID_READINESS: {exc}"
        time.sleep(1.0)

        print("Stopping captures...", flush=True)
        for i, name in enumerate(probe.endpoint_container_names):
            stop_capture(name, ROOT / pid_paths_container[i])
        time.sleep(4.0)

        readiness_debug = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            log_file = ROOT / results_dir_container / f"endpoint_{i}.log"
            readiness_debug[endpoint] = {
                "ready_content": ready_file.read_text().strip() if ready_file.exists() else None,
                "log_tail": log_file.read_text()[-1200:] if log_file.exists() else None,
            }
        findings["readiness_debug"] = readiness_debug
        findings["required_peer_ids_by_endpoint"] = {
            k: sorted(v) for k, v in required_peer_ids_by_endpoint.items()
        }

        pcap_summaries = {}
        for i, endpoint in enumerate(endpoints):
            pcap_host = ROOT / results_dir_container / f"discovery_{i}.pcap"
            lines = read_pcap_summary(pcap_host)
            pcap_summaries[endpoint] = {
                "packet_count": len(lines),
                "first_10": lines[:10],
                "last_10": lines[-10:],
            }
        findings["pcap_summaries"] = pcap_summaries

    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    print(json.dumps({"readiness": findings["readiness"]}, indent=2), flush=True)
    for endpoint, debug in findings["readiness_debug"].items():
        print(f"{endpoint}: ready={debug['ready_content']}", flush=True)
    for endpoint, summary in findings["pcap_summaries"].items():
        print(f"{endpoint}: {summary['packet_count']} discovery-port packets captured", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
