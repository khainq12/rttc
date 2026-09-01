"""Validate budgeted fleet-plan actuation with concurrent ROS 2 robots."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.fleet_optimizer import FleetFlowDemand, FleetOptimizerConfig, RobotQoEState
from fleetqox.live_path_controller import (
    LivePathPlanController,
    LivePathPlanControllerConfig,
    QoEConfidenceFallbackConfig,
    QoESequentialStoppingConfig,
)
from fleetqox.model import FlowClass
from fleetqox.repair_scheduler import (
    FleetRepairSchedule,
    FleetRepairScheduler,
    FleetRepairSchedulerConfig,
    RepairDemand,
    RepairPath,
)
from fleetqox.online_fleet_planner import (
    FleetTopicDemand,
    OnlineFleetPathPlan,
    OnlineFleetPathPlanner,
    OnlineFleetPlannerConfig,
    PathObservation,
)
from scripts.run_rmw_docker_router_multi_robot_proactive_deadline_diversity_probe import (
    fleet_router_command,
    jain_fairness,
)
from scripts.run_rmw_docker_router_proactive_deadline_diversity_probe import (
    docker_shell,
    parse_json_lines,
    parse_last_json,
    qdisc,
    run,
    start_container,
    start_router,
)
from scripts.run_rmw_docker_router_scheduled_reliability_probe import (
    netem_config_for_profile,
)


SCHEMA_VERSION = "fleetrmw.rmw_router_multi_robot_budgeted_fleet_plan_probe.v1"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
DEFAULT_TOPIC_PREFIX = "/fleetqox/budgeted_fleet_plan"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--robot-count", type=int, default=4)
    parser.add_argument("--protected-robot-budget", type=int, default=2)
    parser.add_argument("--topic-prefix", default=DEFAULT_TOPIC_PREFIX)
    parser.add_argument("--deadline-ms", type=int, default=100)
    parser.add_argument("--primary-profile", default="roaming")
    parser.add_argument("--backup-profile", default="wifi")
    parser.add_argument("--loss-percent", type=float, default=0.02)
    parser.add_argument("--epoch-transition", action="store_true")
    parser.add_argument("--qoe-feedback", action="store_true")
    parser.add_argument("--qoe-migration", action="store_true")
    parser.add_argument("--event-triggered-feedback", action="store_true")
    parser.add_argument("--sequential-qoe-feedback", action="store_true")
    parser.add_argument("--sequential-min-samples", type=int, default=3)
    parser.add_argument("--sequential-max-samples", type=int, default=5)
    parser.add_argument("--sequential-confidence-level", type=float, default=0.95)
    parser.add_argument("--sequential-min-sample-stddev", type=float, default=0.005)
    parser.add_argument("--sequential-separation-margin", type=float, default=0.01)
    parser.add_argument("--sequential-migration-hysteresis", type=float, default=0.01)
    parser.add_argument("--sequential-confidence-fallback", action="store_true")
    parser.add_argument("--sequential-fallback-extra-robots", type=int, default=0)
    parser.add_argument("--fallback-recovery-samples", type=int, default=1)
    parser.add_argument("--fallback-repair-budget", type=int, default=16)
    parser.add_argument("--fallback-repair-min-interval-ms", type=int, default=50)
    parser.add_argument("--fallback-repair-max-attempts-per-sequence", type=int, default=2)
    parser.add_argument("--fleet-repair-capacity-bytes", type=int, default=0)
    parser.add_argument("--force-primary-drop-sequence-two", action="store_true")
    parser.add_argument(
        "--repair-capacity-fault",
        action="store_true",
        help="drop sequence 2 once on both paths for scheduled repair candidates",
    )
    parser.add_argument(
        "--summary-json",
        default=(
            "results_rmw_socket/"
            "docker_router_multi_robot_budgeted_fleet_plan_probe_summary.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_probe(
        root=ROOT,
        image=args.image,
        robot_count=max(args.robot_count, 1),
        protected_robot_budget=max(args.protected_robot_budget, 0),
        topic_prefix=args.topic_prefix,
        deadline_ms=max(args.deadline_ms, 1),
        primary_profile=args.primary_profile,
        backup_profile=args.backup_profile,
        loss_percent=max(args.loss_percent, 0.0),
        epoch_transition=args.epoch_transition,
        qoe_feedback=args.qoe_feedback,
        qoe_migration=args.qoe_migration,
        event_triggered_feedback=args.event_triggered_feedback,
        sequential_qoe_feedback=args.sequential_qoe_feedback,
        sequential_min_samples=max(args.sequential_min_samples, 1),
        sequential_max_samples=max(args.sequential_max_samples, 1),
        sequential_confidence_level=args.sequential_confidence_level,
        sequential_min_sample_stddev=max(args.sequential_min_sample_stddev, 0.0),
        sequential_separation_margin=max(args.sequential_separation_margin, 0.0),
        sequential_migration_hysteresis=max(args.sequential_migration_hysteresis, 0.0),
        sequential_confidence_fallback=args.sequential_confidence_fallback,
        sequential_fallback_extra_robots=max(args.sequential_fallback_extra_robots, 0),
        fallback_recovery_samples=max(args.fallback_recovery_samples, 1),
        fallback_repair_budget=max(args.fallback_repair_budget, 0),
        fallback_repair_min_interval_ms=max(args.fallback_repair_min_interval_ms, 0),
        fallback_repair_max_attempts_per_sequence=max(
            args.fallback_repair_max_attempts_per_sequence,
            0,
        ),
        fleet_repair_capacity_bytes=max(args.fleet_repair_capacity_bytes, 0),
        force_primary_drop_sequence_two=args.force_primary_drop_sequence_two,
        repair_capacity_fault=args.repair_capacity_fault,
    )
    output = ROOT / args.summary_json
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print("fleetrmw-router-multi-robot-budgeted-fleet-plan-probe")
        print(f"  status: {summary['status']}")
        print(f"  robots_ok: {summary.get('robots_ok')}/{summary.get('robot_count')}")
        print(f"  protected_robots: {summary.get('protected_robots')}")
        print(f"  actual_path_transmissions: {summary.get('actual_path_transmissions')}")
        print(f"  path_transmission_reduction_ratio: {summary.get('path_transmission_reduction_ratio')}")
    return 0 if summary["status"] == "ok" else 1


def build_budgeted_plan(
    *,
    robot_count: int,
    topics: list[str],
    deadline_ms: int,
    protected_robot_budget: int,
    payload_bytes: int = 700,
) -> OnlineFleetPathPlan:
    if robot_count <= 0 or len(topics) != robot_count:
        raise ValueError("topics must contain exactly one entry per robot")
    protected_count = min(max(protected_robot_budget, 0), robot_count)
    demands = [
        FleetTopicDemand(
            topic,
            FleetFlowDemand(
                flow_id=f"robot_{index:04d}/control",
                robot_id=f"robot_{index:04d}",
                flow_class=FlowClass.CONTROL,
                deadline_ms=float(deadline_ms),
                payload_bytes=payload_bytes,
                rate_hz=20.0,
                criticality=1.0,
            ),
        )
        for index, topic in enumerate(topics)
    ]
    robot_states = [
        RobotQoEState(
            robot_id=f"robot_{index:04d}",
            control_delivery_ratio=0.78 if index < protected_count else 0.99,
            deadline_miss_ratio=0.22 if index < protected_count else 0.0,
            qoe_score=0.72 if index < protected_count else 0.98,
        )
        for index in range(robot_count)
    ]
    planner = OnlineFleetPathPlanner(
        OnlineFleetPlannerConfig(
            optimizer=FleetOptimizerConfig(
                capacity_bytes_per_tick=robot_count * payload_bytes + protected_count * payload_bytes,
                redundancy_budget_bytes_per_tick=protected_count * payload_bytes,
                redundant_deadline_ms=float(deadline_ms),
                redundancy_risk_threshold=0.0,
                require_failure_domain_diversity=True,
            ),
            telemetry_alpha=1.0,
            min_dwell_ticks=0,
        )
    )
    return planner.update(
        tick=0,
        observations=[
            PathObservation(
                "backup_5g",
                latency_ms=24.0,
                jitter_ms=5.0,
                loss=0.02,
                nack_rate=0.01,
                deadline_miss_ratio=0.02,
                bandwidth_utilization=0.35,
                failure_domain="private_5g_core",
            ),
            PathObservation(
                "primary_wifi",
                latency_ms=70.0,
                jitter_ms=18.0,
                loss=0.08,
                nack_rate=0.06,
                deadline_miss_ratio=0.18,
                bandwidth_utilization=0.68,
                failure_domain="warehouse_wifi",
            ),
        ],
        demands=demands,
        robot_states=robot_states,
    )


def build_qoe_feedback_controller(
    *,
    plan_file: Path,
    repair_plan_file: Path | None = None,
    telemetry_paths: list[Path],
    topics: list[str],
    deadline_ms: int,
    protected_robot_budget: int,
    payload_bytes: int = 700,
) -> LivePathPlanController:
    robot_count = len(topics)
    protected_count = min(max(protected_robot_budget, 0), robot_count)
    demands = tuple(
        FleetTopicDemand(
            topic,
            FleetFlowDemand(
                flow_id=f"robot_{index:04d}/control",
                robot_id=f"robot_{index:04d}",
                flow_class=FlowClass.CONTROL,
                deadline_ms=float(deadline_ms),
                payload_bytes=payload_bytes,
                rate_hz=20.0,
                criticality=1.0,
            ),
        )
        for index, topic in enumerate(topics)
    )
    return LivePathPlanController(
        LivePathPlanControllerConfig(
            plan_file=plan_file,
            repair_plan_file=repair_plan_file,
            telemetry_files=(),
            subscriber_telemetry_files=tuple(telemetry_paths),
            demands=demands,
            seed_observations=qoe_path_observations(primary_degraded=True),
            optimizer=FleetOptimizerConfig(
                capacity_bytes_per_tick=robot_count * payload_bytes + protected_count * payload_bytes,
                redundancy_budget_bytes_per_tick=protected_count * payload_bytes,
                redundant_deadline_ms=float(deadline_ms),
                redundancy_risk_threshold=0.0,
                require_failure_domain_diversity=True,
            ),
            telemetry_alpha=1.0,
            min_dwell_ticks=0,
        )
    )


def qoe_path_observations(*, primary_degraded: bool) -> tuple[PathObservation, ...]:
    healthy = {
        "latency_ms": 24.0,
        "jitter_ms": 5.0,
        "loss": 0.02,
        "nack_rate": 0.01,
        "deadline_miss_ratio": 0.02,
        "bandwidth_utilization": 0.35,
    }
    degraded = {
        "latency_ms": 95.0,
        "jitter_ms": 20.0,
        "loss": 0.08,
        "nack_rate": 0.06,
        "deadline_miss_ratio": 0.18,
        "bandwidth_utilization": 0.68,
    }
    primary_values = degraded if primary_degraded else healthy
    backup_values = healthy if primary_degraded else degraded
    return (
        PathObservation(
            "backup_5g",
            **backup_values,
            failure_domain="private_5g_core",
        ),
        PathObservation(
            "primary_wifi",
            **primary_values,
            failure_domain="warehouse_wifi",
        ),
    )


def diagnostic_path_plan(topics: list[str], protected_count: int) -> str:
    return ";".join(
        f"{topic}={'primary_wifi' if index < protected_count else 'backup_5g'}"
        for index, topic in enumerate(topics)
    )


def wait_for_delivery_sequence(
    paths: list[Path],
    sequence_number: int,
    *,
    timeout_s: float,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ready = True
        for path in paths:
            rows = parse_json_lines(path.read_text(encoding="utf-8") if path.exists() else "")
            if not any(
                int(row.get("source_sequence_number", 0)) == sequence_number
                for row in rows
            ):
                ready = False
                break
        if ready:
            return True
        time.sleep(0.02)
    return False


def delivery_sample_counts(
    paths: list[Path],
    *,
    first_sequence: int,
    last_sequence: int,
) -> list[int]:
    counts = []
    for path in paths:
        rows = parse_json_lines(path.read_text(encoding="utf-8") if path.exists() else "")
        seen_sequences = {
            int(row.get("source_sequence_number", 0))
            for row in rows
            if first_sequence <= int(row.get("source_sequence_number", 0)) <= last_sequence
        }
        counts.append(len(seen_sequences))
    return counts


def wait_for_delivery_sample_window(
    paths: list[Path],
    *,
    first_sequence: int,
    last_sequence: int,
    target_samples: int,
    timeout_s: float,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        counts = delivery_sample_counts(
            paths,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
        )
        if counts and min(counts) >= target_samples:
            return True
        time.sleep(0.02)
    return False


def collect_sequential_qoe_epoch(
    *,
    controller: LivePathPlanController,
    telemetry_paths: list[Path],
    publish_trigger_file: Path,
    first_sequence: int,
    protected_robot_budget: int,
    stopping_config: QoESequentialStoppingConfig,
    previous_protected_robots: list[str],
    sample_wait_timeout_s: float = 1.2,
) -> dict[str, Any]:
    started = time.monotonic()
    decisions: list[dict[str, object]] = []
    sample_windows: list[dict[str, object]] = []
    last_sequence = first_sequence - 1
    for offset in range(stopping_config.max_samples_per_robot):
        sequence_number = first_sequence + offset
        write_trigger_epoch(publish_trigger_file, sequence_number)
        window_ready = wait_for_delivery_sample_window(
            telemetry_paths,
            first_sequence=first_sequence,
            last_sequence=sequence_number,
            target_samples=offset + 1,
            timeout_s=max(0.01, sample_wait_timeout_s),
        )
        sample_counts = delivery_sample_counts(
            telemetry_paths,
            first_sequence=first_sequence,
            last_sequence=sequence_number,
        )
        sample_windows.append({
            "released_sequence": sequence_number,
            "target_samples": offset + 1,
            "window_ready": window_ready,
            "min_sample_count": min(sample_counts, default=0),
            "max_sample_count": max(sample_counts, default=0),
        })
        decision = controller.qoe_sequential_stopping_decision(
            protected_robot_budget=protected_robot_budget,
            config=stopping_config,
            previous_protected_robots=previous_protected_robots,
        )
        decisions.append(decision.as_dict())
        last_sequence = sequence_number
        if decision.should_stop:
            break
    final_decision = decisions[-1] if decisions else None
    sample_cap_exhausted = (
        bool(final_decision)
        and last_sequence >= first_sequence + stopping_config.max_samples_per_robot - 1
        and not bool(final_decision["should_stop"])
    )
    return {
        "ready": bool(final_decision and final_decision["should_stop"]),
        "fallback_eligible": bool(
            final_decision
            and (
                final_decision["should_stop"]
                or sample_cap_exhausted
            )
        ),
        "sample_cap_exhausted": sample_cap_exhausted,
        "confidence_separated": bool(
            final_decision and final_decision["confidence_separated"]
        ),
        "sample_count": max(0, last_sequence - first_sequence + 1),
        "last_sequence": last_sequence,
        "wait_ms": (time.monotonic() - started) * 1000.0,
        "sample_windows": sample_windows,
        "decisions": decisions,
        "final_decision": final_decision,
    }


def recovery_window_summary(
    robot_rows: list[dict[str, Any]],
    recovery_sequences: list[int],
) -> dict[str, Any]:
    if not recovery_sequences:
        return {
            "enabled": False,
            "status": "not_applicable",
            "sequences": [],
            "robots_ok": 0,
            "robot_count": len(robot_rows),
            "missing_robot_count": 0,
            "max_latency_ms": 0.0,
            "robots": [],
        }
    expected = set(recovery_sequences)
    rows = []
    max_latency_ms = 0.0
    for row in robot_rows:
        telemetry = row.get("delivery_telemetry", [])
        sequence_rows = {
            int(item.get("source_sequence_number", 0)): item
            for item in telemetry
            if int(item.get("source_sequence_number", 0)) in expected
        }
        on_time = []
        late = []
        for sequence, item in sorted(sequence_rows.items()):
            latency_ms = float(item.get("latency_ms", 0.0))
            max_latency_ms = max(max_latency_ms, latency_ms)
            if item.get("deadline_missed") is False:
                on_time.append(sequence)
            else:
                late.append(sequence)
        missing = sorted(expected - set(sequence_rows))
        ok = not missing and not late and set(on_time) == expected
        rows.append({
            "robot_id": row.get("robot_id", ""),
            "status": "ok" if ok else "failed",
            "on_time_sequences": on_time,
            "late_sequences": late,
            "missing_sequences": missing,
        })
    robots_ok = sum(1 for row in rows if row["status"] == "ok")
    return {
        "enabled": True,
        "status": "ok" if robots_ok == len(robot_rows) else "failed",
        "sequences": recovery_sequences,
        "robots_ok": robots_ok,
        "robot_count": len(robot_rows),
        "missing_robot_count": len(robot_rows) - robots_ok,
        "max_latency_ms": max_latency_ms,
        "robots": rows,
    }


def fallback_repair_summary(
    robot_rows: list[dict[str, Any]],
    repair_sequences: list[int],
) -> dict[str, Any]:
    if not repair_sequences:
        return {
            "enabled": False,
            "status": "not_applicable",
            "sequences": [],
            "robot_count": len(robot_rows),
            "deadline_ok_robot_count": 0,
            "delivered_robot_count": 0,
            "unresolved_robot_count": 0,
            "explicit_candidate_count": 0,
            "missing_sequence_count": 0,
            "late_sequence_count": 0,
            "repair_evidence_robot_count": 0,
            "nack_retransmission_count": 0,
            "idle_repair_ack_nack_count": 0,
            "repair_plan_frame_count": 0,
            "repair_plan_selected_path_count": 0,
            "repair_budget_exhausted_count": 0,
            "repair_requests_coalesced_count": 0,
            "repair_sequence_attempt_limit_exhausted_count": 0,
            "repair_not_admitted_count": 0,
            "robots": [],
        }
    expected = set(repair_sequences)
    rows = []
    deadline_ok_robot_count = 0
    delivered_robot_count = 0
    unresolved_robot_count = 0
    missing_sequence_count = 0
    late_sequence_count = 0
    repair_evidence_robot_count = 0
    nack_retransmission_count = 0
    idle_repair_ack_nack_count = 0
    repair_plan_frame_count = 0
    repair_plan_selected_path_count = 0
    repair_budget_exhausted_count = 0
    repair_requests_coalesced_count = 0
    repair_sequence_attempt_limit_exhausted_count = 0
    repair_not_admitted_count = 0
    late_without_repair_evidence = False
    for row in robot_rows:
        telemetry = row.get("delivery_telemetry", [])
        sequence_rows = {
            int(item.get("source_sequence_number", 0)): item
            for item in telemetry
            if int(item.get("source_sequence_number", 0)) in expected
        }
        on_time = []
        late = []
        for sequence, item in sorted(sequence_rows.items()):
            if item.get("deadline_missed") is False:
                on_time.append(sequence)
            else:
                late.append(sequence)
        missing = sorted(expected - set(sequence_rows))
        publisher = row.get("publisher", {})
        subscriber = row.get("subscriber", {})
        publisher_nack_retransmissions = int(
            publisher.get("nack_retransmissions", 0) or 0
        )
        subscriber_idle_repair_ack_nacks = int(
            subscriber.get("idle_repair_ack_nack_sent", 0) or 0
        )
        subscriber_ack_nacks = int(subscriber.get("ack_nack_sent", 0) or 0)
        publisher_repair_plan_frames = int(
            publisher.get("repair_plan_frames", 0) or 0
        )
        publisher_repair_plan_selected_paths = int(
            publisher.get("repair_plan_selected_path_count", 0) or 0
        )
        publisher_repair_budget_exhausted = int(
            publisher.get("repair_budget_exhausted", 0) or 0
        )
        publisher_repair_requests_coalesced = int(
            publisher.get("repair_requests_coalesced", 0) or 0
        )
        publisher_repair_attempt_limit_exhausted = int(
            publisher.get("repair_sequence_attempt_limit_exhausted", 0) or 0
        )
        publisher_repair_not_admitted = int(
            publisher.get("repair_not_admitted", 0) or 0
        )
        repair_evidence = (
            publisher_nack_retransmissions > 0
            or subscriber_idle_repair_ack_nacks > 0
        )
        if repair_evidence:
            repair_evidence_robot_count += 1
        nack_retransmission_count += publisher_nack_retransmissions
        idle_repair_ack_nack_count += subscriber_idle_repair_ack_nacks
        repair_plan_frame_count += publisher_repair_plan_frames
        repair_plan_selected_path_count += publisher_repair_plan_selected_paths
        repair_budget_exhausted_count += publisher_repair_budget_exhausted
        repair_requests_coalesced_count += publisher_repair_requests_coalesced
        repair_sequence_attempt_limit_exhausted_count += (
            publisher_repair_attempt_limit_exhausted
        )
        repair_not_admitted_count += publisher_repair_not_admitted
        missing_sequence_count += len(missing)
        late_sequence_count += len(late)
        if missing:
            status = "unresolved"
            unresolved_robot_count += 1
        elif late:
            status = "repaired_late" if repair_evidence else "late"
            delivered_robot_count += 1
            if not repair_evidence:
                late_without_repair_evidence = True
        elif repair_evidence:
            status = "repaired_on_time"
            deadline_ok_robot_count += 1
            delivered_robot_count += 1
        else:
            status = "ok"
            deadline_ok_robot_count += 1
            delivered_robot_count += 1
        rows.append({
            "robot_id": row.get("robot_id", ""),
            "status": status,
            "on_time_sequences": on_time,
            "late_sequences": late,
            "missing_sequences": missing,
            "publisher_nack_retransmissions": publisher_nack_retransmissions,
            "subscriber_ack_nack_sent": subscriber_ack_nacks,
            "subscriber_idle_repair_ack_nack_sent": subscriber_idle_repair_ack_nacks,
            "publisher_repair_plan_frames": publisher_repair_plan_frames,
            "publisher_repair_plan_selected_path_count": (
                publisher_repair_plan_selected_paths
            ),
            "publisher_repair_plan_last_paths": publisher.get(
                "repair_plan_last_paths",
                "",
            ),
            "publisher_repair_budget_exhausted": publisher_repair_budget_exhausted,
            "publisher_repair_requests_coalesced": (
                publisher_repair_requests_coalesced
            ),
            "publisher_repair_sequence_attempt_limit_exhausted": (
                publisher_repair_attempt_limit_exhausted
            ),
            "publisher_repair_not_admitted": publisher_repair_not_admitted,
            "repair_evidence": repair_evidence,
        })
    if unresolved_robot_count > 0:
        status = "unresolved"
    elif late_without_repair_evidence:
        status = "late"
    elif late_sequence_count > 0:
        status = "repaired_late"
    elif repair_evidence_robot_count > 0:
        status = "repaired_on_time"
    else:
        status = "ok"
    return {
        "enabled": True,
        "status": status,
        "sequences": repair_sequences,
        "robot_count": len(robot_rows),
        "deadline_ok_robot_count": deadline_ok_robot_count,
        "delivered_robot_count": delivered_robot_count,
        "unresolved_robot_count": unresolved_robot_count,
        "explicit_candidate_count": missing_sequence_count + late_sequence_count,
        "missing_sequence_count": missing_sequence_count,
        "late_sequence_count": late_sequence_count,
        "repair_evidence_robot_count": repair_evidence_robot_count,
        "nack_retransmission_count": nack_retransmission_count,
        "idle_repair_ack_nack_count": idle_repair_ack_nack_count,
        "repair_plan_frame_count": repair_plan_frame_count,
        "repair_plan_selected_path_count": repair_plan_selected_path_count,
        "repair_budget_exhausted_count": repair_budget_exhausted_count,
        "repair_requests_coalesced_count": repair_requests_coalesced_count,
        "repair_sequence_attempt_limit_exhausted_count": (
            repair_sequence_attempt_limit_exhausted_count
        ),
        "repair_not_admitted_count": repair_not_admitted_count,
        "robots": rows,
    }


def run_probe(
    *,
    root: Path,
    image: str,
    robot_count: int,
    protected_robot_budget: int,
    topic_prefix: str,
    deadline_ms: int,
    primary_profile: str,
    backup_profile: str,
    loss_percent: float,
    epoch_transition: bool = False,
    qoe_feedback: bool = False,
    qoe_migration: bool = False,
    event_triggered_feedback: bool = False,
    sequential_qoe_feedback: bool = False,
    sequential_min_samples: int = 3,
    sequential_max_samples: int = 5,
    sequential_confidence_level: float = 0.95,
    sequential_min_sample_stddev: float = 0.005,
    sequential_separation_margin: float = 0.01,
    sequential_migration_hysteresis: float = 0.01,
    sequential_confidence_fallback: bool = False,
    sequential_fallback_extra_robots: int = 0,
    fallback_recovery_samples: int = 1,
    fallback_repair_budget: int = 16,
    fallback_repair_min_interval_ms: int = 50,
    fallback_repair_max_attempts_per_sequence: int = 2,
    fleet_repair_capacity_bytes: int = 0,
    force_primary_drop_sequence_two: bool = False,
    repair_capacity_fault: bool = False,
    reuse_build: bool = False,
) -> dict[str, Any]:
    enabled_modes = sum(bool(value) for value in (epoch_transition, qoe_feedback, qoe_migration))
    if enabled_modes > 1:
        raise ValueError("epoch_transition, qoe_feedback, and qoe_migration are mutually exclusive")
    feedback_enabled = qoe_feedback or qoe_migration
    if sequential_qoe_feedback and (not qoe_migration or not event_triggered_feedback):
        raise ValueError(
            "sequential_qoe_feedback requires qoe_migration and event_triggered_feedback"
        )
    if repair_capacity_fault and (
        fleet_repair_capacity_bytes <= 0 or not sequential_qoe_feedback
    ):
        raise ValueError(
            "repair_capacity_fault requires fleet repair capacity and sequential QoE feedback"
        )
    suffix = str(os.getpid())
    network = f"fleetrmw-budget-plan-net-{suffix}"
    primary_name = f"fleetrmw-budget-plan-primary-{suffix}"
    backup_name = f"fleetrmw-budget-plan-backup-{suffix}"
    subscriber_names = [f"fleetrmw-budget-plan-sub-{suffix}-{i}" for i in range(robot_count)]
    publisher_names = [f"fleetrmw-budget-plan-pub-{suffix}-{i}" for i in range(robot_count)]
    build_base = "/work/.tmp_fleetrmw_budget_plan_build"
    install_base = "/work/.tmp_fleetrmw_budget_plan_install"
    log_base = "/work/.tmp_fleetrmw_budget_plan_log"
    plan_dir = root / f".tmp_fleetrmw_budget_plan_{suffix}"
    plan_file = plan_dir / "path_plan.txt"
    plan_file_container = f"/work/{plan_file.relative_to(root)}"
    repair_plan_file = plan_dir / "repair_path_plan.txt"
    repair_plan_file_container = f"/work/{repair_plan_file.relative_to(root)}"
    publish_trigger_file = plan_dir / "publish_epoch.txt"
    publish_trigger_file_container = (
        f"/work/{publish_trigger_file.relative_to(root)}"
    )
    publisher_ready_files = [
        plan_dir / f"publisher_{index:04d}.ready"
        for index in range(robot_count)
    ]
    endpoint_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_reliable_interprocess_probe"
    )
    router_binary = (
        f"{install_base}/rmw_fleetqox_cpp/lib/rmw_fleetqox_cpp/"
        "fleetrmw_udp_router_probe"
    )
    primary_port = 49450
    backup_port = 49451
    protected_count = min(max(protected_robot_budget, 0), robot_count)
    stopping_config = QoESequentialStoppingConfig(
        min_samples_per_robot=max(1, sequential_min_samples),
        max_samples_per_robot=max(max(1, sequential_min_samples), sequential_max_samples),
        confidence_level=min(0.999999, max(0.000001, sequential_confidence_level)),
        min_sample_stddev=max(0.0, sequential_min_sample_stddev),
        separation_margin=max(0.0, sequential_separation_margin),
        migration_hysteresis=max(0.0, sequential_migration_hysteresis),
    )
    fallback_config = QoEConfidenceFallbackConfig(
        max_extra_protected_robots=max(0, sequential_fallback_extra_robots),
    )
    recovery_samples = max(1, int(fallback_recovery_samples))
    sample_wait_timeout_s = (
        max(0.05, min(0.2, float(deadline_ms) / 1000.0 * 0.25))
        if repair_capacity_fault else
        min(2.0, max(0.5, float(deadline_ms) / 1000.0 * 2.0))
    )
    total_source_frames = (
        stopping_config.max_samples_per_robot * 2 + recovery_samples
        if sequential_qoe_feedback else 3
    )
    startup_budget_ms = 6000 + robot_count * 350
    subscriber_timeout_ms = max(
        14000,
        startup_budget_ms + 5000 + total_source_frames * 1000,
    )
    router_timeout_ms = max(30000, subscriber_timeout_ms + 8000)
    publisher_trigger_timeout_ms = max(10000, 4000 + robot_count * 300)
    publisher_barrier_timeout_s = max(8.0, robot_count * 0.25)
    graph_renew_interval_ms = min(2000, max(500, 500 + max(0, robot_count - 8) * 50))
    payload_sequence = (
        [f"sample-{sequence:04d}" for sequence in range(1, total_source_frames + 1)]
        if sequential_qoe_feedback else ["one", "two", "three"]
    )
    robot_ids = [f"robot_{index:04d}" for index in range(robot_count)]
    repair_candidate_topic_prefix = f"{topic_prefix.rstrip('/')}/repair-candidate"
    topics = [
        (
            f"{repair_candidate_topic_prefix}/robot-{index:04d}/control"
            if repair_capacity_fault and index < protected_count else
            f"{topic_prefix.rstrip('/')}/standard/robot-{index:04d}/control"
            if repair_capacity_fault else
            f"{topic_prefix.rstrip('/')}/robot-{index:04d}/control"
        )
        for index in range(robot_count)
    ]
    telemetry_paths = [
        root / f".tmp_fleetrmw_budget_plan_{suffix}_{index}.jsonl"
        for index in range(robot_count)
    ]
    plan = build_budgeted_plan(
        robot_count=robot_count,
        topics=topics,
        deadline_ms=deadline_ms,
        protected_robot_budget=protected_count,
    )
    initial_plan = (
        build_budgeted_plan(
            robot_count=robot_count,
            topics=topics,
            deadline_ms=deadline_ms,
            protected_robot_budget=robot_count,
        )
        if epoch_transition else plan
    )
    initial_path_plan = (
        diagnostic_path_plan(topics, protected_count)
        if feedback_enabled else initial_plan.path_plan_env
    )
    initial_repair_path_plan = (
        build_budgeted_plan(
            robot_count=robot_count,
            topics=topics,
            deadline_ms=deadline_ms,
            protected_robot_budget=robot_count,
        ).path_plan_env
        if feedback_enabled else initial_path_plan
    )
    feedback_controller = (
        build_qoe_feedback_controller(
            plan_file=plan_file,
            repair_plan_file=(
                repair_plan_file if fleet_repair_capacity_bytes <= 0 else None
            ),
            telemetry_paths=telemetry_paths,
            topics=topics,
            deadline_ms=deadline_ms,
            protected_robot_budget=protected_count,
        )
        if feedback_enabled else None
    )
    decision_by_topic = {decision.topic: decision for decision in plan.topic_decisions}
    protected_robots = sorted(
        decision.robot_id
        for decision in plan.topic_decisions
        if len(decision.selected_paths) > 1
    )
    primary = netem_config_for_profile(primary_profile, netem_loss_percent=loss_percent)
    backup = netem_config_for_profile(backup_profile, netem_loss_percent=loss_percent)
    fleet_repair_schedule: FleetRepairSchedule | None = None
    admitted_repair_robot_ids: set[str] = set()
    deferred_repair_robot_ids: set[str] = set()
    if fleet_repair_capacity_bytes > 0:
        repair_demands = [
            RepairDemand(
                topic=topics[index],
                robot_id=robot_ids[index],
                publisher_id=f"scheduled-publisher-{index:04d}",
                source_sequence_number=2,
                payload_bytes=700,
                remaining_deadline_ms=max(1.0, float(deadline_ms) * 0.8),
                qoe_debt=(1.0 - index / max(1, protected_count)) if index < protected_count else 0.0,
                criticality=1.0,
            )
            for index in range(protected_count)
        ]
        fleet_repair_schedule = FleetRepairScheduler(
            FleetRepairSchedulerConfig(
                capacity_bytes=fleet_repair_capacity_bytes,
                max_paths_per_repair=2,
                require_failure_domain_diversity=True,
            )
        ).schedule(
            repair_demands,
            [
                RepairPath(
                    "primary_wifi",
                    latency_ms=float(primary["delay_ms"]),
                    loss=float(primary["loss_percent"]) / 100.0,
                    failure_domain="warehouse_wifi",
                ),
                RepairPath(
                    "backup_5g",
                    latency_ms=float(backup["delay_ms"]),
                    loss=float(backup["loss_percent"]) / 100.0,
                    failure_domain="private_5g_core",
                ),
            ],
        )
        initial_repair_path_plan = fleet_repair_schedule.policy_text
        admitted_repair_robot_ids = {
            decision.robot_id for decision in fleet_repair_schedule.admitted
        }
        deferred_repair_robot_ids = {
            decision.robot_id
            for decision in fleet_repair_schedule.decisions
            if decision.action != "repair"
        }
    if sequential_qoe_feedback:
        router_expected_backup_frames = max(1, robot_count - protected_count)
        router_expected_primary_frames = max(1, protected_count)
    else:
        router_expected_backup_frames = (
            (robot_count - protected_count) + robot_count * 2
            if qoe_feedback else (robot_count * 2 if qoe_migration else robot_count * 3)
        )
        router_expected_primary_frames = (
            protected_count * 3
            if qoe_feedback else
            robot_count * 2
            if qoe_migration else
            (robot_count + protected_count * 2 if epoch_transition else protected_count * 3)
        )
    expected_backup_frames = router_expected_backup_frames
    expected_primary_frames = router_expected_primary_frames
    full_redundancy_path_transmissions = robot_count * total_source_frames * 2

    try:
        for path in telemetry_paths:
            path.unlink(missing_ok=True)
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(initial_path_plan + "\n", encoding="utf-8")
        repair_plan_file.write_text(initial_repair_path_plan + "\n", encoding="utf-8")
        if event_triggered_feedback:
            write_trigger_epoch(publish_trigger_file, 0)
        run(["docker", "network", "create", network])
        reusable_install = root / install_base.removeprefix("/work/") / "setup.bash"
        if not reuse_build or not reusable_install.exists():
            clean_prefix = "" if reuse_build else f"rm -rf {build_base} {install_base} {log_base} && "
            docker_shell(
                root,
                image,
                "source /opt/ros/jazzy/setup.bash && "
                f"{clean_prefix}"
                "colcon "
                f"--log-base {log_base} "
                "build --base-paths ros2_ws/src --packages-select rmw_fleetqox_cpp "
                f"--build-base {build_base} --install-base {install_base} "
                "--cmake-args -DCMAKE_BUILD_TYPE=Release",
            )
        primary_router_command = fleet_router_command(
            install_base=install_base,
            router_binary=router_binary,
            port=primary_port,
            netem=primary,
            expected_frames=router_expected_primary_frames,
            expected_ack_nack=robot_count * total_source_frames,
            robot_count=robot_count,
            drop_sequence_two=(not qoe_migration or force_primary_drop_sequence_two),
            expected_ack_nack_forwarded=0,
            drop_topic_prefix=(
                repair_candidate_topic_prefix if repair_capacity_fault else ""
            ),
        )
        backup_router_command = fleet_router_command(
            install_base=install_base,
            router_binary=router_binary,
            port=backup_port,
            netem=backup,
            expected_frames=router_expected_backup_frames,
            expected_ack_nack=robot_count * total_source_frames,
            robot_count=robot_count,
            drop_sequence_two=repair_capacity_fault,
            expected_ack_nack_forwarded=(
                0 if sequential_qoe_feedback else
                router_expected_backup_frames if feedback_enabled else robot_count * total_source_frames
            ),
            drop_topic_prefix=(
                repair_candidate_topic_prefix if repair_capacity_fault else ""
            ),
        )
        if sequential_qoe_feedback:
            primary_router_command += (
                f" --post-satisfaction-ms 4000 --timeout-ms {router_timeout_ms}"
            )
            backup_router_command += (
                f" --post-satisfaction-ms 4000 --timeout-ms {router_timeout_ms}"
            )
        start_router(
            root=root,
            image=image,
            name=primary_name,
            network=network,
            command=primary_router_command,
        )
        start_router(
            root=root,
            image=image,
            name=backup_name,
            network=network,
            command=backup_router_command,
        )
        time.sleep(2.0)
        primary_qdisc = qdisc(primary_name)
        backup_qdisc = qdisc(backup_name)

        for index, (name, robot_id, topic, telemetry_path) in enumerate(
            zip(subscriber_names, robot_ids, topics, telemetry_paths)
        ):
            subscriber_payloads = payload_sequence
            subscriber_min_ack_nack = 3
            if repair_capacity_fault and robot_id in deferred_repair_robot_ids:
                subscriber_payloads = [
                    payload
                    for sequence, payload in enumerate(payload_sequence, start=1)
                    if sequence != 2
                ]
                subscriber_min_ack_nack = 2
            start_container(
                root=root,
                image=image,
                name=name,
                network=network,
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS={graph_renew_interval_ms} "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    f"FLEETQOX_RMW_BIND=0.0.0.0:{49500 + index} "
                    f"FLEETQOX_RMW_PEERS={primary_name}:{primary_port},{backup_name}:{backup_port} "
                    f"{endpoint_binary} --mode subscriber --topic {shlex.quote(topic)} "
                    f"--robot-id {shlex.quote(robot_id)} "
                    f"--payload-sequence {shlex.quote(','.join(subscriber_payloads))} "
                    f"--timeout-ms {subscriber_timeout_ms} "
                    f"--min-ack-nack-sent {subscriber_min_ack_nack} "
                    f"--deadline-ms {deadline_ms} --subscriber-deadline-ms {deadline_ms} "
                    f"--subscriber-telemetry-file /work/{telemetry_path.name}"
                ),
            )
        time.sleep(1.0)

        publisher_order = list(range(robot_count))
        leader_index = publisher_order[-1]
        if epoch_transition:
            publisher_order = publisher_order[:-1] + [leader_index]
        for order_position, index in enumerate(publisher_order):
            if epoch_transition and order_position == robot_count - 1:
                time.sleep(0.7)
            name = publisher_names[index]
            robot_id = robot_ids[index]
            topic = topics[index]
            epoch_args = ""
            if epoch_transition:
                epoch_args += "--publish-interval-ms 1200 "
                if index == leader_index:
                    epoch_args += (
                        "--plan-update-after-publishes 1 "
                        f"--plan-update-text {shlex.quote(plan.path_plan_env)} "
                    )
            elif feedback_enabled:
                if event_triggered_feedback:
                    ready_file_container = (
                        f"/work/{publisher_ready_files[index].relative_to(root)}"
                    )
                    epoch_args += (
                        "--publish-interval-ms 0 "
                        f"--publish-trigger-file {shlex.quote(publish_trigger_file_container)} "
                        f"--publish-trigger-timeout-ms {publisher_trigger_timeout_ms} "
                        f"--publisher-ready-file {shlex.quote(ready_file_container)} "
                    )
                else:
                    epoch_args += f"--publish-interval-ms {3000 if qoe_migration else 2000} "
            start_container(
                root=root,
                image=image,
                name=name,
                network=network,
                command=(
                    f"source /opt/ros/jazzy/setup.bash && source {install_base}/setup.bash && "
                    "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp && "
                    f"FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS={graph_renew_interval_ms} "
                    f"FLEETQOX_RMW_ROBOT_ID={shlex.quote(robot_id)} "
                    "FLEETQOX_RMW_BIND=0.0.0.0:0 "
                    "FLEETQOX_RMW_PEER_POLICY=fleet_plan "
                    f"FLEETQOX_RMW_FLEET_PATH_PLAN_FILE={shlex.quote(plan_file_container)} "
                    f"FLEETQOX_RMW_REPAIR_PATH_PLAN_FILE={shlex.quote(repair_plan_file_container)} "
                    "FLEETQOX_RMW_REPAIR_RETRANSMISSION_BUDGET="
                    f"{max(int(fallback_repair_budget), 0)} "
                    "FLEETQOX_RMW_REPAIR_MIN_INTERVAL_MS="
                    f"{max(int(fallback_repair_min_interval_ms), 0)} "
                    "FLEETQOX_RMW_REPAIR_MAX_ATTEMPTS_PER_SEQUENCE="
                    f"{max(int(fallback_repair_max_attempts_per_sequence), 0)} "
                    "FLEETQOX_RMW_REPAIR_ADMISSION_STRICT="
                    f"{1 if fleet_repair_capacity_bytes > 0 else 0} "
                    f"FLEETQOX_RMW_PEERS=primary_wifi={primary_name}:{primary_port},"
                    f"backup_5g={backup_name}:{backup_port} "
                    f"{endpoint_binary} --mode publisher --topic {shlex.quote(topic)} "
                    f"--robot-id {shlex.quote(robot_id)} "
                    f"{epoch_args}"
                    f"--payload-sequence {shlex.quote(','.join(payload_sequence))} "
                    "--hold-ms 10000 --min-retransmissions 0 --min-ack-nack-received 3 "
                    f"--deadline-ms {deadline_ms}"
                ),
            )

        publisher_barrier_started = time.monotonic()
        publisher_barrier_ready = (
            wait_for_paths(publisher_ready_files, timeout_s=publisher_barrier_timeout_s)
            if event_triggered_feedback else True
        )
        publisher_barrier_wait_ms = (
            (time.monotonic() - publisher_barrier_started) * 1000.0
            if event_triggered_feedback else 0.0
        )
        if event_triggered_feedback and not sequential_qoe_feedback:
            write_trigger_epoch(publish_trigger_file, 1)

        feedback_ready = False
        second_feedback_ready = False
        controller_summary: dict[str, object] | None = None
        controller_epoch_summaries: list[dict[str, object]] = []
        epoch_path_plans: list[str] = []
        sequential_qoe_epochs: list[dict[str, Any]] = []
        confidence_fallback_actuations: list[dict[str, object]] = []
        final_released_sequence = total_source_frames if not sequential_qoe_feedback else 0
        feedback_epoch_wait_ms: list[float] = []
        controller_actuation_ms: list[float] = []
        network_transition_actuation_ms = 0.0
        final_primary_qdisc = primary_qdisc
        final_backup_qdisc = backup_qdisc
        if feedback_controller is not None and sequential_qoe_feedback:
            first_epoch = collect_sequential_qoe_epoch(
                controller=feedback_controller,
                telemetry_paths=telemetry_paths,
                publish_trigger_file=publish_trigger_file,
                first_sequence=1,
                protected_robot_budget=protected_count,
                stopping_config=stopping_config,
                previous_protected_robots=[],
                sample_wait_timeout_s=sample_wait_timeout_s,
            )
            sequential_qoe_epochs.append(first_epoch)
            first_epoch_fallback_eligible = bool(
                first_epoch.get("fallback_eligible", False)
            )
            feedback_ready = bool(
                first_epoch["ready"]
                or (sequential_confidence_fallback and first_epoch_fallback_eligible)
            )
            feedback_epoch_wait_ms.append(float(first_epoch["wait_ms"]))
            if feedback_ready:
                controller_actuation_started = time.monotonic()
                first_decision = feedback_controller.qoe_sequential_stopping_decision(
                    protected_robot_budget=protected_count,
                    config=stopping_config,
                    previous_protected_robots=[],
                )
                if sequential_confidence_fallback and not first_decision.confidence_separated:
                    fallback = feedback_controller.apply_qoe_confidence_fallback(
                        decision=first_decision,
                        protected_robot_budget=protected_count,
                        config=fallback_config,
                    )
                    if not first_epoch["ready"]:
                        first_epoch["feedback_safe_mode"] = "sample_cap_exhausted"
                    first_epoch["confidence_fallback"] = fallback.as_dict()
                    confidence_fallback_actuations.append(fallback.as_dict())
                    measured_plan = fallback.plan
                else:
                    measured_plan = feedback_controller.poll_once()
                time.sleep(0.05)
                controller_actuation_ms.append(
                    (time.monotonic() - controller_actuation_started) * 1000.0
                )
                plan = measured_plan
                decision_by_topic = {
                    decision.topic: decision for decision in plan.topic_decisions
                }
                protected_robots = sorted(
                    decision.robot_id
                    for decision in plan.topic_decisions
                    if len(decision.selected_paths) > 1
                )
                controller_summary = feedback_controller.summary()
                controller_epoch_summaries.append(controller_summary)
                epoch_path_plans.append(plan.path_plan_env)
                if qoe_migration:
                    feedback_controller.start_new_epoch(
                        seed_observations=qoe_path_observations(primary_degraded=False)
                    )
                    network_transition_started = time.monotonic()
                    set_router_qdisc(primary_name, backup)
                    set_router_qdisc(backup_name, primary)
                    final_primary_qdisc = qdisc(primary_name)
                    final_backup_qdisc = qdisc(backup_name)
                    network_transition_actuation_ms = (
                        time.monotonic() - network_transition_started
                    ) * 1000.0
                    second_epoch = collect_sequential_qoe_epoch(
                        controller=feedback_controller,
                        telemetry_paths=telemetry_paths,
                        publish_trigger_file=publish_trigger_file,
                        first_sequence=int(first_epoch["last_sequence"]) + 1,
                        protected_robot_budget=protected_count,
                        stopping_config=stopping_config,
                        previous_protected_robots=protected_robots,
                        sample_wait_timeout_s=sample_wait_timeout_s,
                    )
                    sequential_qoe_epochs.append(second_epoch)
                    second_epoch_fallback_eligible = bool(
                        second_epoch.get("fallback_eligible", False)
                    )
                    second_feedback_ready = bool(
                        second_epoch["ready"]
                        or (
                            sequential_confidence_fallback
                            and second_epoch_fallback_eligible
                        )
                    )
                    feedback_epoch_wait_ms.append(float(second_epoch["wait_ms"]))
                    if second_feedback_ready:
                        controller_actuation_started = time.monotonic()
                        second_decision = feedback_controller.qoe_sequential_stopping_decision(
                            protected_robot_budget=protected_count,
                            config=stopping_config,
                            previous_protected_robots=protected_robots,
                        )
                        if (
                            sequential_confidence_fallback
                            and not second_decision.confidence_separated
                        ):
                            fallback = feedback_controller.apply_qoe_confidence_fallback(
                                decision=second_decision,
                                protected_robot_budget=protected_count,
                                config=fallback_config,
                            )
                            if not second_epoch["ready"]:
                                second_epoch["feedback_safe_mode"] = "sample_cap_exhausted"
                            second_epoch["confidence_fallback"] = fallback.as_dict()
                            confidence_fallback_actuations.append(fallback.as_dict())
                            measured_plan = fallback.plan
                        else:
                            measured_plan = feedback_controller.poll_once()
                        time.sleep(0.05)
                        controller_actuation_ms.append(
                            (time.monotonic() - controller_actuation_started) * 1000.0
                        )
                        plan = measured_plan
                        decision_by_topic = {
                            decision.topic: decision for decision in plan.topic_decisions
                        }
                        protected_robots = sorted(
                            decision.robot_id
                            for decision in plan.topic_decisions
                            if len(decision.selected_paths) > 1
                        )
                        controller_summary = feedback_controller.summary()
                        controller_epoch_summaries.append(controller_summary)
                        epoch_path_plans.append(plan.path_plan_env)
                        final_released_sequence = total_source_frames
                        write_trigger_epoch(publish_trigger_file, total_source_frames)
        elif feedback_controller is not None:
            feedback_wait_started = time.monotonic()
            feedback_ready = wait_for_delivery_sequence(
                telemetry_paths, 1, timeout_s=5.0
            )
            feedback_epoch_wait_ms.append(
                (time.monotonic() - feedback_wait_started) * 1000.0
            )
            if feedback_ready:
                controller_actuation_started = time.monotonic()
                measured_plan = feedback_controller.poll_once()
                if event_triggered_feedback:
                    time.sleep(0.05)
                controller_actuation_ms.append(
                    (time.monotonic() - controller_actuation_started) * 1000.0
                )
                plan = measured_plan
                decision_by_topic = {
                    decision.topic: decision for decision in plan.topic_decisions
                }
                protected_robots = sorted(
                    decision.robot_id
                    for decision in plan.topic_decisions
                    if len(decision.selected_paths) > 1
                )
                controller_summary = feedback_controller.summary()
                controller_epoch_summaries.append(controller_summary)
                epoch_path_plans.append(plan.path_plan_env)
                if qoe_migration:
                    feedback_controller.start_new_epoch(
                        seed_observations=qoe_path_observations(primary_degraded=False)
                    )
                    network_transition_started = time.monotonic()
                    set_router_qdisc(primary_name, backup)
                    set_router_qdisc(backup_name, primary)
                    final_primary_qdisc = qdisc(primary_name)
                    final_backup_qdisc = qdisc(backup_name)
                    network_transition_actuation_ms = (
                        time.monotonic() - network_transition_started
                    ) * 1000.0
                    if event_triggered_feedback:
                        write_trigger_epoch(publish_trigger_file, 2)
                    feedback_wait_started = time.monotonic()
                    second_feedback_ready = wait_for_delivery_sequence(
                        telemetry_paths, 2, timeout_s=5.0
                    )
                    feedback_epoch_wait_ms.append(
                        (time.monotonic() - feedback_wait_started) * 1000.0
                    )
                    if second_feedback_ready:
                        controller_actuation_started = time.monotonic()
                        measured_plan = feedback_controller.poll_once()
                        if event_triggered_feedback:
                            time.sleep(0.05)
                        controller_actuation_ms.append(
                            (time.monotonic() - controller_actuation_started) * 1000.0
                        )
                        plan = measured_plan
                        decision_by_topic = {
                            decision.topic: decision for decision in plan.topic_decisions
                        }
                        protected_robots = sorted(
                            decision.robot_id
                            for decision in plan.topic_decisions
                            if len(decision.selected_paths) > 1
                        )
                        controller_summary = feedback_controller.summary()
                        controller_epoch_summaries.append(controller_summary)
                        epoch_path_plans.append(plan.path_plan_env)
                        if event_triggered_feedback:
                            write_trigger_epoch(publish_trigger_file, 3)

        publisher_returncodes = [
            int(run(["docker", "wait", name]).stdout.strip()) for name in publisher_names
        ]
        subscriber_returncodes = [
            int(run(["docker", "wait", name]).stdout.strip()) for name in subscriber_names
        ]
        primary_rc = int(run(["docker", "wait", primary_name]).stdout.strip())
        backup_rc = int(run(["docker", "wait", backup_name]).stdout.strip())
        publishers = [
            parse_last_json(run(["docker", "logs", name], check=False).stdout)
            for name in publisher_names
        ]
        subscribers = [
            parse_last_json(run(["docker", "logs", name], check=False).stdout)
            for name in subscriber_names
        ]
        primary_log = run(["docker", "logs", primary_name], check=False).stdout.strip()
        backup_log = run(["docker", "logs", backup_name], check=False).stdout.strip()
        primary_result = parse_last_json(primary_log)
        backup_result = parse_last_json(backup_log)

        first_sequential_samples = (
            int(sequential_qoe_epochs[0]["sample_count"])
            if sequential_qoe_epochs else 0
        )
        second_sequential_samples = (
            int(sequential_qoe_epochs[1]["sample_count"])
            if len(sequential_qoe_epochs) > 1 else 0
        )
        final_sequential_samples = max(
            0,
            total_source_frames - first_sequential_samples - second_sequential_samples,
        ) if sequential_qoe_feedback else 0
        controller_epoch_protected_robots = [
            sorted(
                decision["robot_id"]
                for decision in epoch["last_plan"]["topic_decisions"]
                if len(decision["selected_paths"]) > 1
            )
            for epoch in controller_epoch_summaries
        ]
        expected_on_time_sequences = list(range(1, total_source_frames + 1))
        recovery_sequences = (
            list(range(total_source_frames - recovery_samples + 1, total_source_frames + 1))
            if sequential_qoe_feedback else []
        )
        fallback_repair_sequences = (
            list(range(1, total_source_frames - recovery_samples + 1))
            if sequential_qoe_feedback else []
        )
        robot_rows = []
        for index, (robot_id, topic, telemetry_path, publisher, subscriber) in enumerate(
            zip(robot_ids, topics, telemetry_paths, publishers, subscribers)
        ):
            telemetry = parse_json_lines(
                telemetry_path.read_text(encoding="utf-8") if telemetry_path.exists() else ""
            )
            sequence_rows = {
                int(row.get("source_sequence_number", 0)): row
                for row in telemetry
                if 1 <= int(row.get("source_sequence_number", 0)) <= total_source_frames
            }
            on_time_sequences = sorted(
                sequence
                for sequence, row in sequence_rows.items()
                if row.get("deadline_missed") is False
                and float(row.get("latency_ms", deadline_ms + 1)) <= deadline_ms
            )
            selected_paths = decision_by_topic[topic].selected_paths
            if sequential_qoe_feedback:
                expected_selected_path_count = first_sequential_samples
                expected_redundant_frames = 0
                first_plan_protected = set(
                    controller_epoch_protected_robots[0]
                    if controller_epoch_protected_robots else []
                )
                second_plan_protected = set(
                    controller_epoch_protected_robots[1]
                    if len(controller_epoch_protected_robots) > 1 else []
                )
                if robot_id in first_plan_protected:
                    expected_redundant_frames += second_sequential_samples
                    expected_selected_path_count += second_sequential_samples * 2
                else:
                    expected_selected_path_count += second_sequential_samples
                if robot_id in second_plan_protected:
                    expected_redundant_frames += final_sequential_samples
                    expected_selected_path_count += final_sequential_samples * 2
                else:
                    expected_selected_path_count += final_sequential_samples
            else:
                expected_redundant_frames = (
                    2 if qoe_feedback and len(selected_paths) > 1 else
                    1 if qoe_migration else
                    3 if len(selected_paths) > 1 else
                    (1 if epoch_transition else 0)
                )
                expected_selected_path_count = (
                    5 if qoe_feedback and len(selected_paths) > 1 else
                    3 if qoe_feedback else
                    4 if qoe_migration else
                    6 if epoch_transition and len(selected_paths) > 1 else
                    4 if epoch_transition else
                    6 if len(selected_paths) > 1 else 3
                )
            payloads = set(subscriber.get("payloads", []))
            is_admitted_repair = robot_id in admitted_repair_robot_ids
            is_deferred_repair = robot_id in deferred_repair_robot_ids
            expected_delivered_sequences = (
                [sequence for sequence in expected_on_time_sequences if sequence != 2]
                if repair_capacity_fault and is_deferred_repair else
                expected_on_time_sequences
            )
            expected_payloads = {
                payload
                for sequence, payload in enumerate(payload_sequence, start=1)
                if not (repair_capacity_fault and is_deferred_repair and sequence == 2)
            }
            expected_nack_repair = repair_capacity_fault and is_admitted_repair
            expected_admission_rejection = repair_capacity_fault and is_deferred_repair
            row_ok = (
                publisher_returncodes[index] == 0
                and subscriber_returncodes[index] == 0
                and publisher.get("status") == "ok"
                and subscriber.get("status") == "ok"
                and publisher.get("peer_policy") == "fleet_plan"
                and int(publisher.get("fleet_plan_frames", 0)) >= total_source_frames
                and int(publisher.get("fleet_plan_redundant_frames", 0)) == expected_redundant_frames
                and int(publisher.get("fleet_plan_selected_path_count", 0)) == expected_selected_path_count
                and publisher.get("fleet_plan_last_paths") == ",".join(selected_paths)
                and (
                    int(publisher.get("nack_retransmissions", 0)) >= 1
                    and int(publisher.get("repair_plan_frames", 0)) >= 1
                    if expected_nack_repair else
                    int(publisher.get("nack_retransmissions", 0)) == 0
                )
                and (
                    int(publisher.get("repair_not_admitted", 0)) >= 1
                    if expected_admission_rejection else True
                )
                and on_time_sequences == expected_delivered_sequences
                and expected_payloads.issubset(payloads)
                and (payload_sequence[1] not in payloads if expected_admission_rejection else True)
            )
            robot_rows.append({
                "robot_id": robot_id,
                "topic": topic,
                "status": "ok" if row_ok else "failed",
                "mode": decision_by_topic[topic].mode,
                "selected_paths": list(selected_paths),
                "expected_redundant_frames": expected_redundant_frames,
                "expected_selected_path_count": expected_selected_path_count,
                "repair_capacity_role": (
                    "admitted" if is_admitted_repair else
                    "deferred" if is_deferred_repair else
                    "unaffected"
                ),
                "on_time_sequences": on_time_sequences,
                "recovery_on_time_sequences": [
                    sequence for sequence in on_time_sequences if sequence in recovery_sequences
                ],
                "max_latency_ms": max(
                    (float(row.get("latency_ms", 0.0)) for row in telemetry),
                    default=0.0,
                ),
                "publisher": publisher,
                "subscriber": subscriber,
                "delivery_telemetry": telemetry,
            })

        recovery_summary = recovery_window_summary(robot_rows, recovery_sequences)
        repair_summary = fallback_repair_summary(robot_rows, fallback_repair_sequences)
        robots_ok = sum(row["status"] == "ok" for row in robot_rows)
        repair_delivery_robots_ok = int(repair_summary.get("delivered_robot_count", 0))
        repair_deadline_robots_ok = int(
            repair_summary.get("deadline_ok_robot_count", 0)
        )
        qoe_recovery_ok = (
            repair_summary.get("status") != "unresolved"
            and recovery_summary.get("status") == "ok"
        )
        success_ratios = [
            len(row["on_time_sequences"]) / max(1.0, float(total_source_frames))
            for row in robot_rows
        ]
        jain_index = jain_fairness(success_ratios)
        actual_path_transmissions = sum(
            int(publisher.get("fleet_plan_selected_path_count", 0))
            + int(publisher.get("repair_plan_selected_path_count", 0))
            for publisher in publishers
        )
        planned_path_transmissions = (
            sum(int(row["expected_selected_path_count"]) for row in robot_rows)
            if sequential_qoe_feedback else
            expected_backup_frames + expected_primary_frames
            if epoch_transition or feedback_enabled else
            sum(len(decision.selected_paths) * total_source_frames for decision in plan.topic_decisions)
        )
        repair_path_transmission_overhead = max(
            0,
            actual_path_transmissions - planned_path_transmissions,
        )
        reduction_ratio = 1.0 - actual_path_transmissions / full_redundancy_path_transmissions
        total_retransmissions = sum(
            int(publisher.get("nack_retransmissions", 0)) for publisher in publishers
        )
        repair_plan_frame_count = sum(
            int(publisher.get("repair_plan_frames", 0)) for publisher in publishers
        )
        repair_plan_selected_path_count = sum(
            int(publisher.get("repair_plan_selected_path_count", 0))
            for publisher in publishers
        )
        repair_not_admitted_robot_count = sum(
            int(publisher.get("repair_not_admitted", 0)) > 0
            for publisher in publishers
        )
        expected_repair_path_transmissions = sum(
            len(decision.selected_paths) for decision in (
                fleet_repair_schedule.admitted if fleet_repair_schedule is not None else ()
            )
        )
        repair_capacity_outcome_ok = (
            repair_capacity_fault
            and fleet_repair_schedule is not None
            and int(primary_result.get("test_dropped_frames", 0)) >= protected_count
            and int(backup_result.get("test_dropped_frames", 0)) >= protected_count
            and repair_summary.get("repair_evidence_robot_count") == len(admitted_repair_robot_ids)
            and repair_summary.get("unresolved_robot_count") == len(deferred_repair_robot_ids)
            and repair_not_admitted_robot_count == len(deferred_repair_robot_ids)
            and total_retransmissions == len(admitted_repair_robot_ids)
            and repair_plan_frame_count == len(admitted_repair_robot_ids)
            and repair_plan_selected_path_count == expected_repair_path_transmissions
        ) if repair_capacity_fault else False
        primary_fault_observed = (
            qoe_migration
            and final_primary_qdisc != primary_qdisc
            and final_backup_qdisc != backup_qdisc
        ) or int(primary_result.get("test_dropped_frames", 0)) >= protected_count
        expected_protected = (
            [f"robot_{index:04d}" for index in range(robot_count - protected_count, robot_count)]
            if qoe_migration else
            [f"robot_{index:04d}" for index in range(protected_count)]
        )
        first_epoch_expected_protected = [
            f"robot_{index:04d}" for index in range(protected_count)
        ]
        first_epoch_protected = (
            controller_epoch_protected_robots[0]
            if controller_epoch_protected_robots else []
        )
        confidence_fallback_applied = any(
            bool(item.get("applied")) for item in confidence_fallback_actuations
        )
        protected_set_churn = sum(
            len(set(previous).symmetric_difference(current))
            for previous, current in zip(
                controller_epoch_protected_robots,
                controller_epoch_protected_robots[1:],
            )
        )
        protection_migration_count = sum(
            max(
                len(set(previous) - set(current)),
                len(set(current) - set(previous)),
            )
            for previous, current in zip(
                controller_epoch_protected_robots,
                controller_epoch_protected_robots[1:],
            )
        )
        epoch_convergence_ms = [
            wait_ms + actuation_ms
            for wait_ms, actuation_ms in zip(
                feedback_epoch_wait_ms, controller_actuation_ms
            )
        ]
        status = (
            robots_ok == robot_count
            and primary_rc == 0
            and backup_rc == 0
            and primary_result.get("status") == "ok"
            and backup_result.get("status") == "ok"
            and (protected_robots == expected_protected or confidence_fallback_applied)
            and (not feedback_enabled or feedback_ready)
            and (not event_triggered_feedback or publisher_barrier_ready)
            and (not qoe_migration or second_feedback_ready)
            and (
                not qoe_migration
                or first_epoch_protected == first_epoch_expected_protected
                or confidence_fallback_applied
            )
            and (not qoe_feedback or plan.path_plan_env == build_budgeted_plan(
                robot_count=robot_count,
                topics=topics,
                deadline_ms=deadline_ms,
                protected_robot_budget=protected_count,
            ).path_plan_env)
            and primary_fault_observed
            and int(backup_result.get("forwarded_frames", 0)) >= expected_backup_frames
            and actual_path_transmissions == (
                planned_path_transmissions + expected_repair_path_transmissions
                if repair_capacity_fault else planned_path_transmissions
            )
            and (
                sequential_qoe_feedback
                or actual_path_transmissions == expected_backup_frames + expected_primary_frames
            )
            and (
                repair_capacity_outcome_ok
                if repair_capacity_fault else total_retransmissions == 0
            )
            and (repair_capacity_fault or jain_index >= 0.999)
            and "netem" in primary_qdisc
            and "netem" in backup_qdisc
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if status else "failed",
            "image": image,
            "robot_count": robot_count,
            "robots_ok": robots_ok,
            "strict_robots_ok": robots_ok,
            "repair_delivery_robots_ok": repair_delivery_robots_ok,
            "repair_deadline_robots_ok": repair_deadline_robots_ok,
            "qoe_recovery_ok": qoe_recovery_ok,
            "protected_robot_budget": protected_count,
            "protected_robots": protected_robots,
            "epoch_transition": epoch_transition,
            "qoe_feedback": qoe_feedback,
            "qoe_migration": qoe_migration,
            "event_triggered_feedback": event_triggered_feedback,
            "sequential_qoe_feedback": sequential_qoe_feedback,
            "sequential_confidence_fallback": sequential_confidence_fallback,
            "sequential_qoe_config": stopping_config.__dict__,
            "sequential_sample_wait_timeout_ms": sample_wait_timeout_s * 1000.0,
            "startup_budget_ms": startup_budget_ms,
            "subscriber_timeout_ms": subscriber_timeout_ms,
            "router_timeout_ms": router_timeout_ms,
            "publisher_trigger_timeout_ms": publisher_trigger_timeout_ms,
            "publisher_barrier_timeout_ms": publisher_barrier_timeout_s * 1000.0,
            "graph_renew_interval_ms": graph_renew_interval_ms,
            "sequential_confidence_fallback_config": fallback_config.__dict__,
            "fallback_recovery_samples": recovery_samples,
            "fallback_repair_budget": max(int(fallback_repair_budget), 0),
            "fallback_repair_min_interval_ms": max(
                int(fallback_repair_min_interval_ms),
                0,
            ),
            "fallback_repair_max_attempts_per_sequence": max(
                int(fallback_repair_max_attempts_per_sequence),
                0,
            ),
            "fleet_repair_capacity_bytes": max(int(fleet_repair_capacity_bytes), 0),
            "fleet_repair_schedule": (
                None if fleet_repair_schedule is None else fleet_repair_schedule.as_dict()
            ),
            "force_primary_drop_sequence_two": force_primary_drop_sequence_two,
            "repair_capacity_fault": repair_capacity_fault,
            "repair_capacity_outcome_ok": repair_capacity_outcome_ok,
            "admitted_repair_robot_ids": sorted(admitted_repair_robot_ids),
            "deferred_repair_robot_ids": sorted(deferred_repair_robot_ids),
            "fallback_recovery_sequences": recovery_sequences,
            "fallback_recovery": recovery_summary,
            "fallback_repair_sequences": fallback_repair_sequences,
            "fallback_repair": repair_summary,
            "sequential_qoe_epochs": sequential_qoe_epochs,
            "confidence_fallback_actuations": confidence_fallback_actuations,
            "confidence_fallback_applied": confidence_fallback_applied,
            "feedback_safe_mode_count": sum(
                1 for epoch in sequential_qoe_epochs if epoch.get("feedback_safe_mode")
            ),
            "total_source_frames": total_source_frames,
            "payload_sequence": payload_sequence,
            "final_released_sequence": final_released_sequence,
            "first_sequential_samples": first_sequential_samples,
            "second_sequential_samples": second_sequential_samples,
            "final_sequential_samples": final_sequential_samples,
            "publisher_barrier_ready": publisher_barrier_ready,
            "publisher_barrier_wait_ms": publisher_barrier_wait_ms,
            "feedback_ready": feedback_ready,
            "second_feedback_ready": second_feedback_ready,
            "deadline_ms": deadline_ms,
            "initial_path_plan": initial_path_plan,
            "path_plan": plan.path_plan_env,
            "initial_online_plan": None if feedback_enabled else initial_plan.as_dict(),
            "online_plan": plan.as_dict(),
            "controller": controller_summary,
            "controller_epochs": controller_epoch_summaries,
            "controller_epoch_count": len(controller_epoch_summaries),
            "controller_epoch_protected_robots": controller_epoch_protected_robots,
            "epoch_path_plans": epoch_path_plans,
            "feedback_epoch_wait_ms": feedback_epoch_wait_ms,
            "controller_actuation_ms": controller_actuation_ms,
            "epoch_convergence_ms": epoch_convergence_ms,
            "max_feedback_epoch_wait_ms": max(feedback_epoch_wait_ms, default=0.0),
            "max_controller_actuation_ms": max(controller_actuation_ms, default=0.0),
            "max_epoch_convergence_ms": max(epoch_convergence_ms, default=0.0),
            "network_transition_actuation_ms": network_transition_actuation_ms,
            "protected_set_churn": protected_set_churn,
            "protection_migration_count": protection_migration_count,
            "primary_profile": primary_profile,
            "backup_profile": backup_profile,
            "primary_qdisc": primary_qdisc,
            "backup_qdisc": backup_qdisc,
            "final_primary_qdisc": final_primary_qdisc,
            "final_backup_qdisc": final_backup_qdisc,
            "primary_fault_observed": primary_fault_observed,
            "deadline_success_jain_index": jain_index,
            "max_latency_ms": max((row["max_latency_ms"] for row in robot_rows), default=0.0),
            "planned_path_transmissions": planned_path_transmissions,
            "actual_path_transmissions": actual_path_transmissions,
            "repair_path_transmission_overhead": repair_path_transmission_overhead,
            "expected_repair_path_transmissions": expected_repair_path_transmissions,
            "repair_plan_frame_count": repair_plan_frame_count,
            "repair_plan_selected_path_count": repair_plan_selected_path_count,
            "repair_not_admitted_robot_count": repair_not_admitted_robot_count,
            "repair_path_transmission_overhead_ratio": (
                repair_path_transmission_overhead / planned_path_transmissions
                if planned_path_transmissions else 0.0
            ),
            "full_redundancy_path_transmissions": full_redundancy_path_transmissions,
            "path_transmission_reduction_ratio": reduction_ratio,
            "total_nack_retransmissions": total_retransmissions,
            "robots": robot_rows,
            "primary_router": primary_result,
            "backup_router": backup_result,
            "primary_router_logs": primary_log,
            "backup_router_logs": backup_log,
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "command": exc.cmd,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    finally:
        run(
            ["docker", "rm", "-f", primary_name, backup_name, *subscriber_names, *publisher_names],
            check=False,
        )
        run(["docker", "network", "rm", network], check=False)
        for path in telemetry_paths:
            path.unlink(missing_ok=True)
        shutil.rmtree(plan_dir, ignore_errors=True)
        if not reuse_build:
            docker_shell(root, image, f"rm -rf {build_base} {install_base} {log_base}", check=False)


def set_router_qdisc(container: str, netem: dict[str, float]) -> None:
    run([
        "docker", "exec", container,
        "tc", "qdisc", "replace", "dev", "eth0", "root", "netem",
        "delay", f"{netem['delay_ms']:g}ms", f"{netem['jitter_ms']:g}ms",
        "loss", f"{netem['loss_percent']:g}%",
        "rate", f"{netem['rate_mbit']:g}mbit",
    ])


def write_trigger_epoch(path: Path, sequence_number: int) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{sequence_number}\n", encoding="utf-8")
    os.replace(temporary, path)


def wait_for_paths(paths: list[Path], *, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            return True
        time.sleep(0.01)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
