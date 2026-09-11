"""Deterministic benchmark workloads for FleetQoX."""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from typing import Callable, Iterable, List, Tuple

from .control_plane import (
    LagrangianRiskPredictiveAdmissionController,
    PredictiveAdmissionController,
    RiskConstrainedPredictiveAdmissionController,
)
from .model import (
    FlowClass,
    FlowDecision,
    FlowObservation,
    FlowSpec,
    NetworkLink,
    QoEProfile,
    QoSProfile,
    TaskContext,
)
from .scheduler import CausalSemanticDeadlineScheduler


PolicyCallable = Callable[
    [list[tuple[FlowSpec, FlowObservation]], NetworkLink],
    list[FlowDecision],
]


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    robots: int
    ticks: int
    sent: int
    dropped: int
    deferred: int
    degraded: int
    compacted: int
    bytes_sent: int
    control_deadline_miss_ratio: float
    stale_state_ratio: float
    qoe_delivery_ratio: float
    utility_score: float

    def as_row(self) -> list[str]:
        return [
            self.name,
            str(self.robots),
            str(self.ticks),
            str(self.sent),
            str(self.deferred),
            str(self.dropped),
            str(self.degraded),
            str(self.compacted),
            str(self.bytes_sent),
            f"{self.control_deadline_miss_ratio:.3f}",
            f"{self.stale_state_ratio:.3f}",
            f"{self.qoe_delivery_ratio:.3f}",
            f"{self.utility_score:.2f}",
        ]


def build_fleet_workload(robots: int, seed: int) -> list[FlowSpec]:
    rng = random.Random(seed)
    flows: list[FlowSpec] = []
    for idx in range(robots):
        robot_id = f"robot_{idx:04d}"
        active = rng.random() < 0.78
        operator_focus = rng.random() < 0.08
        risk_hint = rng.random()
        flows.extend(
            [
                FlowSpec(
                    flow_id=f"{robot_id}:cmd",
                    robot_id=robot_id,
                    topic="/cmd_vel",
                    flow_class=FlowClass.CONTROL,
                    qos=QoSProfile(
                        reliability="reliable",
                        depth=1,
                        deadline_ms=45,
                        lifespan_ms=90,
                    ),
                    qoe=QoEProfile(),
                    nominal_size_bytes=96,
                    nominal_rate_hz=50,
                    causal_task_gain=0.85 if active else 0.2,
                ),
                FlowSpec(
                    flow_id=f"{robot_id}:state",
                    robot_id=robot_id,
                    topic="/fleet_state",
                    flow_class=FlowClass.STATE,
                    qos=QoSProfile(
                        reliability="reliable",
                        depth=3,
                        deadline_ms=120,
                        lifespan_ms=350,
                    ),
                    qoe=QoEProfile(),
                    nominal_size_bytes=320,
                    nominal_rate_hz=10,
                    causal_task_gain=0.75,
                    semantic_delta_ratio=0.55,
                ),
                FlowSpec(
                    flow_id=f"{robot_id}:coord",
                    robot_id=robot_id,
                    topic="/coordination_intent",
                    flow_class=FlowClass.COORDINATION,
                    qos=QoSProfile(
                        reliability="reliable",
                        depth=2,
                        deadline_ms=80,
                        lifespan_ms=200,
                    ),
                    qoe=QoEProfile(),
                    nominal_size_bytes=192,
                    nominal_rate_hz=8,
                    causal_task_gain=0.9 if risk_hint > 0.7 else 0.35,
                ),
                FlowSpec(
                    flow_id=f"{robot_id}:obstacles",
                    robot_id=robot_id,
                    topic="/semantic_obstacles",
                    flow_class=FlowClass.PERCEPTION,
                    qos=QoSProfile(
                        reliability="best_effort",
                        depth=1,
                        deadline_ms=160,
                        lifespan_ms=300,
                    ),
                    qoe=QoEProfile(),
                    nominal_size_bytes=2200,
                    nominal_rate_hz=8,
                    causal_task_gain=0.65,
                    redundancy=0.2,
                    semantic_delta_ratio=0.35,
                ),
                FlowSpec(
                    flow_id=f"{robot_id}:video",
                    robot_id=robot_id,
                    topic="/front_camera/qoe",
                    flow_class=FlowClass.HUMAN_QOE,
                    qos=QoSProfile(
                        reliability="best_effort",
                        depth=1,
                        deadline_ms=120,
                        lifespan_ms=180,
                    ),
                    qoe=QoEProfile(
                        operator_visible=operator_focus,
                        smoothness_weight=0.8,
                        freeze_penalty=1.0,
                        visual_confidence_weight=0.7,
                    ),
                    nominal_size_bytes=9000 if operator_focus else 3500,
                    nominal_rate_hz=12 if operator_focus else 2,
                    causal_task_gain=0.7 if operator_focus else 0.05,
                    redundancy=0.15,
                    semantic_delta_ratio=0.75 if operator_focus else 0.4,
                ),
                FlowSpec(
                    flow_id=f"{robot_id}:debug",
                    robot_id=robot_id,
                    topic="/debug/logs",
                    flow_class=FlowClass.DEBUG,
                    qos=QoSProfile(
                        reliability="best_effort",
                        depth=5,
                        deadline_ms=1000,
                        lifespan_ms=2500,
                    ),
                    qoe=QoEProfile(),
                    nominal_size_bytes=1800,
                    nominal_rate_hz=2,
                    causal_task_gain=0.02,
                    redundancy=0.8,
                ),
            ]
        )
    return flows


def run_benchmark(
    robots: int,
    seconds: int,
    seed: int,
    capacity_bytes_per_second: int | None = None,
) -> list[BenchmarkResult]:
    return run_policy_benchmark_matrix(
        _default_policies(),
        robots=robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=capacity_bytes_per_second,
    )


def run_policy_benchmark(
    name: str,
    policy: PolicyCallable,
    *,
    robots: int,
    seconds: int,
    seed: int,
    capacity_bytes_per_second: int | None = None,
) -> BenchmarkResult:
    return run_policy_benchmark_matrix(
        [(name, policy)],
        robots=robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=capacity_bytes_per_second,
    )[0]


def run_policy_benchmark_matrix(
    policies: Iterable[tuple[str, PolicyCallable]],
    *,
    robots: int,
    seconds: int,
    seed: int,
    capacity_bytes_per_second: int | None = None,
) -> list[BenchmarkResult]:
    ticks_per_second = 50
    tick_ms = 1000.0 / ticks_per_second
    ticks = seconds * ticks_per_second
    capacity_per_tick = (
        capacity_bytes_per_second
        if capacity_bytes_per_second is not None
        else max(200_000, robots * 6_000)
    ) // ticks_per_second

    flows = build_fleet_workload(robots, seed)
    return [
        _run_policy(
            name,
            policy,
            flows,
            robots,
            ticks,
            capacity_per_tick,
            seed,
            tick_ms=tick_ms,
            ticks_per_second=ticks_per_second,
        )
        for name, policy in policies
    ]


def _default_policies() -> list[tuple[str, PolicyCallable]]:
    return [
        ("fifo", fifo_policy),
        ("static_priority", static_priority_policy),
        ("fleetqox_csds", CausalSemanticDeadlineScheduler().schedule),
        ("fleetqox_predictive", PredictiveAdmissionController().schedule),
        (
            "fleetqox_predictive_guarded",
            RiskConstrainedPredictiveAdmissionController().schedule,
        ),
        (
            "fleetqox_predictive_lagrangian",
            LagrangianRiskPredictiveAdmissionController().schedule,
        ),
    ]


def format_results(results: Iterable[BenchmarkResult]) -> str:
    headers = [
        "policy",
        "robots",
        "ticks",
        "sent",
        "defer",
        "drop",
        "degraded",
        "compacted",
        "bytes",
        "cmd_miss",
        "stale_state",
        "qoe_ratio",
        "utility",
    ]
    rows = [headers] + [result.as_row() for result in results]
    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    return "\n".join(
        "  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))
        for row in rows
    )


def fifo_policy(
    candidates: list[tuple[FlowSpec, FlowObservation]],
    link: NetworkLink,
) -> list[FlowDecision]:
    remaining = link.capacity_bytes_per_tick
    remaining_packets = link.capacity_packets_per_tick
    decisions: list[FlowDecision] = []
    for spec, obs in candidates:
        if obs.age_ms > spec.qos.lifespan_ms:
            decisions.append(_decision(spec, "drop", 0, "stale"))
            continue
        size = _size(spec, obs)
        if remaining_packets is not None and remaining_packets <= 0:
            decisions.append(_decision(spec, "defer", 0, "fifo packet-rate full"))
            continue
        if size <= remaining:
            decisions.append(_decision(spec, "send", size, "fifo"))
            remaining -= size
            if remaining_packets is not None:
                remaining_packets -= 1
        else:
            decisions.append(_decision(spec, "defer", 0, "fifo full"))
    return decisions


def static_priority_policy(
    candidates: list[tuple[FlowSpec, FlowObservation]],
    link: NetworkLink,
) -> list[FlowDecision]:
    class_rank = {
        FlowClass.SAFETY: 8,
        FlowClass.CONTROL: 7,
        FlowClass.COORDINATION: 6,
        FlowClass.STATE: 5,
        FlowClass.HUMAN_QOE: 4,
        FlowClass.PERCEPTION: 3,
        FlowClass.DEBUG: 1,
        FlowClass.BULK: 0,
    }
    ordered = sorted(
        candidates,
        key=lambda item: (class_rank[item[0].flow_class], item[1].age_ms),
        reverse=True,
    )
    remaining = link.capacity_bytes_per_tick
    remaining_packets = link.capacity_packets_per_tick
    sent_ids = set()
    decisions: list[FlowDecision] = []
    for spec, obs in ordered:
        if obs.age_ms > spec.qos.lifespan_ms:
            decisions.append(_decision(spec, "drop", 0, "stale"))
            sent_ids.add(spec.flow_id)
            continue
        if remaining_packets is not None and remaining_packets <= 0:
            continue
        size = _size(spec, obs)
        if size <= remaining:
            decisions.append(_decision(spec, "send", size, "static priority"))
            remaining -= size
            if remaining_packets is not None:
                remaining_packets -= 1
            sent_ids.add(spec.flow_id)
    for spec, _ in candidates:
        if spec.flow_id not in sent_ids:
            decisions.append(_decision(spec, "defer", 0, "static priority full"))
    return decisions


def _run_policy(
    name: str,
    policy: Callable[[list[tuple[FlowSpec, FlowObservation]], NetworkLink], list[FlowDecision]],
    flows: list[FlowSpec],
    robots: int,
    ticks: int,
    capacity_per_tick: int,
    seed: int,
    tick_ms: float,
    ticks_per_second: int,
) -> BenchmarkResult:
    rng = random.Random(seed)
    ages = {flow.flow_id: 0.0 for flow in flows}
    sent = dropped = deferred = degraded = compacted = bytes_sent = 0
    control_miss_events = 0
    control_total = 0
    stale_state_events = 0
    state_total = 0
    qoe_sent = 0
    qoe_total = 0
    utilities: list[float] = []

    for tick in range(ticks):
        link = NetworkLink(
            capacity_bytes_per_tick=_vary_capacity(capacity_per_tick, tick),
            loss=0.04 + (0.10 if tick % 83 in range(8) else 0.0),
            jitter_ms=8.0 + (18.0 if tick % 57 in range(6) else 0.0),
            rtt_ms=22.0 + (35.0 if tick % 67 in range(4) else 0.0),
        )
        candidates = []
        for flow in flows:
            ages[flow.flow_id] += tick_ms
            if rng.random() > min(0.95, flow.nominal_rate_hz / ticks_per_second):
                continue
            task = _task_for(flow, rng)
            obs = FlowObservation(
                age_ms=ages[flow.flow_id],
                queue_depth=1 if rng.random() < 0.8 else 2,
                measured_loss=link.loss,
                measured_rtt_ms=link.rtt_ms,
                observed_jitter_ms=link.jitter_ms,
                task=task,
            )
            candidates.append((flow, obs))

        decisions = policy(candidates, link)
        by_id = {decision.flow_id: decision for decision in decisions}
        for flow, obs in candidates:
            decision = by_id.get(flow.flow_id)
            if decision is None:
                continue
            if decision.action in {"send", "send_degraded", "send_compacted"}:
                sent += 1
                bytes_sent += decision.allocated_bytes
                ages[flow.flow_id] = 0.0
                utilities.append(_utility(flow, obs, degraded=decision.degraded))
                if decision.degraded:
                    degraded += 1
                if decision.action == "send_compacted":
                    compacted += 1
                if flow.flow_class is FlowClass.HUMAN_QOE and flow.qoe.operator_visible:
                    qoe_sent += 1
            elif decision.action == "drop":
                dropped += 1
                ages[flow.flow_id] = 0.0
            else:
                deferred += 1

            if flow.flow_class is FlowClass.CONTROL:
                control_total += 1
                if obs.age_ms > flow.qos.deadline_ms:
                    control_miss_events += 1
            if flow.flow_class is FlowClass.STATE:
                state_total += 1
                if obs.age_ms > flow.qos.lifespan_ms:
                    stale_state_events += 1
            if flow.flow_class is FlowClass.HUMAN_QOE and flow.qoe.operator_visible:
                qoe_total += 1

    return BenchmarkResult(
        name=name,
        robots=robots,
        ticks=ticks,
        sent=sent,
        dropped=dropped,
        deferred=deferred,
        degraded=degraded,
        compacted=compacted,
        bytes_sent=bytes_sent,
        control_deadline_miss_ratio=control_miss_events / max(1, control_total),
        stale_state_ratio=stale_state_events / max(1, state_total),
        qoe_delivery_ratio=qoe_sent / max(1, qoe_total),
        utility_score=mean(utilities) if utilities else 0.0,
    )


def _task_for(flow: FlowSpec, rng: random.Random) -> TaskContext:
    active = 0.9 if flow.flow_class not in {FlowClass.DEBUG, FlowClass.BULK} else 0.1
    collision_risk = rng.random()
    if flow.flow_class in {FlowClass.CONTROL, FlowClass.COORDINATION}:
        collision_risk = min(1.0, collision_risk + 0.25)
    operator_attention = 1.0 if flow.qoe.operator_visible else 0.0
    coordination = 0.8 if flow.flow_class is FlowClass.COORDINATION else rng.random() * 0.3
    return TaskContext(
        task_id="warehouse_delivery",
        robot_id=flow.robot_id,
        task_criticality=active,
        collision_risk=collision_risk,
        operator_attention=operator_attention,
        coordination_pressure=coordination,
    )


def _vary_capacity(capacity: int, tick: int) -> int:
    if tick % 101 in range(12):
        return max(1, int(capacity * 0.42))
    if tick % 37 in range(4):
        return max(1, int(capacity * 0.68))
    return capacity


def _size(spec: FlowSpec, obs: FlowObservation) -> int:
    return max(1, int(spec.nominal_size_bytes * spec.semantic_delta_ratio * obs.queue_depth))


def _decision(spec: FlowSpec, action: str, bytes_sent: int, reason: str) -> FlowDecision:
    return FlowDecision(
        flow_id=spec.flow_id,
        action=action,
        priority=0.0,
        allocated_bytes=bytes_sent,
        reason=reason,
        reliability=spec.qos.reliability,
        wire_mode="native",
    )


def _utility(flow: FlowSpec, obs: FlowObservation, degraded: bool) -> float:
    task = obs.task.clipped()
    freshness = max(0.0, 1.0 - obs.age_ms / flow.qos.lifespan_ms)
    qoe = task.operator_attention * (
        flow.qoe.smoothness_weight
        + flow.qoe.freeze_penalty
        + flow.qoe.visual_confidence_weight
    )
    utility = (
        4.0 * flow.causal_task_gain
        + 2.5 * task.collision_risk
        + 1.5 * task.coordination_pressure
        + 2.0 * freshness
        + 1.8 * qoe
        - 1.4 * flow.redundancy
    )
    if degraded:
        utility *= 0.72
    return utility
