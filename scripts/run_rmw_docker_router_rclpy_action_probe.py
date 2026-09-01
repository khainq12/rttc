"""Run a Docker router-mediated rclpy.action probe for rmw_fleetqox_cpp."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import textwrap
import time
from typing import Any

try:
    from scripts.run_rmw_docker_router_scheduled_reliability_probe import (
        NETEM_PROFILES,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_scheduled_reliability_probe import (
        NETEM_PROFILES,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
    )


SCHEMA_VERSION = "fleetrmw.rmw_docker_router_rclpy_action_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
DEFAULT_ACTION = "/fleetqox/lookup_transform"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--action", default=DEFAULT_ACTION)
    parser.add_argument("--forward-delay-ms", type=int, default=0)
    parser.add_argument("--feedback-lifespan-ms", type=int, default=0)
    parser.add_argument("--status-lifespan-ms", type=int, default=0)
    parser.add_argument("--feedback-deadline-ms", type=int, default=0)
    parser.add_argument("--status-deadline-ms", type=int, default=0)
    parser.add_argument("--scheduler-window-ms", type=int, default=0)
    parser.add_argument("--expected-data-frames", type=int, default=0)
    parser.add_argument(
        "--observation-mode",
        choices=("delivered", "dropped"),
        default="delivered",
    )
    parser.add_argument("--expected-qos-drops", type=int, default=0)
    parser.add_argument("--mixed-robot-count", type=int, default=0)
    parser.add_argument(
        "--netem-profile",
        choices=sorted(NETEM_PROFILES),
        default="none",
    )
    parser.add_argument("--netem-loss-percent", type=float, default=None)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_rmw_router_rclpy_action_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        action=args.action,
        forward_delay_ms=args.forward_delay_ms,
        feedback_lifespan_ms=args.feedback_lifespan_ms,
        status_lifespan_ms=args.status_lifespan_ms,
        feedback_deadline_ms=args.feedback_deadline_ms,
        status_deadline_ms=args.status_deadline_ms,
        scheduler_window_ms=args.scheduler_window_ms,
        expected_data_frames=args.expected_data_frames,
        expect_observation_delivery=args.observation_mode == "delivered",
        expected_qos_drops=args.expected_qos_drops,
        mixed_robot_count=max(args.mixed_robot_count, 0),
        netem_profile=args.netem_profile,
        netem_loss_percent=args.netem_loss_percent,
    )
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-rclpy-action-probe")
        print(f"  status: {summary['status']}")
        print(f"  client_status: {summary.get('client', {}).get('status')}")
        print(f"  router_service_forwarded: {summary.get('router', {}).get('service_forwarded')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    action: str,
    forward_delay_ms: int = 0,
    feedback_lifespan_ms: int = 0,
    status_lifespan_ms: int = 0,
    feedback_deadline_ms: int = 0,
    status_deadline_ms: int = 0,
    scheduler_window_ms: int = 0,
    expected_data_frames: int = 0,
    expect_observation_delivery: bool = True,
    expected_qos_drops: int = 0,
    mixed_robot_count: int = 0,
    netem_profile: str = "none",
    netem_loss_percent: float | None = None,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-action-net-{suffix}"
    router_name = f"fleetrmw-action-router-{suffix}"
    server_name = f"fleetrmw-action-server-{suffix}"
    mixed_flows = [
        {
            "robot_id": f"robot_{robot_index:04d}",
            "kind": kind,
            "topic": f"/fleetqox/mixed/robot-{robot_index:04d}/{kind}",
            "deadline_ms": 100 if kind == "control" else 1000,
        }
        for robot_index in range(max(mixed_robot_count, 0))
        for kind in ("control", "state")
    ]
    mixed_subscriber_names = [
        f"fleetrmw-action-mixed-sub-{suffix}-{index}"
        for index in range(len(mixed_flows))
    ]
    mixed_publisher_names = [
        f"fleetrmw-action-mixed-pub-{suffix}-{index}"
        for index in range(len(mixed_flows))
    ]
    build_base = "/work/.tmp_fleetrmw_router_action_build"
    install_base = "/work/.tmp_fleetrmw_router_action_install"
    log_base = "/work/.tmp_fleetrmw_router_action_log"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    netem_config = netem_config_for_profile(
        netem_profile,
        netem_loss_percent=netem_loss_percent,
    )

    def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

    def docker_shell(
        command: str,
        *extra: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return run([
            "docker", "run", *extra,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc", command,
        ], check=check)

    server_python = textwrap.dedent(
        """
        import copy
        import json
        import time
        import traceback

        import rclpy
        from rclpy.action import ActionServer, CancelResponse
        from rclpy.action.server import qos_profile_action_status_default
        from rclpy.duration import Duration
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.qos import QoSProfile
        from tf2_msgs.action import LookupTransform


        def spin_until(executor, predicate, timeout_sec):
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                executor.spin_once(timeout_sec=0.05)
                if predicate():
                    return True
            return predicate()


        summary = {
            "schema_version": "fleetrmw.rclpy_action_router_server_probe.v1",
            "status": "pending",
            "action_name": __ACTION__,
            "events": [],
            "feedback_published": 0,
            "cancel_callbacks": 0,
            "feedback_lifespan_ms": __FEEDBACK_LIFESPAN_MS__,
            "status_lifespan_ms": __STATUS_LIFESPAN_MS__,
            "feedback_deadline_ms": __FEEDBACK_DEADLINE_MS__,
            "status_deadline_ms": __STATUS_DEADLINE_MS__,
        }
        executor = None
        server = None
        node = None
        try:
            rclpy.init()
            node = rclpy.create_node(
                "fleetqox_router_action_server",
                enable_rosout=False,
                start_parameter_services=False)

            def execute_callback(goal_handle):
                target_frame = goal_handle.request.target_frame
                source_frame = goal_handle.request.source_frame
                summary["events"].append("execute:" + target_frame)
                goal_handle.publish_feedback(LookupTransform.Feedback())
                summary["feedback_published"] += 1
                result = LookupTransform.Result()
                result.transform.header.frame_id = target_frame
                result.transform.child_frame_id = source_frame
                result.transform.transform.rotation.w = 1.0
                result.error.error = 0
                result.error.error_string = "ok"
                if target_frame == "cancel_map":
                    for _ in range(80):
                        if goal_handle.is_cancel_requested:
                            summary["events"].append("cancel_requested")
                            result.error.error_string = "canceled"
                            goal_handle.canceled()
                            return result
                        time.sleep(0.05)
                    summary["events"].append("cancel_timeout")
                    result.error.error = 1
                    result.error.error_string = "cancel_timeout"
                    goal_handle.abort()
                    return result
                goal_handle.succeed()
                return result

            def cancel_callback(goal_handle):
                del goal_handle
                summary["events"].append("cancel_callback")
                summary["cancel_callbacks"] += 1
                return CancelResponse.ACCEPT

            feedback_qos = QoSProfile(depth=10)
            status_qos = copy.copy(qos_profile_action_status_default)
            if summary["feedback_lifespan_ms"] > 0:
                feedback_qos.lifespan = Duration(
                    nanoseconds=summary["feedback_lifespan_ms"] * 1000000)
            if summary["status_lifespan_ms"] > 0:
                status_qos.lifespan = Duration(
                    nanoseconds=summary["status_lifespan_ms"] * 1000000)
            if summary["feedback_deadline_ms"] > 0:
                feedback_qos.deadline = Duration(
                    nanoseconds=summary["feedback_deadline_ms"] * 1000000)
            if summary["status_deadline_ms"] > 0:
                status_qos.deadline = Duration(
                    nanoseconds=summary["status_deadline_ms"] * 1000000)

            server = ActionServer(
                node,
                LookupTransform,
                summary["action_name"],
                execute_callback,
                cancel_callback=cancel_callback,
                feedback_pub_qos_profile=feedback_qos,
                status_pub_qos_profile=status_qos)
            executor = MultiThreadedExecutor(num_threads=2)
            executor.add_node(node)
            completed = spin_until(
                executor,
                lambda: (
                    "execute:map" in summary["events"] and
                    "cancel_requested" in summary["events"]),
                14.0)
            summary["completed"] = completed
            spin_until(executor, lambda: False, 2.0)
            summary["status"] = "ok" if (
                completed and
                summary["feedback_published"] >= 2 and
                summary["cancel_callbacks"] >= 1
            ) else "failed"
        except Exception as exc:
            summary["status"] = "exception"
            summary["exception"] = repr(exc)
            summary["traceback"] = traceback.format_exc()
        finally:
            if executor is not None:
                try:
                    executor.shutdown()
                except Exception:
                    pass
            if server is not None:
                try:
                    server.destroy()
                except Exception:
                    pass
            if node is not None:
                try:
                    node.destroy_node()
                except Exception:
                    pass
            try:
                rclpy.shutdown()
            except Exception:
                pass
        print(json.dumps(summary, sort_keys=True))
        """
    ).replace("__ACTION__", json.dumps(action)).replace(
        "__FEEDBACK_LIFESPAN_MS__", str(max(feedback_lifespan_ms, 0))
    ).replace(
        "__STATUS_LIFESPAN_MS__", str(max(status_lifespan_ms, 0))
    ).replace(
        "__FEEDBACK_DEADLINE_MS__", str(max(feedback_deadline_ms, 0))
    ).replace(
        "__STATUS_DEADLINE_MS__", str(max(status_deadline_ms, 0))
    )

    client_python = textwrap.dedent(
        """
        import json
        import time
        import traceback

        from action_msgs.msg import GoalStatusArray
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.executors import MultiThreadedExecutor
        from tf2_msgs.action import LookupTransform


        def spin_until(executor, predicate, timeout_sec):
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                executor.spin_once(timeout_sec=0.05)
                if predicate():
                    return True
            return predicate()


        def graph_snapshot(node, action_name):
            service_names = sorted(name for name, _ in node.get_service_names_and_types())
            return {
                "action_service_names": [
                    name for name in service_names if name.startswith(action_name + "/_action/")
                ],
                "status_publishers": node.count_publishers(action_name + "/_action/status"),
                "feedback_publishers": node.count_publishers(action_name + "/_action/feedback"),
                "status_subscribers": node.count_subscribers(action_name + "/_action/status"),
                "feedback_subscribers": node.count_subscribers(action_name + "/_action/feedback"),
            }


        summary = {
            "schema_version": "fleetrmw.rclpy_action_router_client_probe.v1",
            "status": "pending",
            "action_name": __ACTION__,
            "action_type": "tf2_msgs/action/LookupTransform",
            "feedback_callbacks": [],
            "status_samples": [],
            "expect_observation_delivery": __EXPECT_OBSERVATION_DELIVERY__,
        }
        executor = None
        client = None
        node = None
        try:
            rclpy.init()
            node = rclpy.create_node(
                "fleetqox_router_action_client",
                enable_rosout=False,
                start_parameter_services=False)

            def status_callback(message):
                summary["status_samples"].append([
                    int(status.status) for status in message.status_list
                ])

            status_subscription = node.create_subscription(
                GoalStatusArray,
                summary["action_name"] + "/_action/status",
                status_callback,
                10)
            client = ActionClient(node, LookupTransform, summary["action_name"])
            executor = MultiThreadedExecutor(num_threads=2)
            executor.add_node(node)
            summary["available_before_send"] = spin_until(
                executor,
                lambda: client.server_is_ready(),
                8.0)
            summary["graph_before_send"] = graph_snapshot(node, summary["action_name"])

            def feedback_callback(label):
                def _callback(feedback_message):
                    del feedback_message
                    summary["feedback_callbacks"].append(label)
                return _callback

            success_goal = LookupTransform.Goal()
            success_goal.target_frame = "map"
            success_goal.source_frame = "base_link"
            success_send_future = client.send_goal_async(
                success_goal,
                feedback_callback=feedback_callback("success"))
            summary["success_send_done"] = spin_until(
                executor, lambda: success_send_future.done(), 8.0)
            if summary["success_send_done"]:
                success_handle = success_send_future.result()
                summary["success_goal_accepted"] = bool(success_handle.accepted)
                success_result_future = success_handle.get_result_async()
                summary["success_result_done"] = spin_until(
                    executor, lambda: success_result_future.done(), 8.0)
                if summary["success_result_done"]:
                    result_wrapper = success_result_future.result()
                    result = result_wrapper.result
                    summary["success_result_status"] = int(result_wrapper.status)
                    summary["success_result_frame"] = result.transform.header.frame_id
                    summary["success_result_child_frame"] = result.transform.child_frame_id
                    summary["success_result_error"] = int(result.error.error)

            cancel_goal = LookupTransform.Goal()
            cancel_goal.target_frame = "cancel_map"
            cancel_goal.source_frame = "cancel_base"
            cancel_send_future = client.send_goal_async(
                cancel_goal,
                feedback_callback=feedback_callback("cancel"))
            summary["cancel_send_done"] = spin_until(
                executor, lambda: cancel_send_future.done(), 8.0)
            if summary["cancel_send_done"]:
                cancel_handle = cancel_send_future.result()
                summary["cancel_goal_accepted"] = bool(cancel_handle.accepted)
                summary["cancel_feedback_seen"] = spin_until(
                    executor,
                    lambda: "cancel" in summary["feedback_callbacks"],
                    4.0 if summary["expect_observation_delivery"] else 0.2)
                cancel_future = cancel_handle.cancel_goal_async()
                summary["cancel_response_done"] = spin_until(
                    executor, lambda: cancel_future.done(), 8.0)
                if summary["cancel_response_done"]:
                    cancel_response = cancel_future.result()
                    summary["cancel_goals_canceling"] = len(cancel_response.goals_canceling)
                cancel_result_future = cancel_handle.get_result_async()
                summary["cancel_result_done"] = spin_until(
                    executor, lambda: cancel_result_future.done(), 8.0)
                if summary["cancel_result_done"]:
                    cancel_result_wrapper = cancel_result_future.result()
                    cancel_result = cancel_result_wrapper.result
                    summary["cancel_result_status"] = int(cancel_result_wrapper.status)
                    summary["cancel_result_frame"] = cancel_result.transform.header.frame_id
                    summary["cancel_result_child_frame"] = cancel_result.transform.child_frame_id
                    summary["cancel_result_error"] = int(cancel_result.error.error)
                    summary["cancel_result_error_string"] = cancel_result.error.error_string

            summary["status_observed"] = spin_until(
                executor,
                lambda: any(len(sample) > 0 for sample in summary["status_samples"]),
                2.0)
            summary["available_after_result"] = spin_until(
                executor,
                lambda: client.server_is_ready(),
                1.0)
            spin_until(executor, lambda: False, 0.5)
            summary["graph_after_result"] = graph_snapshot(node, summary["action_name"])
            del status_subscription
            if summary["expect_observation_delivery"]:
                observation_ok = (
                    "success" in summary["feedback_callbacks"] and
                    "cancel" in summary["feedback_callbacks"] and
                    summary.get("status_observed") is True
                )
            else:
                observation_ok = (
                    summary["feedback_callbacks"] == [] and
                    summary.get("status_observed") is False
                )
            summary["status"] = "ok" if (
                summary.get("available_before_send") is True and
                summary.get("available_after_result") is True and
                summary.get("success_send_done") is True and
                summary.get("success_goal_accepted") is True and
                summary.get("success_result_done") is True and
                summary.get("success_result_status") == 4 and
                summary.get("success_result_frame") == "map" and
                summary.get("success_result_child_frame") == "base_link" and
                summary.get("success_result_error") == 0 and
                summary.get("cancel_send_done") is True and
                summary.get("cancel_goal_accepted") is True and
                summary.get("cancel_response_done") is True and
                summary.get("cancel_goals_canceling", 0) >= 1 and
                summary.get("cancel_result_done") is True and
                summary.get("cancel_result_status") == 5 and
                summary.get("cancel_result_frame") == "cancel_map" and
                summary.get("cancel_result_child_frame") == "cancel_base" and
                observation_ok
            ) else "failed"
        except Exception as exc:
            summary["status"] = "exception"
            summary["exception"] = repr(exc)
            summary["traceback"] = traceback.format_exc()
        finally:
            if executor is not None:
                try:
                    executor.shutdown()
                except Exception:
                    pass
            if client is not None:
                try:
                    client.destroy()
                except Exception:
                    pass
            if node is not None:
                try:
                    node.destroy_node()
                except Exception:
                    pass
            try:
                rclpy.shutdown()
            except Exception:
                pass
        print(json.dumps(summary, sort_keys=True))
        """
    ).replace("__ACTION__", json.dumps(action)).replace(
        "__EXPECT_OBSERVATION_DELIVERY__",
        "True" if expect_observation_delivery else "False",
    )

    server_command = (
        "source /opt/ros/jazzy/setup.bash\n"
        f"source {install_base}/setup.bash\n"
        "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp\n"
        "export FLEETQOX_RMW_BIND=0.0.0.0:48401\n"
        f"export FLEETQOX_RMW_PEERS={router_name}:48400\n"
        "python3 - <<'PY'\n"
        f"{server_python}\n"
        "PY\n"
    )
    client_command = (
        "source /opt/ros/jazzy/setup.bash\n"
        f"source {install_base}/setup.bash\n"
        "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp\n"
        "export FLEETQOX_RMW_BIND=0.0.0.0:48402\n"
        f"export FLEETQOX_RMW_PEERS={router_name}:48400\n"
        "python3 - <<'PY'\n"
        f"{client_python}\n"
        "PY\n"
    )

    try:
        docker_shell(
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf {build_base} {install_base} {log_base} && "
            "colcon "
            f"--log-base {log_base} build --base-paths ros2_ws/src "
            "--packages-select fleetrmw_interfaces rmw_fleetqox_cpp "
            f"--build-base {build_base} --install-base {install_base} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release"
        )
        run(["docker", "network", "create", network])
        subnet_result = run(
            ["docker", "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network]
        )
        subnet = ipaddress.ip_network(subnet_result.stdout.strip(), strict=False)
        router_ip = str(subnet.network_address + 10)
        mixed_subscriber_ips = [
            str(subnet.network_address + 20 + index) for index in range(len(mixed_flows))
        ]
        mixed_publisher_ips = [
            str(subnet.network_address + 20 + len(mixed_flows) + index)
            for index in range(len(mixed_flows))
        ]
        mixed_publisher_ports = [48600 + index for index in range(len(mixed_flows))]
        mixed_graph_peers = ",".join(
            [f"{ip}:{48500 + index}" for index, ip in enumerate(mixed_subscriber_ips)]
            + [f"{ip}:{port}" for ip, port in zip(mixed_publisher_ips, mixed_publisher_ports)]
        )
        graph_peers_args = f"--graph-peers {mixed_graph_peers} " if mixed_graph_peers else ""

        mixed_expected_frames = len(mixed_flows) * 4
        mixed_expected_ack_nack = len(mixed_flows) * 3
        total_expected_data_frames = max(expected_data_frames, 0) + mixed_expected_frames
        scheduler_expected_frames = max(expected_data_frames, 0)
        scheduler_topic_prefix = action + "/_action/"
        scheduler_urgent_deadline_ms = 0
        drop_args = ""
        netem_command = ""
        router_extra_args: list[str] = []
        post_satisfaction_ms = 500
        if mixed_flows:
            scheduler_expected_frames = max(len(mixed_flows), 2)
            scheduler_topic_prefix = "/fleetqox/"
            scheduler_urgent_deadline_ms = 100
            drop_args = (
                "--drop-source-sequences 2 "
                "--drop-topic-prefix /fleetqox/mixed/ "
            )
        if netem_profile != "none":
            netem_command = (
                "tc qdisc replace dev eth0 root netem "
                f"delay {netem_config['delay_ms']:g}ms {netem_config['jitter_ms']:g}ms "
                f"loss {netem_config['loss_percent']:g}% "
                f"rate {netem_config['rate_mbit']:g}mbit && "
            )
            router_extra_args = ["--cap-add", "NET_ADMIN"]
            post_satisfaction_ms = max(
                post_satisfaction_ms,
                netem_post_satisfaction_ms(netem_config, enabled=True),
            )
        router = run([
            "docker", "run", "-d",
            "--name", router_name,
            "--network", network,
            "--ip", router_ip,
            *router_extra_args,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            f"source {install_base}/setup.bash && "
            f"{netem_command}"
            f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_router_probe "
            "--bind 0.0.0.0:48400 "
            f"{graph_peers_args}"
            f"--expected-frames {total_expected_data_frames} "
            "--expected-service-frames 10 "
            f"--expected-ack-nack-frames {mixed_expected_ack_nack} "
            f"--expected-route-advertisements {len(mixed_flows)} "
            f"--expected-graph-advertisements {4 + len(mixed_flows) * 2} "
            f"--expected-qos-drops {max(expected_qos_drops, 0)} "
            f"--forward-delay-ms {max(forward_delay_ms, 0)} "
            f"--scheduler-window-ms {max(scheduler_window_ms, 0)} "
            f"--scheduler-expected-frames {scheduler_expected_frames} "
            f"--scheduler-urgent-deadline-ms {scheduler_urgent_deadline_ms} "
            f"--scheduler-topic-prefix {scheduler_topic_prefix} "
            f"{drop_args}"
            f"--post-satisfaction-ms {post_satisfaction_ms} "
            "--timeout-ms 25000",
        ])
        router_container = router.stdout.strip()
        time.sleep(2.0)
        netem_qdisc = (
            run(
                ["docker", "exec", router_name, "tc", "qdisc", "show", "dev", "eth0"],
                check=False,
            ).stdout.strip()
            if netem_profile != "none" else "disabled"
        )

        server = run([
            "docker", "run", "-d",
            "--name", server_name,
            "--network", network,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc",
            server_command,
        ])
        server_container = server.stdout.strip()

        for index, (name, flow) in enumerate(zip(mixed_subscriber_names, mixed_flows)):
            run([
                "docker", "run", "-d",
                "--name", name,
                "--network", network,
                "--ip", mixed_subscriber_ips[index],
                "--entrypoint", "bash",
                "-v", f"{root}:/work",
                "-w", "/work",
                image,
                "-lc",
                (
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(flow['robot_id'])} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{48500 + index} "
                    f"FLEETQOX_RMW_PEERS={router_ip}:48400 "
                    f"{endpoint_binary} --mode subscriber "
                    f"--topic {shlex.quote(flow['topic'])} --timeout-ms 18000 "
                    f"--deadline-ms {flow['deadline_ms']} --min-ack-nack-sent 3"
                ),
            ])
        if mixed_flows:
            time.sleep(0.8)
        # See run_rmw_docker_router_reliability_probe.py for why
        # --pre-publish-wait-ms is needed: any graph advertisement sent
        # before a publisher's own socket is bound is lost (UDP has no
        # receiver queue), so this must delay each publisher's first
        # publish() rather than the orchestrator sleeping earlier.
        for index, (name, flow) in enumerate(zip(mixed_publisher_names, mixed_flows)):
            run([
                "docker", "run", "-d",
                "--name", name,
                "--network", network,
                "--ip", mixed_publisher_ips[index],
                "--entrypoint", "bash",
                "-v", f"{root}:/work",
                "-w", "/work",
                image,
                "-lc",
                (
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(flow['robot_id'])} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{mixed_publisher_ports[index]} "
                    f"FLEETQOX_RMW_PEERS={router_ip}:48400 "
                    f"{endpoint_binary} --mode publisher "
                    f"--topic {shlex.quote(flow['topic'])} --hold-ms 14000 "
                    f"--deadline-ms {flow['deadline_ms']} "
                    "--pre-publish-wait-ms 800 "
                    "--min-ack-nack-received 3 --min-retransmissions 1"
                ),
            ])

        client = docker_shell(client_command, "--network", network, check=False)
        mixed_publisher_returncodes = [
            int(run(["docker", "wait", name], check=False).stdout.strip() or "999")
            for name in mixed_publisher_names
        ]
        mixed_subscriber_returncodes = [
            int(run(["docker", "wait", name], check=False).stdout.strip() or "999")
            for name in mixed_subscriber_names
        ]
        server_wait = run(["docker", "wait", server_container], check=False)
        router_wait = run(["docker", "wait", router_container], check=False)
        server_logs = run(["docker", "logs", server_container], check=False)
        router_logs = run(["docker", "logs", router_container], check=False)
        mixed_publisher_logs = [
            run(["docker", "logs", name], check=False).stdout
            for name in mixed_publisher_names
        ]
        mixed_subscriber_logs = [
            run(["docker", "logs", name], check=False).stdout
            for name in mixed_subscriber_names
        ]

        client_summary = parse_last_json(client.stdout)
        server_summary = parse_last_json(server_logs.stdout)
        router_summary = parse_last_json(router_logs.stdout)
        mixed_publishers = [parse_last_json(log) for log in mixed_publisher_logs]
        mixed_subscribers = [parse_last_json(log) for log in mixed_subscriber_logs]
        mixed_rows = []
        for index, (flow, publisher, subscriber) in enumerate(
            zip(mixed_flows, mixed_publishers, mixed_subscribers)
        ):
            payloads = list(subscriber.get("payloads", []))
            row_ok = (
                mixed_publisher_returncodes[index] == 0 and
                mixed_subscriber_returncodes[index] == 0 and
                publisher.get("status") == "ok" and
                subscriber.get("status") == "ok" and
                publisher.get("nack_retransmissions", 0) >= 1 and
                {"one", "two", "three"}.issubset(payloads)
            )
            mixed_rows.append({
                **flow,
                "status": "ok" if row_ok else "failed",
                "publisher_returncode": mixed_publisher_returncodes[index],
                "subscriber_returncode": mixed_subscriber_returncodes[index],
                "publisher": publisher,
                "subscriber": subscriber,
            })
        summary = {
            "schema_version": SCHEMA_VERSION,
            "status": "pending",
            "docker_network": network,
            "action_name": action,
            "forward_delay_ms": forward_delay_ms,
            "feedback_lifespan_ms": feedback_lifespan_ms,
            "status_lifespan_ms": status_lifespan_ms,
            "feedback_deadline_ms": feedback_deadline_ms,
            "status_deadline_ms": status_deadline_ms,
            "scheduler_window_ms": scheduler_window_ms,
            "expected_data_frames": expected_data_frames,
            "expect_observation_delivery": expect_observation_delivery,
            "expected_qos_drops": expected_qos_drops,
            "mixed_robot_count": mixed_robot_count,
            "mixed_flow_count": len(mixed_flows),
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "netem_qdisc": netem_qdisc,
            "post_satisfaction_ms": post_satisfaction_ms,
            "mixed_rows": mixed_rows,
            "client": client_summary,
            "server": server_summary,
            "router": router_summary,
            "client_returncode": client.returncode,
            "client_stdout": client.stdout,
            "client_stderr": client.stderr,
            "server_returncode": int(server_wait.stdout.strip() or "999"),
            "server_logs": server_logs.stdout,
            "router_returncode": int(router_wait.stdout.strip() or "999"),
            "router_logs": router_logs.stdout,
        }
        observation_ok = (
            (
                "success" in client_summary.get("feedback_callbacks", []) and
                "cancel" in client_summary.get("feedback_callbacks", []) and
                client_summary.get("status_observed") is True
            ) if expect_observation_delivery else (
                client_summary.get("feedback_callbacks") == [] and
                client_summary.get("status_observed") is False
            )
        )
        scheduler_per_robot = router_summary.get("scheduler_per_robot", {})
        mixed_ok = (
            not mixed_flows or (
                all(row["status"] == "ok" for row in mixed_rows) and
                router_summary.get("test_dropped_frames", 0) >= len(mixed_flows) and
                router_summary.get("ack_nack_forwarded", 0) >= mixed_expected_ack_nack and
                all(
                    robot_id in scheduler_per_robot
                    for robot_id in {flow["robot_id"] for flow in mixed_flows}
                )
            )
        )
        summary["status"] = "ok" if (
            client.returncode == 0 and
            summary["server_returncode"] == 0 and
            summary["router_returncode"] == 0 and
            client_summary.get("status") == "ok" and
            client_summary.get("available_before_send") is True and
            client_summary.get("available_after_result") is True and
            client_summary.get("success_result_status") == 4 and
            client_summary.get("cancel_result_status") == 5 and
            observation_ok and
            server_summary.get("status") == "ok" and
            router_summary.get("status") == "ok" and
            router_summary.get("service_frames", 0) >= 10 and
            router_summary.get("service_forwarded", 0) >= 10 and
            router_summary.get("qos_dropped_frames", 0) >= expected_qos_drops and
            router_summary.get("graph_services", 0) >= 3 and
            router_summary.get("graph_clients", 0) >= 3 and
            mixed_ok
        ) else "failed"
        return summary
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "docker_network": network,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run([
            "docker", "rm", "-f", router_name, server_name,
            *mixed_subscriber_names, *mixed_publisher_names,
        ], check=False)
        run(["docker", "network", "rm", network], check=False)
        docker_shell(f"rm -rf {build_base} {install_base} {log_base}", check=False)


def parse_last_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return {"status": "parse_failed", "raw": stripped}
    return {"status": "missing", "raw": output}


if __name__ == "__main__":
    raise SystemExit(main())
