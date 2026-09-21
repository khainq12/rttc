"""LAN internal-logging semantic trace for CycloneDDS (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY SEMANTIC-LAYER
INVESTIGATION"). Unlike Fast DDS (whose Info-level logging is compiled
out of this prebuilt package), CycloneDDS's `<Tracing>` config element
is a standard, always-available runtime configuration (no rebuild
needed) -- adds `<Tracing><Verbosity>finest</Verbosity><OutputFile>`
to the SAME per-endpoint CYCLONEDDS_URI XML config this investigation
already uses for static peers, routing each endpoint's own trace to a
separate file collected afterward. Diagnostic only -- does not change
discovery/QoS/timeout semantics, only where CycloneDDS's own pre-
existing internal trace messages are written.

Reproduces the already-proven launch-position failure at N=4 (last-
launched endpoint fails) with full discovery tracing enabled on every
endpoint, to find the first semantic event (participant discovered,
endpoint discovered, writer-reader match, ...) that differs between
the second-to-last (PASS) and last-launched (FAIL) endpoint.
"""

from __future__ import annotations

import json
import shlex
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
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_cyclonedds_semantic_trace"


def build_cyclonedds_config(probe: ReferenceTopologyProbe, endpoint: str, i: int, trace_container: str) -> str:
    peer_xml = "".join(
        f'<Peer address="{probe.ips[other]}"/>' for other in probe.endpoints if other != endpoint
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<CycloneDDS xmlns="https://cdds.io/config">'
        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
        f"<Discovery><Peers>{peer_xml}</Peers>"
        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
        "<Tracing>"
        "<Verbosity>finest</Verbosity>"
        f"<OutputFile>{trace_container}</OutputFile>"
        "</Tracing>"
        "</Domain></CycloneDDS>"
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_cyclonedds_semantic"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_cyclonedds_semantic",
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
    try:
        probe.start_containers()
        probe.wire_network_lan()
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        launch_times = {}
        t0 = time.monotonic()
        for i, endpoint in enumerate(probe.endpoints):
            trace_container = f"/work/{results_dir_container}/cdds_trace_{i}.log"
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            cyclonedds_config = build_cyclonedds_config(probe, endpoint, i, trace_container)
            cyclonedds_config_path = f"/tmp/cyclonedds_semantic_{i}.xml"
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

        readiness_debug = {}
        cdds_traces = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            readiness_debug[endpoint] = ready_file.read_text().strip() if ready_file.exists() else None
            trace_file = ROOT / results_dir_container / f"cdds_trace_{i}.log"
            cdds_traces[endpoint] = trace_file.read_text() if trace_file.exists() else "(no trace file)"
        findings["readiness_by_endpoint"] = readiness_debug
        findings["launch_times_s"] = launch_times
        findings["cdds_traces"] = cdds_traces
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "readiness": findings["readiness"],
        "readiness_by_endpoint": findings["readiness_by_endpoint"],
        "launch_times_s": findings["launch_times_s"],
    }, indent=2), flush=True)
    for endpoint, trace in findings["cdds_traces"].items():
        print(f"{endpoint}: trace length = {len(trace)} chars, {len(trace.splitlines())} lines", flush=True)
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
