"""Run nested C++ pub/sub and service calls through the FleetRMW router."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_router_service_call_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_router_rclcpp_interprocess_probe.v2"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    suffix = str(os.getpid())
    network = f"fleetrmw-rclcpp-net-{suffix}"
    router_name = f"fleetrmw-rclcpp-router-{suffix}"
    server_name = f"fleetrmw-rclcpp-server-{suffix}"
    client_name = f"fleetrmw-rclcpp-client-{suffix}"
    build_base = "/work/.tmp_fleetrmw_rclcpp_build"
    install_base = "/work/.tmp_fleetrmw_rclcpp_install"
    log_base = "/work/.tmp_fleetrmw_rclcpp_log"

    def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check
        )

    def docker_shell(command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run(
            [
                "docker", "run", "--rm", "--entrypoint", "bash",
                "-v", f"{root}:/work", "-w", "/work", image, "-lc", command,
            ],
            check=check,
        )

    try:
        docker_shell(
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf {build_base} {install_base} {log_base} && "
            f"colcon --log-base {log_base} build --base-paths ros2_ws/src "
            "--packages-select rmw_fleetqox_cpp "
            f"--build-base {build_base} --install-base {install_base} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release"
        )
        run(["docker", "network", "create", network])
        run([
            "docker", "run", "-d", "--name", router_name, "--network", network,
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            f"source {install_base}/setup.bash && "
            f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
            "fleetrmw_udp_router_probe --bind 0.0.0.0:49800 "
            "--expected-frames 4 --expected-service-frames 3 "
            "--expected-graph-advertisements 8 --post-satisfaction-ms 1000 "
            "--timeout-ms 30000",
        ])
        time.sleep(0.4)
        common = (
            "source /opt/ros/jazzy/setup.bash && "
            f"source {install_base}/setup.bash && "
            "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
        )
        executable = (
            f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
            "fleetrmw_rclcpp_interprocess_probe"
        )
        run([
            "docker", "run", "-d", "--name", server_name, "--network", network,
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            common + "export FLEETQOX_RMW_BIND=0.0.0.0:49801 && "
            f"export FLEETQOX_RMW_PEERS={router_name}:49800 && {executable} server",
        ])
        run([
            "docker", "run", "-d", "--name", client_name, "--network", network,
            "--entrypoint", "bash", "-v", f"{root}:/work", "-w", "/work", image, "-lc",
            common + "export FLEETQOX_RMW_BIND=0.0.0.0:49802 && "
            f"export FLEETQOX_RMW_PEERS={router_name}:49800 && {executable} client",
        ])
        client_rc = int(run(["docker", "wait", client_name]).stdout.strip())
        server_rc = int(run(["docker", "wait", server_name]).stdout.strip())
        router_rc = int(run(["docker", "wait", router_name]).stdout.strip())

        def logs(name: str) -> str:
            result = run(["docker", "logs", name], check=False)
            return result.stdout + result.stderr

        client_logs = logs(client_name)
        server_logs = logs(server_name)
        router_logs = logs(router_name)
        client = parse_last_json(client_logs)
        server = parse_last_json(server_logs)
        router = parse_last_json(router_logs)
        ok = (
            client_rc == 0 and server_rc == 0 and router_rc == 0
            and client.get("status") == "ok" and server.get("status") == "ok"
            and router.get("status") == "ok"
            and client.get("pose_roundtrip") is True
            and client.get("path_roundtrip") is True
            and int(client.get("path_pose_count", 0)) == 64
            and client.get("service_ok") is True
            and client.get("plan_service_available") is True
            and client.get("plan_service_ok") is True
            and int(client.get("plan_pose_count", 0)) == 512
            and client.get("plan_response_callback_observed") is True
            and client.get("publisher_network_flow") is True
            and client.get("subscription_network_flow") is True
            and client.get("path_publisher_network_flow") is True
            and client.get("path_subscription_network_flow") is True
            and client.get("response_callback_observed") is True
            and server.get("request_callback_observed") is True
            and server.get("path_received") is True
            and server.get("path_valid") is True
            and server.get("plan_service_received") is True
            and server.get("plan_request_valid") is True
            and int(server.get("plan_pose_count", 0)) == 512
            and int(server.get("path_pose_count", 0)) == 64
            and int(router.get("service_forwarded", 0)) >= 3
            and {
                "/fleetqox/cpp_set_bool",
                "/fleetqox/cpp_get_plan",
            }.issubset(set(router.get("service_names", [])))
            and int(router.get("forwarded_frames", 0)) >= 4
            and int(router.get("invalid_frames", -1)) == 0
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "image": image,
            "client_returncode": client_rc,
            "server_returncode": server_rc,
            "router_returncode": router_rc,
            "client": client,
            "server": server,
            "router": router,
            "client_logs": client_logs,
            "server_logs": server_logs,
            "router_logs": router_logs,
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
        run(["docker", "rm", "-f", router_name, server_name, client_name], check=False)
        run(["docker", "network", "rm", network], check=False)
        docker_shell(f"rm -rf {build_base} {install_base} {log_base}", check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_rclcpp_interprocess_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True) if args.json else summary["status"])
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
