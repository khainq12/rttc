"""Run the direct-baseline multi-topic workload through FleetRMW and its router."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_multi_robot_live_telemetry_plan_probe import (
    NETEM_SCHEMA_VERSION,
    NETEM_SEED_SEMANTICS,
    netem_config_for_path,
    netem_shell_prefix,
    profile_by_name,
)
from scripts.run_ros2_direct_rmw_netem_probe import (
    _float,
    parse_last_json,
    read_json,
    run,
    topic_specs_for_robot_count,
    wait_for_container_path,
    write_probe_scripts,
)


SCHEMA_VERSION = "fleetrmw.router_matched_multi_topic_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
BUILD_BASE = "/work/.tmp_fleetrmw_matched_v2_build"
INSTALL_BASE = "/work/.tmp_fleetrmw_matched_v2_install"
LOG_BASE = "/work/.tmp_fleetrmw_matched_v2_log"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--profile", default="wifi")
    parser.add_argument("--netem-loss-scale", type=float, default=0.1)
    parser.add_argument("--repetition-seed", type=int, default=7)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--robot-count", type=int, default=8)
    parser.add_argument("--publish-interval-ms", type=int, default=50)
    parser.add_argument("--timeout-s", type=float, default=20.0)
    parser.add_argument(
        "--reliable-ack-timeout-ms",
        type=int,
        default=-1,
        help="FleetRMW RELIABLE ACK timeout; -1 derives it from netem and 0 disables it",
    )
    parser.add_argument("--reliable-max-retransmissions", type=int, default=3)
    parser.add_argument("--reuse-build", action="store_true")
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_matched_multi_topic_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        profile=args.profile,
        netem_loss_scale=max(args.netem_loss_scale, 0.0),
        repetition_seed=args.repetition_seed,
        samples=max(args.samples, 1),
        robot_count=max(args.robot_count, 1),
        publish_interval_ms=max(args.publish_interval_ms, 0),
        timeout_s=max(args.timeout_s, 1.0),
        reliable_ack_timeout_ms=(
            None if args.reliable_ack_timeout_ms < 0 else args.reliable_ack_timeout_ms
        ),
        reliable_max_retransmissions=max(args.reliable_max_retransmissions, 0),
        reuse_build=args.reuse_build,
    )
    path = ROOT / args.summary_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-matched-multi-topic-probe")
        print(f"  status: {summary['status']}")
        print(f"  robots/topics: {summary.get('robot_count')}/{summary.get('topic_count')}")
        print(f"  control/state: {summary.get('control_payload_count')}/{summary.get('state_payload_count')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    profile: str,
    netem_loss_scale: float,
    repetition_seed: int,
    samples: int,
    robot_count: int,
    publish_interval_ms: int,
    timeout_s: float,
    reliable_ack_timeout_ms: int | None = None,
    reliable_max_retransmissions: int = 3,
    reuse_build: bool = False,
) -> dict[str, Any]:
    specs = topic_specs_for_robot_count(robot_count)
    expected_each = samples * robot_count
    suffix = f"{os.getpid()}-{time.time_ns()}"
    network = f"fleetrmw-matched-net-{suffix}"
    router_name = f"fleetrmw-matched-router-{suffix}"
    subscriber_name = f"fleetrmw-matched-sub-{suffix}"
    publisher_name = f"fleetrmw-matched-pub-{suffix}"
    work_dir = root / f".tmp_fleetrmw_matched_{suffix}"
    subscriber_script = work_dir / "subscriber.py"
    publisher_script = work_dir / "publisher.py"
    netem_status_path = work_dir / "publisher_netem_status.json"
    netem_status_container = f"/work/{netem_status_path.relative_to(root)}"
    publisher_ready_container = "/tmp/fleetrmw_probe_ready"
    publisher_start_container = "/tmp/fleetrmw_probe_start"
    netem = netem_config_for_path(
        profile_by_name(profile),
        path_id="primary_wifi",
        loss_scale=netem_loss_scale,
        repetition_seed=repetition_seed,
    )
    effective_ack_timeout_ms, publisher_linger_s = reliable_timing_for_netem(
        netem,
        configured_ack_timeout_ms=reliable_ack_timeout_ms,
        max_retransmissions=reliable_max_retransmissions,
    )
    reliable_max_retransmissions = max(int(reliable_max_retransmissions), 0)
    router_post_satisfaction_ms = max(
        1000,
        int(round(publisher_linger_s * 1000.0)) + 1000,
    )

    def docker_shell(command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run([
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc", command,
        ], check=check)

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        write_probe_scripts(
            subscriber_script=subscriber_script,
            publisher_script=publisher_script,
            samples=samples,
            topic_specs=specs,
            publish_interval_ms=publish_interval_ms,
            timeout_s=timeout_s,
            publisher_linger_s=publisher_linger_s,
        )
        install_setup = root / INSTALL_BASE.removeprefix("/work/") / "setup.bash"
        if not reuse_build or not install_setup.exists():
            clean = "" if reuse_build else f"rm -rf {BUILD_BASE} {INSTALL_BASE} {LOG_BASE} && "
            docker_shell(
                "source /opt/ros/jazzy/setup.bash && "
                f"{clean}colcon --log-base {LOG_BASE} build --base-paths ros2_ws/src "
                "--packages-select fleetrmw_interfaces rmw_fleetqox_cpp "
                f"--build-base {BUILD_BASE} --install-base {INSTALL_BASE} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release"
            )
        run(["docker", "network", "create", network])
        run([
            "docker", "run", "-d", "--name", router_name, "--network", network,
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            f"source {INSTALL_BASE}/setup.bash && "
            f"{INSTALL_BASE}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_router_probe "
            "--bind 0.0.0.0:49800 "
            f"--expected-frames {len(specs) * samples} "
            f"--expected-route-advertisements {len(specs)} "
            f"--expected-graph-advertisements {len(specs) * 2} "
            f"--post-satisfaction-ms {router_post_satisfaction_ms} "
            "--timeout-ms 30000",
        ])
        subscriber_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source {INSTALL_BASE}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49801 && "
            f"export FLEETQOX_RMW_PEERS={router_name}:49800 && "
            f"python3 /work/{subscriber_script.relative_to(root)}"
        )
        run([
            "docker", "run", "-d", "--name", subscriber_name, "--network", network,
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            subscriber_command,
        ])
        time.sleep(0.8)
        publisher_command = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source {INSTALL_BASE}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
            f"export FLEETQOX_PROBE_READY_FILE={publisher_ready_container} && "
            f"export FLEETQOX_PROBE_START_FILE={publisher_start_container} && "
            f"export FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS={effective_ack_timeout_ms} && "
            "export FLEETQOX_RMW_RELIABLE_MAX_RETRANSMISSIONS="
            f"{reliable_max_retransmissions} && "
            "export FLEETQOX_RMW_BIND=0.0.0.0:49802 && "
            f"export FLEETQOX_RMW_PEERS={router_name}:49800 && "
            f"python3 /work/{publisher_script.relative_to(root)}"
        )
        run([
            "docker", "run", "-d", "--name", publisher_name, "--network", network,
            "--cap-add", "NET_ADMIN", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc", publisher_command,
        ])
        wait_for_container_path(
            publisher_name,
            publisher_ready_container,
            timeout_s=12.0,
        )
        run([
            "docker", "exec", publisher_name, "bash", "-lc",
            netem_shell_prefix(netem, status_file=netem_status_container, require=True),
        ])
        run(["docker", "exec", publisher_name, "touch", publisher_start_container])
        publisher_rc = int(run(["docker", "wait", publisher_name]).stdout.strip())
        subscriber_rc = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        router_rc = int(run(["docker", "wait", router_name]).stdout.strip())
        publisher = parse_last_json(run(["docker", "logs", publisher_name]).stdout)
        subscriber = parse_last_json(run(["docker", "logs", subscriber_name]).stdout)
        router = parse_last_json(run(["docker", "logs", router_name]).stdout)
        netem_status = read_json(netem_status_path)
        control_count = int(subscriber.get("control_payload_count", 0))
        state_count = int(subscriber.get("state_payload_count", 0))
        logical_data_frames = len(specs) * samples
        router_observed_data_frames = int(router.get("received_frames", 0))
        router_observed_retransmit_overhead = max(
            0,
            router_observed_data_frames - logical_data_frames,
        )
        status = (
            publisher_rc == 0 and subscriber_rc == 0 and router_rc == 0
            and publisher.get("status") == "ok" and subscriber.get("status") == "ok"
            and router.get("status") == "ok" and netem_status.get("status") == "applied"
            and control_count >= expected_each and state_count >= expected_each
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "system": "rmw_fleetqox_cpp_router",
            "topology": "publisher-router-subscriber",
            "image": image,
            "profile": profile,
            "robot_count": robot_count,
            "topic_count": len(specs),
            "samples": samples,
            "repetition_seed": repetition_seed,
            "netem_loss_scale": netem_loss_scale,
            "netem": netem,
            "netem_status": {"direct_pub": netem_status},
            "netem_schema_version": NETEM_SCHEMA_VERSION,
            "netem_seed_semantics": NETEM_SEED_SEMANTICS,
            "reliability_mode": (
                "ack_timeout_retransmit"
                if effective_ack_timeout_ms > 0 and reliable_max_retransmissions > 0
                else "gap_nack_only"
            ),
            "reliable_ack_timeout_ms": effective_ack_timeout_ms,
            "reliable_max_retransmissions": reliable_max_retransmissions,
            "publisher_linger_s": publisher_linger_s,
            "router_post_satisfaction_ms": router_post_satisfaction_ms,
            "logical_data_frames": logical_data_frames,
            "router_observed_data_frames": router_observed_data_frames,
            "router_observed_retransmit_overhead": router_observed_retransmit_overhead,
            "router_observed_retransmit_overhead_ratio": (
                router_observed_retransmit_overhead / logical_data_frames
                if logical_data_frames else 0.0
            ),
            "control_payload_count": control_count,
            "state_payload_count": state_count,
            "control_expected_count": expected_each,
            "state_expected_count": expected_each,
            "control_delivery_ratio": control_count / expected_each,
            "state_delivery_ratio": state_count / expected_each,
            "control_latency_ms_mean": _float(subscriber.get("control_latency_ms_mean")),
            "state_latency_ms_mean": _float(subscriber.get("state_latency_ms_mean")),
            "control_latency_ms_p95": _float(subscriber.get("control_latency_ms_p95")),
            "state_latency_ms_p95": _float(subscriber.get("state_latency_ms_p95")),
            "min_topic_delivery_ratio": _float(subscriber.get("min_topic_delivery_ratio")),
            "publisher_returncode": publisher_rc,
            "subscriber_returncode": subscriber_rc,
            "router_returncode": router_rc,
            "publisher": publisher,
            "subscriber": subscriber,
            "router": router,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "system": "rmw_fleetqox_cpp_router",
            "profile": profile,
            "robot_count": robot_count,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        for name in (publisher_name, subscriber_name, router_name):
            subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True, text=True)
        subprocess.run(["docker", "network", "rm", network], check=False, capture_output=True, text=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        if not reuse_build:
            docker_shell(f"rm -rf {BUILD_BASE} {INSTALL_BASE} {LOG_BASE}", check=False)


def cleanup_reusable_build(*, root: Path, image: str) -> None:
    subprocess.run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            f"rm -rf {BUILD_BASE} {INSTALL_BASE} {LOG_BASE}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def reliable_timing_for_netem(
    netem: dict[str, object],
    *,
    configured_ack_timeout_ms: int | None,
    max_retransmissions: int,
) -> tuple[int, float]:
    one_way_budget_ms = float(netem["delay_ms"]) + 2.0 * float(netem["jitter_ms"])
    ack_timeout_ms = (
        int(math.ceil(2.0 * one_way_budget_ms + 50.0))
        if configured_ack_timeout_ms is None else max(int(configured_ack_timeout_ms), 0)
    )
    retries = max(int(max_retransmissions), 0)
    if ack_timeout_ms <= 0 or retries <= 0:
        return ack_timeout_ms, 0.5
    linger_s = (
        ack_timeout_ms * (retries + 1)
        + 2.0 * one_way_budget_ms
        + 250.0
    ) / 1000.0
    # Fast DDS can need several seconds to complete RELIABLE repair after a
    # burst. Use the same application-level publisher lifetime for FleetRMW
    # and every direct baseline so middleware heartbeat defaults are not
    # mistaken for permanent delivery loss.
    return ack_timeout_ms, max(6.0, linger_s)


if __name__ == "__main__":
    raise SystemExit(main())
