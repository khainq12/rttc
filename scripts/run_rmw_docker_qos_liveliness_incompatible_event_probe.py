"""Build and repeat liveliness kind/lease incompatible QoS event production."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_shared_memory_probe import parse_last_json


SCHEMA_VERSION = "fleetrmw.docker_qos_liveliness_incompatible_event_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"


def parse_json_rows(stdout: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def probe_ok(probe: dict[str, Any]) -> bool:
    return (
        probe.get("status") == "ok"
        and probe.get("liveliness_kind_offered_event_claim") is True
        and probe.get("liveliness_kind_requested_event_claim") is True
        and probe.get("liveliness_kind_automatic_vs_manual_node_offered_claim") is True
        and probe.get("liveliness_kind_automatic_vs_manual_node_requested_claim") is True
        and probe.get("liveliness_kind_manual_node_vs_manual_topic_offered_claim") is True
        and probe.get("liveliness_kind_manual_node_vs_manual_topic_requested_claim") is True
        and probe.get("liveliness_slow_lease_offered_event_claim") is True
        and probe.get("liveliness_slow_lease_requested_event_claim") is True
        and probe.get("liveliness_missing_lease_offered_event_claim") is True
        and probe.get("liveliness_missing_lease_requested_event_claim") is True
        and probe.get("liveliness_compatible_control_claim") is True
        and probe.get("scenario_count") == 11
        and probe.get("incompatible_event_count") == 10
        and int(probe.get("callback_events", 0)) >= 10
        and probe.get("clean_teardown") is True
    )


def run_probe(*, image: str, iterations: int) -> dict[str, Any]:
    run_count = max(iterations, 1)
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        "rm -rf /tmp/fq-live-incompat-build /tmp/fq-live-incompat-install "
        "/tmp/fq-live-incompat-log && "
        "colcon --log-base /tmp/fq-live-incompat-log build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp --build-base /tmp/fq-live-incompat-build "
        "--install-base /tmp/fq-live-incompat-install --cmake-args -DCMAKE_BUILD_TYPE=Release "
        ">/dev/null && source /tmp/fq-live-incompat-install/setup.bash && "
        f"for i in $(seq 1 {run_count}); do "
        "/tmp/fq-live-incompat-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_qos_liveliness_incompatible_event_probe || exit $?; done"
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    rows = parse_json_rows(completed.stdout)
    probe = rows[-1] if rows else parse_last_json(completed.stdout)
    ok_run_count = sum(probe_ok(row) for row in rows)
    ok = (
        completed.returncode == 0
        and len(rows) == run_count
        and ok_run_count == run_count
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "image": image,
        "returncode": completed.returncode,
        "run_count": run_count,
        "ok_run_count": ok_run_count,
        "qos_liveliness_incompatible_event_production_claim": ok,
        "qos_liveliness_incompatible_event_repeated_claim": ok and run_count >= 5,
        "liveliness_kind_offered_event_claim": probe.get(
            "liveliness_kind_offered_event_claim"
        ),
        "liveliness_kind_requested_event_claim": probe.get(
            "liveliness_kind_requested_event_claim"
        ),
        "liveliness_kind_automatic_vs_manual_node_offered_claim": probe.get(
            "liveliness_kind_automatic_vs_manual_node_offered_claim"
        ),
        "liveliness_kind_automatic_vs_manual_node_requested_claim": probe.get(
            "liveliness_kind_automatic_vs_manual_node_requested_claim"
        ),
        "liveliness_kind_manual_node_vs_manual_topic_offered_claim": probe.get(
            "liveliness_kind_manual_node_vs_manual_topic_offered_claim"
        ),
        "liveliness_kind_manual_node_vs_manual_topic_requested_claim": probe.get(
            "liveliness_kind_manual_node_vs_manual_topic_requested_claim"
        ),
        "liveliness_slow_lease_offered_event_claim": probe.get(
            "liveliness_slow_lease_offered_event_claim"
        ),
        "liveliness_slow_lease_requested_event_claim": probe.get(
            "liveliness_slow_lease_requested_event_claim"
        ),
        "liveliness_missing_lease_offered_event_claim": probe.get(
            "liveliness_missing_lease_offered_event_claim"
        ),
        "liveliness_missing_lease_requested_event_claim": probe.get(
            "liveliness_missing_lease_requested_event_claim"
        ),
        "liveliness_compatible_control_claim": probe.get(
            "liveliness_compatible_control_claim"
        ),
        "scenario_count": probe.get("scenario_count"),
        "incompatible_event_count": probe.get("incompatible_event_count"),
        "last_policy_kind": probe.get("last_policy_kind"),
        "callback_events": probe.get("callback_events"),
        "clean_teardown": probe.get("clean_teardown"),
        "probe": probe,
        "runs": rows,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_qos_liveliness_incompatible_event_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(image=args.image, iterations=args.iterations)
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"status={summary['status']}")
        print(f"runs={summary.get('ok_run_count', 0)}/{summary.get('run_count', 0)}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
