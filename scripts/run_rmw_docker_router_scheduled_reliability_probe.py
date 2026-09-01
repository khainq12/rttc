"""Run router-scheduled ACK/NACK repair for rmw_fleetqox_cpp."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_router_scheduled_reliability_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DEFAULT_TOPIC = "/fleetqox/scheduled_reliability_probe"
NETEM_PROFILES: dict[str, dict[str, float]] = {
    "none": {"delay_ms": 0.0, "jitter_ms": 0.0, "rate_mbit": 0.0, "loss_percent": 0.0},
    "wifi": {"delay_ms": 25.0, "jitter_ms": 8.0, "rate_mbit": 20.0, "loss_percent": 0.0},
    "wan": {"delay_ms": 70.0, "jitter_ms": 10.0, "rate_mbit": 10.0, "loss_percent": 0.0},
    "roaming": {"delay_ms": 95.0, "jitter_ms": 20.0, "rate_mbit": 5.0, "loss_percent": 0.0},
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--netem-profile", choices=sorted(NETEM_PROFILES), default="none")
    parser.add_argument("--netem-loss-percent", type=float, default=None)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_scheduled_reliability_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        topic=args.topic,
        netem_profile=args.netem_profile,
        netem_loss_percent=args.netem_loss_percent,
    )
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-scheduled-reliability-probe")
        print(f"  status: {summary['status']}")
        print(f"  netem_profile: {summary.get('netem_profile')}")
        print(f"  router_ack_forwarded: {summary.get('router', {}).get('ack_nack_forwarded')}")
        print(
            "  scheduler_forwarded: "
            f"{summary.get('router', {}).get('scheduler_forwarded_frames')}"
        )
        print(
            "  publisher_retransmissions: "
            f"{summary.get('publisher', {}).get('nack_retransmissions')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    topic: str,
    netem_profile: str = "none",
    netem_loss_percent: float | None = None,
) -> dict[str, Any]:
    if netem_profile not in NETEM_PROFILES:
        raise ValueError(f"unknown netem profile: {netem_profile}")
    suffix = str(os.getpid())
    network = f"fleetrmw-sched-rel-net-{suffix}"
    router_name = f"fleetrmw-sched-rel-router-{suffix}"
    subscriber_name = f"fleetrmw-sched-rel-sub-{suffix}"
    build_base = "/work/.tmp_fleetrmw_router_scheduled_reliability_build"
    install_base = "/work/.tmp_fleetrmw_router_scheduled_reliability_install"
    log_base = "/work/.tmp_fleetrmw_router_scheduled_reliability_log"
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    netem_config = netem_config_for_profile(
        netem_profile,
        netem_loss_percent=netem_loss_percent,
    )

    try:
        run(["docker", "network", "create", network])
        subnet_result = run(
            ["docker", "network", "inspect", "-f", "{{(index .IPAM.Config 0).Subnet}}", network]
        )
        subnet = ipaddress.ip_network(subnet_result.stdout.strip(), strict=False)
        router_ip = str(subnet.network_address + 10)
        subscriber_ip = str(subnet.network_address + 11)
        publisher_ip = str(subnet.network_address + 12)
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
        netem_command = ""
        router_extra_args: tuple[str, ...] = ()
        if netem_profile != "none":
            netem_command = (
                "tc qdisc replace dev eth0 root netem "
                f"delay {netem_config['delay_ms']:g}ms {netem_config['jitter_ms']:g}ms "
                f"loss {netem_config['loss_percent']:g}% "
                f"rate {netem_config['rate_mbit']:g}mbit && "
            )
            router_extra_args = ("--cap-add", "NET_ADMIN")
        post_satisfaction_ms = netem_post_satisfaction_ms(
            netem_config,
            enabled=netem_profile != "none",
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
                f"{router_binary} --bind 0.0.0.0:48740 "
                f"--graph-peers {subscriber_ip}:48741,{publisher_ip}:48742 "
                "--expected-frames 4 --expected-ack-nack-frames 3 "
                "--expected-route-advertisements 1 --expected-graph-advertisements 2 "
                "--drop-source-sequences 2 "
                "--scheduler-window-ms 150 "
                "--scheduler-expected-frames 2 "
                "--scheduler-topic-prefix /fleetqox/ "
                f"--post-satisfaction-ms {post_satisfaction_ms} "
                "--timeout-ms 12000"
            ),
            extra_args=router_extra_args,
        )
        time.sleep(0.5)
        netem_qdisc = (
            run(
                ["docker", "exec", router_name, "tc", "qdisc", "show", "dev", "eth0"],
                check=False,
            ).stdout.strip()
            if netem_profile != "none" else "disabled"
        )
        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            ip_address=subscriber_ip,
            command=(
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                f"FLEETQOX_RMW_BIND=0.0.0.0:48741 FLEETQOX_RMW_PEERS={router_ip}:48740 "
                f"{endpoint_binary} --mode subscriber --topic {topic} "
                "--timeout-ms 10000 --min-ack-nack-sent 3"
            ),
        )
        time.sleep(0.8)
        # See run_rmw_docker_router_reliability_probe.py for why
        # --pre-publish-wait-ms is needed: the subscriber's periodic graph
        # re-advertisement can't reach the publisher until after the
        # publisher's own socket is bound, so this must delay the first
        # publish() rather than the orchestrator sleeping earlier.
        publisher = run([
            "docker", "run", "--rm",
            "--network", network,
            "--ip", publisher_ip,
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc",
            (
                f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                f"FLEETQOX_RMW_BIND=0.0.0.0:48742 FLEETQOX_RMW_PEERS={router_ip}:48740 "
                f"{endpoint_binary} --mode publisher --topic {topic} "
                "--pre-publish-wait-ms 800 "
                "--hold-ms 7000 --min-ack-nack-received 3 --min-retransmissions 1"
            ),
        ])
        subscriber_returncode = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        router_returncode = int(run(["docker", "wait", router_name]).stdout.strip())
        subscriber_log = run(["docker", "logs", subscriber_name]).stdout.strip()
        router_log = run(["docker", "logs", router_name]).stdout.strip()
        publisher_result = parse_last_json(publisher.stdout)
        subscriber_result = parse_last_json(subscriber_log)
        router_result = parse_last_json(router_log)
        subscriber_payloads = set(subscriber_result.get("payloads", []))
        status = (
            publisher.returncode == 0 and
            subscriber_returncode == 0 and
            router_returncode == 0 and
            publisher_result.get("status") == "ok" and
            subscriber_result.get("status") == "ok" and
            router_result.get("status") == "ok" and
            router_result.get("test_dropped_frames", 0) >= 1 and
            router_result.get("ack_nack_frames", 0) >= 3 and
            router_result.get("ack_nack_forwarded", 0) >= 3 and
            router_result.get("scheduler_queued_frames", 0) >= 3 and
            router_result.get("scheduler_forwarded_frames", 0) >= 3 and
            publisher_result.get("nack_retransmissions", 0) >= 1 and
            {"one", "two", "three"}.issubset(subscriber_payloads)
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "docker_network": network,
            "topic": topic,
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "netem_qdisc": netem_qdisc,
            "post_satisfaction_ms": post_satisfaction_ms,
            "publisher_returncode": publisher.returncode,
            "subscriber_returncode": subscriber_returncode,
            "router_returncode": router_returncode,
            "publisher": publisher_result,
            "subscriber": subscriber_result,
            "router": router_result,
            "publisher_stdout": publisher.stdout,
            "publisher_stderr": publisher.stderr,
            "subscriber_logs": subscriber_log,
            "router_logs": router_log,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "docker_network": network,
            "netem_profile": netem_profile,
            "netem_config": netem_config,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run(["docker", "rm", "-f", router_name, subscriber_name], check=False)
        run(["docker", "network", "rm", network], check=False)
        docker_shell(root, image, f"rm -rf {build_base} {install_base} {log_base}", check=False)


def parse_last_json(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return {"status": "parse_failed", "raw": stripped}
    return {"status": "missing", "raw": output}


def netem_config_for_profile(
    profile: str,
    *,
    netem_loss_percent: float | None = None,
) -> dict[str, float]:
    config = dict(NETEM_PROFILES[profile])
    if netem_loss_percent is not None:
        config["loss_percent"] = max(0.0, float(netem_loss_percent))
    return config


def netem_post_satisfaction_ms(
    netem_config: dict[str, float],
    *,
    enabled: bool,
) -> int:
    if not enabled:
        return 0
    return max(
        1000,
        int(
            netem_config["delay_ms"] * 4
            + netem_config["jitter_ms"] * 2
        ),
    )


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
    ip_address: str,
    command: str,
    extra_args: tuple[str, ...] = (),
) -> str:
    result = run([
        "docker", "run", "-d",
        "--name", name,
        "--network", network,
        "--ip", ip_address,
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
