"""Run real Nav2 planner/controller lifecycle activation through FleetRMW.

This probe extends the CI-light Nav2 planner/controller configure proof by
publishing a repeated dynamic `/tf` chain through rmw_fleetqox_cpp and the
FleetRMW UDP router, then driving upstream `planner_server` and
`controller_server` from `inactive` to `active`.

It intentionally still does not claim full navigation execution: the probe
does not provide a map server, odometry source, behavior tree navigator, or a
NavigateToPose goal. It is a lifecycle/runtime-TF activation transport proof.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_planner_controller_activation_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def dynamic_tf_message_yaml() -> str:
    return (
        "{transforms: ["
        "{header: {stamp: {sec: 0, nanosec: 0}, frame_id: 'map'}, "
        "child_frame_id: 'odom', "
        "transform: {translation: {x: 0.0, y: 0.0, z: 0.0}, "
        "rotation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}, "
        "{header: {stamp: {sec: 0, nanosec: 0}, frame_id: 'odom'}, "
        "child_frame_id: 'base_link', "
        "transform: {translation: {x: 0.0, y: 0.0, z: 0.0}, "
        "rotation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}"
        "]}"
    )


def router_metrics(router_summary: dict[str, Any]) -> dict[str, Any]:
    forwarded_topics = router_summary.get("forwarded_topics", [])
    graph_topics = router_summary.get("graph_topics", [])
    return {
        "status": router_summary.get("status"),
        "service_frames": router_summary.get("service_frames", 0),
        "service_forwarded": router_summary.get("service_forwarded", 0),
        "received_frames": router_summary.get("received_frames", 0),
        "forwarded_frames": router_summary.get("forwarded_frames", 0),
        "graph_advertisements": router_summary.get("graph_advertisements", 0),
        "graph_services": router_summary.get("graph_services", 0),
        "graph_clients": router_summary.get("graph_clients", 0),
        "tf_topic_advertised": "/tf" in graph_topics,
        "tf_topic_forwarded": "/tf" in forwarded_topics,
        "bond_topic_forwarded": "/bond" in forwarded_topics,
        "global_costmap_footprint_forwarded": (
            "/global_costmap/published_footprint" in forwarded_topics
        ),
        "local_costmap_footprint_forwarded": (
            "/local_costmap/published_footprint" in forwarded_topics
        ),
    }


def run_probe(*, root: Path, image: str, port_base: int) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_planner_controller_activation_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_pca_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_pca_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_pca_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    params = tmp / "nav2_planner_controller_params.yaml"
    params.write_text(nav2_params_yaml(), encoding="utf-8")

    router_port = port_base
    planner_port = port_base + 1
    controller_port = port_base + 2
    tf_port = port_base + 3
    cli_port = port_base + 20
    expected_service_frames = 28
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_pca_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    quoted_tf_yaml = shlex.quote(dynamic_tf_message_yaml())

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
            "! ros2 pkg prefix nav2_dwb_controller >/dev/null 2>&1 || "
            "! ros2 pkg prefix tf2_msgs >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-planner ros-jazzy-nav2-controller "
            "ros-jazzy-nav2-navfn-planner ros-jazzy-nav2-dwb-controller "
            "ros-jazzy-tf2-msgs "
            ">/tmp/fleetrmw_nav2_activation_install.log; "
            "fi; "
            "source /opt/ros/jazzy/setup.bash; "
            f"source /work/{install_base.relative_to(root)}/setup.bash; "
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 70000 "
            "--post-satisfaction-ms 1000 "
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
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_before.log 2>&1; "
            "planner_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 1} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 14 ros2 lifecycle set /planner_server configure "
            f"> /work/{tmp.relative_to(root)}/planner_configure.log 2>&1; "
            "planner_configure_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 2} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_after_configure.log 2>&1; "
            "planner_get_after_configure_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 3} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /controller_server "
            f"> /work/{tmp.relative_to(root)}/controller_get_before.log 2>&1; "
            "controller_get_before_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 4} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 14 ros2 lifecycle set /controller_server configure "
            f"> /work/{tmp.relative_to(root)}/controller_configure.log 2>&1; "
            "controller_configure_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 5} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 8 ros2 lifecycle get /controller_server "
            f"> /work/{tmp.relative_to(root)}/controller_get_after_configure.log 2>&1; "
            "controller_get_after_configure_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{tf_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 40 ros2 topic pub --rate 10 /tf tf2_msgs/msg/TFMessage "
            f"{quoted_tf_yaml} "
            f"> /work/{tmp.relative_to(root)}/tf_pub.log 2>&1 & "
            "tf_pid=$!; "
            "sleep 2; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 6} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 20 ros2 lifecycle set /planner_server activate "
            f"> /work/{tmp.relative_to(root)}/planner_activate.log 2>&1; "
            "planner_activate_rc=$?; "
            "sleep 0.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 7} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 10 ros2 lifecycle get /planner_server "
            f"> /work/{tmp.relative_to(root)}/planner_get_active.log 2>&1; "
            "planner_get_active_rc=$?; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 8} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 20 ros2 lifecycle set /controller_server activate "
            f"> /work/{tmp.relative_to(root)}/controller_activate.log 2>&1; "
            "controller_activate_rc=$?; "
            "sleep 1.5; "
            f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + 9} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "timeout 12 ros2 lifecycle get /controller_server "
            f"> /work/{tmp.relative_to(root)}/controller_get_active.log 2>&1; "
            "controller_get_active_rc=$?; "
            "kill ${planner_pid} ${controller_pid} ${tf_pid} >/dev/null 2>&1 || true; "
            "wait ${planner_pid} >/dev/null 2>&1 || true; "
            "wait ${controller_pid} >/dev/null 2>&1 || true; "
            "wait ${tf_pid} >/dev/null 2>&1 || true; "
            "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; "
            f"printf 'planner_get_before=%s\\nplanner_configure=%s\\n"
            "planner_get_after_configure=%s\\ncontroller_get_before=%s\\n"
            "controller_configure=%s\\ncontroller_get_after_configure=%s\\n"
            "planner_activate=%s\\nplanner_get_active=%s\\n"
            "controller_activate=%s\\ncontroller_get_active=%s\\nrouter=%s\\n' "
            "${planner_get_before_rc} ${planner_configure_rc} "
            "${planner_get_after_configure_rc} ${controller_get_before_rc} "
            "${controller_configure_rc} ${controller_get_after_configure_rc} "
            "${planner_activate_rc} ${planner_get_active_rc} "
            "${controller_activate_rc} ${controller_get_active_rc} ${router_rc} "
            f"> /work/{tmp.relative_to(root)}/return_codes.log; "
            "if [ ${planner_get_before_rc} -ne 0 ] || "
            "[ ${planner_configure_rc} -ne 0 ] || "
            "[ ${planner_get_after_configure_rc} -ne 0 ] || "
            "[ ${controller_get_before_rc} -ne 0 ] || "
            "[ ${controller_configure_rc} -ne 0 ] || "
            "[ ${controller_get_after_configure_rc} -ne 0 ] || "
            "[ ${planner_activate_rc} -ne 0 ] || "
            "[ ${planner_get_active_rc} -ne 0 ] || "
            "[ ${controller_activate_rc} -ne 0 ] || "
            "[ ${controller_get_active_rc} -ne 0 ]; then exit 20; fi; "
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
        metrics = router_metrics(router_summary)
        planner_get_after_configure = read("planner_get_after_configure.log")
        controller_get_after_configure = read("controller_get_after_configure.log")
        planner_get_active = read("planner_get_active.log")
        controller_get_active = read("controller_get_active.log")
        planner_configure = read("planner_configure.log")
        controller_configure = read("controller_configure.log")
        planner_activate = read("planner_activate.log")
        controller_activate = read("controller_activate.log")

        planner_configured = (
            "Transitioning successful" in planner_configure
            and "inactive [2]" in planner_get_after_configure
            and "Created global planner plugin GridBased" in planner_log
        )
        controller_configured = (
            "Transitioning successful" in controller_configure
            and "inactive [2]" in controller_get_after_configure
            and "Created controller : FollowPath of type dwb_core::DWBLocalPlanner" in controller_log
        )
        planner_activated = (
            "Transitioning successful" in planner_activate
            and "active [3]" in planner_get_active
            and "Activating plugin GridBased" in planner_log
        )
        controller_activated = (
            "Transitioning successful" in controller_activate
            and "active [3]" in controller_get_active
            and "Creating bond (controller_server)" in controller_log
        )
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
            and bool(metrics["tf_topic_advertised"])
            and bool(metrics["tf_topic_forwarded"])
        )
        ok = (
            docker.returncode == 0
            and planner_configured
            and controller_configured
            and planner_activated
            and controller_activated
            and lifecycle_transport_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_controller_server_available": True,
            "planner_plugin": "nav2_navfn_planner::NavfnPlanner",
            "controller_plugin": "dwb_core::DWBLocalPlanner",
            "planner_configure_transition": planner_configured,
            "controller_configure_transition": controller_configured,
            "planner_activate_transition": planner_activated,
            "controller_activate_transition": controller_activated,
            "planner_final_state": "active" if "active [3]" in planner_get_active else "unknown",
            "controller_final_state": "active" if "active [3]" in controller_get_active else "unknown",
            "dynamic_tf_runtime": True,
            "tf_message_type": "tf2_msgs/msg/TFMessage",
            "tf_chain": ["map->odom", "odom->base_link"],
            "tf_topic_advertised": metrics["tf_topic_advertised"],
            "tf_topic_forwarded": metrics["tf_topic_forwarded"],
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "fleetqox_router_graph_advertisements": metrics["graph_advertisements"],
            "activation_claim": True,
            "activation_scope": "planner_controller_lifecycle_active_with_dynamic_tf",
            "map_server_claim": False,
            "odometry_source_claim": False,
            "navigation_goal_claim": False,
            "full_nav2_navigation_stack_claim": False,
            "full_navigation_gap": "map_server_odometry_bt_navigator_and_navigation_goal_not_started",
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "planner_get_before": read("planner_get_before.log"),
            "planner_configure": planner_configure,
            "planner_get_after_configure": planner_get_after_configure,
            "planner_activate": planner_activate,
            "planner_get_active": planner_get_active,
            "controller_get_before": read("controller_get_before.log"),
            "controller_configure": controller_configure,
            "controller_get_after_configure": controller_get_after_configure,
            "controller_activate": controller_activate,
            "controller_get_active": controller_get_active,
            "router": metrics,
            "tf_pub_log_excerpt": read("tf_pub.log")[-3000:],
            "planner_log_excerpt": planner_log[-5000:],
            "controller_log_excerpt": controller_log[-5000:],
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=4680)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_planner_controller_activation_probe_summary.json",
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
            f"status={summary['status']} planner_active={summary.get('planner_activate_transition')} "
            f"controller_active={summary.get('controller_activate_transition')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
