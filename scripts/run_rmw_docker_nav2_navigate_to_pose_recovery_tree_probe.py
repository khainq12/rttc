"""Run a NavigateToPose behavior-tree recovery branch through FleetRMW.

This probe starts upstream `planner_server`, `behavior_server`, and
`bt_navigator`; configures and activates all three through rmw_fleetqox_cpp
lifecycle services; starts a fake base that publishes dynamic `/odom` and
`/tf`; then sends a `nav2_msgs/action/NavigateToPose` goal with a custom BT:

  RecoveryNode(ComputePathToPose with an intentionally missing planner, Spin)

The expected top-level navigation result is a controlled failure after retry,
but the recovery branch must execute: `/spin` must succeed, `/cmd_vel` must be
forwarded, and the fake base must rotate. This proves NavigateToPose BT-level
recovery fallback transport/execution, not a successful obstacle recovery
navigation scenario or a long workload.
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

from scripts.run_rmw_docker_nav2_behavior_spin_probe import (  # noqa: E402
    behavior_server_params_yaml,
)
from scripts.run_rmw_docker_nav2_navigate_to_pose_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    bt_navigator_params_yaml,
    fake_base_node_py,
    navigate_to_pose_goal_yaml,
    run_lifecycle_step,
)
from scripts.run_rmw_docker_nav2_planner_compute_path_probe import (  # noqa: E402
    occupancy_grid_yaml,
)
from scripts.run_rmw_docker_nav2_planner_controller_activation_probe import (  # noqa: E402
    router_metrics,
)
from scripts.run_rmw_docker_nav2_planner_controller_lifecycle_probe import (  # noqa: E402
    nav2_params_yaml,
)
from scripts.run_rmw_docker_router_service_call_probe import parse_last_json  # noqa: E402


SCHEMA_VERSION = "fleetrmw.docker_nav2_navigate_to_pose_recovery_tree_probe.v1"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def recovery_tree_bt_xml(spin_dist: float) -> str:
    return f"""<root BTCPP_format="4" main_tree_to_execute="MainTree">
  <BehaviorTree ID="MainTree">
    <RecoveryNode number_of_retries="1" name="IntentionalPlannerFailureRecovery">
      <ComputePathToPose goal="{{goal}}" path="{{path}}" planner_id="MissingPlanner" error_code_id="{{compute_path_error_code}}"/>
      <Spin spin_dist="{spin_dist}" time_allowance="8.0" error_code_id="{{spin_error_code}}"/>
    </RecoveryNode>
  </BehaviorTree>
</root>
"""


def parse_navigate_recovery_output(text: str) -> dict[str, Any]:
    result_start = text.find("Result:")
    result_text = text[result_start:] if result_start >= 0 else text
    status_match = re.search(r"Goal finished with status:\s*([A-Z_]+)", text)
    error_match = re.search(r"\berror_code:\s*(\d+)\b", result_text)
    return {
        "accepted": "Goal accepted" in text,
        "succeeded": "Goal finished with status: SUCCEEDED" in text,
        "status": status_match.group(1) if status_match else None,
        "error_code": int(error_match.group(1)) if error_match else None,
        "result_observed": result_start >= 0,
    }


def run_probe(
    *,
    root: Path,
    image: str,
    port_base: int,
    spin_dist: float,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    tmp = root / f".tmp_fleetrmw_nav2_recovery_tree_{suffix}"
    build_base = root / ".tmp_fleetrmw_nav2_recovery_tree_v2_build"
    install_base = root / ".tmp_fleetrmw_nav2_recovery_tree_v2_install"
    log_base = root / ".tmp_fleetrmw_nav2_recovery_tree_v2_log"
    tmp.mkdir(parents=True, exist_ok=True)
    bt_xml = tmp / "navigate_recovery_tree.xml"
    bt_xml.write_text(recovery_tree_bt_xml(spin_dist), encoding="utf-8")
    fake_base = tmp / "fake_base_node.py"
    fake_base.write_text(fake_base_node_py(), encoding="utf-8")
    bt_xml_in_container = f"/work/{bt_xml.relative_to(root)}"
    params = tmp / "nav2_recovery_tree_params.yaml"
    params.write_text(
        nav2_params_yaml()
        + bt_navigator_params_yaml(bt_xml_in_container)
        + behavior_server_params_yaml(),
        encoding="utf-8",
    )

    router_port = port_base
    planner_port = port_base + 1
    behavior_port = port_base + 2
    bt_port = port_base + 3
    fake_base_port = port_base + 4
    map_port = port_base + 5
    cli_port = port_base + 20
    expected_service_frames = 54
    router_exe = (
        "/work/.tmp_fleetrmw_nav2_recovery_tree_v2_install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    )
    tmp_rel = tmp.relative_to(root)
    quoted_map_yaml = shlex.quote(occupancy_grid_yaml())
    quoted_goal_yaml = shlex.quote(
        navigate_to_pose_goal_yaml(bt_xml_in_container, goal_x=0.6)
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

        parts: list[str] = [
            "set -e; source /opt/ros/jazzy/setup.bash; ",
            "if ! ros2 pkg prefix nav2_bt_navigator >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_behavior_tree >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_behaviors >/dev/null 2>&1 || "
            "! ros2 pkg prefix nav2_planner >/dev/null 2>&1; then "
            "apt-get update >/dev/null && "
            "apt-get install -y --no-install-recommends "
            "ros-jazzy-nav2-bt-navigator ros-jazzy-nav2-behavior-tree "
            "ros-jazzy-nav2-behaviors ros-jazzy-nav2-planner "
            "ros-jazzy-nav2-navfn-planner ros-jazzy-nav2-msgs "
            "ros-jazzy-nav-msgs ros-jazzy-tf2-msgs ros-jazzy-geometry-msgs "
            ">/tmp/fleetrmw_nav2_recovery_tree_install.log; "
            "fi; source /opt/ros/jazzy/setup.bash; ",
            f"source /work/{install_base.relative_to(root)}/setup.bash; ",
            f"{router_exe} --bind 127.0.0.1:{router_port} "
            "--expected-frames 1 "
            f"--expected-service-frames {expected_service_frames} "
            "--expected-graph-advertisements 4 --timeout-ms 100000 "
            "--post-satisfaction-ms 1000 "
            f"> /work/{tmp_rel}/router.log 2>&1 & router_pid=$!; ",
            "sleep 0.5; export RMW_IMPLEMENTATION=rmw_fleetqox_cpp; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{planner_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_planner planner_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/planner.log 2>&1 & planner_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{behavior_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_behaviors behavior_server --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/behavior_server.log 2>&1 & behavior_pid=$!; ",
            f"FLEETQOX_RMW_BIND=127.0.0.1:{bt_port} "
            f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
            "ros2 run nav2_bt_navigator bt_navigator --ros-args --params-file "
            f"/work/{params.relative_to(root)} "
            f"> /work/{tmp_rel}/bt.log 2>&1 & bt_pid=$!; ",
            "sleep 4; set +e; ",
        ]
        rc_index = 0
        for node_name in ("planner_server", "behavior_server", "bt_navigator"):
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_before.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="set",
                log_name=f"{node_name}_configure.log",
                timeout_s=18,
                rc_name=f"rc_{rc_index}",
            )
            parts[-1] = parts[-1].replace(
                f"ros2 lifecycle set /{node_name}",
                f"ros2 lifecycle set /{node_name} configure",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_configured.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{map_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 80 ros2 topic pub --rate 2 /map nav_msgs/msg/OccupancyGrid "
                f"{quoted_map_yaml} > /work/{tmp_rel}/map_pub.log 2>&1 & map_pid=$!; ",
                f"FLEETQOX_RMW_BIND=127.0.0.1:{fake_base_port} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "FLEETQOX_FAKE_BASE_GOAL_X=0.6 "
                f"FLEETQOX_FAKE_BASE_METRICS=/work/{tmp_rel}/fake_base_metrics.json "
                f"timeout 80 python3 /work/{fake_base.relative_to(root)} "
                f"> /work/{tmp_rel}/fake_base.log 2>&1 & fake_base_pid=$!; ",
                "sleep 3; ",
            ]
        )
        for node_name in ("planner_server", "behavior_server", "bt_navigator"):
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="set",
                log_name=f"{node_name}_activate.log",
                timeout_s=20,
                rc_name=f"rc_{rc_index}",
            )
            parts[-1] = parts[-1].replace(
                f"ros2 lifecycle set /{node_name}",
                f"ros2 lifecycle set /{node_name} activate",
            )
            rc_index += 1
            run_lifecycle_step(
                command_parts=parts,
                tmp_rel=tmp_rel,
                bind=cli_port + rc_index,
                peer=router_port,
                node_name=node_name,
                action="get",
                log_name=f"{node_name}_get_active.log",
                timeout_s=8,
                rc_name=f"rc_{rc_index}",
            )
            rc_index += 1

        parts.extend(
            [
                f"FLEETQOX_RMW_BIND=127.0.0.1:{cli_port + rc_index} "
                f"FLEETQOX_RMW_PEERS=127.0.0.1:{router_port} "
                "timeout 45 ros2 action send_goal /navigate_to_pose "
                "nav2_msgs/action/NavigateToPose "
                f"{quoted_goal_yaml} "
                f"> /work/{tmp_rel}/navigate_recovery_goal.log 2>&1; "
                "navigate_recovery_goal_rc=$?; ",
                "kill ${planner_pid} ${behavior_pid} ${bt_pid} ${map_pid} "
                "${fake_base_pid} >/dev/null 2>&1 || true; ",
                "wait ${planner_pid} >/dev/null 2>&1 || true; ",
                "wait ${behavior_pid} >/dev/null 2>&1 || true; ",
                "wait ${bt_pid} >/dev/null 2>&1 || true; ",
                "wait ${map_pid} >/dev/null 2>&1 || true; ",
                "wait ${fake_base_pid} >/dev/null 2>&1 || true; ",
                "wait ${router_pid} >/dev/null 2>&1; router_rc=$?; ",
                f"printf 'navigate_recovery_goal=%s\\nrouter=%s\\n' "
                "${navigate_recovery_goal_rc} ${router_rc} "
                f"> /work/{tmp_rel}/return_codes.log; ",
                "exit ${router_rc}",
            ]
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
                "".join(parts),
            ]
        )

        def read(name: str) -> str:
            path = tmp / name
            return path.read_text(errors="replace") if path.exists() else ""

        def read_json(name: str) -> dict[str, Any]:
            path = tmp / name
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        planner_log = read("planner.log")
        behavior_log = read("behavior_server.log")
        bt_log = read("bt.log")
        fake_base_log = read("fake_base.log")
        fake_base_metrics = read_json("fake_base_metrics.json")
        router_log = read("router.log")
        router_summary = parse_last_json(router_log)
        metrics = router_metrics(router_summary)
        forwarded_topics = router_summary.get("forwarded_topics", [])
        graph_topics = router_summary.get("graph_topics", [])
        navigate_goal = read("navigate_recovery_goal.log")
        navigate = parse_navigate_recovery_output(navigate_goal)
        planner_active = read("planner_server_get_active.log")
        behavior_active = read("behavior_server_get_active.log")
        bt_active = read("bt_navigator_get_active.log")
        planner_activated = "active [3]" in planner_active and "Activating plugin GridBased" in planner_log
        behavior_activated = "active [3]" in behavior_active and "Activating spin" in behavior_log
        bt_activated = "active [3]" in bt_active and "Creating bond (bt_navigator)" in bt_log
        lifecycle_transport_ok = (
            metrics["status"] == "ok"
            and int(metrics["service_frames"]) >= expected_service_frames
            and int(metrics["service_forwarded"]) >= expected_service_frames
        )
        fake_base_cmd_vel_count = int(fake_base_metrics.get("cmd_vel_count", 0) or 0)
        fake_base_angular_distance = float(fake_base_metrics.get("angular_distance", 0.0) or 0.0)
        spin_recovery_executed = (
            "/spin/_action/status" in forwarded_topics
            and "/spin/_action/feedback" in forwarded_topics
            and "/cmd_vel" in forwarded_topics
            and fake_base_cmd_vel_count > 0
            and fake_base_angular_distance >= spin_dist * 0.5
            and "spin completed successfully" in behavior_log
        )
        planner_failure_observed = (
            "MissingPlanner" in planner_log
            or "MissingPlanner" in bt_log
            or "MissingPlanner" in navigate_goal
            or navigate.get("status") in {"ABORTED", "FAILED"}
        )
        recovery_tree_ok = (
            docker.returncode == 0
            and bool(navigate["accepted"])
            and bool(navigate["result_observed"])
            and planner_failure_observed
            and spin_recovery_executed
        )
        ok = (
            planner_activated
            and behavior_activated
            and bt_activated
            and lifecycle_transport_ok
            and bool(metrics["tf_topic_forwarded"])
            and "/map" in forwarded_topics
            and recovery_tree_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "nav2_planner_server_available": True,
            "nav2_behavior_server_available": True,
            "nav2_bt_navigator_available": True,
            "planner_activate_transition": planner_activated,
            "behavior_server_activate_transition": behavior_activated,
            "bt_navigator_activate_transition": bt_activated,
            "behavior_tree": "recovery_node_compute_path_missing_planner_then_spin",
            "intentional_planner_failure": True,
            "planner_failure_observed": planner_failure_observed,
            "navigate_to_pose_action": True,
            "navigate_to_pose_goal_accepted": navigate["accepted"],
            "navigate_to_pose_goal_succeeded": navigate["succeeded"],
            "navigate_to_pose_result_observed": navigate["result_observed"],
            "navigate_to_pose_status": navigate["status"],
            "navigate_to_pose_error_code": navigate["error_code"],
            "navigate_to_pose_goal_scope": "intentional_planner_failure_recovery_tree",
            "navigate_to_pose_status_forwarded": "/navigate_to_pose/_action/status" in forwarded_topics,
            "compute_path_status_forwarded": "/compute_path_to_pose/_action/status" in forwarded_topics,
            "spin_action": True,
            "spin_goal_succeeded": "spin completed successfully" in behavior_log,
            "spin_target_yaw": spin_dist,
            "spin_status_forwarded": "/spin/_action/status" in forwarded_topics,
            "spin_feedback_forwarded": "/spin/_action/feedback" in forwarded_topics,
            "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
            "cmd_vel_topic_forwarded": "/cmd_vel" in forwarded_topics,
            "fake_base_cmd_vel_count": fake_base_cmd_vel_count,
            "fake_base_max_abs_cmd_theta": fake_base_metrics.get("max_abs_cmd_theta", 0.0),
            "fake_base_final_theta": fake_base_metrics.get("final_theta", 0.0),
            "fake_base_max_abs_theta": fake_base_metrics.get("max_abs_theta", 0.0),
            "fake_base_angular_distance": fake_base_angular_distance,
            "dynamic_tf_runtime": bool(metrics["tf_topic_forwarded"]),
            "map_runtime": "/map" in forwarded_topics,
            "odometry_runtime": "/odom" in graph_topics or "/odom" in forwarded_topics,
            "tf_topic_advertised": metrics["tf_topic_advertised"],
            "tf_topic_forwarded": metrics["tf_topic_forwarded"],
            "map_topic_advertised": "/map" in graph_topics,
            "map_topic_forwarded": "/map" in forwarded_topics,
            "odom_topic_advertised": "/odom" in graph_topics,
            "odom_topic_forwarded": "/odom" in forwarded_topics,
            "lifecycle_transport": lifecycle_transport_ok,
            "expected_service_frames": expected_service_frames,
            "fleetqox_router_service_frames": metrics["service_frames"],
            "fleetqox_router_service_forwarded": metrics["service_forwarded"],
            "fleetqox_router_received_frames": metrics["received_frames"],
            "fleetqox_router_forwarded_frames": metrics["forwarded_frames"],
            "nav2_recovery_behavior_claim": bool(spin_recovery_executed),
            "navigate_to_pose_recovery_tree_claim": bool(recovery_tree_ok),
            "navigate_to_pose_recovery_tree_scope": "intentional_compute_path_failure_spin_recovery_branch",
            "successful_recovered_navigation_claim": False,
            "long_navigation_workload_claim": False,
            "docker_returncode": docker.returncode,
            "docker_stdout": docker.stdout,
            "docker_stderr": docker.stderr,
            "return_codes": read("return_codes.log"),
            "navigate_recovery_goal_excerpt": navigate_goal[-5000:],
            "planner_log_excerpt": planner_log[-3000:],
            "behavior_server_log_excerpt": behavior_log[-5000:],
            "bt_log_excerpt": bt_log[-5000:],
            "fake_base_metrics": fake_base_metrics,
            "fake_base_log_excerpt": fake_base_log[-3000:],
            "router": {
                **metrics,
                "map_topic_advertised": "/map" in graph_topics,
                "map_topic_forwarded": "/map" in forwarded_topics,
                "odom_topic_advertised": "/odom" in graph_topics,
                "odom_topic_forwarded": "/odom" in forwarded_topics,
                "cmd_vel_topic_advertised": "/cmd_vel" in graph_topics,
                "cmd_vel_topic_forwarded": "/cmd_vel" in forwarded_topics,
            },
            "router_log_excerpt": router_log[-5000:],
        }
    finally:
        for path in (tmp, build_base, install_base, log_base):
            shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--port-base", type=int, default=5900)
    parser.add_argument("--spin-dist", type=float, default=0.6)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_nav2_navigate_to_pose_recovery_tree_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        port_base=args.port_base,
        spin_dist=args.spin_dist,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} recovery_tree="
            f"{summary.get('navigate_to_pose_recovery_tree_claim')} "
            f"spin={summary.get('spin_goal_succeeded')} "
            f"cmd_vel={summary.get('fake_base_cmd_vel_count')}"
        )
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
