"""LAN internal-logging semantic trace for Fast DDS (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY SEMANTIC-LAYER
INVESTIGATION"). Fast DDS's own EPROSIMA_LOG_INFO call sites are
compiled OUT of this prebuilt package (confirmed by reading
fastdds/dds/log/Log.hpp directly -- no FASTDDS_ENFORCE_LOG_INFO
define), so Info-level discovery events ("participant discovered",
etc.) cannot be surfaced at ANY runtime verbosity setting. Warning/
Error level logging IS compiled in and IS controllable at runtime via
the public Log::SetVerbosity/RegisterConsumer API -- exposed here via
a small LD_PRELOAD constructor shim (.fastdds_log_shim/verbosity_shim.so,
built from verbosity_shim.cpp, linked against the SAME libfastrtps.so
this process already loads) that forces Warning verbosity + a stdout
consumer before rclpy/the RMW initializes. Diagnostic only -- does not
change any middleware behavior, QoS, or configuration; only where its
own PRE-EXISTING log messages are printed.

Applies LD_PRELOAD via launch_endpoints()'s existing extra_rmw_env
mechanism (no custom launcher needed) for every endpoint in a real N=4
run, then extracts and compares each endpoint's own Fast DDS warning
log against its readiness outcome.
"""

from __future__ import annotations

import json
import sys
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
SHIM_HOST_PATH = ROOT / ".fastdds_log_shim" / "verbosity_shim.so"
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_fastdds_semantic_trace"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_fastdds_semantic"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_fastdds_semantic",
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

        print("Deploying Fast DDS log-verbosity shim to every endpoint...", flush=True)
        for name in probe.endpoint_container_names:
            docker("cp", str(SHIM_HOST_PATH), f"{name}:/tmp/verbosity_shim.so")

        probe.start_fastdds_discovery_server()
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy="fifo",
            start_offset_ms=2000.0,
            drain_s=10.0,
            discovery_timeout_s=15.0,
            static_mode=False,
            static_subscriptions=None,
            extra_rmw_env={"LD_PRELOAD": "/tmp/verbosity_shim.so"},
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
        import time
        time.sleep(2.0)

        readiness_debug = {}
        logs = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            log_file = ROOT / results_dir_container / f"endpoint_{i}.log"
            readiness_debug[endpoint] = ready_file.read_text().strip() if ready_file.exists() else None
            logs[endpoint] = log_file.read_text() if log_file.exists() else ""
        findings["readiness_by_endpoint"] = readiness_debug
        findings["logs_by_endpoint"] = logs
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"readiness": findings["readiness"], "readiness_by_endpoint": findings["readiness_by_endpoint"]}, indent=2), flush=True)
    for endpoint, log in findings["logs_by_endpoint"].items():
        warning_lines = [l for l in log.splitlines() if "Warning]" in l]
        print(f"\n=== {endpoint} ({len(warning_lines)} Fast DDS warnings) ===")
        for l in warning_lines:
            # Strip ANSI color codes for readability.
            import re
            clean = re.sub(r"\x1b\[[0-9;]*m", "", l)
            print(f"  {clean}")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
