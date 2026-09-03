"""Measure FleetRMW repair-capacity versus fleet QoE at 8/16/32 robots."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_router_multi_robot_budgeted_fleet_plan_probe import (
    DEFAULT_IMAGE,
    run_probe,
)


SCHEMA_VERSION = "fleetrmw.fleet_repair_capacity_frontier.v1"
RUNNER_SEMANTICS_VERSION = "fleetrmw.fleet_repair_capacity_frontier.actuated_repair.v3"
REPAIR_BYTES = 700
BUILD_PATHS = (
    ".tmp_fleetrmw_budget_plan_build",
    ".tmp_fleetrmw_budget_plan_install",
    ".tmp_fleetrmw_budget_plan_log",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-counts", default="8,16,32")
    parser.add_argument("--repetitions", default="7,13,29")
    parser.add_argument("--capacity-fractions", default="0.25,0.5,1.0")
    parser.add_argument("--deadline-ms", type=int, default=400)
    parser.add_argument("--loss-percent", type=float, default=0.0)
    parser.add_argument(
        "--summary-json",
        default="results_rmw_socket/docker_fleet_repair_capacity_frontier_summary.json",
    )
    parser.add_argument(
        "--markdown",
        default="results_rmw_socket/docker_fleet_repair_capacity_frontier_report.md",
    )
    parser.add_argument(
        "--resume-summary",
        type=Path,
        help="reuse and reclassify robot/repetition/capacity rows from an earlier campaign",
    )
    parser.add_argument(
        "--force-repetitions",
        default="",
        help="comma-separated repetition IDs that must be rerun even when resumable",
    )
    parser.add_argument("--fail-on-row-failure", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_frontier(
        root=ROOT,
        image=args.image,
        robot_counts=parse_ints(args.robot_counts),
        repetitions=parse_ints(args.repetitions, minimum=0),
        capacity_fractions=parse_floats(args.capacity_fractions),
        deadline_ms=max(args.deadline_ms, 1),
        loss_percent=max(args.loss_percent, 0.0),
        prior_rows=load_prior_rows(args.resume_summary),
        force_repetitions=(
            set(parse_ints(args.force_repetitions, minimum=0))
            if args.force_repetitions.strip()
            else set()
        ),
    )
    summary_path = ROOT / args.summary_json
    report_path = ROOT / args.markdown
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown(summary), encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-fleet-repair-capacity-frontier")
        print(f"  status: {summary['status']}")
        print(f"  runs: {summary['ok_run_count']}/{summary['run_count']} ok")
        print(f"  monotonic_groups: {summary['monotonic_group_count']}/{summary['group_count']}")
        print(f"  summary: {args.summary_json}")
    if args.fail_on_row_failure and summary["status"] != "ok":
        return 1
    return 0 if summary["status"] in {"ok", "partial"} else 1


def run_frontier(
    *,
    root: Path,
    image: str,
    robot_counts: list[int],
    repetitions: list[int],
    capacity_fractions: list[float],
    deadline_ms: int,
    loss_percent: float,
    prior_rows: list[dict[str, Any]] | None = None,
    force_repetitions: set[int] | None = None,
) -> dict[str, Any]:
    cleanup_build(root, image)
    rows: list[dict[str, Any]] = []
    prior_index = {
        (
            int(row.get("robot_count", 0)),
            int(row.get("repetition_id", -1)),
            int(row.get("capacity_bytes", 0)),
        ): row
        for row in (prior_rows or [])
        if reusable_prior_row(row)
    }
    forced = force_repetitions or set()
    try:
        for robot_count in robot_counts:
            protected_count = max(1, robot_count // 2)
            for repetition_id in repetitions:
                for fraction in capacity_fractions:
                    admitted_slots = min(
                        protected_count,
                        max(1, math.ceil(protected_count * fraction)),
                    )
                    capacity_bytes = admitted_slots * REPAIR_BYTES
                    run_key = (robot_count, repetition_id, capacity_bytes)
                    prior = prior_index.get(run_key)
                    if prior is not None and repetition_id not in forced:
                        rows.append(frontier_row(
                            result=as_dict(prior.get("result")),
                            robot_count=robot_count,
                            protected_count=protected_count,
                            repetition_id=repetition_id,
                            capacity_fraction=fraction,
                            capacity_bytes=capacity_bytes,
                            admitted_slots=admitted_slots,
                        ))
                        print(f"reuse frontier {run_key}", file=sys.stderr, flush=True)
                        continue
                    print(f"run frontier {run_key}", file=sys.stderr, flush=True)
                    result = run_probe(
                        root=root,
                        image=image,
                        robot_count=robot_count,
                        protected_robot_budget=protected_count,
                        topic_prefix=(
                            "/fleetqox/repair_frontier/"
                            f"robots-{robot_count}/rep-{repetition_id}/capacity-{capacity_bytes}"
                        ),
                        deadline_ms=deadline_ms,
                        primary_profile="roaming",
                        backup_profile="wifi",
                        loss_percent=loss_percent,
                        qoe_migration=True,
                        event_triggered_feedback=True,
                        sequential_qoe_feedback=True,
                        sequential_min_samples=1,
                        sequential_max_samples=1,
                        sequential_separation_margin=10.0,
                        sequential_confidence_fallback=True,
                        sequential_fallback_extra_robots=protected_count,
                        fallback_recovery_samples=1,
                        fallback_repair_budget=protected_count * 4,
                        fallback_repair_min_interval_ms=50,
                        fallback_repair_max_attempts_per_sequence=1,
                        fleet_repair_capacity_bytes=capacity_bytes,
                        force_primary_drop_sequence_two=True,
                        repair_capacity_fault=True,
                        reuse_build=True,
                    )
                    rows.append(frontier_row(
                        result=result,
                        robot_count=robot_count,
                        protected_count=protected_count,
                        repetition_id=repetition_id,
                        capacity_fraction=fraction,
                        capacity_bytes=capacity_bytes,
                        admitted_slots=admitted_slots,
                    ))
    finally:
        cleanup_build(root, image)

    grouped = aggregate_rows(rows)
    monotonic_groups = sum(1 for group in grouped if group["monotonic"])
    ok_count = sum(1 for row in rows if row["status"] == "ok")
    admission_ok_count = sum(1 for row in rows if row["admission_ok"])
    repair_actuation_ok_count = sum(1 for row in rows if row["repair_actuation_ok"])
    status = "ok" if rows and ok_count == len(rows) and monotonic_groups == len(grouped) else "partial"
    if rows and ok_count == 0:
        status = "failed"
    return {
        "schema_version": SCHEMA_VERSION,
        "runner_semantics_version": RUNNER_SEMANTICS_VERSION,
        "status": status,
        "image": image,
        "robot_counts": robot_counts,
        "repetitions": repetitions,
        "capacity_fractions": capacity_fractions,
        "repair_bytes": REPAIR_BYTES,
        "deadline_ms": deadline_ms,
        "loss_percent": loss_percent,
        "seed_semantics": "repetition_id_only; tc netem random seed is not exposed",
        "execution_mode": "concurrent_containers_per_robot",
        "frontier_mode": "shared_budget_admission_actuated_repair_qoe_frontier",
        "run_count": len(rows),
        "ok_run_count": ok_count,
        "admission_ok_run_count": admission_ok_count,
        "repair_actuation_ok_run_count": repair_actuation_ok_count,
        "live_qoe_ok_run_count": repair_actuation_ok_count,
        "failed_run_count": len(rows) - ok_count,
        "group_count": len(grouped),
        "monotonic_group_count": monotonic_groups,
        "frontier": grouped,
        "runs": rows,
    }


def frontier_row(
    *,
    result: dict[str, Any],
    robot_count: int,
    protected_count: int,
    repetition_id: int,
    capacity_fraction: float,
    capacity_bytes: int,
    admitted_slots: int,
) -> dict[str, Any]:
    schedule = as_dict(result.get("fleet_repair_schedule"))
    fallback = as_dict(result.get("fallback_repair"))
    admitted_count = int(schedule.get("admitted_count", 0))
    deferred_count = int(schedule.get("deferred_count", 0))
    allocated_bytes = int(schedule.get("allocated_bytes", 0))
    live_repair_qualified = int(result.get("repair_deadline_robots_ok", 0))
    qoe_recovery_ok = bool(result.get("qoe_recovery_ok", False))
    admission_ok = (
        admitted_count == admitted_slots
        and deferred_count == protected_count - admitted_slots
        and allocated_bytes <= capacity_bytes
    )
    decisions = [item for item in schedule.get("decisions", []) if isinstance(item, dict)]
    admitted_ids = {
        str(item.get("robot_id", "")) for item in decisions if item.get("action") == "repair"
    }
    deferred_ids = {
        str(item.get("robot_id", "")) for item in decisions if item.get("action") != "repair"
    }
    repair_rows = {
        str(item.get("robot_id", "")): item
        for item in fallback.get("robots", [])
        if isinstance(item, dict)
    }
    # A NACK-triggered repair round trip (detect the gap, request it,
    # relay it, retransmit it) inherently takes longer than a frame that
    # was never dropped, so "repaired_late" is the expected outcome of a
    # successful repair, not a failure -- only "unresolved" (still
    # missing) represents an actual repair failure here.
    actuated_ids = {
        robot_id
        for robot_id in admitted_ids
        if bool(repair_rows.get(robot_id, {}).get("repair_evidence"))
        and repair_rows.get(robot_id, {}).get("status") in (
            "repaired_on_time", "repaired_late",
        )
        and int(repair_rows.get(robot_id, {}).get("publisher_repair_plan_frames", 0)) >= 1
    }
    deferred_evidence_ids = {
        robot_id
        for robot_id in deferred_ids
        if repair_rows.get(robot_id, {}).get("status") == "unresolved"
        and 2 in repair_rows.get(robot_id, {}).get("missing_sequences", [])
        and int(repair_rows.get(robot_id, {}).get("publisher_repair_not_admitted", 0)) >= 1
    }
    expected_live_qualified = robot_count - deferred_count
    repair_actuation_ok = (
        result.get("status") == "ok"
        and bool(result.get("repair_capacity_fault"))
        and bool(result.get("repair_capacity_outcome_ok"))
        and len(actuated_ids) == admitted_count
        and len(deferred_evidence_ids) == deferred_count
        and live_repair_qualified == expected_live_qualified
    )
    admission_ratio = admitted_count / protected_count if protected_count else 0.0
    repair_ratio = len(actuated_ids) / protected_count if protected_count else 0.0
    live_qoe_ratio = live_repair_qualified / robot_count if robot_count else 0.0
    return {
        "schema_version": "fleetrmw.fleet_repair_capacity_frontier.run.v3",
        "runner_semantics_version": RUNNER_SEMANTICS_VERSION,
        "status": "ok" if admission_ok and repair_actuation_ok else "failed",
        "admission_ok": admission_ok,
        "repair_actuation_ok": repair_actuation_ok,
        "live_qoe_ok": repair_actuation_ok,
        "robot_count": robot_count,
        "protected_robot_count": protected_count,
        "repetition_id": repetition_id,
        "capacity_fraction": capacity_fraction,
        "capacity_bytes": capacity_bytes,
        "expected_admitted_count": admitted_slots,
        "admitted_count": admitted_count,
        "deferred_count": deferred_count,
        "allocated_bytes": allocated_bytes,
        "repair_delivery_robots_ok": int(result.get("repair_delivery_robots_ok", 0)),
        "live_repair_deadline_robots_ok": live_repair_qualified,
        "live_qoe_qualified_ratio": live_qoe_ratio,
        "repair_deadline_robots_ok": len(actuated_ids),
        "repair_actuated_count": len(actuated_ids),
        "deferred_evidence_robot_count": len(deferred_evidence_ids),
        "repair_admission_qualified_ratio": admission_ratio,
        "repair_qualified_ratio": repair_ratio,
        "qoe_recovery_ok": qoe_recovery_ok,
        "repair_not_admitted_count": int(fallback.get("repair_not_admitted_count", 0)),
        "repair_path_transmission_overhead": int(
            result.get("repair_path_transmission_overhead", 0)
        ),
        "total_nack_retransmissions": int(result.get("total_nack_retransmissions", 0)),
        "max_latency_ms": float(result.get("max_latency_ms", 0.0)),
        "result_status": result.get("status"),
        "result": result,
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = sorted({(row["robot_count"], row["capacity_bytes"]) for row in rows})
    aggregates: list[dict[str, Any]] = []
    previous_by_robot: dict[int, tuple[float, float, float]] = {}
    for robot_count, capacity_bytes in keys:
        selected = [
            row for row in rows
            if row["robot_count"] == robot_count and row["capacity_bytes"] == capacity_bytes
        ]
        mean_admitted = statistics.fmean(row["admitted_count"] for row in selected)
        mean_ratio = statistics.fmean(row["repair_qualified_ratio"] for row in selected)
        admitted_stats = metric_summary(selected, "admitted_count", lower_bound=0.0)
        ratio_stats = metric_summary(
            selected,
            "repair_qualified_ratio",
            lower_bound=0.0,
            upper_bound=1.0,
        )
        live_qoe_stats = metric_summary(
            selected,
            "live_qoe_qualified_ratio",
            lower_bound=0.0,
            upper_bound=1.0,
        )
        overhead_stats = metric_summary(
            selected, "repair_path_transmission_overhead", lower_bound=0.0
        )
        latency_stats = metric_summary(selected, "max_latency_ms", lower_bound=0.0)
        previous = previous_by_robot.get(robot_count)
        monotonic = previous is None or (
            mean_admitted >= previous[0]
            and mean_ratio >= previous[1]
            and float(live_qoe_stats["mean"]) >= previous[2]
        )
        previous_by_robot[robot_count] = (
            mean_admitted,
            mean_ratio,
            float(live_qoe_stats["mean"]),
        )
        aggregates.append({
            "robot_count": robot_count,
            "capacity_bytes": capacity_bytes,
            "run_count": len(selected),
            "ok_run_count": sum(1 for row in selected if row["status"] == "ok"),
            "admission_ok_run_count": sum(1 for row in selected if row["admission_ok"]),
            "repair_actuation_ok_run_count": sum(
                1 for row in selected if row["repair_actuation_ok"]
            ),
            "live_qoe_ok_run_count": sum(1 for row in selected if row["live_qoe_ok"]),
            "admitted_count_mean": mean_admitted,
            "admitted_count_ci95_low": admitted_stats["ci95_low"],
            "admitted_count_ci95_high": admitted_stats["ci95_high"],
            "admission_qualified_ratio_mean": mean_ratio,
            "admission_qualified_ratio_ci95_low": ratio_stats["ci95_low"],
            "admission_qualified_ratio_ci95_high": ratio_stats["ci95_high"],
            "repair_qualified_ratio_mean": mean_ratio,
            "live_qoe_qualified_ratio_mean": live_qoe_stats["mean"],
            "live_qoe_qualified_ratio_ci95_low": live_qoe_stats["ci95_low"],
            "live_qoe_qualified_ratio_ci95_high": live_qoe_stats["ci95_high"],
            "repair_overhead_mean": overhead_stats["mean"],
            "repair_overhead_ci95_low": overhead_stats["ci95_low"],
            "repair_overhead_ci95_high": overhead_stats["ci95_high"],
            "max_latency_ms_mean": latency_stats["mean"],
            "max_latency_ms_ci95_low": latency_stats["ci95_low"],
            "max_latency_ms_ci95_high": latency_stats["ci95_high"],
            "monotonic": monotonic,
        })
    return aggregates


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# FleetRMW Fleet Repair Capacity-QoE Frontier",
        "",
        f"Status: `{summary['status']}`; actuated-repair runs: `{summary['ok_run_count']}/{summary['run_count']}`; admission runs: `{summary['admission_ok_run_count']}/{summary['run_count']}`.",
        "",
        "| robots | capacity bytes | repair actuation OK | admission OK | admitted [95% CI] | admission-qualified ratio [95% CI] | live QoE-qualified ratio [95% CI] | live repair overhead [95% CI] | max latency ms [95% CI] | monotonic |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["frontier"]:
        lines.append(
            f"| {row['robot_count']} | {row['capacity_bytes']} | "
            f"{row['ok_run_count']}/{row['run_count']} | "
            f"{row['admission_ok_run_count']}/{row['run_count']} | "
            f"{format_ci(row, 'admitted_count', 2)} | "
            f"{format_ci(row, 'admission_qualified_ratio', 4)} | "
            f"{format_ci(row, 'live_qoe_qualified_ratio', 4)} | "
            f"{format_ci(row, 'repair_overhead', 2)} | "
            f"{format_ci(row, 'max_latency_ms', 3)} | "
            f"{'yes' if row['monotonic'] else 'no'} |"
        )
    lines.extend([
        "",
        "A row passes only when admitted gaps are repaired on time, deferred gaps are observably rejected, and unaffected robots remain healthy.",
        "The live QoE-qualified ratio therefore rises with capacity; it is not forced to 100% when the shared repair budget defers candidates.",
        "",
    ])
    return "\n".join(lines)


def metric_summary(
    rows: list[dict[str, Any]],
    key: str,
    *,
    lower_bound: float | None = None,
    upper_bound: float | None = None,
) -> dict[str, float | int]:
    values = [float(row[key]) for row in rows]
    if not values:
        return {"count": 0, "mean": 0.0, "ci95_low": 0.0, "ci95_high": 0.0}
    average = statistics.fmean(values)
    if len(values) == 1:
        low = high = average
    else:
        critical = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571}.get(
            len(values) - 1,
            1.96,
        )
        margin = critical * statistics.stdev(values) / math.sqrt(len(values))
        low, high = average - margin, average + margin
    if lower_bound is not None:
        low = max(lower_bound, low)
    if upper_bound is not None:
        high = min(upper_bound, high)
    return {"count": len(values), "mean": average, "ci95_low": low, "ci95_high": high}


def format_ci(row: dict[str, Any], prefix: str, precision: int) -> str:
    return (
        f"{row[f'{prefix}_mean']:.{precision}f} "
        f"[{row[f'{prefix}_ci95_low']:.{precision}f}, "
        f"{row[f'{prefix}_ci95_high']:.{precision}f}]"
    )


def cleanup_build(root: Path, image: str) -> None:
    joined = " ".join(f"/work/{path}" for path in BUILD_PATHS)
    subprocess.run(
        [
            "docker", "run", "--rm", "--entrypoint", "bash",
            "-v", f"{root}:/work", "-w", "/work", image, "-lc", f"rm -rf {joined}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_ints(value: str, minimum: int = 1) -> list[int]:
    parsed = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not parsed or any(item < minimum for item in parsed):
        raise SystemExit(f"expected comma-separated integers >= {minimum}")
    return list(dict.fromkeys(parsed))


def parse_floats(value: str) -> list[float]:
    parsed = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not parsed or any(item <= 0.0 or item > 1.0 for item in parsed):
        raise SystemExit("capacity fractions must be in (0, 1]")
    return sorted(set(parsed))


def as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def reusable_prior_row(row: dict[str, Any]) -> bool:
    result = row.get("result")
    return (
        row.get("runner_semantics_version") == RUNNER_SEMANTICS_VERSION
        and isinstance(result, dict)
        and bool(result.get("repair_capacity_fault"))
    )


def load_prior_rows(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("runs", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)]


if __name__ == "__main__":
    raise SystemExit(main())
