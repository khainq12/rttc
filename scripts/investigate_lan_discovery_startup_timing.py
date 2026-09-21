"""LAN Phase 1/2/3 diagnostic (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"LAN DISCOVERY-CONVERGENCE ROOT-CAUSE INVESTIGATION"): measures, with a
single common clock (CLOCK_MONOTONIC, host-wide/shared across
containers on this VM -- already established in prior Table VI work),
the exact delay between "process alive" (the harness's own existing
pgrep-based check) and "socket actually listening" (via `ss -tln`
inside the container) for BOTH Fast DDS's discovery-server and Zenoh's
router. Tests the Phase 2/3 hypothesis directly: does the harness start
launching endpoint processes (and therefore their discovery/readiness
clock) before the dependency they need is actually usable?

Read-only diagnostic -- does NOT change run_lan_probe()/launch_endpoints()
or any production code path. N=2 only (per this investigation's own
instruction: start small, not N=16).
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
)

NUM_ROBOTS = 2
OUTPUT_DIR = ROOT / "results_rmw_socket" / "lan_discovery_startup_timing_diag"


def poll_until(check_fn, timeout_s: float, interval_s: float = 0.05) -> float | None:
    """Returns the monotonic timestamp of the first successful check, or
    None if the timeout elapsed first. Polls at interval_s -- NOT a
    fixed sleep, an observable-condition wait."""
    deadline = time.monotonic() + timeout_s
    while True:
        if check_fn():
            return time.monotonic()
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval_s)


def diagnose_fastdds(probe: ReferenceTopologyProbe) -> dict:
    control_station_name = probe.endpoint_container_names[0]
    server_ip = probe.ips[probe.endpoints[0]]
    log_path = "/tmp/fastdds_discovery_diag.log"
    cmd = (
        "source /opt/ros/jazzy/setup.bash && "
        f"fastdds discovery -i 0 -l {server_ip} -p {FASTDDS_DISCOVERY_SERVER_PORT} "
        f"> {log_path} 2>&1"
    )
    t_exec_issued = time.monotonic()
    docker("exec", "-d", control_station_name, "bash", "-lc", cmd)

    def is_alive() -> bool:
        r = docker("exec", control_station_name, "bash", "-lc", "pgrep -f 'fastdds discovery'", check=False)
        return r.returncode == 0

    def is_listening() -> bool:
        r = docker(
            "exec", control_station_name, "bash", "-lc",
            f"ss -tln | grep -q ':{FASTDDS_DISCOVERY_SERVER_PORT} '",
            check=False,
        )
        return r.returncode == 0

    t_alive = poll_until(is_alive, timeout_s=10.0)
    t_listening = poll_until(is_listening, timeout_s=10.0)
    log = docker("exec", control_station_name, "cat", log_path, check=False).stdout
    return {
        "t_exec_issued": t_exec_issued,
        "t_alive": t_alive,
        "t_listening": t_listening,
        "alive_delay_s": (t_alive - t_exec_issued) if t_alive else None,
        "listening_delay_s": (t_listening - t_exec_issued) if t_listening else None,
        "listening_after_alive_s": (t_listening - t_alive) if (t_listening and t_alive) else None,
        "production_sleep_s": 3.0,
        "production_sleep_would_have_seen_listening": (
            (t_listening - t_exec_issued) <= 3.0 if t_listening else False
        ),
        "log_tail": log[-500:],
    }


def diagnose_zenoh(probe: ReferenceTopologyProbe) -> dict:
    control_station_name = probe.endpoint_container_names[0]
    router_config = '{ listen: { endpoints: ["tcp/0.0.0.0:7447"] } }'
    log_path = "/tmp/zenohd_diag.log"
    docker(
        "exec", control_station_name, "bash", "-lc",
        f"echo {shlex.quote(router_config)} > /tmp/zenoh_router_config_diag.json5",
    )
    cmd = (
        "source /opt/ros/jazzy/setup.bash && "
        "export ZENOH_ROUTER_CONFIG_URI=/tmp/zenoh_router_config_diag.json5 && "
        f"ros2 run rmw_zenoh_cpp rmw_zenohd > {log_path} 2>&1"
    )
    t_exec_issued = time.monotonic()
    docker("exec", "-d", control_station_name, "bash", "-lc", cmd)

    def is_alive() -> bool:
        r = docker("exec", control_station_name, "bash", "-lc", "pgrep -f rmw_zenohd", check=False)
        return r.returncode == 0

    def is_listening() -> bool:
        r = docker(
            "exec", control_station_name, "bash", "-lc",
            "ss -tln | grep -q ':7447 '",
            check=False,
        )
        return r.returncode == 0

    t_alive = poll_until(is_alive, timeout_s=10.0)
    t_listening = poll_until(is_listening, timeout_s=10.0)
    log = docker("exec", control_station_name, "cat", log_path, check=False).stdout
    return {
        "t_exec_issued": t_exec_issued,
        "t_alive": t_alive,
        "t_listening": t_listening,
        "alive_delay_s": (t_alive - t_exec_issued) if t_alive else None,
        "listening_delay_s": (t_listening - t_exec_issued) if t_listening else None,
        "listening_after_alive_s": (t_listening - t_alive) if (t_listening and t_alive) else None,
        "production_sleep_s": 3.0,
        "production_sleep_would_have_seen_listening": (
            (t_listening - t_exec_issued) <= 3.0 if t_listening else False
        ),
        "log_tail": log[-500:],
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for name, diag_fn in (("fastdds", diagnose_fastdds), ("zenoh", diagnose_zenoh)):
        run_id = f"lan_discovery_timing_diag_{name}"
        probe = ReferenceTopologyProbe(
            run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=OUTPUT_DIR
        )
        try:
            probe.start_containers()
            probe.wire_network_lan()
            print(f"=== {name} ===", flush=True)
            r = diag_fn(probe)
            results[name] = r
            print(json.dumps({k: v for k, v in r.items() if k != "log_tail"}, indent=2), flush=True)
        finally:
            probe.teardown()

    out_path = OUTPUT_DIR / "findings.json"
    out_path.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
