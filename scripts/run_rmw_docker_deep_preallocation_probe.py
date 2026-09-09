"""Build and repeat the FleetRMW deep-preallocation (frame base64 scratch) probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_deep_preallocation_probe.v1"
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


def probe_ok(row: dict[str, Any]) -> bool:
    return (
        row.get("status") == "ok"
        and row.get("publish_take_ok") is True
        and row.get("base64_scratch_reuse_ok") is True
        and row.get("json_scratch_reuse_ok") is True
        and row.get("retransmit_entry_pool_reuse_ok") is True
        and row.get("cleanup_ok") is True
        and int(row.get("operation_count", 0)) == 8
        and int(row.get("completed_operations", 0)) == 8
        and int(row.get("scratch_capacity_before_any_publish", 999999)) < 256
        and int(row.get("scratch_capacity_after_first_publish", 0)) >= 256
        and row.get("scratch_capacity_after_last_publish")
        == row.get("scratch_capacity_after_first_publish")
        and int(row.get("json_scratch_capacity_after_first_publish", 0)) >
        int(row.get("json_scratch_capacity_before_any_publish", 0))
        and row.get("json_scratch_capacity_after_last_publish")
        == row.get("json_scratch_capacity_after_first_publish")
        and int(row.get("reliable_operation_count", 0)) == 8
        and int(row.get("reliable_completed_operations", 0)) == 8
        and int(row.get("retransmit_pool_hits_after", 0)) >
        int(row.get("retransmit_pool_hits_before", 0))
    )


def run_probe(*, root: Path, image: str, iterations: int) -> dict[str, Any]:
    run_count = max(iterations, 1)
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        "rm -rf /tmp/fq-deep-prealloc-build /tmp/fq-deep-prealloc-install "
        "/tmp/fq-deep-prealloc-log && "
        "colcon --log-base /tmp/fq-deep-prealloc-log build --base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp "
        "--build-base /tmp/fq-deep-prealloc-build "
        "--install-base /tmp/fq-deep-prealloc-install "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release >/dev/null && "
        "source /tmp/fq-deep-prealloc-install/setup.bash && "
        f"for i in $(seq 1 {run_count}); do "
        "/tmp/fq-deep-prealloc-install/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_deep_preallocation_probe || exit $?; done"
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
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
    rows = [
        row for row in parse_json_rows(completed.stdout)
        if row.get("schema_version") == "fleetrmw.deep_preallocation_probe.v1"
    ]
    ok_run_count = sum(probe_ok(row) for row in rows)
    ok = (
        completed.returncode == 0
        and len(rows) == run_count
        and ok_run_count == run_count
    )
    last = rows[-1] if rows else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if ok else "failed",
        "image": image,
        "returncode": completed.returncode,
        "run_count": run_count,
        "ok_run_count": ok_run_count,
        "deep_preallocation_frame_base64_scratch_claim": ok,
        "deep_preallocation_frame_base64_scratch_repeated_claim": ok and run_count >= 5,
        "deep_preallocation_frame_json_scratch_claim": ok,
        "deep_preallocation_retransmit_entry_pool_claim": ok,
        "scratch_capacity_before_any_publish": last.get("scratch_capacity_before_any_publish"),
        "scratch_capacity_after_first_publish": last.get("scratch_capacity_after_first_publish"),
        "scratch_capacity_after_last_publish": last.get("scratch_capacity_after_last_publish"),
        "json_scratch_capacity_before_any_publish": last.get(
            "json_scratch_capacity_before_any_publish"),
        "json_scratch_capacity_after_first_publish": last.get(
            "json_scratch_capacity_after_first_publish"),
        "json_scratch_capacity_after_last_publish": last.get(
            "json_scratch_capacity_after_last_publish"),
        "retransmit_pool_hits_before": last.get("retransmit_pool_hits_before"),
        "retransmit_pool_hits_after": last.get("retransmit_pool_hits_after"),
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
        default="results_rmw_socket/docker_deep_preallocation_probe_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(root=ROOT, image=args.image, iterations=args.iterations)
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
        print(f"runs={summary['ok_run_count']}/{summary['run_count']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
