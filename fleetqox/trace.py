"""Trace generation for network simulators.

The trace generator converts FleetQoX workload decisions into JSON-serializable
events. These events are intended to be consumed by ns-3, OMNeT++, or later ROS
sidecar tooling.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

from .control_plane import (
    LagrangianRiskPredictiveAdmissionController,
    PredictiveAdmissionController,
    RiskConstrainedPredictiveAdmissionController,
)
from .model import FlowClass, FlowDecision, FlowObservation, FlowSpec, NetworkLink
from .scheduler import CausalSemanticDeadlineScheduler
from .sidecar_contract import SIDECAR_TRACE_SCHEMA_VERSION, is_admitted_action
from .simulator import (
    _size,
    _task_for,
    _utility,
    _vary_capacity,
    build_fleet_workload,
    fifo_policy,
    static_priority_policy,
)

TRACE_SCHEMA_VERSION = SIDECAR_TRACE_SCHEMA_VERSION

SIM_CSV_COLUMNS = [
    "event_id",
    "timestamp_ms",
    "policy",
    "flow_id",
    "flow_class",
    "topic",
    "robot_id",
    "src",
    "dst",
    "action",
    "bytes",
    "original_bytes",
    "degraded",
    "deadline_ms",
    "lifespan_ms",
    "qos_reliability",
    "reliability",
    "wire_mode",
    "predicted_slack_ms",
    "reason",
    "priority",
    "semantic_utility",
    "age_ms",
    "link_capacity_bytes_per_tick",
    "link_loss",
    "link_jitter_ms",
    "link_rtt_ms",
]


PolicyFn = Callable[
    [list[tuple[FlowSpec, FlowObservation]], NetworkLink],
    list[FlowDecision],
]


def generate_trace_events(
    *,
    scenario: str,
    robots: int,
    seconds: int,
    seed: int,
    capacity_bytes_per_second: int | None,
    capacity_packets_per_second: int | None = None,
    policies: Iterable[str] | None = None,
    include_non_sent: bool = False,
) -> list[dict[str, object]]:
    """Generate trace events for one T0-style workload scenario.

    capacity_packets_per_second is an optional, separate packet-rate
    ceiling (None = no constraint, matching prior behavior). Real 802.11
    airtime is dominated by mostly size-independent per-packet overhead
    (DIFS/backoff/preamble/ACK), so the byte-rate budget above alone
    under-prices high-frequency small packets (e.g. 50Hz control) relative
    to real channel capacity -- this lets admission control shed that load
    proactively instead of over-admitting packets the real MAC layer can't
    actually carry.
    """

    requested = list(
        policies
        or [
            "fifo",
            "static_priority",
            "fleetqox_csds",
            "fleetqox_predictive",
            "fleetqox_predictive_guarded",
            "fleetqox_predictive_lagrangian",
        ]
    )
    policy_map = _policy_map()
    unknown = [name for name in requested if name not in policy_map]
    if unknown:
        raise ValueError(f"unknown policies: {', '.join(unknown)}")

    ticks_per_second = 50
    tick_ms = 1000.0 / ticks_per_second
    ticks = seconds * ticks_per_second
    capacity_per_tick = (
        capacity_bytes_per_second
        if capacity_bytes_per_second is not None
        else max(200_000, robots * 6_000)
    ) // ticks_per_second
    capacity_packets_per_tick = (
        capacity_packets_per_second // ticks_per_second
        if capacity_packets_per_second is not None
        else None
    )
    flows = build_fleet_workload(robots, seed)

    events: list[dict[str, object]] = []
    for policy_name in requested:
        policy = policy_map[policy_name]
        events.extend(
            _generate_policy_trace(
                scenario=scenario,
                policy_name=policy_name,
                policy=policy,
                flows=flows,
                ticks=ticks,
                seed=seed,
                capacity_per_tick=capacity_per_tick,
                capacity_packets_per_tick=capacity_packets_per_tick,
                tick_ms=tick_ms,
                ticks_per_second=ticks_per_second,
                include_non_sent=include_non_sent,
            )
        )
    return events


def write_simulator_csv(
    events: Iterable[dict[str, object]],
    output: str | Path,
) -> int:
    """Write packet events as a CSV file for ns-3/OMNeT++ importers."""

    output_path = Path(output)
    count = 0
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIM_CSV_COLUMNS)
        writer.writeheader()
        for event_id, event in enumerate(events):
            if event.get("event_type") != "packet":
                continue
            row = {column: event.get(column, "") for column in SIM_CSV_COLUMNS}
            row["event_id"] = event_id
            writer.writerow(row)
            count += 1
    return count


def _generate_policy_trace(
    *,
    scenario: str,
    policy_name: str,
    policy: PolicyFn,
    flows: list[FlowSpec],
    ticks: int,
    seed: int,
    capacity_per_tick: int,
    tick_ms: float,
    ticks_per_second: int,
    include_non_sent: bool,
    capacity_packets_per_tick: int | None = None,
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    ages = {flow.flow_id: 0.0 for flow in flows}
    events: list[dict[str, object]] = []

    for tick in range(ticks):
        timestamp_ms = tick * tick_ms
        link = NetworkLink(
            capacity_bytes_per_tick=_vary_capacity(capacity_per_tick, tick),
            capacity_packets_per_tick=capacity_packets_per_tick,
            loss=0.04 + (0.10 if tick % 83 in range(8) else 0.0),
            jitter_ms=8.0 + (18.0 if tick % 57 in range(6) else 0.0),
            rtt_ms=22.0 + (35.0 if tick % 67 in range(4) else 0.0),
        )
        candidates: list[tuple[FlowSpec, FlowObservation]] = []
        for flow in flows:
            ages[flow.flow_id] += tick_ms
            if rng.random() > min(0.95, flow.nominal_rate_hz / ticks_per_second):
                continue
            obs = FlowObservation(
                age_ms=ages[flow.flow_id],
                queue_depth=1 if rng.random() < 0.8 else 2,
                measured_loss=link.loss,
                measured_rtt_ms=link.rtt_ms,
                observed_jitter_ms=link.jitter_ms,
                task=_task_for(flow, rng),
            )
            candidates.append((flow, obs))

        decisions = policy(candidates, link)
        by_id = {decision.flow_id: decision for decision in decisions}
        for flow, obs in candidates:
            decision = by_id.get(flow.flow_id)
            if decision is None:
                continue
            sent = is_admitted_action(decision.action)
            if sent:
                ages[flow.flow_id] = 0.0
            elif decision.action == "drop":
                ages[flow.flow_id] = 0.0
            if sent or include_non_sent:
                events.append(
                    _event(
                        scenario=scenario,
                        policy_name=policy_name,
                        timestamp_ms=timestamp_ms,
                        tick=tick,
                        flow=flow,
                        obs=obs,
                        link=link,
                        decision=decision,
                        sent=sent,
                    )
                )
    return events


def _event(
    *,
    scenario: str,
    policy_name: str,
    timestamp_ms: float,
    tick: int,
    flow: FlowSpec,
    obs: FlowObservation,
    link: NetworkLink,
    decision: FlowDecision,
    sent: bool,
) -> dict[str, object]:
    original_bytes = _size(flow, obs)
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "event_type": "packet" if sent else "decision",
        "experiment": "fleetrmw_sidecar_contract",
        "scenario": scenario,
        "policy": policy_name,
        "timestamp_ms": timestamp_ms,
        "tick": tick,
        "flow_id": flow.flow_id,
        "flow_class": flow.flow_class.value,
        "topic": flow.topic,
        "robot_id": flow.robot_id,
        "src": _source_for(flow),
        "dst": _destination_for(flow),
        "action": decision.action,
        "bytes": decision.allocated_bytes,
        "original_bytes": original_bytes,
        "degraded": decision.degraded,
        "deadline_ms": flow.qos.deadline_ms,
        "lifespan_ms": flow.qos.lifespan_ms,
        "qos_reliability": flow.qos.reliability,
        "reliability": decision.reliability or flow.qos.reliability,
        "wire_mode": decision.wire_mode or "native",
        "predicted_slack_ms": decision.predicted_slack_ms,
        "reason": decision.reason,
        "durability": flow.qos.durability,
        "priority": decision.priority,
        "semantic_utility": _utility(flow, obs, degraded=decision.degraded),
        "nominal_rate_hz": flow.nominal_rate_hz,
        "causal_task_gain": flow.causal_task_gain,
        "redundancy": flow.redundancy,
        "semantic_delta_ratio": flow.semantic_delta_ratio,
        "age_ms": obs.age_ms,
        "queue_depth": obs.queue_depth,
        "task_criticality": obs.task.task_criticality,
        "collision_risk": obs.task.collision_risk,
        "operator_attention": obs.task.operator_attention,
        "coordination_pressure": obs.task.coordination_pressure,
        "link_capacity_bytes_per_tick": link.capacity_bytes_per_tick,
        "link_loss": link.loss,
        "link_jitter_ms": link.jitter_ms,
        "link_rtt_ms": link.rtt_ms,
    }


def _policy_map() -> dict[str, PolicyFn]:
    return {
        "fifo": fifo_policy,
        "static_priority": static_priority_policy,
        "fleetqox_csds": CausalSemanticDeadlineScheduler().schedule,
        "fleetqox_predictive": PredictiveAdmissionController().schedule,
        "fleetqox_predictive_guarded": RiskConstrainedPredictiveAdmissionController().schedule,
        "fleetqox_predictive_lagrangian": LagrangianRiskPredictiveAdmissionController().schedule,
    }


def _source_for(flow: FlowSpec) -> str:
    if flow.flow_class is FlowClass.CONTROL:
        return "fleet_controller"
    return flow.robot_id


def _destination_for(flow: FlowSpec) -> str:
    if flow.flow_class is FlowClass.CONTROL:
        return flow.robot_id
    if flow.flow_class is FlowClass.HUMAN_QOE:
        return "operator_ui"
    return "fleet_router"
