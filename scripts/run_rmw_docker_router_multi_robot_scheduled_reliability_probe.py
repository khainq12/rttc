"""Run concurrent per-robot scheduled ACK/NACK repair through FleetRMW."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any

try:
    from scripts.run_rmw_docker_router_scheduled_reliability_probe import (
        DEFAULT_IMAGE,
        NETEM_PROFILES,
        docker_shell,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
        parse_last_json,
        run,
        start_container,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_scheduled_reliability_probe import (
        DEFAULT_IMAGE,
        NETEM_PROFILES,
        docker_shell,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
        parse_last_json,
        run,
        start_container,
    )


SCHEMA_VERSION = (
    "fleetrmw.rmw_router_multi_robot_scheduled_reliability_probe.v1"
)
DEFAULT_TOPIC_PREFIX = "/fleetqox/multi_robot_scheduled_reliability"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-count", type=int, default=4)
    parser.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX)
    parser.add_argument(
        "--netem-profile",
        choices=sorted(profile for profile in NETEM_PROFILES if profile != "none"),
        default="roaming",
    )
    parser.add_argument("--netem-loss-percent", type=float, default=0.02)
    parser.add_argument("--scheduler-window-ms", type=int, default=150)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_router_multi_robot_scheduled_reliability_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        robot_count=max(args.robot_count, 1),
        topic_prefix=args.topic_prefix,
        netem_profile=args.netem_profile,
        netem_loss_percent=max(args.netem_loss_percent, 0.0),
        scheduler_window_ms=max(args.scheduler_window_ms, 1),
    )
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-multi-robot-scheduled-reliability-probe")
        print(f"  status: {summary['status']}")
        print(f"  robots_ok: {summary.get('robots_ok')}/{summary.get('robot_count')}")
        print(
            "  router_test_dropped_frames: "
            f"{summary.get('router', {}).get('test_dropped_frames')}"
        )
        print(
            "  total_nack_retransmissions: "
            f"{summary.get('total_nack_retransmissions')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    robot_count: int,
    topic_prefix: str,
    netem_profile: str,
    netem_loss_percent: float,
    scheduler_window_ms: int,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-multi-sched-rel-net-{suffix}"
    router_name = f"fleetrmw-multi-sched-rel-router-{suffix}"
    subscriber_names = [
        f"fleetrmw-multi-sched-rel-sub-{suffix}-{index}"
        for index in range(robot_count)
    ]
    publisher_names = [
        f"fleetrmw-multi-sched-rel-pub-{suffix}-{index}"
        for index in range(robot_count)
    ]
    build_base = "/work/.tmp_fleetrmw_multi_scheduled_reliability_build"
    install_base = "/work/.tmp_fleetrmw_multi_scheduled_reliability_install"
    log_base = "/work/.tmp_fleetrmw_multi_scheduled_reliability_log"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    router_port = 48840
    topics = [
        f"{topic_prefix.rstrip('/')}/robot-{index:04d}/control"
        for index in range(robot_count)
    ]
    robot_ids = [f"robot_{index:04d}" for index in range(robot_count)]
    netem_config = netem_config_for_profile(
        netem_profile,
        netem_loss_percent=netem_loss_percent,
    )
    post_satisfaction_ms = netem_post_satisfaction_ms(
        netem_config,
        enabled=True,
    )
    expected_frames = robot_count * 4
    expected_ack_nack_frames = robot_count * 3

    try:
        run(["docker", "network", "create", network])
        subnet_result = run(
            ["docker", "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network]
        )
        subnet = ipaddress.ip_network(subnet_result.stdout.strip(), strict=False)
        router_ip = str(subnet.network_address + 10)
        subscriber_ips = [
            str(subnet.network_address + 11 + index) for index in range(robot_count)
        ]
        publisher_ips = [
            str(subnet.network_address + 11 + robot_count + index)
            for index in range(robot_count)
        ]
        publisher_ports = [49000 + index for index in range(robot_count)]
        docker_shell(
            root,
            image,
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf {build_base} {install_base} {log_base} && "
            "colcon "
            f"--log-base {log_base} "
            "build --base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
            f"--build-base {build_base} --install-base {install_base} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release",
        )
        netem_command = (
            "tc qdisc replace dev eth0 root netem "
            f"delay {netem_config['delay_ms']:g}ms {netem_config['jitter_ms']:g}ms "
            f"loss {netem_config['loss_percent']:g}% "
            f"rate {netem_config['rate_mbit']:g}mbit && "
        )
        graph_peers = ",".join(
            [f"{ip}:{48900 + index}" for index, ip in enumerate(subscriber_ips)]
            + [f"{ip}:{port}" for ip, port in zip(publisher_ips, publisher_ports)]
        )
        start_container(
            root=root,
            image=image,
            name=router_name,
            network=network,
            ip_address=router_ip,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                f"{netem_command}"
                f"{router_binary} --bind 0.0.0.0:{router_port} "
                f"--graph-peers {graph_peers} "
                f"--expected-frames {expected_frames} "
                f"--expected-ack-nack-frames {expected_ack_nack_frames} "
                f"--expected-route-advertisements {robot_count} "
                f"--expected-graph-advertisements {robot_count * 2} "
                "--drop-source-sequences 2 "
                f"--scheduler-window-ms {scheduler_window_ms} "
                f"--scheduler-expected-frames {max(robot_count * 2, 2)} "
                "--scheduler-topic-prefix /fleetqox/ "
                f"--post-satisfaction-ms {post_satisfaction_ms} "
                "--timeout-ms 18000"
            ),
            extra_args=("--cap-add", "NET_ADMIN"),
        )
        time.sleep(2.0)
        netem_qdisc = run(
            ["docker", "exec", router_name, "tc", "qdisc", "show", "dev", "eth0"],
            check=False,
        ).stdout.strip()

        for index, (subscriber_name, topic, robot_id) in enumerate(
            zip(subscriber_names, topics, robot_ids)
        ):
            start_container(
                root=root,
                image=image,
                name=subscriber_name,
                network=network,
                ip_address=subscriber_ips[index],
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{48900 + index} "
                    f"FLEETQOX_RMW_PEERS={router_ip}:{router_port} "
                    f"{endpoint_binary} --mode subscriber "
                    f"--topic {shlex.quote(topic)} --timeout-ms 13000 "
                    "--min-ack-nack-sent 3"
                ),
            )
        time.sleep(1.0)

        # See run_rmw_docker_router_reliability_probe.py for why
        # --pre-publish-wait-ms is needed: any graph advertisement sent
        # before a publisher's own socket is bound is lost (UDP has no
        # receiver queue), so this must delay each publisher's first
        # publish() rather than the orchestrator sleeping earlier.
        for index, (publisher_name, topic, robot_id) in enumerate(
            zip(publisher_names, topics, robot_ids)
        ):
            start_container(
                root=root,
                image=image,
                name=publisher_name,
                network=network,
                ip_address=publisher_ips[index],
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{publisher_ports[index]} "
                    f"FLEETQOX_RMW_PEERS={router_ip}:{router_port} "
                    f"{endpoint_binary} --mode publisher "
                    f"--topic {shlex.quote(topic)} --hold-ms 9000 "
                    "--pre-publish-wait-ms 800 "
                    "--min-ack-nack-received 3 --min-retransmissions 1"
                ),
            )

        publisher_returncodes = [
            int(run(["docker", "wait", name]).stdout.strip())
            for name in publisher_names
        ]
        subscriber_returncodes = [
            int(run(["docker", "wait", name]).stdout.strip())
            for name in subscriber_names
        ]
        router_returncode = int(run(["docker", "wait", router_name]).stdout.strip())
        publisher_logs = [
            run(["docker", "logs", name], check=False).stdout.strip()
            for name in publisher_names
        ]
        subscriber_logs = [
            run(["docker", "logs", name], check=False).stdout.strip()
            for name in subscriber_names
        ]
        router_logs = run(
            ["docker", "logs", router_name],
            check=False,
        ).stdout.strip()
        publisher_results = [parse_last_json(log) for log in publisher_logs]
        subscriber_results = [parse_last_json(log) for log in subscriber_logs]
        router_result = parse_last_json(router_logs)
        robot_rows = []
        for index, (robot_id, topic, publisher, subscriber) in enumerate(
            zip(robot_ids, topics, publisher_results, subscriber_results)
        ):
            payloads = list(subscriber.get("payloads", []))
            row_ok = (
                publisher_returncodes[index] == 0
                and subscriber_returncodes[index] == 0
                and publisher.get("status") == "ok"
                and subscriber.get("status") == "ok"
                and int(publisher.get("ack_nack_received", 0)) >= 3
                and int(publisher.get("nack_retransmissions", 0)) >= 1
                and int(subscriber.get("ack_nack_sent", 0)) >= 3
                and {"one", "two", "three"}.issubset(payloads)
            )
            robot_rows.append(
                {
                    "robot_id": robot_id,
                    "topic": topic,
                    "status": "ok" if row_ok else "failed",
                    "publisher_returncode": publisher_returncodes[index],
                    "subscriber_returncode": subscriber_returncodes[index],
                    "publisher": publisher,
                    "subscriber": subscriber,
                    "publisher_logs": publisher_logs[index],
                    "subscriber_logs": subscriber_logs[index],
                }
            )

        robots_ok = sum(row["status"] == "ok" for row in robot_rows)
        scheduler_per_robot = router_result.get("scheduler_per_robot", {})
        scheduler_robot_evidence_ok = all(
            robot_id in scheduler_per_robot
            and int(scheduler_per_robot[robot_id].get("forwarded", 0)) >= 3
            and int(scheduler_per_robot[robot_id].get("deadline_misses", 0)) == 0
            for robot_id in robot_ids
        )
        router_ok = (
            router_returncode == 0
            and router_result.get("status") == "ok"
            and int(router_result.get("test_dropped_frames", 0)) >= robot_count
            and int(router_result.get("ack_nack_forwarded", 0))
            >= expected_ack_nack_frames
            and int(router_result.get("scheduler_queued_frames", 0))
            >= robot_count * 3
            and int(router_result.get("scheduler_forwarded_frames", 0))
            >= robot_count * 3
            and scheduler_robot_evidence_ok
            and float(
                router_result.get("scheduler_deadline_success_jain_index", 0.0)
            )
            >= 0.999
        )
        qdisc_ok = "netem" in netem_qdisc and (
            netem_loss_percent <= 0.0 or "loss" in netem_qdisc
        )
        status = robots_ok == robot_count and router_ok and qdisc_ok
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "image": image,
            "docker_network": network,
            "robot_count": robot_count,
            "robots_ok": robots_ok,
            "topics": topics,
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "netem_qdisc": netem_qdisc,
            "scheduler_window_ms": scheduler_window_ms,
            "post_satisfaction_ms": post_satisfaction_ms,
            "scheduler_robot_evidence_ok": scheduler_robot_evidence_ok,
            "total_nack_retransmissions": sum(
                int(row["publisher"].get("nack_retransmissions", 0))
                for row in robot_rows
            ),
            "publisher_returncodes": publisher_returncodes,
            "subscriber_returncodes": subscriber_returncodes,
            "router_returncode": router_returncode,
            "robots": robot_rows,
            "router": router_result,
            "router_logs": router_logs,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "image": image,
            "docker_network": network,
            "robot_count": robot_count,
            "robots_ok": 0,
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run(
            [
                "docker",
                "rm",
                "-f",
                router_name,
                *subscriber_names,
                *publisher_names,
            ],
            check=False,
        )
        run(["docker", "network", "rm", network], check=False)
        docker_shell(
            root,
            image,
            f"rm -rf {build_base} {install_base} {log_base}",
            check=False,
        )


if __name__ == "__main__":
    raise SystemExit(main())
