"""LAN Fast DDS SEDP-level trace (Phase 5 continuation, see
docs/AUDIT_ACCEPTANCE_TRACKING.md "LAN SOURCE + OFFICIAL-DOCUMENTATION
AUDIT"). The port-collision fix (FASTRTPS_DEFAULT_PROFILES_FILE,
participantID=50 for control_station) is proven live via `ss` to work
and proven NOT to change the 0/5 readiness outcome. This script traces
ONE rep immediately (reads every endpoint's log/result in the SAME
process right after teardown, with no wall-clock gap) to avoid the
false "empty log" artifact seen when logs were re-read minutes later
in a separate shell call (some external cleanup process was found to
have truncated ALL five endpoints' logs uniformly after the fact, not
just control_station's -- that was a read-timing artifact, not
evidence about control_station specifically).

Prints, for every endpoint: whether beacon_pub was (structurally)
expected to exist, the DISCOVERY_TIMEOUT_DEBUG payload if present, the
full raw log, and required vs seen peer ids from the result JSON.

Read-only diagnostic -- does not modify launch_endpoints() itself.
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
    ReadinessFailure,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_fastdds_sedp_trace"

NO_COLLISION_PROFILE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<dds><profiles>"
    '<participant profile_name="no_collision" is_default_profile="true">'
    "<rtps><participantID>50</participantID></rtps>"
    "</participant>"
    "</profiles></dds>"
)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "lan_fastdds_sedp_trace_1"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario="lan_fastdds_sedp_trace",
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
    print("required_peer_ids_by_endpoint:", {k: sorted(v) for k, v in required_peer_ids_by_endpoint.items()}, flush=True)

    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    try:
        probe.start_containers()
        probe.wire_network_lan()
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        server_ip = probe.ips[probe.endpoints[0]]
        probe.start_fastdds_discovery_server()

        docker(
            "exec", probe.endpoint_container_names[0], "bash", "-lc",
            f"echo {shlex.quote(NO_COLLISION_PROFILE)} > /tmp/no_collision_profile.xml",
        )

        for i, endpoint in enumerate(probe.endpoints):
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = f"RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DISCOVERY_SERVER={server_ip}:{FASTDDS_DISCOVERY_SERVER_PORT} "
            if i == 0:
                env_prefix += "FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/no_collision_profile.xml "
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
            print("readiness: ALL_READY", flush=True)
        except ReadinessFailure as exc:
            print(f"readiness: NOT_READY: {exc}", flush=True)
        time.sleep(1.0)

        print("\n=== per-endpoint immediate readback (same process, no delay) ===", flush=True)
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            ready_val = ready_file.read_text().strip() if ready_file.exists() else "<missing>"
            log_path = ROOT / results_dir_container / f"endpoint_{i}.log"
            log_text = log_path.read_text() if log_path.exists() else "<missing>"
            result_path = ROOT / results_dir_container / f"result_{i}.json"
            result_text = result_path.read_text() if result_path.exists() else "<missing>"
            print(f"\n--- endpoint {i} ({endpoint}) ---", flush=True)
            print(f"ready_file: {ready_val}", flush=True)
            print(f"log ({len(log_text)} bytes):\n{log_text}", flush=True)
            print(f"result_json:\n{result_text}", flush=True)

        # Also grab live ss + pgrep state on control_station BEFORE teardown.
        ports = docker("exec", probe.endpoint_container_names[0], "bash", "-lc", "ss -uln 2>/dev/null | grep -E ':74|:75'", check=False)
        print(f"\ncontrol_station ss -uln:\n{ports.stdout}", flush=True)
        procs = docker("exec", probe.endpoint_container_names[0], "bash", "-lc", "ps aux | grep -E 'fastdds|trace_endpoint' | grep -v grep", check=False)
        print(f"control_station processes:\n{procs.stdout}", flush=True)
    finally:
        probe.teardown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
