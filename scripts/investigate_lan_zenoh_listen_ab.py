"""LAN Zenoh loopback/listen-asymmetry A/B (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "LAN DISCOVERY SEMANTIC-LAYER
INVESTIGATION"). RUST_LOG=zenoh=debug tracing proved control_station's
own Zenoh session config differs structurally from every robot's: it
gets NO explicit ZENOH_SESSION_CONFIG_URI (launch_endpoints() skips it
for i==0), so it falls back to Zenoh's default `listen.endpoints =
[tcp/localhost:0]` -- meaning its P2P listener is reachable ONLY from
inside its own container. Every robot, by contrast, gets an explicit
config with `listen.endpoints = [tcp/[::]:0]` (all interfaces), fully
reachable. Since Zenoh's gossip-based autoconnect (enabled by default)
has peers try to connect directly to each other's advertised listen
address, control_station's gossiped "localhost" address is meaningless
from any other container's network namespace.

Tests directly: does giving control_station an explicit session config
-- connect to the router (still via localhost, since it is co-located
and that half already works) PLUS an explicit listen endpoint on its
OWN REAL IP -- fix the readiness failures? No routing topology or
workload change; every robot's config is untouched.

A = current (unmodified launch_endpoints(), i==0 gets no config).
B = control_station given an explicit config with a real-IP listen
    endpoint, everything else identical.

Read-only diagnostic -- does not modify launch_endpoints() itself; B is
constructed by bypassing it for control_station's own launch only,
exactly mirroring its normal command construction otherwise.
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
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_zenoh_listen_ab"
REPEATS = 5


def run_variant(variant: str, rep: int) -> dict:
    run_id = f"lan_zenoh_listen_ab_{variant}_{rep}"
    output_dir = OUTPUT_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_{NUM_ROBOTS}robot_seed{SEED}.csv"
    events = generate_trace_events(
        scenario=f"lan_zenoh_listen_ab_{variant}",
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
        probe.start_zenoh_router()

        if variant == "A_current":
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
        else:
            # variant == "B_explicit_listen": identical to launch_endpoints()'s
            # own command construction for EVERY endpoint, except
            # control_station (i==0) now also gets an explicit session
            # config: connect to the router (still localhost, unchanged --
            # that half already worked) PLUS listen on its own real IP so
            # gossip-advertised peer-to-peer connections are reachable.
            docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
            probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
            probe._start_file = f"{results_dir_container}/start"
            router_endpoint = probe.zenoh_router_endpoint()
            for i, endpoint in enumerate(probe.endpoints):
                required = required_peer_ids_by_endpoint.get(endpoint, frozenset())
                result_json = f"{results_dir_container}/result_{i}.json"
                log_file = f"{results_dir_container}/endpoint_{i}.log"
                env_prefix = "RMW_IMPLEMENTATION=rmw_zenoh_cpp "
                own_ip = probe.ips[endpoint]
                if i == 0:
                    session_config = (
                        '{ connect: { endpoints: ["' + router_endpoint + '"] }, '
                        'listen: { endpoints: ["tcp/' + own_ip + ':0"] } }'
                    )
                else:
                    session_config = (
                        '{ connect: { endpoints: ["' + router_endpoint + '"] } }'
                    )
                session_config_path = f"/tmp/zenoh_session_config_ab_{i}.json5"
                docker(
                    "exec", probe.endpoint_container_names[i], "bash", "-lc",
                    f"echo {shlex.quote(session_config)} > {session_config_path}",
                )
                env_prefix += f"ZENOH_SESSION_CONFIG_URI={session_config_path} "
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
    finally:
        probe.teardown()
    return result


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for variant in ("A_current", "B_explicit_listen"):
        for rep in range(1, REPEATS + 1):
            print(f"=== {variant} rep {rep}/{REPEATS} ===", flush=True)
            r = run_variant(variant, rep)
            results.append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "readiness_by_endpoint"}, indent=2), flush=True)
            print(f"  readiness_by_endpoint: {r['readiness_by_endpoint']}", flush=True)

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    for variant in ("A_current", "B_explicit_listen"):
        clean = sum(1 for r in results if r["variant"] == variant and r["all_ready"])
        total = sum(1 for r in results if r["variant"] == variant)
        print(f"{variant}: {clean}/{total} fully ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
