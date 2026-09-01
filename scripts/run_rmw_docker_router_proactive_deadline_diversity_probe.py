"""Validate proactive dual-path protection for deadline-critical ROS 2 data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

try:
    from scripts.run_rmw_docker_router_scheduled_reliability_probe import (
        DEFAULT_IMAGE,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_scheduled_reliability_probe import (
        DEFAULT_IMAGE,
        netem_config_for_profile,
        netem_post_satisfaction_ms,
    )


SCHEMA_VERSION = "fleetrmw.rmw_router_proactive_deadline_diversity_probe.v1"
DEFAULT_TOPIC = "/fleetqox/proactive_deadline_diversity/control"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--deadline-ms", type=int, default=100)
    parser.add_argument("--primary-profile", default="roaming")
    parser.add_argument("--backup-profile", default="wifi")
    parser.add_argument("--loss-percent", type=float, default=0.02)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_router_proactive_deadline_diversity_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        topic=args.topic,
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
        print("fleetrmw-router-proactive-deadline-diversity-probe")
        print(f"  status: {summary['status']}")
        print(f"  on_time_sequences: {summary.get('on_time_sequences')}")
        print(
            "  adaptive_redundant_frames: "
            f"{summary.get('publisher', {}).get('adaptive_redundant_frames')}"
        )
        print(
            "  nack_retransmissions: "
            f"{summary.get('publisher', {}).get('nack_retransmissions')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    topic: str,
    deadline_ms: int,
    primary_profile: str,
    backup_profile: str,
    loss_percent: float,
) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-proactive-diversity-net-{suffix}"
    primary_name = f"fleetrmw-proactive-diversity-primary-{suffix}"
    backup_name = f"fleetrmw-proactive-diversity-backup-{suffix}"
    subscriber_name = f"fleetrmw-proactive-diversity-sub-{suffix}"
    build_base = "/work/.tmp_fleetrmw_proactive_diversity_build"
    install_base = "/work/.tmp_fleetrmw_proactive_diversity_install"
    log_base = "/work/.tmp_fleetrmw_proactive_diversity_log"
    telemetry_rel = f".tmp_fleetrmw_proactive_diversity_{suffix}.jsonl"
    telemetry_host = root / telemetry_rel
    telemetry_container = f"/work/{telemetry_rel}"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    primary = netem_config_for_profile(
        primary_profile,
        netem_loss_percent=loss_percent,
    )
    backup = netem_config_for_profile(
        backup_profile,
        netem_loss_percent=loss_percent,
    )

    try:
        telemetry_host.unlink(missing_ok=True)
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
            command=router_command(
                install_base=install_base,
                router_binary=router_binary,
                port=49100,
                netem=primary,
                drop_sequence_two=True,
                expected_ack_nack_forwarded=0,
            ),
        )
        start_router(
            root=root,
            image=image,
            name=backup_name,
            network=network,
            command=router_command(
                install_base=install_base,
                router_binary=router_binary,
                port=49101,
                netem=backup,
                drop_sequence_two=False,
                expected_ack_nack_forwarded=3,
            ),
        )
        time.sleep(2.0)
        primary_qdisc = qdisc(primary_name)
        backup_qdisc = qdisc(backup_name)
        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                "FLEETQOX_RMW_ROBOT_ID=robot_0000 "
                "FLEETQOX_RMW_BIND=0.0.0.0:49102 "
                f"FLEETQOX_RMW_PEERS={primary_name}:49100,{backup_name}:49101 "
                f"{endpoint_binary} --mode subscriber --topic {topic} "
                "--timeout-ms 10000 --min-ack-nack-sent 3 "
                f"--deadline-ms {deadline_ms} --subscriber-deadline-ms {deadline_ms} "
                f"--subscriber-telemetry-file {telemetry_container}"
            ),
        )
        time.sleep(0.8)
        publisher = run([
            "docker", "run", "--rm",
            "--network", network,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc",
            (
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                "FLEETQOX_RMW_ROBOT_ID=robot_0000 "
                "FLEETQOX_RMW_BIND=0.0.0.0:0 "
                "FLEETQOX_RMW_PEER_POLICY=adaptive_qos "
                f"FLEETQOX_RMW_REDUNDANT_DEADLINE_MS={deadline_ms} "
                f"FLEETQOX_RMW_PEERS={primary_name}:49100,{backup_name}:49101 "
                f"{endpoint_binary} --mode publisher --topic {topic} "
                "--hold-ms 6500 --min-retransmissions 0 --min-ack-nack-received 3 "
                f"--deadline-ms {deadline_ms}"
            ),
        ], check=False)
        subscriber_rc = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        primary_rc = int(run(["docker", "wait", primary_name]).stdout.strip())
        backup_rc = int(run(["docker", "wait", backup_name]).stdout.strip())
        subscriber_log = run(["docker", "logs", subscriber_name], check=False).stdout.strip()
        primary_log = run(["docker", "logs", primary_name], check=False).stdout.strip()
        backup_log = run(["docker", "logs", backup_name], check=False).stdout.strip()
        publisher_result = parse_last_json(publisher.stdout)
        subscriber_result = parse_last_json(subscriber_log)
        primary_result = parse_last_json(primary_log)
        backup_result = parse_last_json(backup_log)
        telemetry = parse_json_lines(
            telemetry_host.read_text(encoding="utf-8") if telemetry_host.exists() else ""
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
        payloads = set(subscriber_result.get("payloads", []))
        primary_fault_observed = (
            primary_result.get("test_dropped_frames", 0) >= 1
            or primary_result.get("received_frames", 0) < 3
            or primary_rc != 0
        )
        status = (
            publisher.returncode == 0
            and subscriber_rc == 0
            and backup_rc == 0
            and publisher_result.get("status") == "ok"
            and subscriber_result.get("status") == "ok"
            and backup_result.get("status") == "ok"
            and publisher_result.get("peer_policy") == "adaptive_qos"
            and publisher_result.get("adaptive_redundant_frames", 0) >= 3
            and publisher_result.get("nack_retransmissions", 0) == 0
            and primary_fault_observed
            and backup_result.get("forwarded_frames", 0) >= 3
            and on_time_sequences == [1, 2, 3]
            and {"one", "two", "three"}.issubset(payloads)
            and "netem" in primary_qdisc
            and "netem" in backup_qdisc
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "topic": topic,
            "deadline_ms": deadline_ms,
            "primary_profile": primary_profile,
            "backup_profile": backup_profile,
            "loss_percent": loss_percent,
            "primary_netem": primary,
            "backup_netem": backup,
            "primary_qdisc": primary_qdisc,
            "backup_qdisc": backup_qdisc,
            "on_time_sequences": on_time_sequences,
            "primary_fault_observed": primary_fault_observed,
            "delivery_telemetry": telemetry,
            "publisher_returncode": publisher.returncode,
            "subscriber_returncode": subscriber_rc,
            "primary_router_returncode": primary_rc,
            "backup_router_returncode": backup_rc,
            "publisher": publisher_result,
            "subscriber": subscriber_result,
            "primary_router": primary_result,
            "backup_router": backup_result,
            "publisher_stderr": publisher.stderr,
            "subscriber_logs": subscriber_log,
            "primary_router_logs": primary_log,
            "backup_router_logs": backup_log,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run(
            ["docker", "rm", "-f", primary_name, backup_name, subscriber_name],
            check=False,
        )
        run(["docker", "network", "rm", network], check=False)
        telemetry_host.unlink(missing_ok=True)
        docker_shell(
            root,
            image,
            f"rm -rf {build_base} {install_base} {log_base}",
            check=False,
        )


def router_command(
    *,
    install_base: str,
    router_binary: str,
    port: int,
    netem: dict[str, float],
    drop_sequence_two: bool,
    expected_ack_nack_forwarded: int,
) -> str:
    dwell_ms = netem_post_satisfaction_ms(netem, enabled=True)
    drop_args = "--drop-source-sequences 2 " if drop_sequence_two else ""
    return (
        f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
        "tc qdisc replace dev eth0 root netem "
        f"delay {netem['delay_ms']:g}ms {netem['jitter_ms']:g}ms "
        f"loss {netem['loss_percent']:g}% rate {netem['rate_mbit']:g}mbit && "
        f"{router_binary} --bind 0.0.0.0:{port} "
        "--expected-frames 3 --expected-ack-nack-frames 3 "
        f"--expected-ack-nack-forwarded {expected_ack_nack_forwarded} "
        "--expected-route-advertisements 1 --expected-graph-advertisements 2 "
        f"{drop_args}--post-satisfaction-ms {dwell_ms} --timeout-ms 12000"
    )


def qdisc(container: str) -> str:
    return run(
        ["docker", "exec", container, "tc", "qdisc", "show", "dev", "eth0"],
        check=False,
    ).stdout.strip()


def parse_json_lines(output: str) -> list[dict[str, Any]]:
    rows = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            rows.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue
    return rows


def parse_last_json(output: str) -> dict[str, Any]:
    rows = parse_json_lines(output)
    return rows[-1] if rows else {"status": "missing", "raw": output}


def run(
    cmd: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def docker_shell(
    root: Path,
    image: str,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run([
        "docker", "run", "--rm",
        "--entrypoint", "bash",
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "-lc", command,
    ], check=check)


def start_router(
    *,
    root: Path,
    image: str,
    name: str,
    network: str,
    command: str,
) -> str:
    return start_container(
        root=root,
        image=image,
        name=name,
        network=network,
        command=command,
        extra_args=("--cap-add", "NET_ADMIN"),
    )


def start_container(
    *,
    root: Path,
    image: str,
    name: str,
    network: str,
    command: str,
    extra_args: tuple[str, ...] = (),
) -> str:
    result = run([
        "docker", "run", "-d",
        "--name", name,
        "--network", network,
        *extra_args,
        "--entrypoint", "bash",
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "-lc", command,
    ])
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
