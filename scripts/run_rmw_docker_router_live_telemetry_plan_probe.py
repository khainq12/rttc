"""Run a Docker router telemetry closed-loop fleet-plan probe."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.fleet_optimizer import FleetFlowDemand, FleetOptimizerConfig, RobotQoEState
from fleetqox.live_path_controller import (
    LivePathPlanController,
    LivePathPlanControllerConfig,
    ROUTER_TELEMETRY_SCHEMA_VERSION,
)
from fleetqox.model import FlowClass
from fleetqox.online_fleet_planner import FleetTopicDemand, PathObservation


SCHEMA_VERSION = "fleetrmw.rmw_router_live_telemetry_plan_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
DEFAULT_TOPIC = "/fleetqox/router_live_telemetry_plan_probe"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_live_telemetry_plan_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe(root=ROOT, image=args.image, topic=args.topic)
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-live-telemetry-plan-probe")
        print(f"  status: {summary['status']}")
        print(f"  controller_records: {summary.get('controller', {}).get('record_count')}")
        print(f"  final_plan: {summary.get('controller_final_path_plan')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(*, root: Path, image: str, topic: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-live-plan-net-{suffix}"
    primary_router_name = f"fleetrmw-live-plan-primary-{suffix}"
    backup_router_name = f"fleetrmw-live-plan-backup-{suffix}"
    subscriber_name = f"fleetrmw-live-plan-sub-{suffix}"
    build_base = "/work/.tmp_fleetrmw_live_plan_build"
    install_base = "/work/.tmp_fleetrmw_live_plan_install"
    log_base = "/work/.tmp_fleetrmw_live_plan_log"
    plan_dir = root / f".tmp_fleetrmw_live_plan_{suffix}"
    plan_file_host = plan_dir / "path_plan.txt"
    primary_telemetry_host = plan_dir / "primary_router_telemetry.jsonl"
    backup_telemetry_host = plan_dir / "backup_router_telemetry.jsonl"
    subscriber_telemetry_host = plan_dir / "subscriber_delivery_telemetry.jsonl"
    plan_file_container = f"/work/{plan_file_host.relative_to(root)}"
    primary_telemetry_container = f"/work/{primary_telemetry_host.relative_to(root)}"
    backup_telemetry_container = f"/work/{backup_telemetry_host.relative_to(root)}"
    subscriber_telemetry_container = f"/work/{subscriber_telemetry_host.relative_to(root)}"
    endpoint_binary = f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_reliable_interprocess_probe"
    router_binary = f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_router_probe"
    controller: LivePathPlanController | None = None
    controller_thread: threading.Thread | None = None
    stop_controller = threading.Event()

    try:
        plan_dir.mkdir(parents=True, exist_ok=True)
        run(["docker", "network", "create", network])
        subnet_result = run(
            ["docker", "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network]
        )
        subnet = ipaddress.ip_network(subnet_result.stdout.strip(), strict=False)
        primary_router_ip = str(subnet.network_address + 10)
        backup_router_ip = str(subnet.network_address + 11)
        subscriber_ip = str(subnet.network_address + 12)
        publisher_ip = str(subnet.network_address + 13)
        graph_peers = f"{subscriber_ip}:48412,{publisher_ip}:48413"
        # ack/nack forwarding targets peer_addresses (from --peers) plus any
        # dynamically learned publisher route -- --graph-peers only affects
        # graph advertisement relay, so the publisher's address must also be
        # a static peer for both routers to reliably forward acks back to it.
        static_peers = f"{publisher_ip}:48413"
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
        controller = live_controller_for_probe(
            topic=topic,
            plan_file=plan_file_host,
            telemetry_files=(primary_telemetry_host, backup_telemetry_host),
            subscriber_telemetry_file=subscriber_telemetry_host,
        )
        initial_plan = controller.poll_once().path_plan_env
        controller_thread = threading.Thread(
            target=run_controller_loop,
            args=(controller, stop_controller),
            daemon=True,
        )
        controller_thread.start()
        start_container(
            root=root,
            image=image,
            name=primary_router_name,
            network=network,
            ip_address=primary_router_ip,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                f"{router_binary} --bind 0.0.0.0:48410 "
                f"--graph-peers {graph_peers} --peers {static_peers} "
                "--path-id primary_wifi "
                f"--telemetry-file {primary_telemetry_container} "
                "--telemetry-latency-ms 58 --telemetry-jitter-ms 22 "
                "--telemetry-loss 0.18 --telemetry-nack-rate 0.16 "
                "--telemetry-deadline-miss-ratio 0.24 --telemetry-capacity-bytes 200000 "
                # The subscriber's read loop exits as soon as the original
                # 3-item payload set ("one,two,three") is complete, so it
                # never acks the later redundant-plan-triggering sends --
                # ack_nack_frames caps at ~3 regardless of data frame count.
                "--expected-frames 6 --expected-ack-nack-frames 3 "
                "--expected-route-advertisements 1 --expected-graph-advertisements 2 "
                "--timeout-ms 17000"
            ),
        )
        start_container(
            root=root,
            image=image,
            name=backup_router_name,
            network=network,
            ip_address=backup_router_ip,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                f"{router_binary} --bind 0.0.0.0:48411 "
                f"--graph-peers {graph_peers} --peers {static_peers} "
                "--path-id backup_5g "
                f"--telemetry-file {backup_telemetry_container} "
                "--telemetry-latency-ms 24 --telemetry-jitter-ms 5 "
                "--telemetry-loss 0.035 --telemetry-nack-rate 0.025 "
                "--telemetry-deadline-miss-ratio 0.04 --telemetry-capacity-bytes 200000 "
                "--expected-frames 3 --expected-ack-nack-frames 3 "
                "--expected-route-advertisements 1 --expected-graph-advertisements 2 "
                "--timeout-ms 17000"
            ),
        )
        time.sleep(0.6)
        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            ip_address=subscriber_ip,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                "FLEETQOX_RMW_BIND=0.0.0.0:48412 "
                f"FLEETQOX_RMW_PEERS={primary_router_ip}:48410,{backup_router_ip}:48411 "
                f"{endpoint_binary} --mode subscriber --topic {topic} "
                f"--subscriber-telemetry-file {subscriber_telemetry_container} "
                "--robot-id robot_0000 --subscriber-deadline-ms 30 "
                "--timeout-ms 16000"
            ),
        )
        time.sleep(0.8)
        # See run_rmw_docker_router_reliability_probe.py for why
        # --pre-publish-wait-ms is needed: any graph advertisement sent
        # before the publisher's own socket is bound is lost (UDP has no
        # receiver queue), so this must delay the first publish() rather
        # than the orchestrator sleeping earlier.
        publisher = run(
            [
                "docker", "run", "--rm",
                "--network", network,
                "--ip", publisher_ip,
                "-v", f"{root}:/work",
                "-w", "/work",
                image,
                "bash", "-lc",
                (
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    "FLEETQOX_RMW_BIND=0.0.0.0:48413 "
                    "FLEETQOX_RMW_PEER_POLICY=fleet_plan "
                    f"FLEETQOX_RMW_FLEET_PATH_PLAN_FILE='{plan_file_container}' "
                    f"FLEETQOX_RMW_PEERS=primary_wifi={primary_router_ip}:48410,backup_5g={backup_router_ip}:48411 "
                    f"{endpoint_binary} --mode publisher --topic {topic} "
                    "--pre-publish-wait-ms 800 "
                    # router telemetry (and thus the live plan's convergence
                    # to redundant primary+backup delivery) only advances
                    # once per data frame actually forwarded, so simply
                    # waiting longer between/after sends never helps -- only
                    # a later send can observe an updated plan. Publish more
                    # messages so a later send has a chance to land after
                    # convergence.
                    "--payload-sequence one,two,three,four,five,six "
                    "--publish-interval-ms 800 --hold-ms 8000 "
                    "--min-retransmissions 0 --min-ack-nack-received 3"
                ),
            ],
        )
        subscriber_returncode = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        primary_router_returncode = int(run(["docker", "wait", primary_router_name]).stdout.strip())
        backup_router_returncode = int(run(["docker", "wait", backup_router_name]).stdout.strip())
        if controller is not None:
            controller.poll_once()
        stop_controller.set()
        if controller_thread is not None:
            controller_thread.join(timeout=2.0)
        subscriber_log = run(["docker", "logs", subscriber_name]).stdout.strip()
        primary_router_log = run(["docker", "logs", primary_router_name]).stdout.strip()
        backup_router_log = run(["docker", "logs", backup_router_name]).stdout.strip()
        publisher_result = parse_last_json(publisher.stdout)
        subscriber_result = parse_last_json(subscriber_log)
        primary_router_result = parse_last_json(primary_router_log)
        backup_router_result = parse_last_json(backup_router_log)
        subscriber_payloads = set(subscriber_result.get("payloads", []))
        controller_summary = controller.summary() if controller is not None else {}
        controller_final_path_plan = str(
            (controller_summary.get("last_plan") or {}).get("path_plan_env", "")
            if isinstance(controller_summary.get("last_plan"), dict)
            else ""
        )
        status = (
            publisher.returncode == 0 and
            subscriber_returncode == 0 and
            primary_router_returncode == 0 and
            backup_router_returncode == 0 and
            publisher_result.get("status") == "ok" and
            subscriber_result.get("status") == "ok" and
            primary_router_result.get("status") == "ok" and
            backup_router_result.get("status") == "ok" and
            initial_plan == f"{topic}=primary_wifi" and
            controller_summary.get("record_count", 0) >= 1 and
            controller_summary.get("subscriber_record_count", 0) >= 3 and
            controller_final_path_plan == f"{topic}=backup_5g+primary_wifi" and
            publisher_result.get("fleet_plan_frames", 0) >= 3 and
            publisher_result.get("fleet_plan_redundant_frames", 0) >= 2 and
            publisher_result.get("fleet_plan_selected_path_count", 0) >= 5 and
            publisher_result.get("fleet_plan_last_paths") == "backup_5g,primary_wifi" and
            primary_router_result.get("received_frames", 0) >= 3 and
            backup_router_result.get("received_frames", 0) >= 2 and
            {"one", "two", "three"}.issubset(subscriber_payloads)
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "router_telemetry_schema_version": ROUTER_TELEMETRY_SCHEMA_VERSION,
            "docker_network": network,
            "topic": topic,
            "initial_path_plan": initial_plan,
            "controller_final_path_plan": controller_final_path_plan,
            "path_plan_file": str(plan_file_host),
            "primary_telemetry_file": str(primary_telemetry_host),
            "backup_telemetry_file": str(backup_telemetry_host),
            "subscriber_telemetry_file": str(subscriber_telemetry_host),
            "controller": controller_summary,
            "publisher_returncode": publisher.returncode,
            "subscriber_returncode": subscriber_returncode,
            "primary_router_returncode": primary_router_returncode,
            "backup_router_returncode": backup_router_returncode,
            "publisher": publisher_result,
            "subscriber": subscriber_result,
            "primary_router": primary_router_result,
            "backup_router": backup_router_result,
            "primary_telemetry": read_jsonl(primary_telemetry_host),
            "backup_telemetry": read_jsonl(backup_telemetry_host),
            "subscriber_telemetry": read_jsonl(subscriber_telemetry_host),
            "publisher_stdout": publisher.stdout,
            "publisher_stderr": publisher.stderr,
            "subscriber_logs": subscriber_log,
            "primary_router_logs": primary_router_log,
            "backup_router_logs": backup_router_log,
        }
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
        stop_controller.set()
        if controller_thread is not None and controller_thread.is_alive():
            controller_thread.join(timeout=2.0)
        run(["docker", "rm", "-f", primary_router_name, backup_router_name, subscriber_name], check=False)
        run(["docker", "network", "rm", network], check=False)
        docker_shell(root, image, f"rm -rf {build_base} {install_base} {log_base}", check=False)
        shutil.rmtree(plan_dir, ignore_errors=True)


def live_controller_for_probe(
    *,
    topic: str,
    plan_file: Path,
    telemetry_files: tuple[Path, Path],
    subscriber_telemetry_file: Path,
) -> LivePathPlanController:
    demand = FleetTopicDemand(
        topic,
        FleetFlowDemand(
            flow_id="robot_0000/cmd_vel",
            robot_id="robot_0000",
            flow_class=FlowClass.CONTROL,
            deadline_ms=30.0,
            payload_bytes=680,
            rate_hz=20.0,
            criticality=0.95,
            qoe_weight=0.10,
            age_ms=12.0,
            lifespan_ms=90.0,
        ),
    )
    return LivePathPlanController(
        LivePathPlanControllerConfig(
            plan_file=plan_file,
            telemetry_files=telemetry_files,
            subscriber_telemetry_files=(subscriber_telemetry_file,),
            demands=(demand,),
            seed_observations=(
                PathObservation(
                    "primary_wifi",
                    latency_ms=10.0,
                    jitter_ms=1.0,
                    loss=0.01,
                    nack_rate=0.01,
                    deadline_miss_ratio=0.0,
                    bandwidth_utilization=0.10,
                ),
                PathObservation(
                    "backup_5g",
                    latency_ms=24.0,
                    jitter_ms=5.0,
                    loss=0.03,
                    nack_rate=0.03,
                    deadline_miss_ratio=0.05,
                    bandwidth_utilization=0.42,
                ),
            ),
            robot_states=(
                RobotQoEState(
                    "robot_0000",
                    control_delivery_ratio=0.90,
                    deadline_miss_ratio=0.18,
                    qoe_score=0.78,
                ),
            ),
            optimizer=FleetOptimizerConfig(
                capacity_bytes_per_tick=4096,
                redundant_deadline_ms=35.0,
                redundancy_risk_threshold=1.0,
            ),
            telemetry_alpha=1.0,
            min_dwell_ticks=0,
            switch_score_margin=0.20,
        )
    )


def run_controller_loop(controller: LivePathPlanController, stop: threading.Event) -> None:
    while not stop.wait(0.05):
        controller.poll_once()


def parse_last_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return {"status": "parse_failed", "raw": stripped}
    return {"status": "missing", "raw": output}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def docker_shell(
    root: Path,
    image: str,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run([
        "docker", "run", "--rm",
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "bash", "-lc", command,
    ], check=check)


def start_container(
    *, root: Path, image: str, name: str, network: str, ip_address: str, command: str
) -> str:
    result = run([
        "docker", "run", "-d",
        "--name", name,
        "--network", network,
        "--ip", ip_address,
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "bash", "-lc", command,
    ])
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
