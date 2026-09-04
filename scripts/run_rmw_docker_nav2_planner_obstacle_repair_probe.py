"""Run a Nav2 planner static-obstacle repair/replan probe through FleetRMW.

This probe starts upstream `planner_server`, publishes a static occupancy-grid
wall that blocks the start-to-goal corridor, verifies `ComputePathToPose` does
not succeed, then replaces the map with a clear occupancy grid and verifies the
same `ComputePathToPose` goal succeeds. It is scoped to planner-level
static-map obstacle repair/replan evidence, not full NavigateToPose recovery.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_nav2_planner_compute_path_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    compute_path_goal_yaml,
    occupancy_grid_yaml,
    parse_compute_path_output,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    dynamic_tf_message_yaml,
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_planner_obstacle_repair_probe.v1"


def run(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr if isinstance(exc.stderr, str) else "") + "\nsubprocess timeout\n",
        )


def blocked_occupancy_grid_yaml(
    width: int = 30,
    height: int = 30,
    resolution: float = 0.1,
    wall_x_index: int = 19,
) -> str:
    values: list[str] = []
    for _y in range(height):
        for x in range(width):
            values.append("100" if x == wall_x_index else "0")
    data = ", ".join(values)
    return (
        "{header: {stamp: {sec: 0, nanosec: 0}, frame_id: 'map'}, "
        "info: {map_load_time: {sec: 0, nanosec: 0}, "
        f"resolution: {resolution}, width: {width}, height: {height}, "
        "origin: {position: {x: -1.5, y: -1.5, z: 0.0}, "
        "orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}, "
        f"data: [{data}]}}"
    )


def parse_compute_path_output_with_error_code(text: str) -> dict[str, Any]:
    parsed = dict(parse_compute_path_output(text))
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    match = re.search(r"\berror_code:\s*(\d+)\b", result_text)
    if match:
        parsed["error_code"] = int(match.group(1))
    return parsed


def run_probe(*, root: Path, image: str, port_base: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_planner_obstacle_repair_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_obstacle_repair_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_obstacle_repair_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_obstacle_repair_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    params = tmp / "nav2_params.yaml"
    params.write_text(nav2_params_yaml(), encoding="utf-8")

    router_port = port_base
    planner_port = port_base + 1
    tf_port = port_base + 2
    blocked_map_port = port_base + 3
    clear_map_port = port_base + 4
    cli_port = port_base + 20
    expected_service_frames = 18
    router_post_satisfaction_ms = 45000
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_obstacle_repair_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    quoted_tf_yaml = shlex.quote(dynamic_tf_message_yaml())
    quoted_blocked_map_yaml = shlex.quote(blocked_occupancy_grid_yaml())
    quoted_clear_map_yaml = shlex.quote(occupancy_grid_yaml())
    quoted_goal_yaml = shlex.quote(compute_path_goal_yaml())

    try:
        build = run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "bash",
                "-v",
                f"{root}:/work",
                "-w",
                "/work",
                image,
                "-lc",
                "source /opt/ros/jazzy/setup.bash && "
                f"rm -rf /work/{build_base.relative_to(root)} "
                f"/work/{install_base.relative_to(root)} "
                f"/work/{log_base.relative_to(root)} && "
                f"colcon --log-base /work/{log_base.relative_to(root)} build "
                "--base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base /work/{build_base.relative_to(root)} "
                f"--install-base /work/{install_base.relative_to(root)} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release",
            ]
        )
        if build.returncode != 0:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "stage": "build",
                "stdout": build.stdout,
                "stderr": build.stderr,
            }

        command = (
            "set -e; "
            "source /opt/ros/jazzy/setup.bash; "
            "if ! ros2 pkg prefix nav2_planner >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_navfn_planner >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_msgs >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav_msgs >/dev/null 2>&1 || "
            "! ros2 pkg prefix tf2_msgs >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-navfn-planner "
            "ros-jazzy-nav2-msgs ros-jazzy-nav-msgs ros-jazzy-tf2-msgs "
            ">/tmp/fleetrmw_nav2_obstacle_repair_install.log; "
            "fi; "
            "source /opt/ros/jazzy/setup.bash; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            "wait_with_timeout() { "
            "pid=$1; limit=$2; elapsed=0; "
            "while kill -0 ${pid} >/dev/null 2>&1 && [ ${elapsed} -lt ${limit} ]; do "
            "sleep 1; elapsed=$((elapsed + 1)); "
            "done; "
            "if kill -0 ${pid} >/dev/null 2>&1; then return 124; fi; "
            "wait ${pid} >/dev/null 2>&1; return $?; "
            "}; "
            "stop_pid() { "
            "pid=$1; "
            "kill ${pid} >/dev/null 2>&1 || true; "
            "wait_with_timeout ${pid} 5 >/dev/null 2>&1 || true; "
            "kill -9 ${pid} >/dev/null 2>&1 || true; "
            "wait ${pid} >/dev/null 2>&1 || true; "
            "}; "
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 2 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 120000 "
            f"--post-satisfaction-ms {router_post_satisfaction_ms} "
            f"> /work/{tmp_rel}/router.log 2>&1 & router_pid=$!; "
            "sleep 0.5; "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/planner.log 2>&1 & planner_pid=$!; "
            "sleep 3; "
            "set +e; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp_rel}/planner_get_before.log 2>&1; "
            "planner_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 1} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 14 ros2 lifecycle set /planner_server configure "
            f"> /work/{tmp_rel}/planner_configure.log 2>&1; "
            "planner_configure_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 2} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp_rel}/planner_get_configured.log 2>&1; "
            "planner_get_configured_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{tf_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 80 ros2 topic pub --rate 10 /tf tf2_msgs/msg/TFMessage "
            f"{quoted_tf_yaml} "
            f"> /work/{tmp_rel}/tf_pub.log 2>&1 & tf_pid=$!; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{blocked_map_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 80 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
            f"{quoted_blocked_map_yaml} "
            f"> /work/{tmp_rel}/blocked_map_pub.log 2>&1 & blocked_map_pid=$!; "
            "sleep 3; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 3} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 20 ros2 lifecycle set /planner_server activate "
            f"> /work/{tmp_rel}/planner_activate.log 2>&1; "
            "planner_activate_rc=$?; "
            "sleep 1; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 4} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp_rel}/planner_get_active.log 2>&1; "
            "planner_get_active_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 5} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 30 ros2 action send_goal /compute_path_to_pose "
            "nav2_msgs/action/ComputePathToPose "
            f"{quoted_goal_yaml} "
            f"> /work/{tmp_rel}/blocked_compute_path_goal.log 2>&1; "
            "blocked_compute_path_goal_rc=$?; "
            "stop_pid ${blocked_map_pid}; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{clear_map_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 70 ros2 topic pub --rate 3 /map nav_msgs/msg/OccupancyGrid "
            f"{quoted_clear_map_yaml} "
            f"> /work/{tmp_rel}/clear_map_pub.log 2>&1 & clear_map_pid=$!; "
            "sleep 5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 6} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 35 ros2 action send_goal /compute_path_to_pose "
            "nav2_msgs/action/ComputePathToPose "
            f"{quoted_goal_yaml} "
            f"> /work/{tmp_rel}/clear_compute_path_goal.log 2>&1; "
            "clear_compute_path_goal_rc=$?; "
            "stop_pid ${planner_pid}; "
            "stop_pid ${tf_pid}; "
            "stop_pid ${clear_map_pid}; "
            "wait_with_timeout ${router_pid} 130; router_rc=$?; "
            "if [ ${router_rc} -eq 124 ]; then "
            "kill ${router_pid} >/dev/null 2>&1 || true; "
            "wait_with_timeout ${router_pid} 3 >/dev/null 2>&1 || true; "
            "kill -9 ${router_pid} >/dev/null 2>&1 || true; "
            "wait ${router_pid} >/dev/null 2>&1 || true; "
            "fi; "
            f"printf 'planner_get_before=%s\\nplanner_configure=%s\\n"
            "planner_get_configured=%s\\nplanner_activate=%s\\n"
            "planner_get_active=%s\\nblocked_compute_path_goal=%s\\n"
            "clear_compute_path_goal=%s\\nrouter=%s\\n' "
            "${planner_get_before_rc} ${planner_configure_rc} "
            "${planner_get_configured_rc} ${planner_activate_rc} "
            "${planner_get_active_rc} ${blocked_compute_path_goal_rc} "
            "${clear_compute_path_goal_rc} ${router_rc} "
            f"> /work/{tmp_rel}/return_codes.log; "
            "if [ ${planner_get_before_rc} -ne 0 ] || "
            "[ ${planner_configure_rc} -ne 0 ] || "
            "[ ${planner_get_configured_rc} -ne 0 ] || "
            "[ ${planner_activate_rc} -ne 0 ] || "
            "[ ${planner_get_active_rc} -ne 0 ] || "
            "[ ${clear_compute_path_goal_rc} -ne 0 ]; then exit 20; fi; "
            "exit ${router_rc}"
        )
        docker = run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "bash",
                "-v",
                f"{root}:/work",
                "-w",
                "/work",
                image,
                "-lc",
                command,
            ]
            ,
            timeout=900,
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        planner_log = read("planner.log")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        blocked_goal = read("blocked_compute_path_goal.log")
        clear_goal = read("clear_compute_path_goal.log")
        blocked_path = parse_compute_path_output_with_error_code(blocked_goal)
        clear_path = parse_compute_path_output_with_error_code(clear_goal)
        planner_get_configured = read("planner_get_configured.log")
        planner_get_active = read("planner_get_active.log")
        planner_configure = read("planner_configure.log")
        planner_activate = read("planner_activate.log")
        planner_configured = (
            "Transitioning successful" in planner_configure
            and "inactive [2]" in planner_get_configured
            and "Created global planner plugin GridBased" in planner_log
        )
        planner_activated = (
            "Transitioning successful" in planner_activate
            and "active [3]" in planner_get_active
            and "Activating plugin GridBased" in planner_log
        )
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
        )
        blocked_compute_path_failed = (
            bool(blocked_path["accepted"])
            and not bool(blocked_path["succeeded"])
        )
        clear_compute_path_ok = (
            bool(clear_path["accepted"])
            and bool(clear_path["succeeded"])
            and clear_path["error_code"] == 0
            and int(clear_path["path_pose_count"]) > 0
        )
        map_runtime_ok = "/map" in graph_topics and "/map" in forwarded_topics
        tf_runtime_ok = bool(metrics["tf_topic_advertised"]) and bool(metrics["tf_topic_forwarded"])
        obstacle_repair_ok = (
            blocked_compute_path_failed
            and clear_compute_path_ok
            and map_runtime_ok
            and tf_runtime_ok
        )
        ok = (
            docker.returncode == 0
            and planner_configured
            and planner_activated
            and lifecycle_transport_ok
            and obstacle_repair_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "planner_configure_transition": planner_configured,
            "planner_activate_transition": planner_activated,
            "planner_final_state": "active" if "active [3]" in planner_get_active else "unknown",
            "dynamic_tf_runtime": tf_runtime_ok,
            "map_runtime": map_runtime_ok,
            "map_message_type": "nav_msgs/msg/OccupancyGrid",
            "blocked_map_wall_x_index": 19,
            "blocked_map_obstacle_cells": 30,
            "clear_map_obstacle_cells": 0,
            "blocked_compute_path_goal_accepted": blocked_path["accepted"],
            "blocked_compute_path_goal_succeeded": blocked_path["succeeded"],
            "blocked_compute_path_error_code": blocked_path["error_code"],
            "blocked_compute_path_failed": blocked_compute_path_failed,
            "clear_compute_path_goal_accepted": clear_path["accepted"],
            "clear_compute_path_goal_succeeded": clear_path["succeeded"],
            "clear_compute_path_error_code": clear_path["error_code"],
            "clear_compute_path_path_pose_count": clear_path["path_pose_count"],
            "planner_static_obstacle_repair_claim": bool(obstacle_repair_ok),
            "planner_static_obstacle_repair_scope": (
                "blocked_static_occupancy_grid_then_clear_map_replan"
            ),
            "obstacle_field_recovery_claim": bool(obstacle_repair_ok),
            "obstacle_field_recovery_scope": (
                "planner_level_static_map_obstacle_blocks_then_clear_map_replans"
            ),
            "full_nav2_obstacle_recovery_claim": False,
            "full_nav2_obstacle_recovery_scope": (
                "not_bt_navigate_to_pose_controller_recovery"
            ),
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "router_post_satisfaction_ms": router_post_satisfaction_ms,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "planner_get_before": read("planner_get_before.log"),
            "planner_configure": planner_configure,
            "planner_get_configured": planner_get_configured,
            "planner_activate": planner_activate,
            "planner_get_active": planner_get_active,
            "blocked_compute_path_goal_excerpt": blocked_goal[-5000:],
            "clear_compute_path_goal_excerpt": clear_goal[-5000:],
            "tf_pub_log_excerpt": read("tf_pub.log")[-1500:],
            "blocked_map_pub_log_excerpt": read("blocked_map_pub.log")[-1500:],
            "clear_map_pub_log_excerpt": read("clear_map_pub.log")[-1500:],
            "planner_log_excerpt": planner_log[-5000:],
            "router": {
                **metrics,
                "map_topic_advertised": "/map" in graph_topics,
                "map_topic_forwarded": "/map" in forwarded_topics,
            },
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=7200)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_planner_obstacle_repair_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, port_base=args.port_base)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} "
            f"blocked_failed={summary.get('blocked_compute_path_failed')} "
            f"clear_succeeded={summary.get('clear_compute_path_goal_succeeded')} "
            f"obstacle_repair={summary.get('planner_static_obstacle_repair_claim')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
