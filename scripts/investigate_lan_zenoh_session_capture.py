"""LAN Phase 5 (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY
FIRST-DIVERGENCE INVESTIGATION"): Zenoh is not DDS -- traces the actual
session lifecycle instead: TCP connect -> Zenoh session established ->
declaration exchange -> harness-observed readiness. Socket mapping
(investigate_lan_discovery_socket_mapping.py) already showed a
STRUCTURAL asymmetry: control_station's own client connects to its
co-located router via 127.0.0.1 (Zenoh's default scouting/connect
behavior, since launch_endpoints() deliberately skips giving
control_station an explicit --connect session config, unlike every
robot which points directly at the router's real overlay IP -- see
"Skipped for the router's own container" in that function's own
comment). This script captures TCP port 7447 on control_station for a
live N=4 run and compares byte-volume-over-time between control_station's
own loopback session and a robot's real-IP session, to see whether the
loopback session is structurally quieter (stuck) or comparably active.

Read-only diagnostic -- does not change any production code path.
"""

from __future__ import annotations

import json
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
    ReadinessFailure,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
TCPDUMP_PORTABLE_HOST_DIR = ROOT / ".tcpdump_portable"
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_zenoh_session_capture"


def deploy_portable_tcpdump(container_name: str) -> None:
    docker("cp", str(TCPDUMP_PORTABLE_HOST_DIR), f"{container_name}:/tmp/tcpdump_portable")


def start_capture(container_name: str, pcap_path_container: str, pid_file_container: str) -> None:
    cmd = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i eth0 -n -U "
        f"'tcp port 7447 or udp port 7446' -w /work/{pcap_path_container} "
        f"> /work/{pcap_path_container}.log 2>&1 & "
        f"echo $! > /work/{pid_file_container}"
    )
    docker("exec", "-d", container_name, "bash", "-lc", cmd)
    # Also capture on loopback (lo) -- control_station's own session to
    # its co-located router goes over 127.0.0.1, invisible on eth0.
    cmd_lo = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i lo -n -U "
        f"'tcp port 7447' -w /work/{pcap_path_container}.lo "
        f"> /work/{pcap_path_container}.lo.log 2>&1 & "
        f"echo $! > /work/{pid_file_container}.lo"
    )
    docker("exec", "-d", container_name, "bash", "-lc", cmd_lo)


def stop_capture(container_name: str, pid_file_host: Path) -> None:
    for suffix in ("", ".lo"):
        p = Path(str(pid_file_host) + suffix)
        if not p.exists():
            continue
        pid = p.read_text().strip()
        if pid:
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
    run_id = "lan_zenoh_session"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_zenoh_session",
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
    findings: dict[str, Any] = {}
    pid_paths = {}
    try:
        probe.start_containers()
        probe.wire_network_lan()

        print("Deploying portable tcpdump...", flush=True)
        for name in probe.endpoint_container_names:
            deploy_portable_tcpdump(name)
        for i, name in enumerate(probe.endpoint_container_names):
            pcap_c = f"{results_dir_container}/zen_{i}.pcap"
            pid_c = f"{results_dir_container}/zen_{i}.pid"
            pid_paths[i] = pid_c
            start_capture(name, pcap_c, pid_c)
        time.sleep(2.0)

        probe.start_zenoh_router()

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
            rmw_implementation="rmw_zenoh_cpp",
            discovery_mode="default",
            required_peer_ids_by_endpoint=required_peer_ids_by_endpoint,
        )
        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            findings["readiness"] = "ALL_READY"
        except ReadinessFailure as exc:
            findings["readiness"] = f"INVALID_READINESS: {exc}"
        time.sleep(2.0)

        print("Stopping captures...", flush=True)
        for i, name in enumerate(probe.endpoint_container_names):
            stop_capture(name, ROOT / pid_paths[i])
        time.sleep(4.0)

        readiness_debug = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            readiness_debug[endpoint] = ready_file.read_text().strip() if ready_file.exists() else None
        findings["readiness_by_endpoint"] = readiness_debug

        pcap_summaries = {}
        for i, endpoint in enumerate(endpoints):
            eth_lines = read_pcap_summary(ROOT / results_dir_container / f"zen_{i}.pcap")
            lo_lines = read_pcap_summary(ROOT / results_dir_container / f"zen_{i}.pcap.lo")
            pcap_summaries[endpoint] = {"eth0": eth_lines, "lo": lo_lines}
        findings["pcap_by_endpoint"] = pcap_summaries
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"readiness": findings["readiness"], "readiness_by_endpoint": findings["readiness_by_endpoint"]}, indent=2), flush=True)
    for endpoint, s in findings["pcap_by_endpoint"].items():
        print(f"{endpoint}: eth0={len(s['eth0'])} lo={len(s['lo'])} packets", flush=True)
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
