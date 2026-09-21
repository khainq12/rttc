"""LAN Phase 4 (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY
FIRST-DIVERGENCE INVESTIGATION"): packet-level capture of CycloneDDS's
proven last-launched-endpoint isolation. Ports 7410/7411 (CycloneDDS's
own SPDP/SEDP metatraffic ports, confirmed via
investigate_lan_discovery_socket_mapping.py -- no discovery-server
process complicates this middleware, unlike Fast DDS). Captures on
BOTH the second-to-last and the last-launched endpoint's own eth0,
across the whole discovery window, to test directly:
  - do earlier participants continue sending discovery announcements
    after the last endpoint starts (packets FROM them arriving at the
    last endpoint, timestamped)?
  - does the last endpoint announce itself (packets FROM it arriving
    at earlier participants)?
This distinguishes packet-level loss/absence from a middleware-
processing-layer explanation (SPDP backoff, SEDP delay, or CPU
scheduling) -- the same distinction already drawn for Fast DDS.

Read-only diagnostic -- does not change any production code path.
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
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
TCPDUMP_PORTABLE_HOST_DIR = ROOT / ".tcpdump_portable"
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_cyclonedds_spdp_capture"


def deploy_portable_tcpdump(container_name: str) -> None:
    docker("cp", str(TCPDUMP_PORTABLE_HOST_DIR), f"{container_name}:/tmp/tcpdump_portable")


def start_capture(container_name: str, pcap_path_container: str, pid_file_container: str) -> None:
    cmd = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i eth0 -n -U "
        f"'portrange 7405-7415' -w /work/{pcap_path_container} "
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


def build_cyclonedds_config(probe: ReferenceTopologyProbe, endpoint: str) -> str:
    peer_xml = "".join(
        f'<Peer address="{probe.ips[other]}"/>' for other in probe.endpoints if other != endpoint
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<CycloneDDS xmlns="https://cdds.io/config">'
        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
        f"<Discovery><Peers>{peer_xml}</Peers>"
        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
        "</Domain></CycloneDDS>"
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_cyclonedds_spdp"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_cyclonedds_spdp",
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
    launch_times = {}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        print("Deploying portable tcpdump to every endpoint...", flush=True)
        for name in probe.endpoint_container_names:
            deploy_portable_tcpdump(name)
        for i, name in enumerate(probe.endpoint_container_names):
            pcap_c = f"{results_dir_container}/spdp_{i}.pcap"
            pid_c = f"{results_dir_container}/spdp_{i}.pid"
            pid_paths[i] = pid_c
            start_capture(name, pcap_c, pid_c)
        time.sleep(2.0)

        t0 = time.monotonic()
        for i, endpoint in enumerate(probe.endpoints):
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            cyclonedds_config = build_cyclonedds_config(probe, endpoint)
            cyclonedds_config_path = f"/tmp/cyclonedds_spdp_{i}.xml"
            docker(
                "exec", probe.endpoint_container_names[i], "bash", "-lc",
                f"echo {shlex.quote(cyclonedds_config)} > {cyclonedds_config_path}",
            )
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = f"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp CYCLONEDDS_URI={cyclonedds_config_path} "
            inner = (
                "source /opt/ros/jazzy/setup.bash && "
                f"export {env_prefix}&& "
                f"python3 -B /work/scripts/fleetqox_rmw_trace_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} "
                f"--endpoint={shlex.quote(endpoint)} "
                f"--policy=fifo "
                f"--start-offset-ms=2000.0 "
                f"--drain-s=10.0 "
                f"--discovery-timeout-s=15.0 "
                f"--start-wait-timeout-s=45.0 "
                f"--expected-peer-count={NUM_ROBOTS}"
                f" --required-peer-ids={shlex.quote(','.join(sorted(required)))} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{probe._ready_files[i]} "
                f"--start-file=/work/{probe._start_file}"
            )
            cmd = f"{inner} > /work/{log_file} 2>&1"
            docker("exec", "-d", probe.endpoint_container_names[i], "bash", "-lc", cmd)
            launch_times[endpoint] = time.monotonic() - t0

        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            findings["readiness"] = "ALL_READY"
        except Exception as exc:  # noqa: BLE001
            findings["readiness"] = f"NOT_READY: {exc}"
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
        findings["launch_times_s"] = launch_times

        pcap_summaries = {}
        for i, endpoint in enumerate(endpoints):
            pcap_host = ROOT / results_dir_container / f"spdp_{i}.pcap"
            pcap_summaries[endpoint] = read_pcap_summary(pcap_host)
        findings["pcap_by_endpoint"] = pcap_summaries
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "readiness": findings["readiness"],
        "readiness_by_endpoint": findings["readiness_by_endpoint"],
        "launch_times_s": findings["launch_times_s"],
    }, indent=2), flush=True)
    for endpoint, lines in findings["pcap_by_endpoint"].items():
        print(f"{endpoint}: {len(lines)} packets captured", flush=True)
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
