"""Prove local publisher/subscription endpoints report a real type hash.

Closes one specific gap listed under the type_support partial capability:
rmw_get_publishers_info_by_topic/rmw_get_subscriptions_info_by_topic used
to always return the zero-initialized/unset topic_type_hash sentinel, even
for this process's own endpoints, whose message type hash is trivially
knowable from the type support handle's own get_type_hash_func. This does
not implement full RMW_MIDDLEWARE_SUPPORTS_TYPE_DISCOVERY (deferred type
support population for remotely-discovered, not-yet-known types), only
honest local-endpoint hash reporting.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_docker_topic_type_hash_probe.v1"
PROBE_SCHEMA_VERSION = "fleetrmw.rmw_topic_type_hash_probe.v1"
DEFAULT_IMAGE = "ros:jazzy-ros-base"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_topic_type_hash_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_probe(root=root, image=args.image)
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-topic-type-hash-probe")
        print(f"  status: {summary['status']}")
        probe = summary.get("probe", {})
        print(f"  expected_hash_version: {probe.get('expected_hash_version')}")
        print(f"  publisher_hash_matches: {probe.get('publisher_hash_matches')}")
        print(f"  subscription_hash_matches: {probe.get('subscription_hash_matches')}")
    return 0 if summary["status"] == "ok" else 1


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    command = """
source /opt/ros/jazzy/setup.bash
rm -rf /tmp/fleetrmw_hash_build /tmp/fleetrmw_hash_install /tmp/fleetrmw_hash_log
colcon --log-base /tmp/fleetrmw_hash_log build \\
  --base-paths ros2_ws/src \\
  --packages-select fleetrmw_interfaces rmw_fleetqox_cpp \\
  --build-base /tmp/fleetrmw_hash_build \\
  --install-base /tmp/fleetrmw_hash_install \\
  --cmake-args -DCMAKE_BUILD_TYPE=Release >/tmp/fleetrmw_hash_build.log 2>&1
build_ret=$?
if [ "$build_ret" -ne 0 ]; then
  cat /tmp/fleetrmw_hash_build.log >&2
  exit "$build_ret"
fi
source /tmp/fleetrmw_hash_install/setup.bash
export RMW_IMPLEMENTATION=rmw_fleetqox_cpp
/tmp/fleetrmw_hash_install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_topic_type_hash_probe \\
  > /tmp/fleetrmw_topic_type_hash_probe.out 2> /tmp/fleetrmw_topic_type_hash_probe.err
probe_ret=$?
PROBE_RET="$probe_ret" python3 - <<'PY'
import json
import os
from pathlib import Path

stdout = Path("/tmp/fleetrmw_topic_type_hash_probe.out").read_text()
stderr = Path("/tmp/fleetrmw_topic_type_hash_probe.err").read_text()
probe = {}
for line in reversed(stdout.splitlines()):
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            probe = json.loads(stripped)
        except json.JSONDecodeError:
            probe = {"status": "parse_failed", "raw": stripped}
        break
if not probe:
    probe = {"status": "missing", "raw_stdout": stdout}

summary = {
    "schema_version": "fleetrmw.rmw_docker_topic_type_hash_probe.v1",
    "status": "pending",
    "probe": probe,
    "probe_stdout": stdout,
    "probe_stderr": stderr,
    "probe_returncode": int(os.environ["PROBE_RET"]),
}
summary["status"] = "ok" if (
    summary["probe_returncode"] == 0 and
    probe.get("schema_version") == "fleetrmw.rmw_topic_type_hash_probe.v1" and
    probe.get("status") == "ok" and
    probe.get("expected_hash_available") is True and
    probe.get("expected_hash_version", -1) > 0 and
    probe.get("publisher_hash_matches") is True and
    probe.get("subscription_hash_matches") is True and
    stderr == ""
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
    summary: dict[str, Any] = json.loads(lines[-1])
    summary["docker_returncode"] = result.returncode
    summary["docker_stderr"] = result.stderr
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
