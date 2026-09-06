"""Build and repeat per-@key-instance retransmit ledger bounding coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "fleetrmw.docker_keyed_instance_retransmit_probe.v1"
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
        and row.get("publish_ok") is True
        and row.get("per_instance_bounding_observed") is True
        and int(row.get("distinct_instances", 0)) == 2
        and int(row.get("max_entries_per_instance", 0)) == 3
        and int(row.get("total_entries", 0)) == 6
        and row.get("keyless_publish_ok") is True
        and row.get("keyless_behavior_unchanged") is True
        and int(row.get("keyless_distinct_instances", 0)) == 1
        and int(row.get("keyless_total_entries", 0)) == 3
    )


def run_probe(*, root: Path, image: str, iterations: int) -> dict[str, Any]:
    run_count = max(iterations, 1)
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        "rm -rf /tmp/fq-keyed-instance-retransmit-build "
        "/tmp/fq-keyed-instance-retransmit-install "
        "/tmp/fq-keyed-instance-retransmit-log && "
        "colcon --log-base /tmp/fq-keyed-instance-retransmit-log build "
        "--base-paths ros2_ws/src "
        "--packages-select fleetrmw_interfaces rmw_fleetqox_cpp "
        "--build-base /tmp/fq-keyed-instance-retransmit-build "
        "--install-base /tmp/fq-keyed-instance-retransmit-install "
        "--cmake-args -DCMAKE_BUILD_TYPE=Release >/dev/null && "
        "source /tmp/fq-keyed-instance-retransmit-install/setup.bash && "
        f"for i in $(seq 1 {run_count}); do "
        "/tmp/fq-keyed-instance-retransmit-install/rmw_fleetqox_cpp/lib/"
        "rmw_fleetqox_cpp/fleetrmw_keyed_instance_retransmit_probe || exit $?; done"
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
        if row.get("schema_version") == "fleetrmw.keyed_instance_retransmit_probe.v1"
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
        "keyed_instance_scope": (
            "rosidl_typesupport_introspection_c/cpp MessageMember::is_key_ "
            "(the OMG IDL4 @key annotation, only expressible via a "
            "hand-authored .idl -- see KeyedInstanceSample.idl) now scopes "
            "the reliable retransmit ledger's history bound to one "
            "DDS-style instance per @key value, instead of the whole "
            "publisher; a message with no @key field keeps the exact prior "
            "per-publisher-only bound"
        ),
        "reliability_key_instance_bound_claim": ok,
        "per_instance_bounding_observed": last.get("per_instance_bounding_observed"),
        "keyless_behavior_unchanged": last.get("keyless_behavior_unchanged"),
        "total_entries": last.get("total_entries"),
        "distinct_instances": last.get("distinct_instances"),
        "max_entries_per_instance": last.get("max_entries_per_instance"),
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
        default="results_rmw_socket/docker_keyed_instance_retransmit_probe_summary.json",
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
