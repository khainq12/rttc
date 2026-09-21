"""LAN internal-logging semantic trace for Zenoh (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY SEMANTIC-LAYER
INVESTIGATION"). rmw_zenoh_cpp/zenoh-cpp-vendor is built on Rust
zenoh, which uses the standard `env_logger`/`tracing` RUST_LOG
environment variable -- no XML profile or LD_PRELOAD shim needed.
Diagnostic only -- does not change any middleware behavior/QoS/routing,
only where its own pre-existing log messages are printed.

Reproduces a live N=4 run with RUST_LOG=zenoh=debug on every endpoint
(via launch_endpoints()'s existing extra_rmw_env mechanism) and on the
router process itself, to find the first semantic event (session
established, declaration sent/received, subscription registered,
routing state) that differs between a PASS and FAIL endpoint.
"""

from __future__ import annotations

import json
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
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_zenoh_semantic_trace"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_zenoh_semantic"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_zenoh_semantic",
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
        probe.start_zenoh_router()

        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy="fifo",
            start_offset_ms=2000.0,
            drain_s=10.0,
            discovery_timeout_s=15.0,
            static_mode=False,
            static_subscriptions=None,
            extra_rmw_env={"RUST_LOG": "zenoh=debug"},
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

        readiness_debug = {}
        logs = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            log_file = ROOT / results_dir_container / f"endpoint_{i}.log"
            readiness_debug[endpoint] = ready_file.read_text().strip() if ready_file.exists() else None
            logs[endpoint] = log_file.read_text() if log_file.exists() else ""
        findings["readiness_by_endpoint"] = readiness_debug
        findings["log_lengths"] = {k: len(v) for k, v in logs.items()}
        (output_dir / "logs").mkdir(exist_ok=True)
        for endpoint, log in logs.items():
            (output_dir / "logs" / f"{endpoint}.log").write_text(log)
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(findings, indent=2), flush=True)
    print(f"\nWrote {out_path}, per-endpoint logs in {output_dir / 'logs'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
