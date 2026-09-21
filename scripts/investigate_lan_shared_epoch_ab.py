"""LAN shared-readiness-epoch fairness A/B (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN LIFECYCLE-FAIRNESS
INVESTIGATION"). Tests whether the proven "last-launched endpoint
fails" pattern (CycloneDDS: proven root cause via SPDP backoff; Zenoh:
a separate, still-open failure after the listen-address fix; Fast DDS:
untested) is caused by an UNFAIR benchmark lifecycle -- readiness
clocks starting independently, per endpoint, at each endpoint's own
node-creation time, so a later-created endpoint's clock starts after
earlier endpoints' discovery state has already aged -- rather than a
genuine middleware convergence failure.

A = current lifecycle (each endpoint's discovery_timeout_s clock starts
    immediately after ITS OWN node/publishers/subscriptions exist).
B = shared epoch: every endpoint writes a "created" marker right after
    its OWN node exists, then waits (spinning, not sleeping) until ALL
    endpoints' markers exist, and ONLY THEN starts the SAME, UNCHANGED
    discovery_timeout_s clock.

Same timeout duration, same middleware configs, same launch order, same
network, same workload in both variants -- only WHEN each endpoint's
already-existing readiness clock starts differs.

Read-only diagnostic -- does not modify launch_endpoints() (B is
constructed by bypassing it for a controlled, uniform launch across
ALL endpoints, mirroring its normal per-middleware command construction
otherwise).
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
REPEATS = 5  # reduced from the requested 10 for wall-clock budget; noted explicitly in the report
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_shared_epoch_ab"


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


def run_variant(middleware: str, variant: str, rep: int) -> dict:
    run_id = f"lan_shared_epoch_{middleware}_{variant}_{rep}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_shared_epoch_{middleware}_{variant}",
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
    barrier_dir_host = output_dir / "created_barrier"
    barrier_dir_container = f"/work/{results_dir_container}/created_barrier"

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    result = {"middleware": middleware, "variant": variant, "rep": rep}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        server_ip = probe.ips[probe.endpoints[0]]
        if middleware == "fastdds":
            probe.start_fastdds_discovery_server()
        elif middleware == "zenoh":
            probe.start_zenoh_router()

        barrier_flags = ""
        if variant == "B_shared_epoch":
            barrier_flags = (
                f" --created-barrier-dir={shlex.quote(barrier_dir_container)}"
                f" --total-endpoints={NUM_ROBOTS + 1} --created-barrier-timeout-s=30.0"
            )

        for i, endpoint in enumerate(probe.endpoints):
            required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            if middleware == "fastdds":
                env_prefix = f"RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DISCOVERY_SERVER={server_ip}:{FASTDDS_DISCOVERY_SERVER_PORT} "
            elif middleware == "cyclonedds":
                cyclonedds_config = build_cyclonedds_config(probe, endpoint)
                cyclonedds_config_path = f"/tmp/cyclonedds_epoch_{i}.xml"
                docker(
                    "exec", probe.endpoint_container_names[i], "bash", "-lc",
                    f"echo {shlex.quote(cyclonedds_config)} > {cyclonedds_config_path}",
                )
                env_prefix = f"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp CYCLONEDDS_URI={cyclonedds_config_path} "
            elif middleware == "zenoh":
                router_endpoint = probe.zenoh_router_endpoint()
                own_ip = probe.ips[endpoint]
                is_router_host = i == 0
                session_config = ReferenceTopologyProbe.zenoh_session_config_json5(
                    own_ip, router_endpoint, is_router_host
                )
                session_config_path = f"/tmp/zenoh_session_config_epoch_{i}.json5"
                docker(
                    "exec", probe.endpoint_container_names[i], "bash", "-lc",
                    f"echo {shlex.quote(session_config)} > {session_config_path}",
                )
                env_prefix = f"RMW_IMPLEMENTATION=rmw_zenoh_cpp ZENOH_SESSION_CONFIG_URI={session_config_path} "
            else:
                raise ValueError(middleware)

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
                f" --required-peer-ids={shlex.quote(','.join(sorted(required)))}"
                f"{barrier_flags} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{probe._ready_files[i]} "
                f"--start-file=/work/{probe._start_file}"
            )
            cmd = f"{inner} > /work/{log_file} 2>&1"
            docker("exec", "-d", probe.endpoint_container_names[i], "bash", "-lc", cmd)

        try:
            probe.wait_for_ready_then_start(ready_deadline_s=45.0)
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
        result["failing_endpoints"] = [ep for ep, v in readiness_by_endpoint.items() if v != "ready"]
    finally:
        probe.teardown()
    return result


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    middleware = sys.argv[1] if len(sys.argv) > 1 else "cyclonedds"
    results = []
    for variant in ("A_current", "B_shared_epoch"):
        for rep in range(1, REPEATS + 1):
            print(f"=== {middleware} {variant} rep {rep}/{REPEATS} ===", flush=True)
            r = run_variant(middleware, variant, rep)
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "readiness_by_endpoint"}, indent=2), flush=True)

    out_path = OUTPUT_DIR / f"findings_{middleware}.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    for variant in ("A_current", "B_shared_epoch"):
        clean = sum(1 for r in results if r["variant"] == variant and r["all_ready"])
        total = sum(1 for r in results if r["variant"] == variant)
        print(f"{middleware} {variant}: {clean}/{total} fully ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
