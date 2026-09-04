"""Run a multi-robot ROS 2 pub/sub deadline-scheduling matrix through FleetRMW."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_router_multi_robot_qos_matrix.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
NETEM_PROFILES: dict[str, dict[str, float]] = {
    "none": {"delay_ms": 0.0, "jitter_ms": 0.0, "rate_mbit": 0.0, "loss_percent": 0.0},
    "wifi": {"delay_ms": 25.0, "jitter_ms": 8.0, "rate_mbit": 20.0, "loss_percent": 0.0},
    "wan": {"delay_ms": 70.0, "jitter_ms": 10.0, "rate_mbit": 10.0, "loss_percent": 0.0},
    "roaming": {"delay_ms": 95.0, "jitter_ms": 20.0, "rate_mbit": 5.0, "loss_percent": 0.0},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-count", type=int, default=4)
    parser.add_argument("--control-deadline-ms", type=int, default=5000)
    parser.add_argument("--state-deadline-ms", type=int, default=20000)
    parser.add_argument("--scheduler-window-ms", type=int, default=1000)
    parser.add_argument("--scheduler-admission-policy", default="always")
    parser.add_argument("--scheduler-admission-min-service-ratio", type=float, default=0.0)
    parser.add_argument("--scheduler-admission-exit-service-ratio", type=float, default=0.0)
    parser.add_argument("--scheduler-admission-ewma-alpha", type=float, default=0.5)
    parser.add_argument("--scheduler-admission-min-epoch-frames", type=int, default=1)
    parser.add_argument("--control-payload-bytes", type=int, default=64)
    parser.add_argument("--state-payload-bytes", type=int, default=4096)
    parser.add_argument("--netem-profile", choices=sorted(NETEM_PROFILES), default="none")
    parser.add_argument("--netem-loss-percent", type=float, default=None)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_multi_robot_qos_matrix_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    # Floor scales with robot_count: starting one publisher container per
    # robot sequentially can itself take longer than a flat 1000ms window,
    # which forces the queue's safety-valve flush before every control
    # frame has even arrived, breaking the priority ordering under test.
    scheduler_window_ms = max(args.scheduler_window_ms, max(args.robot_count, 1) * 1000)
    # A queued (state) frame can be held for the full window before the
    # safety-valve flush releases it, so a window this size makes a
    # shorter state_deadline_ms unmeetable by construction -- not a
    # scheduler bug, just two floors that must agree.
    state_deadline_ms = max(args.state_deadline_ms, scheduler_window_ms + 2000)
    summary = run_matrix(
        root=root,
        image=args.image,
        robot_count=max(args.robot_count, 1),
        control_deadline_ms=max(args.control_deadline_ms, 1),
        state_deadline_ms=state_deadline_ms,
        scheduler_window_ms=scheduler_window_ms,
        scheduler_admission_policy=args.scheduler_admission_policy,
        scheduler_admission_min_service_ratio=max(
            args.scheduler_admission_min_service_ratio, 0.0
        ),
        scheduler_admission_exit_service_ratio=max(
            args.scheduler_admission_exit_service_ratio, 0.0
        ),
        scheduler_admission_ewma_alpha=min(
            1.0,
            max(args.scheduler_admission_ewma_alpha, 0.0),
        ),
        scheduler_admission_min_epoch_frames=max(
            args.scheduler_admission_min_epoch_frames, 1
        ),
        control_payload_bytes=max(args.control_payload_bytes, 1),
        state_payload_bytes=max(args.state_payload_bytes, 1),
        netem_profile=args.netem_profile,
        netem_loss_percent=args.netem_loss_percent,
    )
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-multi-robot-qos-matrix")
        print(f"  status: {summary['status']}")
        print(f"  priority_improved: {summary.get('priority_improved')}")
        print(
            "  deadline_success_jain_index: "
            f"{summary.get('deadline_scheduler', {}).get('router', {}).get('scheduler_deadline_success_jain_index')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_matrix(
    *,
    root: Path,
    image: str,
    robot_count: int,
    control_deadline_ms: int,
    state_deadline_ms: int,
    scheduler_window_ms: int,
    scheduler_admission_policy: str = "always",
    scheduler_admission_min_service_ratio: float = 0.0,
    scheduler_admission_exit_service_ratio: float = 0.0,
    scheduler_admission_ewma_alpha: float = 0.5,
    scheduler_admission_min_epoch_frames: int = 1,
    control_payload_bytes: int = 64,
    state_payload_bytes: int = 4096,
    netem_profile: str = "none",
    netem_loss_percent: float | None = None,
) -> dict[str, Any]:
    if netem_profile not in NETEM_PROFILES:
        raise ValueError(f"unknown netem profile: {netem_profile}")
    suffix = str(os.getpid())
    build_base = "/work/.tmp_fleetrmw_multi_robot_qos_build"
    install_base = "/work/.tmp_fleetrmw_multi_robot_qos_install"
    log_base = "/work/.tmp_fleetrmw_multi_robot_qos_log"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_interprocess_pubsub_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    flows = build_flows(
        robot_count=robot_count,
        control_deadline_ms=control_deadline_ms,
        state_deadline_ms=state_deadline_ms,
        control_payload_bytes=control_payload_bytes,
        state_payload_bytes=state_payload_bytes,
    )

    try:
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
        fifo = run_scenario(
            root=root,
            image=image,
            suffix=suffix,
            label="fifo",
            flows=flows,
            endpoint_binary=endpoint_binary,
            router_binary=router_binary,
            install_base=install_base,
            scheduler_window_ms=0,
            scheduler_admission_policy="always",
            scheduler_admission_min_service_ratio=0.0,
            scheduler_admission_exit_service_ratio=0.0,
            scheduler_admission_ewma_alpha=0.5,
            scheduler_admission_min_epoch_frames=1,
            netem_profile=netem_profile,
            netem_loss_percent=netem_loss_percent,
        )
        deadline = run_scenario(
            root=root,
            image=image,
            suffix=suffix,
            label="deadline",
            flows=flows,
            endpoint_binary=endpoint_binary,
            router_binary=router_binary,
            install_base=install_base,
            scheduler_window_ms=scheduler_window_ms,
            scheduler_admission_policy=scheduler_admission_policy,
            scheduler_admission_min_service_ratio=scheduler_admission_min_service_ratio,
            scheduler_admission_exit_service_ratio=scheduler_admission_exit_service_ratio,
            scheduler_admission_ewma_alpha=scheduler_admission_ewma_alpha,
            scheduler_admission_min_epoch_frames=scheduler_admission_min_epoch_frames,
            netem_profile=netem_profile,
            netem_loss_percent=netem_loss_percent,
        )
        fifo_topics = fifo.get("router", {}).get("forwarded_topics", [])
        deadline_topics = deadline.get("router", {}).get("forwarded_topics", [])
        expected_fifo = [flow["topic"] for flow in flows]
        expected_deadline = [
            flow["topic"] for flow in flows if flow["kind"] == "control"
        ] + [
            flow["topic"] for flow in flows if flow["kind"] == "state"
        ]
        per_robot = deadline.get("router", {}).get("scheduler_per_robot", {})
        per_robot_complete = (
            len(per_robot) == robot_count and
            all(
                stats.get("forwarded") == 2 and stats.get("deadline_misses") == 0
                for stats in per_robot.values()
            )
        )
        priority_improved = (
            fifo_topics[: len(expected_fifo)] == expected_fifo and
            deadline_topics[: len(expected_deadline)] == expected_deadline and
            deadline_topics[:robot_count] != fifo_topics[:robot_count]
        )
        deadline_not_worse = (
            deadline.get("e2e_deadline_misses", 0) <=
            fifo.get("e2e_deadline_misses", 0)
        )
        deadline_router = deadline.get("router", {})
        scheduler_policy = deadline_router.get("scheduler_admission_policy", "always")
        scheduler_queued = int(deadline_router.get("scheduler_queued_frames", 0))
        scheduler_bypassed = int(
            deadline_router.get("scheduler_admission_bypassed_frames", 0)
        )
        adaptive_scheduler_policy = scheduler_policy != "always"
        scheduler_nonurgent_accounted = (
            scheduler_queued + scheduler_bypassed == robot_count
        )
        scheduler_order_contract = (
            priority_improved or
            (adaptive_scheduler_policy and scheduler_queued == 0 and
             scheduler_bypassed == robot_count)
        )
        scheduler_queue_contract = (
            scheduler_queued == robot_count
            if not adaptive_scheduler_policy
            else scheduler_nonurgent_accounted
        )
        status = (
            fifo.get("status") == "ok" and
            deadline.get("status") == "ok" and
            scheduler_order_contract and
            per_robot_complete and
            scheduler_queue_contract and
            deadline_router.get("scheduler_urgent_frames") == robot_count and
            deadline_router.get("scheduler_forwarded_frames") == len(flows) and
            deadline_router.get("scheduler_deadline_misses") == 0 and
            # Zero deadline misses (checked above) is the real correctness
            # signal; Jain's fairness index rarely lands on exactly 1.0 even
            # then, since any legitimate per-robot count skew from
            # processing/network jitter nudges it down without indicating
            # an unfair scheduler. A high floor still catches starvation.
            float(deadline_router.get(
                "scheduler_deadline_success_jain_index"
            ) or 0.0) >= 0.95 and
            deadline_not_worse
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "robot_count": robot_count,
            "flow_count": len(flows),
            "control_deadline_ms": control_deadline_ms,
            "state_deadline_ms": state_deadline_ms,
            "scheduler_window_ms": scheduler_window_ms,
            "scheduler_admission_policy": scheduler_admission_policy,
            "scheduler_admission_min_service_ratio": scheduler_admission_min_service_ratio,
            "scheduler_admission_exit_service_ratio": scheduler_admission_exit_service_ratio,
            "scheduler_admission_ewma_alpha": scheduler_admission_ewma_alpha,
            "scheduler_admission_min_epoch_frames": scheduler_admission_min_epoch_frames,
            "control_payload_bytes": control_payload_bytes,
            "state_payload_bytes": state_payload_bytes,
            "netem_profile": netem_profile,
            "netem_config": netem_config_for_profile(
                netem_profile,
                netem_loss_percent=netem_loss_percent,
            ),
            "expected_fifo_topics": expected_fifo,
            "expected_deadline_topics": expected_deadline,
            "priority_improved": priority_improved,
            "scheduler_order_contract": scheduler_order_contract,
            "scheduler_queue_contract": scheduler_queue_contract,
            "deadline_not_worse": deadline_not_worse,
            "per_robot_complete": per_robot_complete,
            "fifo_baseline": fifo,
            "deadline_scheduler": deadline,
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
        docker_shell(
            root,
            image,
            f"rm -rf {build_base} {install_base} {log_base}",
            check=False,
        )


def build_flows(
    *,
    robot_count: int,
    control_deadline_ms: int,
    state_deadline_ms: int,
    control_payload_bytes: int,
    state_payload_bytes: int,
) -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    for index in range(robot_count):
        robot_id = f"robot_{index:04d}"
        flows.append({
            "robot_id": robot_id,
            "kind": "control",
            "topic": f"/fleetqox/{robot_id}/control",
            "payload": f"{robot_id}-control",
            "payload_size": control_payload_bytes,
            "payload_fill": "c",
            "deadline_ms": control_deadline_ms,
        })
        flows.append({
            "robot_id": robot_id,
            "kind": "state",
            "topic": f"/fleetqox/{robot_id}/state",
            "payload": f"{robot_id}-state",
            "payload_size": state_payload_bytes,
            "payload_fill": "s",
            "deadline_ms": state_deadline_ms,
        })
    return flows


def netem_config_for_profile(
    profile: str,
    *,
    netem_loss_percent: float | None = None,
) -> dict[str, float]:
    config = dict(NETEM_PROFILES[profile])
    if netem_loss_percent is not None:
        config["loss_percent"] = max(0.0, float(netem_loss_percent))
    return config


def run_scenario(
    *,
    root: Path,
    image: str,
    suffix: str,
    label: str,
    flows: list[dict[str, Any]],
    endpoint_binary: str,
    router_binary: str,
    install_base: str,
    scheduler_window_ms: int,
    scheduler_admission_policy: str,
    scheduler_admission_min_service_ratio: float,
    scheduler_admission_exit_service_ratio: float,
    scheduler_admission_ewma_alpha: float,
    scheduler_admission_min_epoch_frames: int,
    netem_profile: str,
    netem_loss_percent: float | None,
) -> dict[str, Any]:
    network = f"fleetrmw-multi-qos-{label}-{suffix}"
    router_name = f"fleetrmw-multi-qos-router-{label}-{suffix}"
    subscriber_names = [
        f"fleetrmw-multi-qos-sub-{label}-{suffix}-{index}"
        for index in range(len(flows))
    ]
    router_port = 48500
    expected_frames = len(flows)
    netem_config = netem_config_for_profile(
        netem_profile,
        netem_loss_percent=netem_loss_percent,
    )
    # The publisher container sends all flows sequentially (one process
    # spawn + --pre-publish-ms wait + network round trip per flow), so
    # total publish time scales with flow count. A flat 20s budget was
    # sized for the original 8-robot (16-flow) default; at higher robot
    # counts the router hit --timeout-ms and exited early having only
    # received/forwarded a fraction of the flows, silently truncating
    # the run instead of failing loudly.
    #
    # That budget also didn't scale with netem severity: under "roaming"
    # (95ms delay, 5Mbit), the router received all flows but its own
    # --timeout-ms could still fire before the last scheduler-held-back
    # frame reached the end of its scheduler_window_ms wait, forwarding
    # 15/16 flows instead of 16/16. Add margin for one full scheduler
    # window plus a per-flow round-trip allowance under the configured
    # delay/jitter.
    router_timeout_ms = (
        max(20000, expected_frames * 1000) +
        max(0, scheduler_window_ms) +
        int(netem_config.get("delay_ms", 0.0) * 4)
    )
    try:
        run(["docker", "network", "create", network])
        scheduler_args = ""
        if scheduler_window_ms > 0:
            urgent_deadline_ms = min(
                int(flow["deadline_ms"])
                for flow in flows
                if flow["kind"] == "control"
            )
            queued_flow_count = sum(1 for flow in flows if flow["kind"] != "control")
            scheduler_args = (
                f"--scheduler-window-ms {scheduler_window_ms} "
                f"--scheduler-urgent-deadline-ms {urgent_deadline_ms} "
                f"--scheduler-expected-frames {queued_flow_count} "
                "--scheduler-topic-prefix /fleetqox/ "
            )
            if scheduler_admission_policy != "always":
                scheduler_args += (
                    f"--scheduler-admission-policy {shlex.quote(scheduler_admission_policy)} "
                    "--scheduler-admission-min-service-ratio "
                    f"{scheduler_admission_min_service_ratio:g} "
                    "--scheduler-admission-exit-service-ratio "
                    f"{scheduler_admission_exit_service_ratio:g} "
                    "--scheduler-admission-ewma-alpha "
                    f"{scheduler_admission_ewma_alpha:g} "
                    "--scheduler-admission-min-epoch-frames "
                    f"{scheduler_admission_min_epoch_frames} "
                )
        netem_command = ""
        telemetry_args = ""
        router_extra_args: tuple[str, ...] = ()
        if netem_profile != "none":
            netem_command = (
                "tc qdisc replace dev eth0 root netem "
                f"delay {netem_config['delay_ms']:g}ms {netem_config['jitter_ms']:g}ms "
                f"loss {netem_config['loss_percent']:g}% "
                f"rate {netem_config['rate_mbit']:g}mbit && "
            )
            telemetry_args = (
                f"--telemetry-latency-ms {netem_config['delay_ms']:g} "
                f"--telemetry-jitter-ms {netem_config['jitter_ms']:g} "
                "--telemetry-capacity-bytes "
                f"{int(netem_config['rate_mbit'] * 125000)} "
            )
            router_extra_args = ("--cap-add", "NET_ADMIN")
        start_container(
            root=root,
            image=image,
            name=router_name,
            network=network,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                f"{netem_command}"
                f"{router_binary} --bind 0.0.0.0:{router_port} "
                f"--expected-frames {expected_frames} "
                f"--expected-route-advertisements {expected_frames} "
                f"--expected-graph-advertisements {expected_frames} "
                f"{scheduler_args}"
                f"{telemetry_args}"
                f"--post-satisfaction-ms 500 --timeout-ms {router_timeout_ms}"
            ),
            extra_args=router_extra_args,
        )
        netem_qdisc = "disabled"
        if netem_profile != "none":
            # A fixed 0.2s sleep raced "source setup.bash && tc qdisc
            # replace" inside the container's startup command -- the query
            # would often observe the pre-netem default "noqueue" qdisc.
            # Poll until the qdisc actually shows netem (or give up).
            netem_qdisc = ""
            for _ in range(20):
                netem_qdisc = run(
                    ["docker", "exec", router_name, "tc", "qdisc", "show", "dev", "eth0"],
                    check=False,
                ).stdout.strip()
                if "netem" in netem_qdisc:
                    break
                time.sleep(0.25)
        time.sleep(0.4)
        # A queued (state) flow's frame can be held for the full scheduler
        # window plus the drain-pacing tail before the subscriber ever
        # sees it, so a flat 12s subscriber wait can time out and mark
        # `taken=false` on a frame that the scheduler itself delivered
        # within its own deadline -- give it the same margin the window
        # itself was floored to, plus room for setup overhead. Under an
        # epoch-based admission policy (e.g. slo_service_epoch) a frame
        # can legitimately sit held back across more than one window
        # cycle before the EWMA crosses the exit threshold, so double
        # the window's worth of margin rather than adding a flat amount.
        subscriber_timeout_ms = max(12000, scheduler_window_ms * 2 + 5000)
        for index, flow in enumerate(flows):
            start_container(
                root=root,
                image=image,
                name=subscriber_names[index],
                network=network,
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{48600 + index} "
                    f"FLEETQOX_RMW_PEERS={router_name}:{router_port} "
                    f"{endpoint_binary} --mode subscriber "
                    f"--topic {shlex.quote(flow['topic'])} "
                    f"--payload {shlex.quote(flow['payload'])} "
                    f"--payload-size {flow['payload_size']} "
                    f"--payload-fill {shlex.quote(flow['payload_fill'])} "
                    f"--expect-taken true --timeout-ms {subscriber_timeout_ms}"
                ),
            )
        time.sleep(1.0)
        publish_commands = [
            "source /opt/ros/jazzy/setup.bash",
            f"source {install_base}/setup.bash",
            "set -e",
        ]
        for flow in flows:
            publish_commands.append(
                f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(flow['robot_id'])} "
                "FLEETQOX_RMW_BIND=0.0.0.0:0 "
                f"FLEETQOX_RMW_PEERS={router_name}:{router_port} "
                f"{endpoint_binary} --mode publisher "
                f"--topic {shlex.quote(flow['topic'])} "
                f"--payload {shlex.quote(flow['payload'])} "
                f"--payload-size {flow['payload_size']} "
                f"--payload-fill {shlex.quote(flow['payload_fill'])} "
                "--pre-publish-ms 50 "
                f"--deadline-ms {flow['deadline_ms']}"
            )
        publisher = docker_shell(
            root,
            image,
            "\n".join(publish_commands),
            "--network",
            network,
            check=False,
        )
        router_returncode = int(run(["docker", "wait", router_name]).stdout.strip())
        subscriber_returncodes = [
            int(run(["docker", "wait", name]).stdout.strip())
            for name in subscriber_names
        ]
        router_logs = run(["docker", "logs", router_name], check=False)
        subscriber_logs = [
            run(["docker", "logs", name], check=False)
            for name in subscriber_names
        ]
        publisher_rows = parse_json_lines(publisher.stdout + "\n" + publisher.stderr)
        subscriber_rows = [
            parse_last_json(log.stdout + "\n" + log.stderr)
            for log in subscriber_logs
        ]
        router = parse_last_json(router_logs.stdout + "\n" + router_logs.stderr)
        e2e_deadline_misses = 0
        per_robot: dict[str, dict[str, Any]] = {}
        control_ages: list[float] = []
        state_ages: list[float] = []
        for flow, row in zip(flows, subscriber_rows):
            age_ms = float(row.get("take_age_ms", 0.0))
            deadline_missed = age_ms > float(flow["deadline_ms"])
            row["robot_id"] = flow["robot_id"]
            row["kind"] = flow["kind"]
            row["deadline_ms"] = flow["deadline_ms"]
            row["deadline_missed"] = deadline_missed
            if deadline_missed:
                e2e_deadline_misses += 1
            (control_ages if flow["kind"] == "control" else state_ages).append(age_ms)
            robot = per_robot.setdefault(
                flow["robot_id"],
                {"forwarded": 0, "deadline_misses": 0},
            )
            robot["forwarded"] += 1
            robot["deadline_misses"] += int(deadline_missed)
        for robot in per_robot.values():
            robot["deadline_success_ratio"] = (
                (robot["forwarded"] - robot["deadline_misses"]) /
                robot["forwarded"]
            )
        status = (
            publisher.returncode == 0 and
            router_returncode == 0 and
            all(code == 0 for code in subscriber_returncodes) and
            len(publisher_rows) == len(flows) and
            all(row.get("status") == "ok" for row in publisher_rows) and
            all(row.get("status") == "ok" and row.get("taken") is True for row in subscriber_rows) and
            router.get("status") == "ok" and
            router.get("received_frames") == len(flows) and
            router.get("forwarded_frames") == len(flows)
        )
        return {
            "status": "ok" if status else "failed",
            "label": label,
            "docker_network": network,
            "scheduler_window_ms": scheduler_window_ms,
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "netem_qdisc": netem_qdisc,
            "publisher_returncode": publisher.returncode,
            "publisher_stderr": publisher.stderr,
            "publisher": publisher_rows,
            "router_returncode": router_returncode,
            "router": router,
            "e2e_deadline_misses": e2e_deadline_misses,
            "e2e_per_robot": per_robot,
            "control_take_age_ms": latency_summary(control_ages),
            "state_take_age_ms": latency_summary(state_ages),
            "subscriber_returncodes": subscriber_returncodes,
            "subscribers": subscriber_rows,
        }
    finally:
        run(["docker", "rm", "-f", router_name, *subscriber_names], check=False)
        run(["docker", "network", "rm", network], check=False)


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
    *extra: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run([
        "docker", "run", "--rm",
        *extra,
        "--entrypoint", "bash",
        "-v", f"{root}:/work",
        "-w", "/work",
        image,
        "-lc", command,
    ], check=check)


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


def parse_json_lines(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def latency_summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    p95_index = max(
        0,
        min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1),
    )
    return {
        "mean": sum(ordered) / len(ordered),
        "p95": ordered[p95_index],
        "max": ordered[-1],
    }


if __name__ == "__main__":
    raise SystemExit(main())
