"""Run real Nav2 planner/controller lifecycle configure through FleetRMW router.

This probe starts upstream Nav2 `planner_server` and `controller_server` with
real planner/controller plugins, drives their lifecycle `configure` transition
through rmw_fleetqox_cpp service transport and the FleetRMW UDP router, and
verifies both nodes reach `inactive`.

It intentionally does not claim full navigation activation/execution: activation
requires map/TF/costmap runtime inputs that are outside this CI-light slice.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_router_service_call_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_nav2_planner_controller_lifecycle_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def nav2_params_yaml() -> str:
    return """planner_server:
  ros__parameters:
    expected_planner_frequency: 1.0
    planner_plugins: ["GridBased"]
    GridBased:
      plugin: "nav2_navfn_planner::NavfnPlanner"
controller_server:
  ros__parameters:
    controller_frequency: 1.0
    min_x_velocity_threshold: 0.001
    min_y_velocity_threshold: 0.5
    min_theta_velocity_threshold: 0.001
    progress_checker_plugin: "progress_checker"
    goal_checker_plugins: ["general_goal_checker"]
    controller_plugins: ["FollowPath"]
    progress_checker:
      plugin: "nav2_controller::SimpleProgressChecker"
      required_movement_radius: 0.5
      movement_time_allowance: 10.0
    general_goal_checker:
      stateful: true
      plugin: "nav2_controller::SimpleGoalChecker"
      xy_goal_tolerance: 0.25
      yaw_goal_tolerance: 0.25
    FollowPath:
      plugin: "dwb_core::DWBLocalPlanner"
      debug_trajectory_details: false
      min_vel_x: 0.0
      min_vel_y: 0.0
      max_vel_x: 0.26
      max_vel_y: 0.0
      max_vel_theta: 1.0
      min_speed_xy: 0.0
      max_speed_xy: 0.26
      min_speed_theta: 0.0
      acc_lim_x: 2.5
      acc_lim_y: 0.0
      acc_lim_theta: 3.2
      decel_lim_x: -2.5
      decel_lim_y: 0.0
      decel_lim_theta: -3.2
      vx_samples: 20
      vy_samples: 5
      vtheta_samples: 20
      sim_time: 1.7
      linear_granularity: 0.05
      angular_granularity: 0.025
      transform_tolerance: 0.2
      xy_goal_tolerance: 0.25
      trans_stopped_velocity: 0.25
      short_circuit_trajectory_evaluation: true
      stateful: true
      critics:
        - "RotateToGoal"
        - "Oscillation"
        - "BaseObstacle"
        - "GoalAlign"
        - "PathAlign"
        - "PathDist"
        - "GoalDist"
      BaseObstacle.scale: 0.02
      PathAlign.scale: 32.0
      PathAlign.forward_point_distance: 0.1
      GoalAlign.scale: 24.0
      GoalAlign.forward_point_distance: 0.1
      PathDist.scale: 32.0
      GoalDist.scale: 24.0
      RotateToGoal.scale: 32.0
      RotateToGoal.slowing_factor: 5.0
      RotateToGoal.lookahead_time: -1.0
"""


def run_probe(*, root: Path, image: str, port_base: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_planner_controller_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_pc_build"
    install_base = root / ".tmp_fleetrmw_nav2_pc_install"
    log_base = root / ".tmp_fleetrmw_nav2_pc_log"
    tmp.mkdir(parents=True, exist_ok=True)
    params = tmp / "nav2_planner_controller_params.yaml"
    params.write_text(nav2_params_yaml(), encoding="utf-8")

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    cli_port = port_base + 10
    expected_service_frames = 16
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_pc_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
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
            "! ros2 pkg prefix nav2_controller >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_navfn_planner >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_dwb_controller >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-controller "
            "ros-jazzy-nav2-navfn-planner ros-jazzy-nav2-dwb-controller "
            ">/tmp/fleetrmw_nav2_plugin_install.log; "
            "fi; "
            "source /opt/ros/jazzy/setup.bash; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            f"--expected-frames 0 --expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 60000 "
            # Default post-satisfaction dwell is 0ms: the router exits the
            # instant it has seen >= expected-service-frames, counting
            # duplicate/retried request frames the same as originals. With
            # FLEETQOX_RMW_SERVICE_REQUEST_REPEATS raised well above the
            # default, get_after's own request retries alone can satisfy
            # this threshold before controller_server has even generated a
            # response, let alone before the router relays it -- the router
            # exits (taking the whole relay path down) mid-exchange. Give it
            # real dwell time so a late response still gets forwarded.
            "--post-satisfaction-ms 20000 "
            f"> /work/{tmp.relative_to(root)}/router.log 2>&1 & "
            "router_pid=$!; "
            "sleep 0.5; "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp.relative_to(root)}/planner.log 2>&1 & "
            "planner_pid=$!; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{controller_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"ros2 run nav2_controller controller_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp.relative_to(root)}/controller.log 2>&1 & "
            "controller_pid=$!; "
            "sleep 3; "
            "set +e; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_before.log 2>&1; "
            "planner_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 1} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"timeout 14 ros2 lifecycle set /planner_server configure "
            f"> /work/{tmp.relative_to(root)}/planner_configure.log 2>&1; "
            "planner_configure_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 2} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_after.log 2>&1; "
            "planner_get_after_rc=$?; "
            # controller_server's local_costmap (static/obstacle/inflation
            # layers, topic subscriptions, TF buffering) keeps its executor
            # busy well after configure returns, so a fresh CLI client's
            # get_state request can arrive while the server hasn't gotten
            # back around to servicing new requests yet. The default
            # service-request retry budget (5 retries x 100ms = ~500ms) is
            # nowhere near enough headroom for that -- give these
            # controller_server lifecycle queries a much longer budget
            # (40 x 250ms = 10s) instead of raising the outer CLI timeout,
            # which doesn't help once the retry budget itself is exhausted.
            "FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=40 "
            "FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS=250 "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 3} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"timeout 20 ros2 lifecycle get /controller_server "
            f"> /work/{tmp.relative_to(root)}/controller_get_before.log 2>&1; "
            "controller_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 4} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            # controller_server owns a local_costmap with several plugins
            # (static/obstacle layers, topic subscriptions) that take
            # meaningfully longer to settle than the planner's configure --
            # give it more room than the planner's 14s/8s timeouts.
            f"timeout 30 ros2 lifecycle set /controller_server configure "
            f"> /work/{tmp.relative_to(root)}/controller_configure.log 2>&1; "
            "controller_configure_rc=$?; "
            "sleep 2; "
            "FLEETQOX_RMW_SERVICE_REQUEST_REPEATS=60 "
            "FLEETQOX_RMW_SERVICE_REQUEST_REPEAT_INTERVAL_MS=200 "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 5} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            f"timeout 20 ros2 lifecycle get /controller_server "
            f"> /work/{tmp.relative_to(root)}/controller_get_after.log 2>&1; "
            "controller_get_after_rc=$?; "
            "sleep 0.5; "
            "kill ${planner_pid} ${controller_pid} >/dev/null 2>&1 || true; "
            "wait ${planner_pid} >/dev/null 2>&1 || true; "
            "wait ${controller_pid} >/dev/null 2>&1 || true; "
            "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; "
            f"printf '%s\\n' done >/work/{tmp.relative_to(root)}/done.marker; "
            "if [ ${planner_get_before_rc} -ne 0 ] || "
            "[ ${planner_configure_rc} -ne 0 ] || "
            "[ ${planner_get_after_rc} -ne 0 ] || "
            "[ ${controller_get_before_rc} -ne 0 ] || "
            "[ ${controller_configure_rc} -ne 0 ] || "
            "[ ${controller_get_after_rc} -ne 0 ]; then exit 20; fi; "
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
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        planner_log = read("planner.log")
        controller_log = read("controller.log")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        planner_get_before = read("planner_get_before.log")
        planner_configure = read("planner_configure.log")
        planner_get_after = read("planner_get_after.log")
        controller_get_before = read("controller_get_before.log")
        controller_configure = read("controller_configure.log")
        controller_get_after = read("controller_get_after.log")
        planner_configured = (
            "Transitioning successful" in planner_configure
            and "inactive [2]" in planner_get_after
            and "Created global planner plugin GridBased" in planner_log
        )
        controller_configured = (
            "Transitioning successful" in controller_configure
            and "inactive [2]" in controller_get_after
            and "Created controller : FollowPath of type dwb_core::DWBLocalPlanner" in controller_log
        )
        lifecycle_transport_ok = (
            router_summary.get("status") == "ok"
            and int(router_summary.get("service_frames", 0)) >= expected_service_frames
            and int(router_summary.get("service_forwarded", 0)) >= expected_service_frames
        )
        ok = docker.returncode == 0 and planner_configured and controller_configured and lifecycle_transport_ok
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_controller_server_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "controller_plugin": "dwb_core::DWBLocalPlanner",
            "planner_configure_transition": planner_configured,
            "controller_configure_transition": controller_configured,
            "planner_final_state": "inactive" if "inactive [2]" in planner_get_after else "unknown",
            "controller_final_state": "inactive" if "inactive [2]" in controller_get_after else "unknown",
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": router_summary.get("service_frames", 0),
            "fleetqox_router_service_forwarded": router_summary.get("service_forwarded", 0),
            "activation_claim": False,
            "activation_gap": "map_tf_costmap_runtime_not_started",
            "full_nav2_navigation_stack_claim": False,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "planner_get_before": planner_get_before,
            "planner_configure": planner_configure,
            "planner_get_after": planner_get_after,
            "controller_get_before": controller_get_before,
            "controller_configure": controller_configure,
            "controller_get_after": controller_get_after,
            "router": router_summary,
            "planner_log_excerpt": planner_log[-4000:],
            "controller_log_excerpt": controller_log[-4000:],
            "router_log_excerpt": router_log[-4000:],
        }
    finally:
        if not os.environ.get("FLEETQOX_DEBUG_KEEP_TMP"):
            for path in (tmp, build_base, install_base, log_base):
                shutil.rmtree(path, ignore_errors=True)
        else:
            print(f"kept tmp dir: {tmp}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=4480)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_planner_controller_lifecycle_probe_summary.json",
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
            f"status={summary['status']} planner={summary.get('planner_configure_transition')} "
            f"controller={summary.get('controller_configure_transition')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
