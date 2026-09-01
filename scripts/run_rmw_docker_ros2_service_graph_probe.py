"""Run Docker ROS 2 CLI service-list probe against rmw_fleetqox_cpp."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_ros2_service_graph_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
DEFAULT_SERVICE = "/fleetqox/set_bool"
DEFAULT_TYPE = "std_srvs/srv/SetBool"
DEFAULT_NODE_FULL_NAME = "/fleetqox/fleetqox_rcl_service_node"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--observer-bind", default="127.0.0.1:48280")
    parser.add_argument("--spin-time", type=float, default=3.0)
    parser.add_argument("--startup-delay", type=float, default=0.4)
    parser.add_argument("--hold-ms", type=int, default=15000)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_ros2_service_graph_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        service=args.service,
        observer_bind=args.observer_bind,
        spin_time=args.spin_time,
        startup_delay=args.startup_delay,
        hold_ms=args.hold_ms,
    )
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-ros2-service-graph-probe")
        print(f"  status: {summary['status']}")
        print(f"  service_found: {summary['service_found']}")
        print(f"  type_found: {summary['type_found']}")
        print(f"  node_service_found: {summary['node_service_found']}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    service: str,
    observer_bind: str,
    spin_time: float,
    startup_delay: float,
    hold_ms: int,
) -> dict[str, Any]:
    quoted_service = shlex.quote(service)
    quoted_observer_bind = shlex.quote(observer_bind)
    quoted_node_full_name = shlex.quote(DEFAULT_NODE_FULL_NAME)
    command = f"""
source /opt/ros/jazzy/setup.bash
rm -rf /tmp/fleetrmw_build /tmp/fleetrmw_install /tmp/fleetrmw_log
colcon --log-base /tmp/fleetrmw_log build \
  --base-paths ros2_ws/src \
  --packages-select fleetrmw_interfaces rmw_fleetqox_cpp \
  --build-base /tmp/fleetrmw_build \
  --install-base /tmp/fleetrmw_install \
  --cmake-args -DCMAKE_BUILD_TYPE=Release >/tmp/fleetrmw_build.log 2>&1
build_ret=$?
if [ "$build_ret" -ne 0 ]; then
  cat /tmp/fleetrmw_build.log >&2
  exit "$build_ret"
fi
source /tmp/fleetrmw_install/setup.bash
export RMW_IMPLEMENTATION=rmw_fleetqox_cpp
FLEETQOX_RMW_PEERS={quoted_observer_bind} \
  /tmp/fleetrmw_install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_rcl_service_node \
  --service {quoted_service} --hold-ms {hold_ms} \
  > /tmp/fleetrmw_service_node.out 2> /tmp/fleetrmw_service_node.err &
service_pid=$!
sleep {startup_delay}
FLEETQOX_RMW_BIND={quoted_observer_bind} \
  ros2 service list --no-daemon --spin-time {spin_time} -t \
  > /tmp/fleetrmw_service_list.out 2> /tmp/fleetrmw_service_list.err
list_ret=$?
FLEETQOX_RMW_BIND={quoted_observer_bind} \
  ros2 node info --no-daemon --spin-time {spin_time} {quoted_node_full_name} \
  > /tmp/fleetrmw_service_node_info.out 2> /tmp/fleetrmw_service_node_info.err
info_ret=$?
wait "$service_pid"
service_ret=$?
LIST_RET="$list_ret" INFO_RET="$info_ret" SERVICE_RET="$service_ret" python3 - <<'PY'
import json
import os
from pathlib import Path

service = {service!r}
expected_type = {DEFAULT_TYPE!r}
node_full_name = {DEFAULT_NODE_FULL_NAME!r}
service_list_stdout = Path("/tmp/fleetrmw_service_list.out").read_text()
service_list_stderr = Path("/tmp/fleetrmw_service_list.err").read_text()
node_info_stdout = Path("/tmp/fleetrmw_service_node_info.out").read_text()
node_info_stderr = Path("/tmp/fleetrmw_service_node_info.err").read_text()
service_node_stdout = Path("/tmp/fleetrmw_service_node.out").read_text()
service_node_stderr = Path("/tmp/fleetrmw_service_node.err").read_text()
try:
    service_summary = json.loads(service_node_stdout.strip().splitlines()[-1])
except Exception:
    service_summary = {{"status": "parse_failed", "raw_stdout": service_node_stdout}}
summary = {{
    "schema_version": {SCHEMA_VERSION!r},
    "status": "pending",
    "service": service,
    "expected_type": expected_type,
    "node_full_name": node_full_name,
    "service_found": service in service_list_stdout,
    "type_found": expected_type in service_list_stdout,
    "node_service_found": service in node_info_stdout and expected_type in node_info_stdout,
    "service_list_stdout": service_list_stdout,
    "service_list_stderr": service_list_stderr,
    "node_info_stdout": node_info_stdout,
    "node_info_stderr": node_info_stderr,
    "service_node_stdout": service_node_stdout,
    "service_node_stderr": service_node_stderr,
    "service_node": service_summary,
    "service_list_returncode": int(os.environ["LIST_RET"]),
    "node_info_returncode": int(os.environ["INFO_RET"]),
    "service_node_returncode": int(os.environ["SERVICE_RET"]),
}}
summary["status"] = "ok" if (
    summary["service_list_returncode"] == 0 and
    summary["node_info_returncode"] == 0 and
    summary["service_node_returncode"] == 0 and
    summary["service_found"] and
    summary["type_found"] and
    summary["node_service_found"] and
    service_list_stderr == "" and
    node_info_stderr == "" and
    service_node_stderr == "" and
    service_summary.get("status") == "ok"
) else "failed"
print(json.dumps(summary, sort_keys=True))
PY
"""
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--entrypoint", "bash",
            "-v", f"{root}:/work",
            "-w", "/work",
            image,
            "-lc", command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "docker_returncode": result.returncode,
            "docker_stdout": result.stdout,
            "docker_stderr": result.stderr,
        }
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "docker_returncode": result.returncode,
            "docker_stdout": result.stdout,
            "docker_stderr": result.stderr,
        }
    summary = json.loads(lines[-1])
    summary["docker_returncode"] = result.returncode
    summary["docker_stderr"] = result.stderr
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
