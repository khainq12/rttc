"""Build and repeat wstring content-filter reflection coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_wstring_content_filter_probe.v1"
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
        and row.get("filter_set_ok") is True
        and row.get("publish_ok") is True
        and row.get("round_trip_ok") is True
        and row.get("surrogate_pair_preserved") is True
        and row.get("no_second_match") is True
        and int(row.get("content_filters_evaluated", 0)) == 2
        and int(row.get("content_filters_matched", 0)) == 1
        and int(row.get("content_filters_dropped", 0)) == 1
    )


def run_probe(*, root: Path, image: str, iterations: int) -> dict[str, Any]:
    run_count = max(iterations, 1)
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        "rm -rf /tmp/fq-wstring-content-filter-build "
        "/tmp/fq-wstring-content-filter-install /tmp/fq-wstring-content-filter-log && "
        "colcon --log-base /tmp/fq-wstring-content-filter-log build "
        "--base-paths ros2_ws/src "
        "--packages-select fleetrmw_interfaces "
        "--build-base /tmp/fq-wstring-content-filter-build "
        "--install-base /tmp/fq-wstring-content-filter-install "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release >/dev/null && "
        # rmw_fleetqox_cpp does not declare fleetrmw_interfaces as a package.xml
        # dependency (most run_rmw_docker_*.py scripts build it alone, without
        # fleetrmw_interfaces, so it can't be a hard colcon dependency). Its
        # find_package(fleetrmw_interfaces QUIET) only succeeds if the install
        # is already sourced -- hence two separate colcon invocations here,
        # not one combined --packages-select.
        "source /tmp/fq-wstring-content-filter-install/setup.bash && "
        "colcon --log-base /tmp/fq-wstring-content-filter-log build "
        "--base-paths ros2_ws/src "
        "--packages-select rmw_fleetqox_cpp "
        "--build-base /tmp/fq-wstring-content-filter-build "
        "--install-base /tmp/fq-wstring-content-filter-install "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release >/dev/null && "
        "source /tmp/fq-wstring-content-filter-install/setup.bash && "
        f"for i in $(seq 1 {run_count}); do "
        "/tmp/fq-wstring-content-filter-install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_wstring_content_filter_probe || exit $?; done"
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
        row
        for row in parse_json_rows(completed.stdout)
        if row.get("schema_version") == "fleetrmw.wstring_content_filter_probe.v1"
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
        "wstring_scope": (
            "ROSIDL introspection C++ WSTRING field round-trip (including a "
            "non-BMP surrogate-pair code point) through serialize/"
            "deserialize, plus content-filter predicate matching against "
            "the field's UTF-16-to-UTF-8 reflected text"
        ),
        "content_filter_wstring_field_claim": ok,
        "round_trip_ok": last.get("round_trip_ok"),
        "surrogate_pair_preserved": last.get("surrogate_pair_preserved"),
        "content_filters_evaluated": last.get("content_filters_evaluated"),
        "content_filters_matched": last.get("content_filters_matched"),
        "content_filters_dropped": last.get("content_filters_dropped"),
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
        default="results_rmw_socket/docker_wstring_content_filter_probe_summary.json",
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
