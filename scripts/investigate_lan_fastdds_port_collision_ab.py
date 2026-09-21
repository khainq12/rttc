"""LAN Fast DDS port-collision A/B (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"LAN DISCOVERY SEMANTIC-LAYER INVESTIGATION"). Socket mapping proved
control_station's own client process collides with the co-located
discovery-server process over port 7411 (the standard participant-ID-0
metatraffic/user port), landing on a non-standard 7410+7413 pair
instead of the standard 7410+7411 pair every robot gets. Tests
directly: does giving control_station's client an EXPLICIT,
non-conflicting participant ID (via Fast DDS's own documented
`<rtps><participantID>` XML profile element -- confirmed present in
fastRTPS_profiles.xsd) -- landing it on a completely clean, standard
port pair far from the discovery server's occupied range -- make
readiness deterministic?

A = current (no explicit participantID, the observed collision).
B = control_station given an explicit, non-conflicting participantID
    (50) via FASTDDS_DEFAULT_PROFILES_FILE; every robot's config
    unchanged.

Read-only diagnostic -- does not modify launch_endpoints() itself.
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
    ReadinessFailure,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

NUM_ROBOTS = 4
SEED = 7
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_fastdds_port_collision_ab"
REPEATS = 5

NO_COLLISION_PROFILE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<dds><profiles>"
    '<participant profile_name="no_collision" is_default_profile="true">'
    "<rtps><participantID>50</participantID></rtps>"
    "</participant>"
    "</profiles></dds>"
)


def run_variant(variant: str, rep: int) -> dict:
    run_id = f"lan_fastdds_pcab_{variant}_{rep}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_fastdds_pcab_{variant}",
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
    result = {"variant": variant, "rep": rep}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        probe.start_fastdds_discovery_server()

        if variant == "B_no_collision":
            docker(
                "exec", probe.endpoint_container_names[0], "bash", "-lc",
                f"echo {shlex.quote(NO_COLLISION_PROFILE)} > /tmp/no_collision_profile.xml",
            )

        extra_env = None  # applied uniformly below only for control_station in B

        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"
        server_ip = probe.ips[probe.endpoints[0]]
        from scripts.run_ns3_docker_container_fleet_probe import FASTDDS_DISCOVERY_SERVER_PORT

        for i, endpoint in enumerate(probe.endpoints):
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            env_prefix = f"RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DISCOVERY_SERVER={server_ip}:{FASTDDS_DISCOVERY_SERVER_PORT} "
            if variant == "B_no_collision" and i == 0:
                env_prefix += "FASTDDS_DEFAULT_PROFILES_FILE=/tmp/no_collision_profile.xml "
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
            result["readiness"] = "ALL_READY"
        except ReadinessFailure as exc:
            result["readiness"] = f"NOT_READY: {exc}"
        except Exception as exc:  # noqa: BLE001
            result["readiness"] = f"NOT_READY: {exc}"
        time.sleep(1.0)

        readiness_by_endpoint = {}
        for i, endpoint in enumerate(endpoints):
            ready_file = ROOT / results_dir_container / f"ready_{i}"
            readiness_by_endpoint[endpoint] = ready_file.read_text().strip() if ready_file.exists() else None
        result["readiness_by_endpoint"] = readiness_by_endpoint
        result["all_ready"] = all(v == "ready" for v in readiness_by_endpoint.values())

        if variant == "B_no_collision":
            r = docker("exec", probe.endpoint_container_names[0], "bash", "-lc", "ss -uln 2>/dev/null | grep -E ':74|:75'", check=False)
            result["control_station_ports"] = r.stdout
    finally:
        probe.teardown()
    return result


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for variant in ("A_current", "B_no_collision"):
        for rep in range(1, REPEATS + 1):
            print(f"=== {variant} rep {rep}/{REPEATS} ===", flush=True)
            r = run_variant(variant, rep)
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "readiness_by_endpoint"}, indent=2), flush=True)
            print(f"  readiness_by_endpoint: {r['readiness_by_endpoint']}", flush=True)

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    for variant in ("A_current", "B_no_collision"):
        clean = sum(1 for r in results if r["variant"] == variant and r["all_ready"])
        total = sum(1 for r in results if r["variant"] == variant)
        print(f"{variant}: {clean}/{total} fully ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
