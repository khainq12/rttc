"""LAN Phase 2 hypothesis test (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"LAN DISCOVERY-CONVERGENCE ROOT-CAUSE INVESTIGATION"): control_station
hosts the Fast DDS discovery server AND runs its own ROS2 endpoint
process as a CLIENT of that same server, both in the SAME container.
Every endpoint (including control_station itself) is currently pointed
at the server's REAL overlay IP via ROS_DISCOVERY_SERVER. Packet
capture (investigate_lan_fastdds_discovery_packet_loss.py) showed
control_station's client sees ZERO of the other participants while
every remote robot sees control_station just fine -- consistent with a
co-located-client addressing/routing asymmetry.

This script tests ONE targeted hypothesis: does control_station's OWN
client discover its peers if IT ALONE uses 127.0.0.1 (loopback) to
reach its own co-located server, while every remote robot keeps using
the server's real overlay IP unchanged? A minimal, targeted,
diagnostic-only change -- manually launches control_station's endpoint
process with a patched ROS_DISCOVERY_SERVER instead of going through
launch_endpoints() (which currently gives every endpoint the identical
real-IP value) -- does NOT modify any production script.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    FASTDDS_DISCOVERY_SERVER_PORT,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_fastdds_loopback_hypothesis_n4"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_fastdds_loopback_n4"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_fastdds_loopback_{NUM_ROBOTS}robot",
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
    findings = {}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        probe.start_fastdds_discovery_server()

        # Manually launch each endpoint's own Fast DDS process with an
        # explicit per-endpoint ROS_DISCOVERY_SERVER, mirroring
        # launch_endpoints()'s own command construction for the
        # standard-RMW branch, EXCEPT control_station (index 0) gets
        # 127.0.0.1 instead of its own real IP -- NOT going through
        # launch_endpoints() at all here (it would give every endpoint
        # the identical real-IP value, the thing under test).
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"
        server_ip = probe.ips[probe.endpoints[0]]
        for i, endpoint in enumerate(probe.endpoints):
            discovery_server_value = (
                f"127.0.0.1:{FASTDDS_DISCOVERY_SERVER_PORT}" if i == 0
                else f"{server_ip}:{FASTDDS_DISCOVERY_SERVER_PORT}"
            )
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = f"RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DISCOVERY_SERVER={discovery_server_value} "
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

        try:
            probe.wait_for_ready_then_start(ready_deadline_s=30.0)
            findings["readiness"] = "ALL_READY"
        except Exception as exc:  # noqa: BLE001
            findings["readiness"] = f"NOT_READY: {exc}"
        time.sleep(1.0)

        readiness_debug = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            log_file = ROOT / results_dir_container / f"endpoint_{i}.log"
            readiness_debug[endpoint] = {
                "ready_content": ready_file.read_text().strip() if ready_file.exists() else None,
                "log_tail": log_file.read_text()[-800:] if log_file.exists() else None,
            }
        findings["readiness_debug"] = readiness_debug
    finally:
        probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"readiness": findings["readiness"]}, indent=2), flush=True)
    for endpoint, debug in findings["readiness_debug"].items():
        print(f"{endpoint}: ready={debug['ready_content']}", flush=True)
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
