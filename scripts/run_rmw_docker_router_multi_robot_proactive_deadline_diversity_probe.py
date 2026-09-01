"""Run concurrent proactive deadline diversity for a ROS 2 robot fleet."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import time
from typing import Any

try:
    from scripts.run_rmw_docker_router_proactive_deadline_diversity_probe import (
        DEFAULT_IMAGE,
        docker_shell,
        parse_json_lines,
        parse_last_json,
        qdisc,
        router_command,
        run,
        start_container,
        start_router,
    )
    from scripts.run_rmw_docker_router_scheduled_reliability_probe import (
        netem_config_for_profile,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_proactive_deadline_diversity_probe import (
        DEFAULT_IMAGE,
        docker_shell,
        parse_json_lines,
        parse_last_json,
        qdisc,
        router_command,
        run,
        start_container,
        start_router,
    )
    from run_rmw_docker_router_scheduled_reliability_probe import (
        netem_config_for_profile,
    )


SCHEMA_VERSION = (
    "fleetrmw.rmw_router_multi_robot_proactive_deadline_diversity_probe.v1"
)
DEFAULT_TOPIC_PREFIX = "/fleetqox/multi_robot_proactive_deadline"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-count", type=int, default=4)
    parser.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX)
    parser.add_argument("--deadline-ms", type=int, default=100)
    parser.add_argument("--primary-profile", default="roaming")
    parser.add_argument("--backup-profile", default="wifi")
    parser.add_argument("--loss-percent", type=float, default=0.02)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_router_multi_robot_proactive_deadline_diversity_probe_summary.json"
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
        deadline_ms=max(args.deadline_ms, 1),
        primary_profile=args.primary_profile,
        backup_profile=args.backup_profile,
        loss_percent=max(args.loss_percent, 0.0),
    )
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-multi-robot-proactive-deadline-diversity-probe")
        print(f"  status: {summary['status']}")
        print(f"  robots_ok: {summary.get('robots_ok')}/{summary.get('robot_count')}")
        print(f"  max_latency_ms: {summary.get('max_latency_ms')}")
        print(f"  deadline_success_jain_index: {summary.get('deadline_success_jain_index')}")
        print(f"  total_nack_retransmissions: {summary.get('total_nack_retransmissions')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    robot_count: int,
    topic_prefix: str,
    deadline_ms: int,
    primary_profile: str,
    backup_profile: str,
    loss_percent: float,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-multi-proactive-net-{suffix}"
    primary_name = f"fleetrmw-multi-proactive-primary-{suffix}"
    backup_name = f"fleetrmw-multi-proactive-backup-{suffix}"
    subscriber_names = [
        f"fleetrmw-multi-proactive-sub-{suffix}-{index}"
        for index in range(robot_count)
    ]
    publisher_names = [
        f"fleetrmw-multi-proactive-pub-{suffix}-{index}"
        for index in range(robot_count)
    ]
    build_base = "/work/.tmp_fleetrmw_multi_proactive_build"
    install_base = "/work/.tmp_fleetrmw_multi_proactive_install"
    log_base = "/work/.tmp_fleetrmw_multi_proactive_log"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    primary_port = 49250
    backup_port = 49251
    expected_frames = robot_count * 3
    expected_ack_nack = robot_count * 3
    robot_ids = [f"robot_{index:04d}" for index in range(robot_count)]
    topics = [
        f"{topic_prefix.rstrip('/')}/robot-{index:04d}/control"
        for index in range(robot_count)
    ]
    telemetry_paths = [
        root / f".tmp_fleetrmw_multi_proactive_{suffix}_{index}.jsonl"
        for index in range(robot_count)
    ]
    primary = netem_config_for_profile(
        primary_profile,
        netem_loss_percent=loss_percent,
    )
    backup = netem_config_for_profile(
        backup_profile,
        netem_loss_percent=loss_percent,
    )

    try:
        for path in telemetry_paths:
            path.unlink(missing_ok=True)
        run(["docker", "network", "create", network])
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
        start_router(
            root=root,
            image=image,
            name=primary_name,
            network=network,
            command=fleet_router_command(
                install_base=install_base,
                router_binary=router_binary,
                port=primary_port,
                netem=primary,
                expected_frames=expected_frames,
                expected_ack_nack=expected_ack_nack,
                robot_count=robot_count,
                drop_sequence_two=True,
                expected_ack_nack_forwarded=0,
            ),
        )
        start_router(
            root=root,
            image=image,
            name=backup_name,
            network=network,
            command=fleet_router_command(
                install_base=install_base,
                router_binary=router_binary,
                port=backup_port,
                netem=backup,
                expected_frames=expected_frames,
                expected_ack_nack=expected_ack_nack,
                robot_count=robot_count,
                drop_sequence_two=False,
                expected_ack_nack_forwarded=expected_ack_nack,
            ),
        )
        time.sleep(2.0)
        primary_qdisc = qdisc(primary_name)
        backup_qdisc = qdisc(backup_name)

        for index, (name, robot_id, topic, telemetry_path) in enumerate(
            zip(subscriber_names, robot_ids, topics, telemetry_paths)
        ):
            start_container(
                root=root,
                image=image,
                name=name,
                network=network,
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{49300 + index} "
                    f"FLEETQOX_RMW_PEERS={primary_name}:{primary_port},"
                    f"{backup_name}:{backup_port} "
                    f"{endpoint_binary} --mode subscriber "
                    f"--topic {shlex.quote(topic)} --timeout-ms 14000 "
                    "--min-ack-nack-sent 3 "
                    f"--deadline-ms {deadline_ms} --subscriber-deadline-ms {deadline_ms} "
                    f"--subscriber-telemetry-file /work/{telemetry_path.name}"
                ),
            )
        time.sleep(1.0)

        for name, robot_id, topic in zip(publisher_names, robot_ids, topics):
            start_container(
                root=root,
                image=image,
                name=name,
                network=network,
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    "FLEETQOX_RMW_BIND=0.0.0.0:0 "
                    "FLEETQOX_RMW_PEER_POLICY=adaptive_qos "
                    f"FLEETQOX_RMW_REDUNDANT_DEADLINE_MS={deadline_ms} "
                    f"FLEETQOX_RMW_PEERS={primary_name}:{primary_port},"
                    f"{backup_name}:{backup_port} "
                    f"{endpoint_binary} --mode publisher "
                    f"--topic {shlex.quote(topic)} --hold-ms 10000 "
                    "--min-retransmissions 0 --min-ack-nack-received 3 "
                    f"--deadline-ms {deadline_ms}"
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
        primary_rc = int(run(["docker", "wait", primary_name]).stdout.strip())
        backup_rc = int(run(["docker", "wait", backup_name]).stdout.strip())
        publisher_logs = [
            run(["docker", "logs", name], check=False).stdout.strip()
            for name in publisher_names
        ]
        subscriber_logs = [
            run(["docker", "logs", name], check=False).stdout.strip()
            for name in subscriber_names
        ]
        primary_log = run(["docker", "logs", primary_name], check=False).stdout.strip()
        backup_log = run(["docker", "logs", backup_name], check=False).stdout.strip()
        publishers = [parse_last_json(log) for log in publisher_logs]
        subscribers = [parse_last_json(log) for log in subscriber_logs]
        primary_result = parse_last_json(primary_log)
        backup_result = parse_last_json(backup_log)

        robot_rows = []
        for index, (robot_id, topic, telemetry_path, publisher, subscriber) in enumerate(
            zip(robot_ids, topics, telemetry_paths, publishers, subscribers)
        ):
            telemetry = parse_json_lines(
                telemetry_path.read_text(encoding="utf-8")
                if telemetry_path.exists() else ""
            )
            sequence_rows = {
                int(row.get("source_sequence_number", 0)): row
                for row in telemetry
                if int(row.get("source_sequence_number", 0)) in (1, 2, 3)
            }
            on_time_sequences = sorted(
                sequence
                for sequence, row in sequence_rows.items()
                if row.get("deadline_missed") is False
                and float(row.get("latency_ms", deadline_ms + 1)) <= deadline_ms
            )
            max_latency_ms = max(
                (float(row.get("latency_ms", 0.0)) for row in telemetry),
                default=0.0,
            )
            payloads = set(subscriber.get("payloads", []))
            row_ok = (
                publisher_returncodes[index] == 0
                and subscriber_returncodes[index] == 0
                and publisher.get("status") == "ok"
                and subscriber.get("status") == "ok"
                and publisher.get("peer_policy") == "adaptive_qos"
                and int(publisher.get("adaptive_redundant_frames", 0)) >= 3
                and int(publisher.get("nack_retransmissions", 0)) == 0
                and on_time_sequences == [1, 2, 3]
                and {"one", "two", "three"}.issubset(payloads)
            )
            robot_rows.append({
                "robot_id": robot_id,
                "topic": topic,
                "status": "ok" if row_ok else "failed",
                "on_time_sequences": on_time_sequences,
                "max_latency_ms": max_latency_ms,
                "publisher_returncode": publisher_returncodes[index],
                "subscriber_returncode": subscriber_returncodes[index],
                "publisher": publisher,
                "subscriber": subscriber,
                "delivery_telemetry": telemetry,
            })

        robots_ok = sum(row["status"] == "ok" for row in robot_rows)
        success_ratios = [
            len(row["on_time_sequences"]) / 3.0
            for row in robot_rows
        ]
        jain_index = jain_fairness(success_ratios)
        total_redundant_frames = sum(
            int(row["publisher"].get("adaptive_redundant_frames", 0))
            for row in robot_rows
        )
        total_retransmissions = sum(
            int(row["publisher"].get("nack_retransmissions", 0))
            for row in robot_rows
        )
        primary_fault_observed = (
            int(primary_result.get("test_dropped_frames", 0)) >= robot_count
            or int(primary_result.get("received_frames", 0)) < expected_frames
            or primary_rc != 0
        )
        status = (
            robots_ok == robot_count
            and backup_rc == 0
            and backup_result.get("status") == "ok"
            and int(backup_result.get("forwarded_frames", 0)) >= expected_frames
            and primary_fault_observed
            and total_redundant_frames >= expected_frames
            and total_retransmissions == 0
            and jain_index >= 0.999
            and "netem" in primary_qdisc
            and "netem" in backup_qdisc
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "image": image,
            "robot_count": robot_count,
            "robots_ok": robots_ok,
            "deadline_ms": deadline_ms,
            "primary_profile": primary_profile,
            "backup_profile": backup_profile,
            "loss_percent": loss_percent,
            "primary_qdisc": primary_qdisc,
            "backup_qdisc": backup_qdisc,
            "primary_fault_observed": primary_fault_observed,
            "deadline_success_jain_index": jain_index,
            "max_latency_ms": max(
                (float(row["max_latency_ms"]) for row in robot_rows),
                default=0.0,
            ),
            "protected_source_frames": total_redundant_frames,
            "proactive_path_transmissions": total_redundant_frames * 2,
            "path_transmission_overhead_ratio": 2.0,
            "total_nack_retransmissions": total_retransmissions,
            "publisher_returncodes": publisher_returncodes,
            "subscriber_returncodes": subscriber_returncodes,
            "primary_router_returncode": primary_rc,
            "backup_router_returncode": backup_rc,
            "robots": robot_rows,
            "primary_router": primary_result,
            "backup_router": backup_result,
            "primary_router_logs": primary_log,
            "backup_router_logs": backup_log,
        }
    finally:
        run([
            "docker", "rm", "-f", primary_name, backup_name,
            *subscriber_names, *publisher_names,
        ], check=False)
        run(["docker", "network", "rm", network], check=False)
        for path in telemetry_paths:
            path.unlink(missing_ok=True)
        docker_shell(
            root,
            image,
            f"rm -rf {build_base} {install_base} {log_base}",
            check=False,
        )


def fleet_router_command(
    *,
    install_base: str,
    router_binary: str,
    port: int,
    netem: dict[str, float],
    expected_frames: int,
    expected_ack_nack: int,
    robot_count: int,
    drop_sequence_two: bool,
    expected_ack_nack_forwarded: int,
    drop_topic_prefix: str = "",
) -> str:
    command = router_command(
        install_base=install_base,
        router_binary=router_binary,
        port=port,
        netem=netem,
        drop_sequence_two=drop_sequence_two,
        expected_ack_nack_forwarded=expected_ack_nack_forwarded,
    )
    command = command.replace(
        "--expected-frames 3 --expected-ack-nack-frames 3 ",
        f"--expected-frames {expected_frames} "
        f"--expected-ack-nack-frames {expected_ack_nack} ",
    ).replace(
        "--expected-route-advertisements 1 --expected-graph-advertisements 2 ",
        f"--expected-route-advertisements {robot_count} "
        f"--expected-graph-advertisements {robot_count * 2} ",
    ).replace("--timeout-ms 12000", "--timeout-ms 18000")
    if drop_topic_prefix:
        command += f" --drop-topic-prefix {shlex.quote(drop_topic_prefix)}"
    return command


def jain_fairness(values: list[float]) -> float:
    if not values:
        return 0.0
    denominator = len(values) * sum(value * value for value in values)
    if denominator <= 0.0:
        return 0.0
    total = sum(values)
    return total * total / denominator


if __name__ == "__main__":
    raise SystemExit(main())
