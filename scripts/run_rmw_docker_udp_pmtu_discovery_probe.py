"""Prove automatic UDP path-MTU discovery for the socket transport.

Lowers the container's own loopback MTU (requires NET_ADMIN) and checks
that a payload exceeding the resulting path MTU is rejected via the
newly discovered budget rather than an opaque kernel EMSGSIZE, and that a
small payload sent afterwards still succeeds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


SCHEMA_VERSION = "fleetrmw.rmw_docker_udp_pmtu_discovery_probe.v1"
PROBE_SCHEMA_VERSION = "fleetrmw.rmw_udp_pmtu_discovery_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
LOOPBACK_MTU = 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_udp_pmtu_discovery_probe_summary.json",
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
        print("fleetrmw-udp-pmtu-discovery-probe")
        print(f"  status: {summary['status']}")
        probe = summary.get("probe", {})
        print(f"  oversized_payload_rejected: {probe.get('oversized_payload_rejected')}")
        print(f"  udp_pmtu_discovered_min_bytes: {probe.get('udp_pmtu_discovered_min_bytes')}")
        print(
            "  small_payload_after_discovery_succeeded: "
            f"{probe.get('small_payload_after_discovery_succeeded')}"
        )
    return 0 if summary["status"] == "ok" else 1


def run_probe(*, root: Path, image: str) -> dict[str, Any]:
    command = f"""
source /opt/ros/jazzy/setup.bash
ip link set dev lo mtu {LOOPBACK_MTU}
rm -rf /tmp/fleetrmw_pmtu_build /tmp/fleetrmw_pmtu_install /tmp/fleetrmw_pmtu_log
colcon --log-base /tmp/fleetrmw_pmtu_log build \\
  --base-paths ros2_ws/src \\
  --packages-select fleetrmw_interfaces rmw_fleetqox_cpp \\
  --build-base /tmp/fleetrmw_pmtu_build \\
  --install-base /tmp/fleetrmw_pmtu_install \\
  --cmake-args -DCMAKE_BUILD_TYPE=Release >/tmp/fleetrmw_pmtu_build.log 2>&1
build_ret=$?
if [ "$build_ret" -ne 0 ]; then
  cat /tmp/fleetrmw_pmtu_build.log >&2
  exit "$build_ret"
fi
source /tmp/fleetrmw_pmtu_install/setup.bash
export RMW_IMPLEMENTATION=rmw_fleetqox_cpp
/tmp/fleetrmw_pmtu_install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/fleetrmw_udp_pmtu_discovery_probe \\
  > /tmp/fleetrmw_udp_pmtu_probe.out 2> /tmp/fleetrmw_udp_pmtu_probe.err
probe_ret=$?
PROBE_RET="$probe_ret" python3 - <<'PY'
import json
import os
from pathlib import Path

stdout = Path("/tmp/fleetrmw_udp_pmtu_probe.out").read_text()
stderr = Path("/tmp/fleetrmw_udp_pmtu_probe.err").read_text()
probe = {{}}
for line in reversed(stdout.splitlines()):
    stripped = line.strip()
    if stripped.startswith("{{"):
        try:
            probe = json.loads(stripped)
        except json.JSONDecodeError:
            probe = {{"status": "parse_failed", "raw": stripped}}
        break
if not probe:
    probe = {{"status": "missing", "raw_stdout": stdout}}

summary = {{
    "schema_version": "{SCHEMA_VERSION}",
    "status": "pending",
    "probe": probe,
    "probe_stdout": stdout,
    "probe_stderr": stderr,
    "probe_returncode": int(os.environ["PROBE_RET"]),
    "loopback_mtu": {LOOPBACK_MTU},
}}
summary["status"] = "ok" if (
    summary["probe_returncode"] == 0 and
    probe.get("schema_version") == "{PROBE_SCHEMA_VERSION}" and
    probe.get("status") == "ok" and
    probe.get("oversized_payload_rejected") is True and
    probe.get("udp_pmtu_discovery_events", 0) >= 1 and
    0 < probe.get("udp_pmtu_discovered_min_bytes", 0) < {LOOPBACK_MTU} and
    probe.get("small_payload_after_discovery_succeeded") is True
) else "failed"
print(json.dumps(summary, sort_keys=True))
PY
"""
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--cap-add", "NET_ADMIN",
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
