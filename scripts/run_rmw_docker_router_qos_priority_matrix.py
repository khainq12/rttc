"""Compare FIFO routing with deadline-priority routing for rmw_fleetqox_cpp."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from run_rmw_docker_router_qos_priority_probe import DEFAULT_IMAGE, run_probe


SCHEMA_VERSION = "fleetrmw.rmw_router_qos_priority_matrix.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--summary-json", default="results_rmw_socket/docker_router_qos_priority_matrix_summary.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    summary = run_matrix(root=root, image=args.image)
    summary_path = root / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-qos-priority-matrix")
        print(f"  status: {summary['status']}")
        print(f"  priority_improved: {summary['priority_improved']}")
    return 0 if summary["status"] == "ok" else 1


def run_matrix(*, root: Path, image: str) -> dict[str, Any]:
    scenarios = [
        {
            "name": "fifo_baseline",
            "scheduler_window_ms": 0,
            "expected_order": "fifo",
        },
        {
            "name": "deadline_scheduler",
            # 800ms was tighter than the 5000ms default that
            # router_qos_priority_probe.py itself uses reliably elsewhere --
            # too tight a margin for the bulk/critical containers to both
            # start and publish before the queue's window-timeout flush
            # fires, breaking priority ordering under test.
            "scheduler_window_ms": 5000,
            "expected_order": "priority",
        },
    ]
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = run_probe(
            root=root,
            image=image,
            bulk_topic=f"/fleetqox/{scenario['name']}/bulk",
            critical_topic=f"/fleetqox/{scenario['name']}/critical",
            bulk_deadline_ms=500,
            critical_deadline_ms=20,
            scheduler_window_ms=int(scenario["scheduler_window_ms"]),
            expected_order=str(scenario["expected_order"]),
        )
        rows.append({"scenario": scenario, "result": result})

    fifo_topics = rows[0]["result"].get("router", {}).get("forwarded_topics", [])
    scheduler_topics = rows[1]["result"].get("router", {}).get("forwarded_topics", [])
    priority_improved = (
        len(fifo_topics) >= 2 and
        len(scheduler_topics) >= 2 and
        fifo_topics[0].endswith("/bulk") and
        scheduler_topics[0].endswith("/critical")
    )
    status = all(row["result"].get("status") == "ok" for row in rows) and priority_improved
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if status else "failed",
        "priority_improved": priority_improved,
        "rows": rows,
        "fifo_forwarded_topics": fifo_topics,
        "scheduler_forwarded_topics": scheduler_topics,
    }


if __name__ == "__main__":
    raise SystemExit(main())
