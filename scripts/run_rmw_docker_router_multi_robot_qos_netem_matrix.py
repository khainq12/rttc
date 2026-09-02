"""Run FleetRMW online deadline scheduling across real tc-netem profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.run_rmw_docker_router_multi_robot_qos_matrix import (
        DEFAULT_IMAGE,
        NETEM_PROFILES,
        run_matrix,
    )
except ModuleNotFoundError:
    from run_rmw_docker_router_multi_robot_qos_matrix import (
        DEFAULT_IMAGE,
        NETEM_PROFILES,
        run_matrix,
    )


SCHEMA_VERSION = "fleetrmw.rmw_router_multi_robot_qos_netem_matrix.v1"
DEFAULT_PROFILES = "wifi,wan,roaming"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--profiles", default=DEFAULT_PROFILES)
    parser.add_argument("--robot-count", type=int, default=8)
    parser.add_argument("--control-deadline-ms", type=int, default=1500)
    parser.add_argument("--state-deadline-ms", type=int, default=5000)
    parser.add_argument("--scheduler-window-ms", type=int, default=1000)
    parser.add_argument("--control-payload-bytes", type=int, default=256)
    parser.add_argument("--state-payload-bytes", type=int, default=30000)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_router_multi_robot_qos_netem_matrix_summary.json",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    profiles = parse_profiles(args.profiles)
    root = Path(__file__).resolve().parents[1]
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        result = run_matrix(
            root=root,
            image=args.image,
            robot_count=max(args.robot_count, 1),
            control_deadline_ms=max(args.control_deadline_ms, 1),
            state_deadline_ms=max(args.state_deadline_ms, 1),
            scheduler_window_ms=max(args.scheduler_window_ms, args.robot_count * 1000),
            control_payload_bytes=max(args.control_payload_bytes, 1),
            state_payload_bytes=max(args.state_payload_bytes, 1),
            netem_profile=profile,
        )
        fifo = result.get("fifo_baseline", {})
        deadline = result.get("deadline_scheduler", {})
        fifo_control_p95 = float(fifo.get("control_take_age_ms", {}).get("p95", 0.0))
        deadline_control_p95 = float(
            deadline.get("control_take_age_ms", {}).get("p95", 0.0)
        )
        row = {
            "profile": profile,
            "status": result.get("status"),
            "netem_qdisc": deadline.get("netem_qdisc", ""),
            "fifo_control_p95_ms": fifo_control_p95,
            "deadline_control_p95_ms": deadline_control_p95,
            "control_p95_reduction_ms": fifo_control_p95 - deadline_control_p95,
            "fifo_state_p95_ms": float(
                fifo.get("state_take_age_ms", {}).get("p95", 0.0)
            ),
            "deadline_state_p95_ms": float(
                deadline.get("state_take_age_ms", {}).get("p95", 0.0)
            ),
            "fifo_deadline_misses": fifo.get("e2e_deadline_misses"),
            "scheduler_deadline_misses": deadline.get("e2e_deadline_misses"),
            "scheduler_urgent_frames": deadline.get("router", {}).get(
                "scheduler_urgent_frames"
            ),
            "scheduler_queued_frames": deadline.get("router", {}).get(
                "scheduler_queued_frames"
            ),
            "scheduler_fairness": deadline.get("router", {}).get(
                "scheduler_deadline_success_jain_index"
            ),
            "result": result,
        }
        row["evidence_ok"] = (
            row["status"] == "ok" and
            "netem" in str(row["netem_qdisc"]) and
            row["scheduler_deadline_misses"] == 0 and
            row["scheduler_urgent_frames"] == max(args.robot_count, 1) and
            row["scheduler_queued_frames"] == max(args.robot_count, 1) and
            row["scheduler_fairness"] == 1
        )
        row["adaptive_selected_policy"] = adaptive_selected_policy(
            row,
            state_deadline_ms=max(args.state_deadline_ms, 1),
        )
        row["adaptive_control_p95_ms"] = (
            row["deadline_control_p95_ms"]
            if row["adaptive_selected_policy"] == "deadline_gated_holdback"
            else row["fifo_control_p95_ms"]
        )
        row["adaptive_state_p95_ms"] = (
            row["deadline_state_p95_ms"]
            if row["adaptive_selected_policy"] == "deadline_gated_holdback"
            else row["fifo_state_p95_ms"]
        )
        row["adaptive_control_p95_reduction_ms"] = (
            row["fifo_control_p95_ms"] - row["adaptive_control_p95_ms"]
        )
        rows.append(row)

    improved_rows = [row for row in rows if row["control_p95_reduction_ms"] > 0.0]
    mean_reduction = (
        sum(float(row["control_p95_reduction_ms"]) for row in rows) / len(rows)
        if rows else 0.0
    )
    adaptive_mean_reduction = (
        sum(float(row["adaptive_control_p95_reduction_ms"]) for row in rows) / len(rows)
        if rows else 0.0
    )
    adaptive_worse_rows = [
        row for row in rows if row["adaptive_control_p95_reduction_ms"] < -1e-9
    ]
    status = (
        "ok"
        if rows and
        all(row["evidence_ok"] for row in rows) and
        improved_rows and
        adaptive_mean_reduction > 0.0 and
        not adaptive_worse_rows
        else "failed"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "image": args.image,
        "profiles": profiles,
        "robot_count": max(args.robot_count, 1),
        "flow_count": max(args.robot_count, 1) * 2,
        "control_deadline_ms": max(args.control_deadline_ms, 1),
        "state_deadline_ms": max(args.state_deadline_ms, 1),
        "control_payload_bytes": max(args.control_payload_bytes, 1),
        "state_payload_bytes": max(args.state_payload_bytes, 1),
        "improved_profile_count": len(improved_rows),
        "mean_control_p95_reduction_ms": mean_reduction,
        "adaptive_worse_profile_count": len(adaptive_worse_rows),
        "adaptive_mean_control_p95_reduction_ms": adaptive_mean_reduction,
        "rows": rows,
    }
    output = root / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-multi-robot-qos-netem-matrix")
        print(f"  status: {status}")
        print(f"  improved_profiles: {len(improved_rows)}/{len(rows)}")
        print(f"  mean_control_p95_reduction_ms: {mean_reduction:.3f}")
        print(f"  adaptive_mean_control_p95_reduction_ms: {adaptive_mean_reduction:.3f}")
    return 0 if status == "ok" else 1


def parse_profiles(value: str) -> list[str]:
    profiles = [item.strip() for item in value.split(",") if item.strip()]
    if not profiles:
        raise ValueError("at least one netem profile is required")
    unknown = [profile for profile in profiles if profile == "none" or profile not in NETEM_PROFILES]
    if unknown:
        raise ValueError(f"unknown or non-netem profiles: {','.join(unknown)}")
    return profiles


def adaptive_selected_policy(row: dict[str, Any], *, state_deadline_ms: int) -> str:
    scheduler_control_not_worse = (
        float(row["deadline_control_p95_ms"]) <= float(row["fifo_control_p95_ms"])
    )
    scheduler_state_admissible = float(row["deadline_state_p95_ms"]) <= float(state_deadline_ms)
    if row["evidence_ok"] and scheduler_control_not_worse and scheduler_state_admissible:
        return "deadline_gated_holdback"
    return "fifo"


if __name__ == "__main__":
    raise SystemExit(main())
