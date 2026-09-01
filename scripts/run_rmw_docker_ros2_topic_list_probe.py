"""Run a Docker ROS 2 CLI topic-list probe against rmw_fleetqox_cpp.

This is the first ROS CLI/tooling smoke for the custom RMW.  It starts
`ros2 topic list --no-daemon --spin-time ... -t` as a graph observer and then
starts a real `rcl` talker process that advertises a `std_msgs/msg/String`
publisher through FleetRMW graph advertisements.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_ros2_topic_list_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"
DEFAULT_TOPIC = "/fleetqox/rcl_graph_talker"
DEFAULT_TYPE = "std_msgs/msg/String"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--observer-bind", default="127.0.0.1:48252")
    parser.add_argument("--spin-time", type=float, default=3.0)
    parser.add_argument("--startup-delay", type=float, default=0.4)
    parser.add_argument("--hold-ms", type=int, default=10000)
    parser.add_argument("--summary-json", default="results_rmw_socket/docker_ros2_topic_list_probe_summary.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(
        root=root,
        image=args.image,
        topic=args.topic,
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
        print("fleetrmw-ros2-topic-list-probe")
        print(f"  status: {summary['status']}")
        print(f"  topic_found: {summary['topic_found']}")
        print(f"  type_found: {summary['type_found']}")
        print(f"  list_returncode: {summary['topic_list_returncode']}")
        print(f"  talker_returncode: {summary['talker_returncode']}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(
    *,
    root: Path,
    image: str,
    topic: str,
    observer_bind: str,
    spin_time: float,
    startup_delay: float,
    hold_ms: int,
) -> dict[str, Any]:
    quoted_topic = shlex.quote(topic)
    quoted_observer_bind = shlex.quote(observer_bind)
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
  /tmp/fleetrmw_install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_rcl_graph_talker \
  --topic {quoted_topic} --hold-ms {hold_ms} --period-ms 100 \
  > /tmp/fleetrmw_talker.out 2> /tmp/fleetrmw_talker.err &
talker_pid=$!
sleep {startup_delay}
FLEETQOX_RMW_BIND={quoted_observer_bind} \
  ros2 topic list --no-daemon --spin-time {spin_time} -t \
  > /tmp/fleetrmw_topic_list.out 2> /tmp/fleetrmw_topic_list.err
list_ret=$?
wait "$talker_pid"
talker_ret=$?
LIST_RET="$list_ret" TALKER_RET="$talker_ret" python3 - <<'PY'
import json
import os
from pathlib import Path

topic = {topic!r}
expected_type = {DEFAULT_TYPE!r}
topic_list_stdout = Path("/tmp/fleetrmw_topic_list.out").read_text()
topic_list_stderr = Path("/tmp/fleetrmw_topic_list.err").read_text()
talker_stdout = Path("/tmp/fleetrmw_talker.out").read_text()
talker_stderr = Path("/tmp/fleetrmw_talker.err").read_text()
try:
    talker_summary = json.loads(talker_stdout.strip().splitlines()[-1])
except Exception:
    talker_summary = {{"status": "parse_failed", "raw_stdout": talker_stdout}}
topic_line = f"{{topic}} [{{expected_type}}]"
summary = {{
    "schema_version": {SCHEMA_VERSION!r},
    "status": "pending",
    "topic": topic,
    "expected_type": expected_type,
    "topic_found": topic in topic_list_stdout,
    "type_found": topic_line in topic_list_stdout,
    "topic_list_stdout": topic_list_stdout,
    "topic_list_stderr": topic_list_stderr,
    "talker_stdout": talker_stdout,
    "talker_stderr": talker_stderr,
    "talker": talker_summary,
    "topic_list_returncode": int(os.environ["LIST_RET"]),
    "talker_returncode": int(os.environ["TALKER_RET"]),
}}
summary["status"] = "ok" if (
    summary["topic_list_returncode"] == 0 and
    summary["talker_returncode"] == 0 and
    summary["topic_found"] and
    summary["type_found"] and
    topic_list_stderr == "" and
    talker_stderr == "" and
    talker_summary.get("status") == "ok"
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
