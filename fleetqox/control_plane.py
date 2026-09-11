"""Predictive FleetQoX control plane prototype.

This is the second research prototype after CSDS. It models the behavior that a
future FleetRMW layer would place before the concrete network transport:

- predict near-term link budget rather than reacting only to the current tick;
- protect control/coordination/state first;
- use semantic compaction for deadline-sensitive flows under pressure;
- choose reliability by freshness and deadline slack.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Mapping, Sequence

from .model import FlowClass, FlowDecision, FlowObservation, FlowSpec, NetworkLink
from .scheduler import CausalSemanticDeadlineScheduler, SchedulerWeights
from .semantic_contract import (
    TransformCandidate,
    TransformKind,
    control_intent_deadline_ms,
    path_tail_wire_ms,
    transform_candidates,
)


CallablePolicy = Callable[
    [Iterable[tuple[FlowSpec, FlowObservation]], NetworkLink],
    list[FlowDecision],
]


_CORE_CLASSES = {
    FlowClass.SAFETY,
    FlowClass.CONTROL,
    FlowClass.COORDINATION,
    FlowClass.STATE,
}
_SENT_ACTIONS = {
    "send",
    "send_compacted",
    "send_degraded",
    "send_intent",
    "send_supervisory_intent",
}


@dataclass(frozen=True)
class PredictiveAdmissionConfig:
    ewma_alpha: float = 0.32
    safety_margin: float = 0.88
    pressure_compaction_threshold: float = 0.82
    pressure_drop_threshold: float = 1.12
    qoe_floor_fraction: float = 0.08
    semantic_compaction_ratio: float = 0.55
    opportunistic_degradation_ratio: float = 0.14
    stale_drop_ratio: float = 0.82


@dataclass(frozen=True)
class RiskConstrainedAdmissionConfig(PredictiveAdmissionConfig):
    deadline_tail_sigma: float = 1.65
    loss_tail_rtt_fraction: float = 0.25
    min_wire_slack_ms: float = 0.0
    guarded_classes: tuple[FlowClass, ...] = (
        FlowClass.SAFETY,
        FlowClass.CONTROL,
    )


@dataclass(frozen=True)
class LagrangianAdmissionConfig(PredictiveAdmissionConfig):
    deadline_risk_budget: float = 0.08
    qoe_risk_budget: float = 0.10
    lambda_learning_rate: float = 0.16
    initial_deadline_lambda: float = 1.8
    initial_qoe_lambda: float = 1.0
    max_deadline_lambda: float = 12.0
    max_qoe_lambda: float = 8.0
    risk_temperature_ms: float = 12.0
    semantic_compaction_value_ratio: float = 0.88
    degraded_value_ratio: float = 0.72
    deadline_drop_risk: float = 0.45
    risk_barrier_start: float = 0.70
    risk_barrier_scale: float = 12.0


@dataclass(frozen=True)
class RobotBudgetConfig:
    """Per-robot SLO controller for budget-aware admission wrappers."""

    min_control_delivery_ratio: float = 0.90
    max_deadline_risk: float = 0.35
    ewma_alpha: float = 0.30
    deficit_decay: float = 0.86
    control_learning_rate: float = 0.90
    deadline_learning_rate: float = 0.65
    critical_gain_scale: float = 0.45
    task_pressure_scale: float = 0.10
    max_pressure: float = 4.0
    risk_temperature_ms: float = 18.0
    network_tail_risk_gain: float = 1.0
    feedback_learning_scale: float = 0.08
    feedback_reference_samples: int = 12
    feedback_deadline_risk_cap: float = 0.55
    feedback_latency_risk_cap: float = 0.65
    feedback_latency_risk_span: float = 1.0
    max_tail_latency_deadline_ratio: float = 1.0
    latency_learning_rate: float = 0.40
    latency_pressure_gain: float = 0.65
    control_first_qoe_margin: float = 0.04
    deadline_shaping_gain: float = 0.0
    egress_feedback_control_weight: float = 1.0
    egress_feedback_deadline_weight: float = 1.0
    egress_feedback_latency_weight: float = 1.0
    local_feedback_success_weight: float = 0.65
    local_feedback_failure_weight: float = 0.80
    local_feedback_deadline_success_weight: float = 0.45
    local_feedback_deadline_failure_weight: float = 0.60
    projection_feedback_latency_weight: float = 0.30
    action_deadline_learning_rate: float = 0.65
    deadline_debt_shed_gain: float = 0.0
    deadline_debt_shed_max_fraction: float = 0.55
    deadline_horizon_lift_enabled: bool = False
    deadline_horizon_lift_min_deficit: float = 0.22
    action_deadline_horizon_lift_min_deficit: float = 0.12
    action_deadline_horizon_lift_min_rtt_ms: float = 90.0
    deadline_firewall_enabled: bool = True
    deadline_firewall_rtt_factor: float = 1.0
    deadline_firewall_jitter_sigma: float = 1.35
    deadline_firewall_loss_rtt_fraction: float = 0.22
    min_control_floor_pressure: float = 0.05
    n_aware_control_floor_enabled: bool = False
    n_aware_control_floor_min_robots: int = 4
    n_aware_control_floor_pressure: float = 0.12
    pressure_shed_start: float = 0.08
    pressure_shed_max_fraction: float = 0.35
    floor_intent_ratio: float = 0.50
    floor_min_intent_bytes: int = 48
    floor_semantic_compaction_ratio: float = 0.55
    floor_degraded_ratio: float = 0.14


@dataclass(frozen=True)
class LinkProfile:
    """Network regime selected from observed path metrics."""

    label: str
    config: LagrangianAdmissionConfig


@dataclass(frozen=True)
class ProfileEnvelope:
    """One candidate Lagrangian operating envelope for a link profile."""

    label: str
    config: LagrangianAdmissionConfig
    critical_non_delivery_budget: float


@dataclass
class _EnvelopeState:
    pulls: int = 0
    utility_ewma: float = 0.0
    deadline_risk_ewma: float = 0.0
    critical_non_delivery_ewma: float = 0.0


@dataclass(frozen=True)
class _Entry:
    priority: float
    density: float
    base_size: int
    slack_ms: float
    reliability: str
    spec: FlowSpec
    obs: FlowObservation


@dataclass(frozen=True)
class _ActionCandidate:
    entry: _Entry
    action: str
    allocated_bytes: int
    net_score: float
    density: float
    risk: float
    qoe_risk: float
    value: float
    degraded: bool
    wire_mode: str
    reason: str


@dataclass(frozen=True)
class _EnvelopeEvaluation:
    profile_label: str
    envelope: ProfileEnvelope
    decisions: list[FlowDecision]
    utility_per_flow: float
    deadline_risk: float
    critical_non_delivery: float
    score: float
    feasible: bool


@dataclass(frozen=True)
class _CertifiedAction:
    spec: FlowSpec
    obs: FlowObservation
    candidate: TransformCandidate
    score: float
    density: float
    loss_penalty: float


@dataclass
class _SemanticVariantState:
    pulls: int = 0
    utility_ewma: float = 0.0
    deadline_risk_ewma: float = 0.0
    critical_non_delivery_ewma: float = 0.0
    loss_exposure_ewma: float = 0.0


@dataclass
class _RobotBudgetState:
    pulls: int = 0
    control_delivery_ewma: float = 1.0
    deadline_risk_ewma: float = 0.0
    latency_risk_ewma: float = 0.0
    control_deficit: float = 0.0
    deadline_deficit: float = 0.0
    latency_deficit: float = 0.0
    action_deadline_deficits: dict[str, float] = field(default_factory=dict)

    def service_pressure(self, config: RobotBudgetConfig) -> float:
        return min(config.max_pressure, self.control_deficit + self.deadline_deficit)

    def deadline_shaping_pressure(self, config: RobotBudgetConfig) -> float:
        return config.deadline_shaping_gain * self.deadline_deficit

    def latency_pressure(self, config: RobotBudgetConfig) -> float:
        margin = max(0.001, config.control_first_qoe_margin)
        control_headroom = self.control_delivery_ewma - config.min_control_delivery_ratio
        control_gate = max(0.0, min(1.0, control_headroom / margin))
        return config.latency_pressure_gain * self.latency_deficit * control_gate

    def pressure(self, config: RobotBudgetConfig) -> float:
        return min(
            config.max_pressure,
            self.service_pressure(config)
            + self.deadline_shaping_pressure(config)
            + self.latency_pressure(config),
        )

    def as_record(self, config: RobotBudgetConfig) -> dict[str, object]:
        return {
            "pulls": self.pulls,
            "control_delivery_ewma": self.control_delivery_ewma,
            "deadline_risk_ewma": self.deadline_risk_ewma,
            "latency_risk_ewma": self.latency_risk_ewma,
            "control_deficit": self.control_deficit,
            "deadline_deficit": self.deadline_deficit,
            "latency_deficit": self.latency_deficit,
            "service_pressure": self.service_pressure(config),
            "deadline_shaping_pressure": self.deadline_shaping_pressure(config),
            "latency_pressure": self.latency_pressure(config),
            "pressure": self.pressure(config),
            "action_deadline_deficits": dict(sorted(self.action_deadline_deficits.items())),
        }


@dataclass(frozen=True)
class _RobotFeedbackSignal:
    source: str
    control_delivery: float | None = None
    deadline_risk: float | None = None
    latency_risk: float | None = None
    action_deadline_risks: Mapping[str, tuple[float, float]] | None = None
    control_weight: float = 1.0
    deadline_weight: float = 1.0
    latency_weight: float = 1.0

    def has_updates(self) -> bool:
        return (
            (self.control_delivery is not None and self.control_weight > 0.0)
            or (self.deadline_risk is not None and self.deadline_weight > 0.0)
            or (self.latency_risk is not None and self.latency_weight > 0.0)
            or bool(self.action_deadline_risks)
        )


@dataclass(frozen=True)
class _SemanticVariantBudgets:
    deadline_risk: float
    critical_non_delivery: float
    loss_exposure: float


@dataclass(frozen=True)
class _SemanticVariantEvaluation:
    label: str
    decisions: list[FlowDecision]
    utility_per_flow: float
    deadline_risk: float
    critical_non_delivery: float
    loss_exposure: float
    score: float
    feasible: bool


class PredictiveAdmissionController(CausalSemanticDeadlineScheduler):
    """Predictive admission and adaptive reliability for fleet communication."""

    def __init__(
        self,
        config: PredictiveAdmissionConfig | None = None,
        weights: SchedulerWeights | None = None,
    ) -> None:
        self.config = config or PredictiveAdmissionConfig()
        super().__init__(
            weights=weights,
            critical_budget_fraction=0.72,
            qoe_budget_fraction=self.config.qoe_floor_fraction,
            degradation_floor=self.config.opportunistic_degradation_ratio,
        )
        self._capacity_ewma: float | None = None
        self._loss_ewma = 0.0
        self._rtt_ewma = 20.0
        self._jitter_ewma = 0.0

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        self._update_link_estimate(link)

        entries: list[_Entry] = []
        decisions: list[FlowDecision] = []
        for spec, obs in candidates:
            spec.validates()
            if obs.age_ms > spec.qos.lifespan_ms:
                decisions.append(self._drop(spec, "stale beyond lifespan", obs))
                continue
            if self._should_preemptively_drop(spec, obs):
                decisions.append(self._drop(spec, "predictive stale drop", obs))
                continue
            base_size = self._effective_size(spec, obs)
            priority = self._predictive_priority(spec, obs, link)
            reliability = self._adaptive_reliability(spec, obs, link)
            entries.append(
                _Entry(
                    priority=priority,
                    density=priority / max(1, base_size),
                    base_size=base_size,
                    slack_ms=spec.qos.deadline_ms - obs.age_ms,
                    reliability=reliability,
                    spec=spec,
                    obs=obs,
                )
            )

        pressure = self._pressure(entries, link)
        remaining = link.capacity_bytes_per_tick
        remaining_packets = link.capacity_packets_per_tick
        selected: list[FlowDecision] = []

        for partition in self._ordered_partitions(entries):
            admitted, remaining, remaining_packets = self._admit_partition(
                partition,
                capacity=remaining,
                pressure=pressure,
                remaining_packets=remaining_packets,
            )
            selected.extend(admitted)

        selected_ids = {decision.flow_id for decision in selected}
        decisions.extend(selected)
        for entry in entries:
            if entry.spec.flow_id in selected_ids:
                continue
            if self._drop_when_unadmitted(entry, pressure):
                decisions.append(self._drop(entry.spec, "predictive queue shedding", entry.obs))
            else:
                decisions.append(
                    FlowDecision(
                        flow_id=entry.spec.flow_id,
                        action="defer",
                        priority=entry.priority,
                        allocated_bytes=0,
                        reason="predictive admission: no budget",
                        reliability=entry.reliability,
                        predicted_slack_ms=entry.slack_ms,
                    )
                )

        return sorted(decisions, key=lambda item: item.flow_id)

    def _update_link_estimate(self, link: NetworkLink) -> None:
        alpha = self.config.ewma_alpha
        if self._capacity_ewma is None:
            self._capacity_ewma = float(link.capacity_bytes_per_tick)
        else:
            self._capacity_ewma = (
                alpha * link.capacity_bytes_per_tick
                + (1.0 - alpha) * self._capacity_ewma
            )
        self._loss_ewma = alpha * link.loss + (1.0 - alpha) * self._loss_ewma
        self._rtt_ewma = alpha * link.rtt_ms + (1.0 - alpha) * self._rtt_ewma
        self._jitter_ewma = alpha * link.jitter_ms + (1.0 - alpha) * self._jitter_ewma

    def _pressure(self, entries: Sequence[_Entry], link: NetworkLink) -> float:
        offered = sum(entry.base_size for entry in entries)
        predicted_capacity = min(
            float(link.capacity_bytes_per_tick),
            (self._capacity_ewma or float(link.capacity_bytes_per_tick))
            * self.config.safety_margin
            * (1.0 - min(0.35, self._loss_ewma)),
        )
        return offered / max(1.0, predicted_capacity)

    def _ordered_partitions(self, entries: Sequence[_Entry]) -> list[list[_Entry]]:
        safety_control = [
            entry
            for entry in entries
            if entry.spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}
        ]
        coordination_state = [
            entry
            for entry in entries
            if entry.spec.flow_class in {FlowClass.COORDINATION, FlowClass.STATE}
        ]
        operator_qoe = [
            entry
            for entry in entries
            if entry.spec.flow_class is FlowClass.HUMAN_QOE
            and entry.obs.task.operator_attention > 0
        ]
        remaining = [
            entry
            for entry in entries
            if entry not in safety_control
            and entry not in coordination_state
            and entry not in operator_qoe
        ]
        return [
            sorted(safety_control, key=lambda item: (item.slack_ms, -item.priority)),
            sorted(coordination_state, key=lambda item: (item.slack_ms, -item.priority)),
            sorted(operator_qoe, key=lambda item: (-item.priority, item.slack_ms)),
            sorted(remaining, key=lambda item: (item.density, item.priority), reverse=True),
        ]

    def _admit_partition(
        self,
        entries: Sequence[_Entry],
        *,
        capacity: int,
        pressure: float,
        remaining_packets: int | None = None,
    ) -> tuple[list[FlowDecision], int, int | None]:
        decisions: list[FlowDecision] = []
        remaining = capacity
        for entry in entries:
            if remaining_packets is not None and remaining_packets <= 0:
                # Real 802.11 airtime per packet is mostly fixed overhead
                # (DIFS/backoff/preamble/ACK), independent of payload size --
                # once the packet-rate ceiling is hit, no further entry can
                # be admitted regardless of remaining byte budget.
                break
            decision = self._decision_for_entry(entry, pressure, remaining)
            if decision and decision.allocated_bytes <= remaining:
                decisions.append(decision)
                remaining -= decision.allocated_bytes
                if remaining_packets is not None:
                    remaining_packets -= 1
        return decisions, remaining, remaining_packets

    def _decision_for_entry(
        self,
        entry: _Entry,
        pressure: float,
        remaining: int,
    ) -> FlowDecision | None:
        spec = entry.spec
        should_compact = self._should_compact(entry, pressure, remaining)
        if should_compact:
            compacted = max(1, int(entry.base_size * self.config.semantic_compaction_ratio))
            return FlowDecision(
                flow_id=spec.flow_id,
                action="send_compacted",
                priority=entry.priority,
                allocated_bytes=compacted,
                reason="predictive admission: semantic compaction",
                reliability=entry.reliability,
                wire_mode="semantic_delta",
                predicted_slack_ms=entry.slack_ms,
            )

        if entry.base_size <= remaining:
            return FlowDecision(
                flow_id=spec.flow_id,
                action="send",
                priority=entry.priority,
                allocated_bytes=entry.base_size,
                reason="predictive admission",
                reliability=entry.reliability,
                wire_mode="native",
                predicted_slack_ms=entry.slack_ms,
            )

        if self._degradable(spec):
            degraded = max(1, int(entry.base_size * self.config.opportunistic_degradation_ratio))
            if degraded <= remaining:
                return FlowDecision(
                    flow_id=spec.flow_id,
                    action="send_degraded",
                    priority=entry.priority,
                    allocated_bytes=degraded,
                    reason="predictive admission: QoE degradation",
                    degraded=True,
                    reliability=entry.reliability,
                    wire_mode="degraded",
                    predicted_slack_ms=entry.slack_ms,
                )
        return None

    def _should_compact(self, entry: _Entry, pressure: float, remaining: int) -> bool:
        if entry.spec.flow_class not in _CORE_CLASSES:
            return False
        if pressure >= self.config.pressure_compaction_threshold:
            return True
        if entry.base_size > remaining and entry.slack_ms <= entry.spec.qos.deadline_ms:
            return True
        return False

    def _adaptive_reliability(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
    ) -> str:
        slack = spec.qos.deadline_ms - obs.age_ms
        fresh_enough_for_retry = slack > max(link.rtt_ms, self._rtt_ewma) * 1.35
        if spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL, FlowClass.COORDINATION}:
            return "reliable" if fresh_enough_for_retry and link.loss >= 0.03 else "best_effort_fresh"
        if spec.flow_class is FlowClass.STATE:
            return "reliable" if fresh_enough_for_retry and link.loss < 0.08 else "best_effort_fresh"
        return "best_effort_fresh"

    def _predictive_priority(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
    ) -> float:
        base = self._priority(spec, obs, link)
        slack = spec.qos.deadline_ms - obs.age_ms
        deadline_urgency = max(0.0, 1.0 - slack / spec.qos.deadline_ms)
        forecast_penalty = self._loss_ewma + self._jitter_ewma / max(1.0, spec.qos.deadline_ms)
        core_boost = 2.5 if spec.flow_class in _CORE_CLASSES else 0.0
        return base + 5.0 * deadline_urgency + core_boost - forecast_penalty

    def _should_preemptively_drop(self, spec: FlowSpec, obs: FlowObservation) -> bool:
        if spec.flow_class in _CORE_CLASSES:
            return False
        return obs.age_ms >= spec.qos.lifespan_ms * self.config.stale_drop_ratio

    def _drop_when_unadmitted(self, entry: _Entry, pressure: float) -> bool:
        if entry.spec.flow_class in _CORE_CLASSES:
            return False
        if pressure >= self.config.pressure_drop_threshold:
            return True
        return entry.obs.age_ms >= entry.spec.qos.lifespan_ms * 0.65

    def _drop(self, spec: FlowSpec, reason: str, obs: FlowObservation) -> FlowDecision:
        return FlowDecision(
            flow_id=spec.flow_id,
            action="drop",
            priority=-self.weights.stale_penalty,
            allocated_bytes=0,
            reason=reason,
            predicted_slack_ms=spec.qos.deadline_ms - obs.age_ms,
        )


class RiskConstrainedPredictiveAdmissionController(PredictiveAdmissionController):
    """Predictive admission with a deadline-risk safety layer.

    The base predictive controller maximizes delivered semantic value under a
    predicted capacity budget. This variant adds a final guard: deadline-bound
    flows are not transmitted if the estimated tail wire time no longer fits
    inside their remaining slack. This keeps stale-but-high-value samples from
    consuming the network and appearing as deadline misses at the receiver.
    """

    def __init__(
        self,
        config: RiskConstrainedAdmissionConfig | None = None,
        weights: SchedulerWeights | None = None,
    ) -> None:
        self.risk_config = config or RiskConstrainedAdmissionConfig()
        super().__init__(config=self.risk_config, weights=weights)

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        candidate_list = list(candidates)
        link.validates()
        self._update_link_estimate(link)
        tail_wire_ms = self._tail_wire_time_ms(link)

        safe_candidates: list[tuple[FlowSpec, FlowObservation]] = []
        guarded_drops: list[FlowDecision] = []
        for spec, obs in candidate_list:
            if self._deadline_risky(spec, obs, tail_wire_ms):
                guarded_drops.append(self._guard_drop(spec, obs, link, tail_wire_ms))
            else:
                safe_candidates.append((spec, obs))

        decisions = super().schedule(safe_candidates, link)
        return sorted(decisions + guarded_drops, key=lambda item: item.flow_id)

    def _deadline_risky(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        tail_wire_ms: float,
    ) -> bool:
        if spec.flow_class not in self.risk_config.guarded_classes:
            return False
        slack_ms = spec.qos.deadline_ms - obs.age_ms
        return slack_ms < tail_wire_ms + self.risk_config.min_wire_slack_ms

    def _guard_drop(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        tail_wire_ms: float,
    ) -> FlowDecision:
        slack_ms = spec.qos.deadline_ms - obs.age_ms
        return FlowDecision(
            flow_id=spec.flow_id,
            action="drop",
            priority=-self.weights.stale_penalty,
            allocated_bytes=0,
            reason=(
                "deadline-risk guard: "
                f"tail_wire_ms={tail_wire_ms:.1f} exceeds slack_ms={slack_ms:.1f}"
            ),
            reliability=self._adaptive_reliability(spec, obs, link),
            wire_mode="",
            predicted_slack_ms=slack_ms,
        )

    def _tail_wire_time_ms(self, link: NetworkLink) -> float:
        rtt_ms = max(link.rtt_ms, self._rtt_ewma)
        jitter_ms = max(link.jitter_ms, self._jitter_ewma)
        loss = max(link.loss, self._loss_ewma)
        one_way_ms = 0.5 * rtt_ms
        jitter_tail_ms = self.risk_config.deadline_tail_sigma * jitter_ms
        loss_tail_ms = self.risk_config.loss_tail_rtt_fraction * loss * rtt_ms
        return one_way_ms + jitter_tail_ms + loss_tail_ms


class LagrangianRiskPredictiveAdmissionController(PredictiveAdmissionController):
    """Soft risk-constrained predictive admission.

    This policy keeps the predictive/semantic admission idea, but replaces the
    hard deadline gate with online Lagrangian penalties. It estimates deadline
    and operator-QoE risk for each possible wire action, then selects actions by
    utility minus learned risk cost under the current byte budget.
    """

    def __init__(
        self,
        config: LagrangianAdmissionConfig | None = None,
        weights: SchedulerWeights | None = None,
    ) -> None:
        self.lagrangian_config = config or LagrangianAdmissionConfig()
        super().__init__(config=self.lagrangian_config, weights=weights)
        self._deadline_lambda = self.lagrangian_config.initial_deadline_lambda
        self._qoe_lambda = self.lagrangian_config.initial_qoe_lambda

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        self._update_link_estimate(link)

        entries: list[_Entry] = []
        decisions: list[FlowDecision] = []
        for spec, obs in candidates:
            spec.validates()
            if obs.age_ms > spec.qos.lifespan_ms:
                decisions.append(self._drop(spec, "stale beyond lifespan", obs))
                continue
            if self._should_preemptively_drop(spec, obs):
                decisions.append(self._drop(spec, "lagrangian stale drop", obs))
                continue
            base_size = self._effective_size(spec, obs)
            priority = self._predictive_priority(spec, obs, link)
            reliability = self._adaptive_reliability(spec, obs, link)
            entries.append(
                _Entry(
                    priority=priority,
                    density=priority / max(1, base_size),
                    base_size=base_size,
                    slack_ms=spec.qos.deadline_ms - obs.age_ms,
                    reliability=reliability,
                    spec=spec,
                    obs=obs,
                )
            )

        pressure = self._pressure(entries, link)
        selected: list[_ActionCandidate] = []
        remaining = link.capacity_bytes_per_tick
        for partition in self._lagrangian_partitions(entries):
            choices = [self._best_candidate(entry, link, pressure) for entry in partition]
            admitted, remaining = self._admit_lagrangian_partition(choices, remaining)
            selected.extend(admitted)

        selected_ids = {candidate.entry.spec.flow_id for candidate in selected}
        for candidate in selected:
            decisions.append(self._decision_from_candidate(candidate))

        for entry in entries:
            if entry.spec.flow_id in selected_ids:
                continue
            risk = self._deadline_risk(entry.spec, entry.obs, link, entry.base_size)
            if risk >= self.lagrangian_config.deadline_drop_risk:
                decisions.append(
                    self._drop(
                        entry.spec,
                        f"lagrangian risk reset: risk={risk:.3f}",
                        entry.obs,
                    )
                )
            elif self._drop_when_unadmitted(entry, pressure):
                decisions.append(self._drop(entry.spec, "lagrangian queue shedding", entry.obs))
            else:
                decisions.append(
                    FlowDecision(
                        flow_id=entry.spec.flow_id,
                        action="defer",
                        priority=entry.priority,
                        allocated_bytes=0,
                        reason="lagrangian admission: no positive budget",
                        reliability=entry.reliability,
                        predicted_slack_ms=entry.slack_ms,
                    )
                )

        self._update_multipliers(selected)
        return sorted(decisions, key=lambda item: item.flow_id)

    def _lagrangian_partitions(self, entries: Sequence[_Entry]) -> list[list[_Entry]]:
        safety_control = [
            entry
            for entry in entries
            if entry.spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}
        ]
        focused_qoe = [
            entry
            for entry in entries
            if entry.spec.flow_class is FlowClass.HUMAN_QOE
            and entry.obs.task.operator_attention > 0
        ]
        coordination_state = [
            entry
            for entry in entries
            if entry.spec.flow_class in {FlowClass.COORDINATION, FlowClass.STATE}
        ]
        remaining = [
            entry
            for entry in entries
            if entry not in safety_control
            and entry not in focused_qoe
            and entry not in coordination_state
        ]
        return [
            sorted(safety_control, key=lambda item: (item.slack_ms, -item.priority)),
            sorted(focused_qoe, key=lambda item: (-item.priority, item.slack_ms)),
            sorted(coordination_state, key=lambda item: (item.slack_ms, -item.priority)),
            sorted(remaining, key=lambda item: (item.density, item.priority), reverse=True),
        ]

    def _best_candidate(
        self,
        entry: _Entry,
        link: NetworkLink,
        pressure: float,
    ) -> _ActionCandidate | None:
        candidates = self._action_candidates(entry, link, pressure)
        positive = [candidate for candidate in candidates if candidate.net_score > 0.0]
        if not positive:
            return None
        return max(positive, key=lambda item: (item.density, item.net_score))

    def _action_candidates(
        self,
        entry: _Entry,
        link: NetworkLink,
        pressure: float,
    ) -> list[_ActionCandidate]:
        spec = entry.spec
        base_value = self._semantic_value(spec, entry.obs)
        candidates = [
            self._score_action(
                entry=entry,
                link=link,
                action="send",
                allocated_bytes=entry.base_size,
                value=base_value,
                degraded=False,
                wire_mode="native",
                reason="lagrangian admission",
            )
        ]

        if spec.flow_class in _CORE_CLASSES and pressure >= self.config.pressure_compaction_threshold:
            compacted = max(1, int(entry.base_size * self.config.semantic_compaction_ratio))
            candidates.append(
                self._score_action(
                    entry=entry,
                    link=link,
                    action="send_compacted",
                    allocated_bytes=compacted,
                    value=base_value * self.lagrangian_config.semantic_compaction_value_ratio,
                    degraded=False,
                    wire_mode="semantic_delta",
                    reason="lagrangian admission: semantic compaction",
                )
            )

        if self._degradable(spec):
            degraded_bytes = max(
                1,
                int(entry.base_size * self.config.opportunistic_degradation_ratio),
            )
            candidates.append(
                self._score_action(
                    entry=entry,
                    link=link,
                    action="send_degraded",
                    allocated_bytes=degraded_bytes,
                    value=base_value * self.lagrangian_config.degraded_value_ratio,
                    degraded=True,
                    wire_mode="degraded",
                    reason="lagrangian admission: QoE degradation",
                )
            )
        return candidates

    def _score_action(
        self,
        *,
        entry: _Entry,
        link: NetworkLink,
        action: str,
        allocated_bytes: int,
        value: float,
        degraded: bool,
        wire_mode: str,
        reason: str,
    ) -> _ActionCandidate:
        risk = self._deadline_risk(entry.spec, entry.obs, link, allocated_bytes)
        qoe_risk = self._qoe_risk(entry.spec, entry.obs, link, allocated_bytes)
        class_weight = self._risk_class_weight(entry.spec.flow_class)
        high_risk_excess = max(0.0, risk - self.lagrangian_config.risk_barrier_start)
        barrier_penalty = (
            self._deadline_lambda
            * class_weight
            * self.lagrangian_config.risk_barrier_scale
            * high_risk_excess
            * high_risk_excess
        )
        net_score = (
            value
            + 0.45 * entry.priority
            - self._deadline_lambda * class_weight * risk
            - barrier_penalty
            - self._qoe_lambda * qoe_risk
        )
        density = net_score / max(1, allocated_bytes)
        return _ActionCandidate(
            entry=entry,
            action=action,
            allocated_bytes=allocated_bytes,
            net_score=net_score,
            density=density,
            risk=risk,
            qoe_risk=qoe_risk,
            value=value,
            degraded=degraded,
            wire_mode=wire_mode,
            reason=reason,
        )

    def _admit_lagrangian_partition(
        self,
        candidates: Sequence[_ActionCandidate | None],
        capacity: int,
    ) -> tuple[list[_ActionCandidate], int]:
        admitted: list[_ActionCandidate] = []
        remaining = capacity
        ordered = sorted(
            [candidate for candidate in candidates if candidate is not None],
            key=lambda item: (item.density, item.net_score),
            reverse=True,
        )
        for candidate in ordered:
            if candidate.allocated_bytes <= remaining:
                admitted.append(candidate)
                remaining -= candidate.allocated_bytes
        return admitted, remaining

    def _decision_from_candidate(self, candidate: _ActionCandidate) -> FlowDecision:
        return FlowDecision(
            flow_id=candidate.entry.spec.flow_id,
            action=candidate.action,
            priority=candidate.net_score,
            allocated_bytes=candidate.allocated_bytes,
            reason=(
                f"{candidate.reason}: risk={candidate.risk:.3f} "
                f"lambda={self._deadline_lambda:.2f}"
            ),
            degraded=candidate.degraded,
            reliability=candidate.entry.reliability,
            wire_mode=candidate.wire_mode,
            predicted_slack_ms=candidate.entry.slack_ms,
        )

    def _deadline_risk(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        allocated_bytes: int,
    ) -> float:
        if spec.flow_class not in _CORE_CLASSES and spec.flow_class is not FlowClass.HUMAN_QOE:
            return 0.0
        slack_ms = spec.qos.deadline_ms - obs.age_ms
        tail_ms = self._tail_wire_time_ms(link, allocated_bytes)
        return self._logistic_risk(tail_ms - slack_ms)

    def _qoe_risk(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        allocated_bytes: int,
    ) -> float:
        if spec.flow_class is not FlowClass.HUMAN_QOE or obs.task.operator_attention <= 0:
            return 0.0
        slack_ms = spec.qos.deadline_ms - obs.age_ms
        tail_ms = self._tail_wire_time_ms(link, allocated_bytes)
        freeze_pressure = max(0.0, obs.age_ms / max(1.0, spec.qos.lifespan_ms))
        return min(1.0, 0.5 * self._logistic_risk(tail_ms - slack_ms) + 0.5 * freeze_pressure)

    def _tail_wire_time_ms(self, link: NetworkLink, allocated_bytes: int) -> float:
        rtt_ms = max(link.rtt_ms, self._rtt_ewma)
        jitter_ms = max(link.jitter_ms, self._jitter_ewma)
        loss = max(link.loss, self._loss_ewma)
        serialization_ms = 20.0 * allocated_bytes / max(1.0, float(link.capacity_bytes_per_tick))
        return 0.5 * rtt_ms + 1.35 * jitter_ms + 0.22 * loss * rtt_ms + serialization_ms

    def _logistic_risk(self, margin_ms: float) -> float:
        temperature = max(1.0, self.lagrangian_config.risk_temperature_ms)
        x = max(-60.0, min(60.0, margin_ms / temperature))
        return 1.0 / (1.0 + math.exp(-x))

    def _risk_class_weight(self, flow_class: FlowClass) -> float:
        if flow_class is FlowClass.SAFETY:
            return 5.0
        if flow_class is FlowClass.CONTROL:
            return 3.0
        if flow_class is FlowClass.COORDINATION:
            return 1.3
        if flow_class is FlowClass.STATE:
            return 0.65
        if flow_class is FlowClass.HUMAN_QOE:
            return 0.8
        return 0.0

    def _semantic_value(self, flow: FlowSpec, obs: FlowObservation) -> float:
        task = obs.task.clipped()
        freshness = max(0.0, 1.0 - obs.age_ms / flow.qos.lifespan_ms)
        qoe = task.operator_attention * (
            flow.qoe.smoothness_weight
            + flow.qoe.freeze_penalty
            + flow.qoe.visual_confidence_weight
        )
        return (
            4.0 * flow.causal_task_gain
            + 2.5 * task.collision_risk
            + 1.5 * task.coordination_pressure
            + 2.0 * freshness
            + 1.8 * qoe
            - 1.4 * flow.redundancy
        )

    def _update_multipliers(self, selected: Sequence[_ActionCandidate]) -> None:
        deadline_items = [item.risk for item in selected if item.risk > 0.0]
        qoe_items = [item.qoe_risk for item in selected if item.qoe_risk > 0.0]
        deadline_risk = sum(deadline_items) / max(1, len(deadline_items))
        qoe_risk = sum(qoe_items) / max(1, len(qoe_items))
        cfg = self.lagrangian_config
        self._deadline_lambda = min(
            cfg.max_deadline_lambda,
            max(
                0.0,
                self._deadline_lambda
                + cfg.lambda_learning_rate * (deadline_risk - cfg.deadline_risk_budget),
            ),
        )
        self._qoe_lambda = min(
            cfg.max_qoe_lambda,
            max(
                0.0,
                self._qoe_lambda
                + cfg.lambda_learning_rate * (qoe_risk - cfg.qoe_risk_budget),
            ),
        )


class SemanticContractAdmissionController(LagrangianRiskPredictiveAdmissionController):
    """Schedule certified semantic transform candidates directly.

    This controller is the first version where `raw`, `semantic_delta`,
    `degraded`, and `control_intent` are all first-class scheduling candidates.
    The byte budget is enforced after candidate generation, so intent packets no
    longer appear as an after-the-fact rewrite of dropped control samples.
    """

    def __init__(
        self,
        config: LagrangianAdmissionConfig | None = None,
        weights: SchedulerWeights | None = None,
        *,
        intent_ratio: float = 0.50,
        min_intent_bytes: int = 48,
        enable_loss_shadow: bool = False,
    ) -> None:
        super().__init__(config=config, weights=weights)
        self.intent_ratio = intent_ratio
        self.min_intent_bytes = min_intent_bytes
        self.enable_loss_shadow = enable_loss_shadow

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        self._update_link_estimate(link)
        candidate_list = list(candidates)

        decisions: list[FlowDecision] = []
        certified: list[_CertifiedAction] = []
        generated: dict[str, tuple[FlowSpec, FlowObservation, tuple[TransformCandidate, ...]]] = {}
        stale_ids: set[str] = set()

        for spec, obs in candidate_list:
            spec.validates()
            if obs.age_ms > spec.qos.lifespan_ms:
                decisions.append(self._drop(spec, "stale beyond lifespan", obs))
                stale_ids.add(spec.flow_id)
                continue
            transform_options = transform_candidates(
                spec,
                obs,
                link,
                min_intent_bytes=self.min_intent_bytes,
                semantic_compaction_ratio=self.config.semantic_compaction_ratio,
                degraded_ratio=self.config.opportunistic_degradation_ratio,
                intent_ratio=self.intent_ratio,
            )
            if any(
                item.transform.kind is TransformKind.RAW and item.certificate.feasible
                for item in transform_options
            ):
                transform_options = tuple(
                    item
                    for item in transform_options
                    if item.transform.kind
                    not in {
                        TransformKind.CONTROL_INTENT,
                        TransformKind.SUPERVISORY_INTENT,
                    }
                )
            elif any(
                item.transform.kind is TransformKind.CONTROL_INTENT
                and item.certificate.feasible
                for item in transform_options
            ):
                transform_options = tuple(
                    item
                    for item in transform_options
                    if item.transform.kind is not TransformKind.SUPERVISORY_INTENT
                )
            generated[spec.flow_id] = (spec, obs, transform_options)

        scarcity = self._semantic_scarcity(generated.values(), link)
        for spec, obs, transform_options in generated.values():
            for candidate in transform_options:
                if not candidate.certificate.feasible:
                    continue
                if self._pruned_by_semantic_scarcity(
                    spec,
                    candidate,
                    transform_options,
                    scarcity,
                ):
                    continue
                action = self._certified_action(spec, obs, candidate, link, scarcity)
                if action.score > 0.0:
                    certified.append(action)

        remaining = link.capacity_bytes_per_tick
        noncritical_packet_budget = self._noncritical_packet_budget(
            link,
            scarcity,
            certified,
        )
        selected: list[_CertifiedAction] = []
        selected_ids: set[str] = set()
        for partition in self._certified_partitions(certified):
            ordered = sorted(
                partition,
                key=lambda item: (item.score, item.density),
                reverse=True,
            )
            for action in ordered:
                if action.spec.flow_id in selected_ids:
                    continue
                if action.candidate.allocated_bytes > remaining:
                    continue
                if (
                    self._counts_against_loss_packet_budget(action.spec.flow_class)
                    and noncritical_packet_budget is not None
                    and noncritical_packet_budget <= 0
                ):
                    continue
                selected.append(action)
                selected_ids.add(action.spec.flow_id)
                remaining -= action.candidate.allocated_bytes
                if (
                    self._counts_against_loss_packet_budget(action.spec.flow_class)
                    and noncritical_packet_budget is not None
                ):
                    noncritical_packet_budget -= 1

        decisions.extend(self._decision_from_certified(action) for action in selected)
        for spec, obs in candidate_list:
            if spec.flow_id in selected_ids or spec.flow_id in stale_ids:
                continue
            transform_options = generated.get(spec.flow_id, (spec, obs, ()))[2]
            if any(item.certificate.feasible for item in transform_options):
                best_reason = "no budget"
                best_risk = min(
                    (item.certificate.deadline_risk for item in transform_options),
                    default=1.0,
                )
                decisions.append(
                    FlowDecision(
                        flow_id=spec.flow_id,
                        action="defer",
                        priority=0.0,
                        allocated_bytes=0,
                        reason=(
                            "semantic contract: feasible transform not admitted; "
                            f"{best_reason}; best_risk={best_risk:.3f}"
                        ),
                        reliability=spec.qos.reliability,
                        predicted_slack_ms=spec.qos.deadline_ms - obs.age_ms,
                    )
                )
            else:
                best = min(
                    transform_options,
                    key=lambda item: item.certificate.deadline_risk,
                    default=None,
                )
                detail = best.certificate.reason if best else "no transform"
                risk = best.certificate.deadline_risk if best else 1.0
                decisions.append(
                    self._drop(
                        spec,
                        f"semantic contract: no feasible transform; {detail}; risk={risk:.3f}",
                        obs,
                    )
                )

        return sorted(decisions, key=lambda item: item.flow_id)

    def _certified_action(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        candidate: TransformCandidate,
        link: NetworkLink,
        scarcity: float,
    ) -> _CertifiedAction:
        certificate = candidate.certificate
        transform = candidate.transform
        value = self._semantic_value(spec, obs) * transform.value_ratio
        urgency = max(
            0.0,
            1.0 - obs.age_ms / max(1.0, transform.effective_deadline_ms),
        )
        class_bonus = self._semantic_contract_class_bonus(spec.flow_class)
        if transform.kind is TransformKind.CONTROL_INTENT:
            intent_bonus = 1.5
        elif transform.kind is TransformKind.SUPERVISORY_INTENT:
            intent_bonus = 0.8
        else:
            intent_bonus = 0.0
        risk_penalty = (
            9.0
            * self._risk_class_weight(spec.flow_class)
            * certificate.deadline_risk
        )
        loss_penalty = self._semantic_loss_penalty(spec, candidate, link, scarcity)
        score = (
            value
            + 2.5 * urgency
            + class_bonus
            + intent_bonus
            - risk_penalty
            - loss_penalty
        )
        density = score / max(1, candidate.allocated_bytes)
        return _CertifiedAction(
            spec=spec,
            obs=obs,
            candidate=candidate,
            score=score,
            density=density,
            loss_penalty=loss_penalty,
        )

    def _semantic_loss_penalty(
        self,
        spec: FlowSpec,
        candidate: TransformCandidate,
        link: NetworkLink,
        scarcity: float,
    ) -> float:
        if not self.enable_loss_shadow:
            return 0.0
        pressure = self._loss_shadow_pressure(link, scarcity)
        if pressure <= 0.0:
            return 0.0
        packet_cost = 1.0 + min(
            2.0,
            candidate.allocated_bytes / max(1.0, float(link.capacity_bytes_per_tick)),
        )
        class_weight = {
            FlowClass.SAFETY: 0.10,
            FlowClass.CONTROL: 0.14,
            FlowClass.COORDINATION: 0.65,
            FlowClass.STATE: 0.85,
            FlowClass.HUMAN_QOE: 1.00 if spec.qoe.operator_visible else 1.25,
            FlowClass.PERCEPTION: 1.35,
            FlowClass.DEBUG: 2.20,
            FlowClass.BULK: 2.80,
        }[spec.flow_class]
        transform_weight = {
            TransformKind.CONTROL_INTENT: 0.22,
            TransformKind.SUPERVISORY_INTENT: 0.18,
            TransformKind.SEMANTIC_DELTA: 0.72,
            TransformKind.DEGRADED: 0.90,
            TransformKind.RAW: 1.15,
        }[candidate.transform.kind]
        return 2.8 * pressure * packet_cost * class_weight * transform_weight

    def _loss_shadow_pressure(self, link: NetworkLink, scarcity: float) -> float:
        if not self.enable_loss_shadow:
            return 0.0
        loss = max(link.loss, self._loss_ewma)
        loss_excess = max(0.0, loss - 0.012) / 0.018
        scarcity_excess = max(0.0, scarcity - 1.0) * 0.35
        jitter_excess = max(0.0, link.jitter_ms - 12.0) / 120.0
        return min(2.5, loss_excess + scarcity_excess + jitter_excess)

    def _noncritical_packet_budget(
        self,
        link: NetworkLink,
        scarcity: float,
        actions: Sequence[_CertifiedAction],
    ) -> int | None:
        if not self.enable_loss_shadow:
            return None
        pressure = self._loss_shadow_pressure(link, scarcity)
        if pressure <= 0.05:
            return None
        noncritical = [
            item
            for item in actions
            if self._counts_against_loss_packet_budget(item.spec.flow_class)
        ]
        if not noncritical:
            return None
        baseline = max(1, int(math.ceil(len(noncritical) * 0.78)))
        budget = baseline - int(math.floor(pressure))
        return max(1, min(len(noncritical), budget))

    def _counts_against_loss_packet_budget(self, flow_class: FlowClass) -> bool:
        return flow_class not in {FlowClass.SAFETY, FlowClass.CONTROL}

    def _semantic_scarcity(
        self,
        generated: Iterable[tuple[FlowSpec, FlowObservation, tuple[TransformCandidate, ...]]],
        link: NetworkLink,
    ) -> float:
        preferred_raw_demand = 0
        for _spec, _obs, candidates in generated:
            feasible = [item for item in candidates if item.certificate.feasible]
            if not feasible:
                continue
            raw = next(
                (
                    item
                    for item in feasible
                    if item.transform.kind is TransformKind.RAW
                ),
                None,
            )
            if raw is not None:
                preferred_raw_demand += raw.allocated_bytes
            else:
                preferred_raw_demand += min(
                    item.allocated_bytes for item in feasible
                )
        return preferred_raw_demand / max(1, link.capacity_bytes_per_tick)

    def _pruned_by_semantic_scarcity(
        self,
        spec: FlowSpec,
        candidate: TransformCandidate,
        candidates: Sequence[TransformCandidate],
        scarcity: float,
    ) -> bool:
        if scarcity <= 1.0:
            return False
        if candidate.transform.kind is not TransformKind.RAW:
            return False
        if spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}:
            return False
        alternatives = [
            item
            for item in candidates
            if item.transform.kind is not TransformKind.RAW
            and item.certificate.feasible
            and item.allocated_bytes < candidate.allocated_bytes
        ]
        if not alternatives:
            return False
        best_alternative = max(
            alternatives,
            key=lambda item: (
                item.transform.value_ratio,
                -item.certificate.deadline_risk,
                -item.allocated_bytes,
            ),
        )
        return best_alternative.transform.value_ratio >= 0.65

    def _certified_partitions(
        self,
        actions: Sequence[_CertifiedAction],
    ) -> list[list[_CertifiedAction]]:
        safety_control = [
            item
            for item in actions
            if item.spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}
        ]
        coordination_state = [
            item
            for item in actions
            if item.spec.flow_class in {FlowClass.COORDINATION, FlowClass.STATE}
        ]
        focused_qoe = [
            item
            for item in actions
            if item.spec.flow_class is FlowClass.HUMAN_QOE
            and item.obs.task.operator_attention > 0
        ]
        remaining = [
            item
            for item in actions
            if item not in safety_control
            and item not in coordination_state
            and item not in focused_qoe
        ]
        return [safety_control, coordination_state, focused_qoe, remaining]

    def _decision_from_certified(self, action: _CertifiedAction) -> FlowDecision:
        candidate = action.candidate
        certificate = candidate.certificate
        transform = candidate.transform
        loss_detail = (
            f"; loss_price={action.loss_penalty:.2f}"
            if action.loss_penalty > 0.0
            else ""
        )
        return FlowDecision(
            flow_id=action.spec.flow_id,
            action=transform.action,
            priority=action.score,
            allocated_bytes=candidate.allocated_bytes,
            reason=(
                "semantic contract: "
                f"transform={transform.kind.value}; "
                f"certificate={certificate.reason}; "
                f"risk={certificate.deadline_risk:.3f}; "
                f"slack={certificate.slack_after_wire_ms:.1f}"
                f"{loss_detail}"
            ),
            degraded=transform.kind is TransformKind.DEGRADED,
            reliability=transform.reliability,
            wire_mode=transform.wire_mode,
            predicted_slack_ms=certificate.slack_after_wire_ms,
        )

    def _semantic_contract_class_bonus(self, flow_class: FlowClass) -> float:
        if flow_class is FlowClass.SAFETY:
            return 7.0
        if flow_class is FlowClass.CONTROL:
            return 5.0
        if flow_class is FlowClass.COORDINATION:
            return 2.2
        if flow_class is FlowClass.STATE:
            return 1.2
        if flow_class is FlowClass.HUMAN_QOE:
            return 1.0
        return 0.0


class AdaptiveSemanticContractAdmissionController(SemanticContractAdmissionController):
    """Primal-dual selector over semantic-contract operating modes.

    `SemanticContractAdmissionController` maximizes semantic utility from the
    currently certified representations. Its loss-aware sibling deliberately
    spends fewer non-critical packets under unstable links. This selector keeps
    both as shadow controllers, previews their decisions on the same batch, and
    chooses the variant that maximizes utility after contract-derived penalties
    for deadline risk, critical non-delivery, and packet loss exposure.
    """

    def __init__(
        self,
        config: LagrangianAdmissionConfig | None = None,
        weights: SchedulerWeights | None = None,
        *,
        intent_ratio: float = 0.50,
        min_intent_bytes: int = 48,
        reward_alpha: float = 0.24,
        dual_learning_rate: float = 0.38,
    ) -> None:
        super().__init__(
            config=config,
            weights=weights,
            intent_ratio=intent_ratio,
            min_intent_bytes=min_intent_bytes,
        )
        self.reward_alpha = reward_alpha
        self.dual_learning_rate = dual_learning_rate
        self.variant_controllers = {
            "utility": SemanticContractAdmissionController(
                config=self.lagrangian_config,
                weights=weights,
                intent_ratio=intent_ratio,
                min_intent_bytes=min_intent_bytes,
                enable_loss_shadow=False,
            ),
            "tail_shield": SemanticContractAdmissionController(
                config=self.lagrangian_config,
                weights=weights,
                intent_ratio=intent_ratio,
                min_intent_bytes=min_intent_bytes,
                enable_loss_shadow=True,
            ),
        }
        self.variant_states = {
            label: _SemanticVariantState()
            for label in self.variant_controllers
        }
        self._variant_deadline_lambda = 2.0
        self._variant_critical_lambda = 5.0
        self._variant_loss_lambda = 1.0

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        candidate_list = list(candidates)
        if not candidate_list:
            return []

        budgets = self._semantic_variant_budgets(candidate_list, link)
        evaluations = [
            self._evaluate_semantic_variant(
                label,
                controller,
                candidate_list,
                link,
                budgets,
                controller.schedule(candidate_list, link),
            )
            for label, controller in self.variant_controllers.items()
        ]
        selected = self._select_semantic_variant(evaluations, link)
        self._update_semantic_variant_state(selected, budgets)

        prefix = (
            f"semantic_variant={selected.label}; "
            f"variant_score={selected.score:.3f}; "
            f"variant_risk={selected.deadline_risk:.3f}; "
            f"critical_non_delivery={selected.critical_non_delivery:.3f}; "
            f"loss_exposure={selected.loss_exposure:.3f}; "
        )
        return [
            replace(decision, reason=f"{prefix}{decision.reason}")
            for decision in selected.decisions
        ]

    def _semantic_variant_budgets(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> _SemanticVariantBudgets:
        deadline_budget = 0.0
        deadline_weight = 0.0
        critical_budget = 0.0
        critical_count = 0
        loss_budget = 0.0
        loss_weight = 0.0

        for spec, obs in candidates:
            options = transform_candidates(
                spec,
                obs,
                link,
                min_intent_bytes=self.min_intent_bytes,
                semantic_compaction_ratio=self.config.semantic_compaction_ratio,
                degraded_ratio=self.config.opportunistic_degradation_ratio,
                intent_ratio=self.intent_ratio,
            )
            if not options:
                continue
            contract = options[0].contract
            weight = 1.0 + self._risk_class_weight(spec.flow_class)
            deadline_budget += contract.max_deadline_risk * weight
            deadline_weight += weight

            delivery_shortfall = 1.0 - contract.min_delivery_ratio
            if spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}:
                critical_budget += delivery_shortfall
                critical_count += 1

            loss_budget += delivery_shortfall * weight
            loss_weight += weight

        return _SemanticVariantBudgets(
            deadline_risk=deadline_budget / max(1.0, deadline_weight),
            critical_non_delivery=critical_budget / max(1, critical_count),
            loss_exposure=loss_budget / max(1.0, loss_weight),
        )

    def _evaluate_semantic_variant(
        self,
        label: str,
        controller: SemanticContractAdmissionController,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
        budgets: _SemanticVariantBudgets,
        decisions: list[FlowDecision],
    ) -> _SemanticVariantEvaluation:
        by_id = {decision.flow_id: decision for decision in decisions}
        utility = 0.0
        deadline_risk = 0.0
        deadline_weight = 0.0
        critical_total = 0
        critical_not_delivered = 0
        loss_exposure = 0.0

        for spec, obs in candidates:
            decision = by_id.get(spec.flow_id)
            if decision is None:
                continue
            if spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}:
                critical_total += 1
                if decision.action not in _SENT_ACTIONS:
                    critical_not_delivered += 1
            if decision.action not in _SENT_ACTIONS:
                continue

            utility += self._semantic_value(spec, obs) * self._decision_value_ratio(decision)
            risk_weight = 1.0 + self._risk_class_weight(spec.flow_class)
            deadline_risk += self._decision_deadline_risk(decision) * risk_weight
            deadline_weight += risk_weight
            loss_exposure += self._decision_loss_exposure(spec, decision, link)

        utility_per_flow = utility / max(1, len(candidates))
        mean_deadline_risk = deadline_risk / max(1.0, deadline_weight)
        critical_non_delivery = critical_not_delivered / max(1, critical_total)
        mean_loss_exposure = loss_exposure / max(1, len(candidates))
        deadline_excess = max(0.0, mean_deadline_risk - budgets.deadline_risk)
        critical_excess = max(0.0, critical_non_delivery - budgets.critical_non_delivery)
        loss_excess = max(0.0, mean_loss_exposure - budgets.loss_exposure)
        state = self.variant_states[label]
        history_penalty = (
            self._variant_deadline_lambda
            * max(0.0, state.deadline_risk_ewma - budgets.deadline_risk)
            + self._variant_critical_lambda
            * max(0.0, state.critical_non_delivery_ewma - budgets.critical_non_delivery)
            + self._variant_loss_lambda
            * max(0.0, state.loss_exposure_ewma - budgets.loss_exposure)
        )
        path_instability = self._semantic_path_instability(link)
        loss_pressure = path_instability if path_instability > 1.4 else 0.0
        score = (
            utility_per_flow
            - self._variant_deadline_lambda * deadline_excess
            - self._variant_critical_lambda * critical_excess
            - self._variant_loss_lambda * loss_pressure * mean_loss_exposure
            - self._variant_loss_lambda * path_instability * loss_excess
            - history_penalty
        )
        feasible = (
            mean_deadline_risk <= max(budgets.deadline_risk, budgets.deadline_risk + 0.015)
            and critical_non_delivery <= budgets.critical_non_delivery
        )
        return _SemanticVariantEvaluation(
            label=label,
            decisions=decisions,
            utility_per_flow=utility_per_flow,
            deadline_risk=mean_deadline_risk,
            critical_non_delivery=critical_non_delivery,
            loss_exposure=mean_loss_exposure,
            score=score,
            feasible=feasible,
        )

    def _select_semantic_variant(
        self,
        evaluations: Sequence[_SemanticVariantEvaluation],
        link: NetworkLink,
    ) -> _SemanticVariantEvaluation:
        feasible = [item for item in evaluations if item.feasible]
        if feasible:
            path_instability = self._semantic_path_instability(link)
            if path_instability > 1.4:
                best_utility = max(item.utility_per_flow for item in feasible)
                regret_fraction = min(0.45, 0.18 * path_instability)
                risk_floor = min(item.deadline_risk for item in feasible)
                robust = [
                    item
                    for item in feasible
                    if item.utility_per_flow >= best_utility * (1.0 - regret_fraction)
                    and item.deadline_risk <= risk_floor + 0.015
                ]
                if robust:
                    return min(
                        robust,
                        key=lambda item: (
                            item.loss_exposure,
                            item.deadline_risk,
                            -item.utility_per_flow,
                            item.label,
                        ),
                    )
            return max(
                feasible,
                key=lambda item: (
                    item.score,
                    -item.deadline_risk,
                    -item.loss_exposure,
                    item.utility_per_flow,
                    item.label,
                ),
            )
        return min(
            evaluations,
            key=lambda item: (
                item.critical_non_delivery,
                item.deadline_risk,
                item.loss_exposure,
                -item.utility_per_flow,
                item.label,
            ),
        )

    def _update_semantic_variant_state(
        self,
        evaluation: _SemanticVariantEvaluation,
        budgets: _SemanticVariantBudgets,
    ) -> None:
        state = self.variant_states[evaluation.label]
        alpha = self.reward_alpha
        if state.pulls == 0:
            state.utility_ewma = evaluation.utility_per_flow
            state.deadline_risk_ewma = evaluation.deadline_risk
            state.critical_non_delivery_ewma = evaluation.critical_non_delivery
            state.loss_exposure_ewma = evaluation.loss_exposure
        else:
            state.utility_ewma = (
                alpha * evaluation.utility_per_flow + (1.0 - alpha) * state.utility_ewma
            )
            state.deadline_risk_ewma = (
                alpha * evaluation.deadline_risk + (1.0 - alpha) * state.deadline_risk_ewma
            )
            state.critical_non_delivery_ewma = (
                alpha * evaluation.critical_non_delivery
                + (1.0 - alpha) * state.critical_non_delivery_ewma
            )
            state.loss_exposure_ewma = (
                alpha * evaluation.loss_exposure + (1.0 - alpha) * state.loss_exposure_ewma
            )
        state.pulls += 1
        self._variant_deadline_lambda = self._dual_update(
            self._variant_deadline_lambda,
            evaluation.deadline_risk - budgets.deadline_risk,
            floor=0.25,
            ceiling=18.0,
        )
        self._variant_critical_lambda = self._dual_update(
            self._variant_critical_lambda,
            evaluation.critical_non_delivery - budgets.critical_non_delivery,
            floor=1.0,
            ceiling=25.0,
        )
        self._variant_loss_lambda = self._dual_update(
            self._variant_loss_lambda,
            evaluation.loss_exposure - budgets.loss_exposure,
            floor=0.25,
            ceiling=18.0,
        )

    def _dual_update(
        self,
        current: float,
        excess: float,
        *,
        floor: float,
        ceiling: float,
    ) -> float:
        return min(
            ceiling,
            max(floor, current + self.dual_learning_rate * excess),
        )

    def _decision_value_ratio(self, decision: FlowDecision) -> float:
        if decision.wire_mode == "control_intent":
            return 0.92
        if decision.wire_mode == "supervisory_intent":
            return 0.74
        if decision.wire_mode == "semantic_delta":
            return self.lagrangian_config.semantic_compaction_value_ratio
        if decision.wire_mode == "degraded":
            return self.lagrangian_config.degraded_value_ratio
        return 1.0

    def _decision_deadline_risk(self, decision: FlowDecision) -> float:
        return self._logistic_risk(-decision.predicted_slack_ms)

    def _decision_loss_exposure(
        self,
        spec: FlowSpec,
        decision: FlowDecision,
        link: NetworkLink,
    ) -> float:
        packet_cost = 1.0 + min(
            2.0,
            decision.allocated_bytes / max(1.0, float(link.capacity_bytes_per_tick)),
        )
        class_weight = {
            FlowClass.SAFETY: 0.18,
            FlowClass.CONTROL: 0.22,
            FlowClass.COORDINATION: 0.65,
            FlowClass.STATE: 0.85,
            FlowClass.HUMAN_QOE: 1.00 if spec.qoe.operator_visible else 1.20,
            FlowClass.PERCEPTION: 1.35,
            FlowClass.DEBUG: 2.00,
            FlowClass.BULK: 2.40,
        }[spec.flow_class]
        transform_weight = {
            "control_intent": 0.22,
            "supervisory_intent": 0.18,
            "semantic_delta": 0.72,
            "degraded": 0.90,
            "native": 1.15,
            "": 1.15,
        }.get(decision.wire_mode, 1.15)
        return max(link.loss, self._loss_ewma) * packet_cost * class_weight * transform_weight

    def _semantic_path_instability(self, link: NetworkLink) -> float:
        loss = max(link.loss, self._loss_ewma)
        loss_pressure = min(3.0, loss / 0.012)
        jitter_pressure = min(2.0, link.jitter_ms / 12.0)
        rtt_pressure = min(2.0, link.rtt_ms / 120.0)
        return max(1.0, 0.55 * loss_pressure + 0.30 * jitter_pressure + 0.15 * rtt_pressure)


class RobotBudgetAwareAdmissionController:
    """Primal-dual per-robot SLO wrapper over an existing admission policy.

    The wrapper keeps virtual queues for every robot. A robot accumulates
    pressure when recent critical-flow delivery falls below the configured SLO
    or when its predicted deadline risk exceeds the budget. That pressure is
    injected into the next scheduling round by raising the task value of the
    robot's critical flows. The base policy still owns packet transforms and
    byte admission, so this controller can wrap semantic-contract, Lagrangian,
    or future RMW-native policies without duplicating their data-plane logic.
    """

    _CONTROL_CLASSES = {FlowClass.SAFETY, FlowClass.CONTROL}
    _DEADLINE_CLASSES = {
        FlowClass.SAFETY,
        FlowClass.CONTROL,
        FlowClass.COORDINATION,
        FlowClass.STATE,
    }
    _PRESSURE_SHED_CLASSES = {
        FlowClass.STATE,
        FlowClass.PERCEPTION,
        FlowClass.HUMAN_QOE,
        FlowClass.DEBUG,
        FlowClass.BULK,
    }
    _PRESSURE_DEFER_CLASSES = {
        FlowClass.PERCEPTION,
        FlowClass.HUMAN_QOE,
        FlowClass.DEBUG,
        FlowClass.BULK,
    }
    _DEADLINE_FIREWALL_CLASSES = {
        FlowClass.STATE,
        FlowClass.PERCEPTION,
        FlowClass.HUMAN_QOE,
        FlowClass.DEBUG,
        FlowClass.BULK,
    }

    def __init__(
        self,
        base_policy: CallablePolicy | None = None,
        config: RobotBudgetConfig | None = None,
    ) -> None:
        self.config = config or RobotBudgetConfig()
        self.base_policy = base_policy or AdaptiveSemanticContractAdmissionController().schedule
        self.states: dict[str, _RobotBudgetState] = {}
        self._schedule_tick = 0
        self._control_floor_last_served_tick: dict[str, int] = {}

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        candidate_list = list(candidates)
        if not candidate_list:
            return []
        self._schedule_tick += 1

        augmented = [
            self._budget_augmented_candidate(spec, obs)
            for spec, obs in candidate_list
        ]
        decisions = self.base_policy(augmented, link)
        decisions = self._apply_control_service_floor(candidate_list, decisions, link)
        spec_by_id = self._spec_by_flow_id(candidate_list)
        decisions = self._apply_deadline_feasibility_firewall(
            candidate_list,
            decisions,
            link,
        )
        decision_by_id = {decision.flow_id: decision for decision in decisions}
        annotated = [
            self._annotate_decision(
                decision,
                spec_by_id.get(decision.flow_id),
            )
            for decision in decisions
        ]
        self._update_states(candidate_list, decision_by_id, link)
        return sorted(annotated, key=lambda item: item.flow_id)

    def robot_budget_snapshot(self) -> dict[str, dict[str, float | int]]:
        return {
            robot_id: state.as_record(self.config)
            for robot_id, state in sorted(self.states.items())
        }

    def apply_feedback_records(
        self,
        records: Iterable[Mapping[str, object]],
    ) -> dict[str, object]:
        """Update robot virtual queues from observed receiver/controller feedback.

        The scheduler-side loop predicts deadline risk from decisions and link
        tails. This method is the feedback boundary for measured ROS 2 outcomes
        such as receiver delivery, local-controller command publication, or
        projection-gate acceptance ratios.
        """

        applied = 0
        ignored = 0
        robots: dict[str, int] = {}
        sources: dict[str, int] = {}
        for record in records:
            robot_id = str(record.get("robot_id", "")).strip()
            if not robot_id:
                ignored += 1
                continue
            signal = self._feedback_signal(record)
            if signal is None or not signal.has_updates():
                ignored += 1
                continue
            self._apply_robot_feedback_signal(
                robot_id=robot_id,
                signal=signal,
                sample_count=self._feedback_sample_count(record),
            )
            applied += 1
            robots[robot_id] = robots.get(robot_id, 0) + 1
            sources[signal.source] = sources.get(signal.source, 0) + 1
        return {
            "applied": applied,
            "ignored": ignored,
            "robots": robots,
            "sources": sources,
            "snapshot": self.robot_budget_snapshot(),
        }

    def _apply_robot_feedback_signal(
        self,
        *,
        robot_id: str,
        signal: _RobotFeedbackSignal,
        sample_count: float,
    ) -> None:
        state = self.states.setdefault(robot_id, _RobotBudgetState())
        weight = self._feedback_learning_weight(sample_count)
        if weight <= 0.0:
            return
        updated = False
        if signal.control_delivery is not None and signal.control_weight > 0.0:
            updated = True
            self._update_feedback_dimension(
                state,
                value=max(0.0, min(1.0, signal.control_delivery)),
                deficit_name="control_deficit",
                ewma_name="control_delivery_ewma",
                learning_rate=self.config.control_learning_rate * weight * signal.control_weight,
                decay_weight=signal.control_weight,
                ewma_alpha=self.config.ewma_alpha * min(1.0, weight * signal.control_weight),
                excess=lambda value: max(0.0, self.config.min_control_delivery_ratio - value),
            )
        if signal.deadline_risk is not None and signal.deadline_weight > 0.0:
            updated = True
            self._update_feedback_dimension(
                state,
                value=min(
                    self.config.feedback_deadline_risk_cap,
                    max(0.0, min(1.0, signal.deadline_risk)),
                ),
                deficit_name="deadline_deficit",
                ewma_name="deadline_risk_ewma",
                learning_rate=self.config.deadline_learning_rate * weight * signal.deadline_weight,
                decay_weight=signal.deadline_weight,
                ewma_alpha=self.config.ewma_alpha * min(1.0, weight * signal.deadline_weight),
                excess=lambda value: max(0.0, value - self.config.max_deadline_risk),
            )
        if signal.latency_risk is not None and signal.latency_weight > 0.0:
            updated = True
            self._update_feedback_dimension(
                state,
                value=min(
                    self.config.feedback_latency_risk_cap,
                    max(0.0, min(1.0, signal.latency_risk)),
                ),
                deficit_name="latency_deficit",
                ewma_name="latency_risk_ewma",
                learning_rate=self.config.latency_learning_rate * weight * signal.latency_weight,
                decay_weight=signal.latency_weight,
                ewma_alpha=self.config.ewma_alpha * min(1.0, weight * signal.latency_weight),
                excess=lambda value: max(0.0, value),
            )
        if signal.action_deadline_risks:
            updated = True
            for action_key, (risk, action_sample_count) in signal.action_deadline_risks.items():
                self._update_action_deadline_feedback(
                    state,
                    action_key=action_key,
                    deadline_risk=risk,
                    sample_count=action_sample_count,
                    source_weight=signal.deadline_weight,
                )
        if updated:
            state.pulls += 1

    def _feedback_signal(
        self,
        record: Mapping[str, object],
    ) -> _RobotFeedbackSignal | None:
        source = self._feedback_source(record)
        control_delivery = self._feedback_control_delivery(record)
        deadline_risk = self._feedback_deadline_risk(record)
        latency_risk = self._feedback_latency_risk(record)
        action_deadline_risks = self._feedback_action_deadline_risks(record)

        if source == "projection_quality_gate":
            return _RobotFeedbackSignal(
                source=source,
                latency_risk=latency_risk,
                latency_weight=self.config.projection_feedback_latency_weight,
            )

        if source == "local_controller":
            return _RobotFeedbackSignal(
                source=source,
                control_delivery=control_delivery,
                deadline_risk=deadline_risk,
                action_deadline_risks=action_deadline_risks,
                control_weight=self._local_feedback_control_weight(control_delivery),
                deadline_weight=self._local_feedback_deadline_weight(deadline_risk),
                latency_weight=0.0,
            )

        return _RobotFeedbackSignal(
            source=source,
            control_delivery=control_delivery,
            deadline_risk=deadline_risk,
            latency_risk=latency_risk,
            action_deadline_risks=action_deadline_risks,
            control_weight=self.config.egress_feedback_control_weight,
            deadline_weight=self.config.egress_feedback_deadline_weight,
            latency_weight=self.config.egress_feedback_latency_weight,
        )

    def _feedback_control_delivery(
        self,
        record: Mapping[str, object],
    ) -> float | None:
        for key in (
            "control_delivery_ratio",
            "command_delivery_ratio",
            "local_command_delivery_ratio",
        ):
            value = self._optional_feedback_float(record.get(key))
            if value is not None:
                return max(0.0, min(1.0, value))
        if "control_delivered" in record:
            return 1.0 if bool(record.get("control_delivered")) else 0.0
        if str(record.get("event_type", "")) == "command" and "publish" in record:
            return 1.0 if bool(record.get("publish")) else 0.0
        return None

    def _feedback_source(self, record: Mapping[str, object]) -> str:
        source = str(record.get("source", "")).strip().lower()
        if source:
            return source
        event_type = str(record.get("event_type", "")).strip().lower()
        if event_type in {"projection_quality", "quality_projection", "qualified_projection", "signature_matched_projection"}:
            return "projection_quality_gate"
        if event_type == "command":
            return "local_controller"
        return "egress"

    def _local_feedback_control_weight(self, control_delivery: float | None) -> float:
        if control_delivery is None:
            return 0.0
        if control_delivery >= self.config.min_control_delivery_ratio:
            return self.config.local_feedback_success_weight
        return self.config.local_feedback_failure_weight

    def _local_feedback_deadline_weight(self, deadline_risk: float | None) -> float:
        if deadline_risk is None:
            return 0.0
        if deadline_risk <= self.config.max_deadline_risk:
            return self.config.local_feedback_deadline_success_weight
        return self.config.local_feedback_deadline_failure_weight

    def _feedback_deadline_risk(
        self,
        record: Mapping[str, object],
    ) -> float | None:
        risks = []
        for key in ("deadline_risk", "deadline_miss_ratio", "late_ratio"):
            value = self._optional_feedback_float(record.get(key))
            if value is not None:
                risks.append(max(0.0, min(1.0, value)))
        if "deadline_met" in record:
            risks.append(0.0 if bool(record.get("deadline_met")) else 1.0)
        return max(risks) if risks else None

    def _feedback_action_deadline_risks(
        self,
        record: Mapping[str, object],
    ) -> dict[str, tuple[float, float]]:
        risks: dict[str, tuple[float, float]] = {}
        ratios = record.get("deadline_miss_by_transform")
        counts = record.get("deadline_sample_count_by_transform")
        ratio_map = ratios if isinstance(ratios, Mapping) else {}
        count_map = counts if isinstance(counts, Mapping) else {}
        for raw_key, raw_value in ratio_map.items():
            key = self._feedback_transform_key_from_value(raw_key)
            value = self._optional_feedback_float(raw_value)
            if not key or value is None:
                continue
            sample_count = self._optional_feedback_float(count_map.get(raw_key))
            risks[key] = (
                max(0.0, min(1.0, value)),
                max(1.0, sample_count or self.config.feedback_reference_samples),
            )

        if "deadline_met" in record:
            key = self._feedback_transform_key(record)
            if key:
                risks[key] = (
                    0.0 if bool(record.get("deadline_met")) else 1.0,
                    self._feedback_sample_count(record),
                )
        return risks

    def _feedback_latency_risk(
        self,
        record: Mapping[str, object],
    ) -> float | None:
        risks = []
        for key in ("latency_risk", "tail_latency_risk", "qoe_risk"):
            value = self._optional_feedback_float(record.get(key))
            if value is not None:
                risks.append(max(0.0, min(1.0, value)))

        ratio = self._optional_feedback_float(record.get("latency_deadline_ratio"))
        if ratio is not None:
            risks.append(self._latency_ratio_feedback_risk(ratio))

        latency_ms = self._first_feedback_float(
            record,
            ("tail_latency_ms", "p95_latency_ms", "latency_p95_ms", "latency_ms"),
        )
        budget_ms = self._first_feedback_float(
            record,
            (
                "latency_budget_ms",
                "mean_deadline_ms",
                "deadline_ms",
                "source_deadline_ms",
            ),
        )
        if latency_ms is not None and budget_ms is not None and budget_ms > 0.0:
            risks.append(self._latency_ratio_feedback_risk(latency_ms / budget_ms))
        return max(risks) if risks else None

    def _optional_feedback_float(self, value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _first_feedback_float(
        self,
        record: Mapping[str, object],
        keys: Sequence[str],
    ) -> float | None:
        for key in keys:
            value = self._optional_feedback_float(record.get(key))
            if value is not None:
                return value
        return None

    def _latency_ratio_feedback_risk(self, ratio: float) -> float:
        budget = max(0.1, self.config.max_tail_latency_deadline_ratio)
        span = max(0.001, self.config.feedback_latency_risk_span)
        return max(0.0, min(1.0, (ratio - budget) / span))

    def _feedback_sample_count(self, record: Mapping[str, object]) -> float:
        counts = [
            self._optional_feedback_float(record.get(key))
            for key in (
                "feedback_sample_count",
                "latency_sample_count",
                "deadline_sample_count",
                "control_sample_count",
                "sample_count",
            )
        ]
        return max((item for item in counts if item is not None), default=float(self.config.feedback_reference_samples))

    def _feedback_transform_key(self, record: Mapping[str, object]) -> str:
        flow_class = str(record.get("flow_class", "")).strip().lower()
        wire_mode = str(record.get("wire_mode", "") or record.get("action", "")).strip().lower()
        return self._feedback_transform_key_from_parts(flow_class, wire_mode)

    def _feedback_transform_key_from_value(self, value: object) -> str:
        text = str(value).strip().lower()
        if ":" not in text:
            return self._feedback_transform_key_from_parts("", text)
        flow_class, wire_mode = text.split(":", 1)
        return self._feedback_transform_key_from_parts(flow_class, wire_mode)

    def _feedback_transform_key_from_parts(self, flow_class: str, wire_mode: str) -> str:
        flow = flow_class.strip().lower()
        mode = wire_mode.strip().lower()
        if not mode:
            return ""
        return f"{flow}:{mode}" if flow else mode

    def _feedback_learning_weight(self, sample_count: float) -> float:
        reference = max(1.0, float(self.config.feedback_reference_samples))
        sample_factor = min(1.0, max(0.0, sample_count) / reference)
        return max(0.0, self.config.feedback_learning_scale) * sample_factor

    def _budget_augmented_candidate(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
    ) -> tuple[FlowSpec, FlowObservation]:
        pressure = self._service_pressure_for(spec.robot_id)
        if pressure <= 0.0 or spec.flow_class not in self._DEADLINE_CLASSES:
            return spec, obs

        gain = 1.0 + self.config.critical_gain_scale * pressure
        task_pressure = self.config.task_pressure_scale * pressure
        task = obs.task.clipped()
        boosted_task = replace(
            task,
            task_criticality=min(1.0, task.task_criticality + task_pressure),
            collision_risk=min(1.0, task.collision_risk + task_pressure),
            coordination_pressure=min(1.0, task.coordination_pressure + task_pressure),
        )
        tags = dict(spec.tags)
        tags["robot_budget_pressure"] = f"{pressure:.3f}"
        boosted_spec = replace(
            spec,
            causal_task_gain=spec.causal_task_gain * gain,
            redundancy=max(0.0, spec.redundancy - 0.04 * pressure),
            tags=tags,
        )
        return boosted_spec, replace(obs, task=boosted_task)

    def _annotate_decision(
        self,
        decision: FlowDecision,
        spec: FlowSpec | None,
    ) -> FlowDecision:
        if spec is None:
            return decision
        state = self.states.get(spec.robot_id)
        if state is None:
            return decision
        pressure = state.pressure(self.config)
        if pressure <= 0.0:
            return decision
        return replace(
            decision,
            reason=(
                "robot_budget=active; "
                f"robot_pressure={pressure:.3f}; "
                f"control_q={state.control_deficit:.3f}; "
                f"deadline_q={state.deadline_deficit:.3f}; "
                f"latency_q={state.latency_deficit:.3f}; "
                f"control_intent_deadline_q={state.action_deadline_deficits.get('control:control_intent', 0.0):.3f}; "
                f"{decision.reason}"
            ),
        )

    def _update_states(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        decisions: Mapping[str, FlowDecision],
        link: NetworkLink,
    ) -> None:
        by_robot: dict[str, list[tuple[FlowSpec, FlowObservation]]] = {}
        for spec, obs in candidates:
            by_robot.setdefault(spec.robot_id, []).append((spec, obs))

        for robot_id, robot_candidates in by_robot.items():
            state = self.states.setdefault(robot_id, _RobotBudgetState())
            control_candidates = [
                (spec, obs)
                for spec, obs in robot_candidates
                if spec.flow_class in self._CONTROL_CLASSES
            ]
            delivered_control = sum(
                1
                for spec, _obs in control_candidates
                if self._is_sent(decisions.get(spec.flow_id))
            )
            control_delivery = (
                delivered_control / len(control_candidates)
                if control_candidates
                else 1.0
            )
            deadline_items = [
                self._decision_deadline_risk(
                    spec,
                    obs,
                    decisions.get(spec.flow_id),
                    link,
                )
                for spec, obs in robot_candidates
                if spec.flow_class in self._DEADLINE_CLASSES
            ]
            deadline_risk = sum(deadline_items) / max(1, len(deadline_items))
            self._update_state(state, control_delivery, deadline_risk)

    def _update_feedback_dimension(
        self,
        state: _RobotBudgetState,
        *,
        value: float,
        deficit_name: str,
        ewma_name: str,
        learning_rate: float,
        decay_weight: float,
        ewma_alpha: float,
        excess: Callable[[float], float],
    ) -> None:
        lr = max(0.0, learning_rate)
        alpha = max(0.0, min(1.0, ewma_alpha))
        current_ewma = float(getattr(state, ewma_name))
        if state.pulls == 0:
            setattr(state, ewma_name, value)
        else:
            setattr(state, ewma_name, alpha * value + (1.0 - alpha) * current_ewma)
        current_deficit = float(getattr(state, deficit_name))
        decay_responsibility = max(0.0, min(1.0, decay_weight))
        effective_decay = 1.0 - (1.0 - self.config.deficit_decay) * decay_responsibility
        setattr(
            state,
            deficit_name,
            min(
                self.config.max_pressure,
                effective_decay * current_deficit + lr * excess(value),
            ),
        )

    def _update_action_deadline_feedback(
        self,
        state: _RobotBudgetState,
        *,
        action_key: str,
        deadline_risk: float,
        sample_count: float,
        source_weight: float,
    ) -> None:
        key = action_key.strip().lower()
        if not key:
            return
        weight = self._feedback_learning_weight(sample_count) * max(0.0, source_weight)
        if weight <= 0.0:
            return
        capped_risk = min(
            self.config.feedback_deadline_risk_cap,
            max(0.0, min(1.0, deadline_risk)),
        )
        excess = max(0.0, capped_risk - self.config.max_deadline_risk)
        current = state.action_deadline_deficits.get(key, 0.0)
        state.action_deadline_deficits[key] = min(
            self.config.max_pressure,
            self.config.deficit_decay * current
            + self.config.action_deadline_learning_rate * weight * excess,
        )

    def _update_state(
        self,
        state: _RobotBudgetState,
        control_delivery: float,
        deadline_risk: float,
        latency_risk: float = 0.0,
        *,
        control_learning_rate: float | None = None,
        deadline_learning_rate: float | None = None,
        latency_learning_rate: float | None = None,
        ewma_alpha: float | None = None,
    ) -> None:
        alpha = self.config.ewma_alpha if ewma_alpha is None else max(0.0, min(1.0, ewma_alpha))
        control_lr = (
            self.config.control_learning_rate
            if control_learning_rate is None
            else max(0.0, control_learning_rate)
        )
        deadline_lr = (
            self.config.deadline_learning_rate
            if deadline_learning_rate is None
            else max(0.0, deadline_learning_rate)
        )
        latency_lr = (
            self.config.latency_learning_rate
            if latency_learning_rate is None
            else max(0.0, latency_learning_rate)
        )
        if state.pulls == 0:
            state.control_delivery_ewma = control_delivery
            state.deadline_risk_ewma = deadline_risk
            state.latency_risk_ewma = latency_risk
        else:
            state.control_delivery_ewma = (
                alpha * control_delivery + (1.0 - alpha) * state.control_delivery_ewma
            )
            state.deadline_risk_ewma = (
                alpha * deadline_risk + (1.0 - alpha) * state.deadline_risk_ewma
            )
            state.latency_risk_ewma = (
                alpha * latency_risk + (1.0 - alpha) * state.latency_risk_ewma
            )
        control_shortfall = max(0.0, self.config.min_control_delivery_ratio - control_delivery)
        deadline_excess = max(0.0, deadline_risk - self.config.max_deadline_risk)
        latency_excess = max(0.0, latency_risk)
        state.control_deficit = min(
            self.config.max_pressure,
            self.config.deficit_decay * state.control_deficit
            + control_lr * control_shortfall,
        )
        state.deadline_deficit = min(
            self.config.max_pressure,
            self.config.deficit_decay * state.deadline_deficit
            + deadline_lr * deadline_excess,
        )
        state.latency_deficit = min(
            self.config.max_pressure,
            self.config.deficit_decay * state.latency_deficit
            + latency_lr * latency_excess,
        )
        state.pulls += 1

    def _apply_control_service_floor(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        decisions: Sequence[FlowDecision],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        by_id = {decision.flow_id: decision for decision in decisions}
        spec_by_id = self._spec_by_flow_id(candidates)
        obs_by_id = self._obs_by_flow_id(candidates)
        sent_total = sum(
            decision.allocated_bytes for decision in by_id.values()
            if self._is_sent(decision)
        )
        pressure_by_robot: dict[str, float] = {}
        sent_total = self._apply_n_aware_control_service_floor(
            candidates,
            by_id,
            spec_by_id,
            link,
            sent_total,
        )
        for spec, obs in candidates:
            if spec.flow_class not in self._CONTROL_CLASSES:
                continue
            service_pressure = self._service_pressure_for(spec.robot_id)
            total_pressure = self._pressure_for(spec.robot_id)
            if service_pressure < self.config.min_control_floor_pressure:
                continue
            pressure_by_robot[spec.robot_id] = max(
                pressure_by_robot.get(spec.robot_id, 0.0),
                total_pressure,
            )
            if self._is_sent(by_id.get(spec.flow_id)):
                continue
            floor_decision = self._control_floor_decision(
                spec,
                obs,
                link,
                service_pressure,
            )
            if floor_decision is None:
                continue
            needed = max(
                0,
                sent_total + floor_decision.allocated_bytes - link.capacity_bytes_per_tick,
            )
            if needed > 0:
                reclaimed = self._reclaim_noncritical_capacity(
                    by_id,
                    spec_by_id,
                    needed,
                )
                sent_total -= reclaimed
            if sent_total + floor_decision.allocated_bytes <= link.capacity_bytes_per_tick:
                by_id[spec.flow_id] = floor_decision
                sent_total += floor_decision.allocated_bytes
        sent_total = self._apply_control_deadline_horizon_lift(
            candidates,
            by_id,
            spec_by_id,
            link,
            sent_total,
        )
        for robot_id in {spec.robot_id for spec, _obs in candidates}:
            total_pressure = self._pressure_for(robot_id)
            if total_pressure >= self.config.pressure_shed_start:
                pressure_by_robot[robot_id] = max(
                    pressure_by_robot.get(robot_id, 0.0),
                    total_pressure,
                )
        for robot_id, pressure in sorted(pressure_by_robot.items()):
            sent_total -= self._shape_noncritical_for_robot_pressure(
                by_id,
                spec_by_id,
                obs_by_id,
                link,
                robot_id,
                pressure,
            )
        return sorted(by_id.values(), key=lambda item: item.flow_id)

    def _apply_n_aware_control_service_floor(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        decisions: dict[str, FlowDecision],
        specs: Mapping[str, FlowSpec],
        link: NetworkLink,
        sent_total: int,
    ) -> int:
        if not self.config.n_aware_control_floor_enabled:
            return sent_total
        control_by_robot: dict[str, list[tuple[FlowSpec, FlowObservation]]] = {}
        for spec, obs in candidates:
            if spec.flow_class in self._CONTROL_CLASSES:
                control_by_robot.setdefault(spec.robot_id, []).append((spec, obs))
        active_robot_count = len(control_by_robot)
        if active_robot_count < max(1, int(self.config.n_aware_control_floor_min_robots)):
            return sent_total

        missing: list[tuple[str, list[tuple[FlowSpec, FlowObservation]]]] = []
        for robot_id, robot_controls in control_by_robot.items():
            if any(self._is_sent(decisions.get(spec.flow_id)) for spec, _obs in robot_controls):
                self._control_floor_last_served_tick[robot_id] = self._schedule_tick
                continue
            missing.append((robot_id, robot_controls))
        if not missing:
            return sent_total

        for robot_id, robot_controls in sorted(
            missing,
            key=lambda item: self._n_aware_control_floor_rank(item[0]),
        ):
            pressure = max(
                self.config.n_aware_control_floor_pressure,
                self._service_pressure_for(robot_id),
            )
            floor_decision = self._n_aware_control_floor_decision(
                robot_controls,
                link,
                pressure,
            )
            if floor_decision is None:
                continue
            needed = max(
                0,
                sent_total + floor_decision.allocated_bytes - link.capacity_bytes_per_tick,
            )
            if needed > 0:
                reclaimed = self._reclaim_noncritical_capacity(
                    decisions,
                    specs,
                    needed,
                )
                sent_total -= reclaimed
            if sent_total + floor_decision.allocated_bytes > link.capacity_bytes_per_tick:
                continue
            decisions[floor_decision.flow_id] = floor_decision
            sent_total += floor_decision.allocated_bytes
            self._control_floor_last_served_tick[robot_id] = self._schedule_tick
        return sent_total

    def _n_aware_control_floor_rank(self, robot_id: str) -> tuple[int, float, str]:
        last_served = self._control_floor_last_served_tick.get(robot_id, -1_000_000)
        return (last_served, -self._service_pressure_for(robot_id), robot_id)

    def _n_aware_control_floor_decision(
        self,
        controls: Sequence[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
        pressure: float,
    ) -> FlowDecision | None:
        options = [
            decision
            for spec, obs in controls
            for decision in [self._control_floor_decision(spec, obs, link, pressure)]
            if decision is not None
        ]
        if not options:
            return None
        selected = min(
            options,
            key=lambda item: (
                item.allocated_bytes,
                item.predicted_slack_ms < 0.0,
                -item.predicted_slack_ms,
                item.flow_id,
            ),
        )
        return replace(
            selected,
            priority=max(selected.priority, 1200.0 + 100.0 * pressure),
            reason=(
                "robot_budget=n_aware_control_floor; "
                f"tick={self._schedule_tick}; "
                f"{selected.reason}"
            ),
        )

    def _apply_control_deadline_horizon_lift(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        decisions: dict[str, FlowDecision],
        specs: Mapping[str, FlowSpec],
        link: NetworkLink,
        sent_total: int,
    ) -> int:
        if not self.config.deadline_horizon_lift_enabled:
            return sent_total
        for spec, obs in candidates:
            if spec.flow_class not in self._CONTROL_CLASSES:
                continue
            state = self.states.get(spec.robot_id)
            if state is None:
                continue
            action_deadline_deficit = state.action_deadline_deficits.get("control:control_intent", 0.0)
            if action_deadline_deficit < self.config.action_deadline_horizon_lift_min_deficit:
                continue
            if link.rtt_ms < self.config.action_deadline_horizon_lift_min_rtt_ms:
                continue
            decision = decisions.get(spec.flow_id)
            if not self._is_sent(decision) or decision.action != "send_intent":
                continue
            lifted = self._control_deadline_horizon_decision(
                spec,
                obs,
                link,
                max(state.deadline_deficit, action_deadline_deficit),
                decision,
                action_deadline_deficit=action_deadline_deficit,
            )
            if lifted is None:
                continue
            needed = max(0, sent_total + lifted.allocated_bytes - decision.allocated_bytes - link.capacity_bytes_per_tick)
            if needed > 0:
                reclaimed = self._reclaim_noncritical_capacity(decisions, specs, needed)
                sent_total -= reclaimed
            if sent_total + lifted.allocated_bytes - decision.allocated_bytes <= link.capacity_bytes_per_tick:
                decisions[spec.flow_id] = lifted
                sent_total += lifted.allocated_bytes - decision.allocated_bytes
        return sent_total

    def _control_deadline_horizon_decision(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        deadline_deficit: float,
        decision: FlowDecision,
        action_deadline_deficit: float,
    ) -> FlowDecision | None:
        options = [
            option
            for option in transform_candidates(
                spec,
                obs,
                link,
                min_intent_bytes=self.config.floor_min_intent_bytes,
                semantic_compaction_ratio=self.config.floor_semantic_compaction_ratio,
                degraded_ratio=self.config.floor_degraded_ratio,
                intent_ratio=self.config.floor_intent_ratio,
            )
            if option.certificate.feasible
            and option.transform.kind is TransformKind.SUPERVISORY_INTENT
            and option.transform.action in _SENT_ACTIONS
        ]
        if not options:
            return None
        selected = min(
            options,
            key=lambda item: (
                item.certificate.deadline_risk,
                item.allocated_bytes,
            ),
        )
        return FlowDecision(
            flow_id=spec.flow_id,
            action=selected.transform.action,
            priority=max(decision.priority, 100.0 + 100.0 * deadline_deficit),
            allocated_bytes=selected.allocated_bytes,
            reason=(
                "robot_budget=deadline_horizon_lift; "
                f"deadline_q={deadline_deficit:.3f}; "
                f"action_deadline_q={action_deadline_deficit:.3f}; "
                f"from={decision.wire_mode or decision.action}; "
                f"horizon_ms={selected.transform.effective_deadline_ms:.1f}; "
                f"risk={selected.certificate.deadline_risk:.3f}; "
                f"slack={selected.certificate.slack_after_wire_ms:.1f}; "
                f"{decision.reason}"
            ),
            degraded=False,
            reliability=selected.transform.reliability,
            wire_mode=selected.transform.wire_mode,
            predicted_slack_ms=selected.certificate.slack_after_wire_ms,
        )

    def _control_floor_decision(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        pressure: float,
    ) -> FlowDecision | None:
        options = [
            option
            for option in transform_candidates(
                spec,
                obs,
                link,
                min_intent_bytes=self.config.floor_min_intent_bytes,
                semantic_compaction_ratio=self.config.floor_semantic_compaction_ratio,
                degraded_ratio=self.config.floor_degraded_ratio,
                intent_ratio=self.config.floor_intent_ratio,
            )
            if option.certificate.feasible and option.transform.action in _SENT_ACTIONS
        ]
        if not options:
            return None
        selected = min(
            options,
            key=lambda item: (
                item.allocated_bytes,
                item.certificate.deadline_risk,
                self._floor_transform_preference(item.transform.kind),
            ),
        )
        return FlowDecision(
            flow_id=spec.flow_id,
            action=selected.transform.action,
            priority=1000.0 + 100.0 * pressure,
            allocated_bytes=selected.allocated_bytes,
            reason=(
                "robot budget control floor: "
                f"transform={selected.transform.kind.value}; "
                f"pressure={pressure:.3f}; "
                f"risk={selected.certificate.deadline_risk:.3f}; "
                f"slack={selected.certificate.slack_after_wire_ms:.1f}"
            ),
            degraded=selected.transform.kind is TransformKind.DEGRADED,
            reliability=selected.transform.reliability,
            wire_mode=selected.transform.wire_mode,
            predicted_slack_ms=selected.certificate.slack_after_wire_ms,
        )

    def _reclaim_noncritical_capacity(
        self,
        decisions: dict[str, FlowDecision],
        specs: Mapping[str, FlowSpec],
        needed_bytes: int,
    ) -> int:
        reclaimed = 0
        reclaimable = [
            decision
            for decision in decisions.values()
            if self._is_sent(decision)
            and specs.get(decision.flow_id) is not None
            and specs[decision.flow_id].flow_class not in self._CONTROL_CLASSES
        ]
        for decision in sorted(reclaimable, key=lambda item: (item.priority, item.allocated_bytes)):
            if reclaimed >= needed_bytes:
                break
            decisions[decision.flow_id] = replace(
                decision,
                action="defer",
                allocated_bytes=0,
                reason=(
                    "robot budget control floor: reclaimed capacity; "
                    f"{decision.reason}"
                ),
                degraded=False,
                wire_mode="",
                predicted_slack_ms=0.0,
            )
            reclaimed += decision.allocated_bytes
        return reclaimed

    def _floor_transform_preference(self, kind: TransformKind) -> int:
        order = {
            TransformKind.CONTROL_INTENT: 0,
            TransformKind.SUPERVISORY_INTENT: 1,
            TransformKind.SEMANTIC_DELTA: 2,
            TransformKind.DEGRADED: 3,
            TransformKind.RAW: 4,
        }
        return order.get(kind, 99)

    def _shape_noncritical_for_robot_pressure(
        self,
        decisions: dict[str, FlowDecision],
        specs: Mapping[str, FlowSpec],
        observations: Mapping[str, FlowObservation],
        link: NetworkLink,
        robot_id: str,
        pressure: float,
    ) -> int:
        if pressure < self.config.pressure_shed_start:
            return 0
        reclaimable = [
            decision
            for decision in decisions.values()
            if self._is_sent(decision)
            and specs.get(decision.flow_id) is not None
            and observations.get(decision.flow_id) is not None
            and specs[decision.flow_id].robot_id == robot_id
            and specs[decision.flow_id].flow_class in self._PRESSURE_SHED_CLASSES
        ]
        if not reclaimable:
            return 0
        total = sum(decision.allocated_bytes for decision in reclaimable)
        pressure_fraction = min(
            self.config.pressure_shed_max_fraction,
            self.config.pressure_shed_max_fraction
            * max(0.0, pressure - self.config.pressure_shed_start)
            / max(0.001, self.config.max_pressure - self.config.pressure_shed_start),
        )
        state = self.states.get(robot_id)
        if state is not None and state.deadline_deficit > 0.0:
            pressure_fraction = min(
                self.config.deadline_debt_shed_max_fraction,
                pressure_fraction
                + self.config.deadline_debt_shed_gain * state.deadline_deficit,
            )
        target = int(total * pressure_fraction)
        reclaimed = 0
        for decision in sorted(
            reclaimable,
            key=lambda item: (
                self._pressure_shed_class_rank(specs[item.flow_id].flow_class),
                item.priority,
                -item.allocated_bytes,
            ),
        ):
            if reclaimed >= target:
                break
            spec = specs[decision.flow_id]
            obs = observations[decision.flow_id]
            replacement = self._pressure_shaped_decision(
                spec,
                obs,
                link,
                decision,
                pressure,
            )
            if replacement is None:
                continue
            decisions[decision.flow_id] = replacement
            reclaimed += max(0, decision.allocated_bytes - replacement.allocated_bytes)
        return reclaimed

    def _pressure_shaped_decision(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        decision: FlowDecision,
        pressure: float,
    ) -> FlowDecision | None:
        options = [
            option
            for option in transform_candidates(
                spec,
                obs,
                link,
                min_intent_bytes=self.config.floor_min_intent_bytes,
                semantic_compaction_ratio=self.config.floor_semantic_compaction_ratio,
                degraded_ratio=self.config.floor_degraded_ratio,
                intent_ratio=self.config.floor_intent_ratio,
            )
            if option.certificate.feasible
            and option.transform.action in _SENT_ACTIONS
            and option.allocated_bytes < decision.allocated_bytes
        ]
        if options:
            selected = min(
                options,
                key=lambda item: (
                    item.allocated_bytes,
                    item.certificate.deadline_risk,
                    self._pressure_transform_preference(item.transform.kind),
                ),
            )
            return FlowDecision(
                flow_id=spec.flow_id,
                action=selected.transform.action,
                priority=max(decision.priority, 100.0 + pressure),
                allocated_bytes=selected.allocated_bytes,
                reason=(
                    "robot_budget=pressure_shaping; "
                    f"robot_pressure={pressure:.3f}; "
                    f"transform={selected.transform.kind.value}; "
                    f"from={decision.wire_mode or decision.action}; "
                    f"risk={selected.certificate.deadline_risk:.3f}; "
                    f"slack={selected.certificate.slack_after_wire_ms:.1f}; "
                    f"{decision.reason}"
                ),
                degraded=selected.transform.kind is TransformKind.DEGRADED,
                reliability=selected.transform.reliability,
                wire_mode=selected.transform.wire_mode,
                predicted_slack_ms=selected.certificate.slack_after_wire_ms,
            )
        if spec.flow_class in self._PRESSURE_DEFER_CLASSES:
            return replace(
                decision,
                action="defer",
                allocated_bytes=0,
                reason=(
                    "robot_budget=pressure_shaping; "
                    f"robot_pressure={pressure:.3f}; defer noncritical; "
                    f"{decision.reason}"
                ),
                degraded=False,
                wire_mode="",
                predicted_slack_ms=0.0,
            )
        return None

    def _pressure_transform_preference(self, kind: TransformKind) -> int:
        order = {
            TransformKind.DEGRADED: 0,
            TransformKind.SEMANTIC_DELTA: 1,
            TransformKind.SUPERVISORY_INTENT: 2,
            TransformKind.CONTROL_INTENT: 3,
            TransformKind.RAW: 4,
        }
        return order.get(kind, 99)

    def _pressure_shed_class_rank(self, flow_class: FlowClass) -> int:
        order = {
            FlowClass.BULK: 0,
            FlowClass.DEBUG: 1,
            FlowClass.PERCEPTION: 2,
            FlowClass.HUMAN_QOE: 3,
            FlowClass.STATE: 4,
        }
        return order.get(flow_class, 99)

    def _apply_deadline_feasibility_firewall(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
        decisions: Sequence[FlowDecision],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        if not self.config.deadline_firewall_enabled:
            return list(decisions)
        by_id = {decision.flow_id: decision for decision in decisions}
        for spec, obs in candidates:
            decision = by_id.get(spec.flow_id)
            if not self._deadline_firewall_applies(spec, decision):
                continue
            slack_ms = spec.qos.deadline_ms - obs.age_ms
            tail_ms = self._deadline_firewall_tail_ms(
                link,
                max(1, decision.allocated_bytes),
            )
            if tail_ms <= slack_ms:
                continue
            replacement = self._deadline_firewall_replacement(
                spec,
                obs,
                link,
                decision,
            )
            by_id[spec.flow_id] = replacement or replace(
                decision,
                action="defer",
                allocated_bytes=0,
                reason=(
                    "deadline_firewall=defer; "
                    f"tail_ms={tail_ms:.1f}; "
                    f"slack_ms={slack_ms:.1f}; "
                    f"from={decision.wire_mode or decision.action}; "
                    f"{decision.reason}"
                ),
                degraded=False,
                wire_mode="",
                predicted_slack_ms=slack_ms - tail_ms,
            )
        return sorted(by_id.values(), key=lambda item: item.flow_id)

    def _deadline_firewall_applies(
        self,
        spec: FlowSpec,
        decision: FlowDecision | None,
    ) -> bool:
        return (
            decision is not None
            and self._is_sent(decision)
            and spec.flow_class in self._DEADLINE_FIREWALL_CLASSES
        )

    def _deadline_firewall_replacement(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        decision: FlowDecision,
    ) -> FlowDecision | None:
        options = []
        for option in transform_candidates(
            spec,
            obs,
            link,
            min_intent_bytes=self.config.floor_min_intent_bytes,
            semantic_compaction_ratio=self.config.floor_semantic_compaction_ratio,
            degraded_ratio=self.config.floor_degraded_ratio,
            intent_ratio=self.config.floor_intent_ratio,
        ):
            if option.transform.action not in _SENT_ACTIONS:
                continue
            if option.allocated_bytes >= max(1, decision.allocated_bytes):
                continue
            tail_ms = self._deadline_firewall_tail_ms(link, option.allocated_bytes)
            slack_ms = spec.qos.deadline_ms - obs.age_ms
            if tail_ms > slack_ms:
                continue
            options.append((option, tail_ms, slack_ms))
        if not options:
            return None
        selected, tail_ms, slack_ms = min(
            options,
            key=lambda item: (
                item[0].allocated_bytes,
                item[0].certificate.deadline_risk,
                self._pressure_transform_preference(item[0].transform.kind),
            ),
        )
        return FlowDecision(
            flow_id=spec.flow_id,
            action=selected.transform.action,
            priority=max(decision.priority, 100.0),
            allocated_bytes=selected.allocated_bytes,
            reason=(
                "deadline_firewall=reshape; "
                f"transform={selected.transform.kind.value}; "
                f"tail_ms={tail_ms:.1f}; "
                f"slack_ms={slack_ms:.1f}; "
                f"from={decision.wire_mode or decision.action}; "
                f"{decision.reason}"
            ),
            degraded=selected.transform.kind is TransformKind.DEGRADED,
            reliability=selected.transform.reliability,
            wire_mode=selected.transform.wire_mode,
            predicted_slack_ms=slack_ms - tail_ms,
        )

    def _deadline_firewall_tail_ms(
        self,
        link: NetworkLink,
        allocated_bytes: int,
    ) -> float:
        serialization_ms = 20.0 * allocated_bytes / max(
            1.0,
            float(link.capacity_bytes_per_tick),
        )
        conservative_tail = (
            self.config.deadline_firewall_rtt_factor * link.rtt_ms
            + self.config.deadline_firewall_jitter_sigma * link.jitter_ms
            + self.config.deadline_firewall_loss_rtt_fraction * link.loss * link.rtt_ms
            + serialization_ms
        )
        return max(path_tail_wire_ms(link, allocated_bytes), conservative_tail)

    def _decision_deadline_risk(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        decision: FlowDecision | None,
        link: NetworkLink,
    ) -> float:
        if decision is None or not self._is_sent(decision):
            return 1.0 if spec.flow_class in self._CONTROL_CLASSES else 0.5
        slack_ms = decision.predicted_slack_ms
        if slack_ms == 0.0:
            slack_ms = spec.qos.deadline_ms - obs.age_ms
        x = max(
            -60.0,
            min(60.0, -slack_ms / max(1.0, self.config.risk_temperature_ms)),
        )
        predicted_risk = 1.0 / (1.0 + math.exp(-x))
        return max(
            predicted_risk,
            self.config.network_tail_risk_gain
            * self._network_tail_deadline_risk(spec, obs, decision, link),
        )

    def _network_tail_deadline_risk(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        decision: FlowDecision,
        link: NetworkLink,
    ) -> float:
        if spec.flow_class not in self._DEADLINE_CLASSES:
            return 0.0
        allocated_bytes = max(1, decision.allocated_bytes or spec.nominal_size_bytes)
        tail_ms = path_tail_wire_ms(link, allocated_bytes)
        slack_ms = spec.qos.deadline_ms - obs.age_ms
        x = max(
            -60.0,
            min(
                60.0,
                (tail_ms - slack_ms)
                / max(1.0, self.config.risk_temperature_ms),
            ),
        )
        return 1.0 / (1.0 + math.exp(-x))

    def _pressure_for(self, robot_id: str) -> float:
        state = self.states.get(robot_id)
        return 0.0 if state is None else state.pressure(self.config)

    def _service_pressure_for(self, robot_id: str) -> float:
        state = self.states.get(robot_id)
        return 0.0 if state is None else state.service_pressure(self.config)

    def _spec_by_flow_id(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
    ) -> dict[str, FlowSpec]:
        return {spec.flow_id: spec for spec, _obs in candidates}

    def _obs_by_flow_id(
        self,
        candidates: Sequence[tuple[FlowSpec, FlowObservation]],
    ) -> dict[str, FlowObservation]:
        return {spec.flow_id: obs for spec, obs in candidates}

    def _is_sent(self, decision: FlowDecision | None) -> bool:
        return decision is not None and decision.action in _SENT_ACTIONS


class ProfileAwareLagrangianAdmissionController:
    """Select a Lagrangian controller from observed LAN/Wi-Fi/WAN regimes.

    The key difference from parameter tuning is that this controller does not
    assume one global network condition. It classifies the current path from the
    link estimate and maintains separate Lagrangian dual state per regime.
    """

    def __init__(self) -> None:
        self.profiles = default_lagrangian_profiles()
        self.controllers = {
            label: LagrangianRiskPredictiveAdmissionController(config=profile.config)
            for label, profile in self.profiles.items()
        }

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        label = classify_link_profile(link)
        controller = self.controllers[label]
        decisions = controller.schedule(candidates, link)
        return [
            replace(decision, reason=f"profile={label}; {decision.reason}")
            for decision in decisions
        ]


class ContextualProfiledLagrangianAdmissionController:
    """Shielded contextual selection over profile-specific Lagrangian envelopes.

    `ProfileAwareLagrangianAdmissionController` chooses one fixed controller per
    link regime. This controller keeps that regime split, but gives each regime
    several candidate Lagrangian envelopes and selects the most useful envelope
    whose previewed deadline risk and critical-flow non-delivery stay inside the
    safety shield.
    """

    def __init__(
        self,
        envelopes: dict[str, tuple[ProfileEnvelope, ...]] | None = None,
        *,
        reward_alpha: float = 0.28,
        exploration_scale: float = 0.10,
        deadline_history_penalty: float = 16.0,
        non_delivery_history_penalty: float = 3.5,
    ) -> None:
        self.envelopes = envelopes or default_contextual_lagrangian_envelopes()
        self.controllers = {
            profile: {
                envelope.label: LagrangianRiskPredictiveAdmissionController(
                    config=envelope.config,
                )
                for envelope in profile_envelopes
            }
            for profile, profile_envelopes in self.envelopes.items()
        }
        self.states = {
            profile: {
                envelope.label: _EnvelopeState()
                for envelope in profile_envelopes
            }
            for profile, profile_envelopes in self.envelopes.items()
        }
        self.profile_pulls = {profile: 0 for profile in self.envelopes}
        self.reward_alpha = reward_alpha
        self.exploration_scale = exploration_scale
        self.deadline_history_penalty = deadline_history_penalty
        self.non_delivery_history_penalty = non_delivery_history_penalty

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        link.validates()
        candidate_list = list(candidates)
        if not candidate_list:
            return []

        profile_label = classify_link_profile(link)
        evaluations = [
            self._preview_envelope(profile_label, envelope, candidate_list, link)
            for envelope in self.envelopes[profile_label]
        ]
        selected = self._select_envelope(profile_label, evaluations)
        controller = self.controllers[profile_label][selected.envelope.label]
        decisions = controller.schedule(candidate_list, link)
        actual = self._evaluate_decisions(
            profile_label,
            selected.envelope,
            controller,
            candidate_list,
            link,
            decisions,
        )
        self._update_state(profile_label, actual)

        prefix = (
            f"profile={profile_label}; envelope={selected.envelope.label}; "
            f"risk={actual.deadline_risk:.3f}; "
            f"critical_non_delivery={actual.critical_non_delivery:.3f}; "
        )
        return [
            replace(decision, reason=f"{prefix}{decision.reason}")
            for decision in decisions
        ]

    def _preview_envelope(
        self,
        profile_label: str,
        envelope: ProfileEnvelope,
        candidates: list[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> _EnvelopeEvaluation:
        preview = LagrangianRiskPredictiveAdmissionController(config=envelope.config)
        decisions = preview.schedule(candidates, link)
        return self._evaluate_decisions(
            profile_label,
            envelope,
            preview,
            candidates,
            link,
            decisions,
        )

    def _select_envelope(
        self,
        profile_label: str,
        evaluations: Sequence[_EnvelopeEvaluation],
    ) -> _EnvelopeEvaluation:
        feasible = [item for item in evaluations if item.feasible]
        if not feasible:
            return min(
                evaluations,
                key=lambda item: (
                    item.deadline_risk,
                    item.critical_non_delivery,
                    -item.utility_per_flow,
                ),
            )

        total_pulls = self.profile_pulls[profile_label] + 1

        def rank(item: _EnvelopeEvaluation) -> tuple[float, float, float, str]:
            state = self.states[profile_label][item.envelope.label]
            if state.pulls == 0:
                exploration = self.exploration_scale
            else:
                exploration = self.exploration_scale * math.sqrt(
                    math.log(total_pulls + 1.0) / (state.pulls + 1.0)
                )
            history_penalty = (
                self.deadline_history_penalty
                * max(0.0, state.deadline_risk_ewma - item.envelope.config.deadline_risk_budget)
                + self.non_delivery_history_penalty
                * max(
                    0.0,
                    state.critical_non_delivery_ewma
                    - item.envelope.critical_non_delivery_budget,
                )
            )
            history_reward = 0.18 * state.utility_ewma
            return (
                item.score + exploration + history_reward - history_penalty,
                item.utility_per_flow,
                -item.deadline_risk,
                item.envelope.label,
            )

        return max(feasible, key=rank)

    def _evaluate_decisions(
        self,
        profile_label: str,
        envelope: ProfileEnvelope,
        controller: LagrangianRiskPredictiveAdmissionController,
        candidates: list[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
        decisions: Sequence[FlowDecision],
    ) -> _EnvelopeEvaluation:
        by_id = {decision.flow_id: decision for decision in decisions}
        utility = 0.0
        risks: list[float] = []
        critical_total = 0
        critical_not_delivered = 0

        for spec, obs in candidates:
            decision = by_id.get(spec.flow_id)
            if decision is None:
                continue
            if spec.flow_class in {FlowClass.SAFETY, FlowClass.CONTROL}:
                critical_total += 1
                if decision.action not in _SENT_ACTIONS:
                    critical_not_delivered += 1
            if decision.action not in _SENT_ACTIONS:
                continue

            value = controller._semantic_value(spec, obs)
            if decision.action == "send_compacted":
                value *= controller.lagrangian_config.semantic_compaction_value_ratio
            elif decision.action == "send_degraded":
                value *= controller.lagrangian_config.degraded_value_ratio
            utility += value

            risk = controller._deadline_risk(
                spec,
                obs,
                link,
                max(1, decision.allocated_bytes),
            )
            if risk > 0.0:
                risks.append(risk)

        deadline_risk = sum(risks) / max(1, len(risks))
        critical_non_delivery = critical_not_delivered / max(1, critical_total)
        utility_per_flow = utility / max(1, len(candidates))
        risk_excess = max(0.0, deadline_risk - envelope.config.deadline_risk_budget)
        non_delivery_excess = max(
            0.0,
            critical_non_delivery - envelope.critical_non_delivery_budget,
        )
        score = utility_per_flow - 18.0 * risk_excess - 4.0 * non_delivery_excess
        feasible = (
            deadline_risk
            <= max(
                envelope.config.deadline_risk_budget * 1.35,
                envelope.config.deadline_risk_budget + 0.02,
            )
            and critical_non_delivery <= envelope.critical_non_delivery_budget
        )
        return _EnvelopeEvaluation(
            profile_label=profile_label,
            envelope=envelope,
            decisions=list(decisions),
            utility_per_flow=utility_per_flow,
            deadline_risk=deadline_risk,
            critical_non_delivery=critical_non_delivery,
            score=score,
            feasible=feasible,
        )

    def _update_state(
        self,
        profile_label: str,
        evaluation: _EnvelopeEvaluation,
    ) -> None:
        state = self.states[profile_label][evaluation.envelope.label]
        alpha = self.reward_alpha
        if state.pulls == 0:
            state.utility_ewma = evaluation.utility_per_flow
            state.deadline_risk_ewma = evaluation.deadline_risk
            state.critical_non_delivery_ewma = evaluation.critical_non_delivery
        else:
            state.utility_ewma = (
                alpha * evaluation.utility_per_flow + (1.0 - alpha) * state.utility_ewma
            )
            state.deadline_risk_ewma = (
                alpha * evaluation.deadline_risk
                + (1.0 - alpha) * state.deadline_risk_ewma
            )
            state.critical_non_delivery_ewma = (
                alpha * evaluation.critical_non_delivery
                + (1.0 - alpha) * state.critical_non_delivery_ewma
            )
        state.pulls += 1
        self.profile_pulls[profile_label] += 1


class IntentAwareContextualAdmissionController:
    """Add a control-intent fallback when per-sample control deadlines are infeasible.

    WAN/roaming links can make a 45 ms teleop/control sample impossible before
    scheduling begins. In that regime, dropping the sample protects measured
    packet deadlines but delivers no control. This wrapper keeps the contextual
    profile controller for normal traffic, then converts infeasible dropped
    control samples into compact control-intent packets with a path-aware horizon.
    """

    def __init__(
        self,
        base: ContextualProfiledLagrangianAdmissionController | None = None,
        *,
        intent_ratio: float = 0.50,
        min_intent_bytes: int = 48,
    ) -> None:
        self.base = base or ContextualProfiledLagrangianAdmissionController()
        self.intent_ratio = intent_ratio
        self.min_intent_bytes = min_intent_bytes

    def schedule(
        self,
        candidates: Iterable[tuple[FlowSpec, FlowObservation]],
        link: NetworkLink,
    ) -> list[FlowDecision]:
        candidate_list = list(candidates)
        if not candidate_list:
            return []
        profile_label = classify_link_profile(link)
        decisions = self.base.schedule(candidate_list, link)
        if profile_label not in {"wan", "roaming"}:
            return decisions

        by_id = {decision.flow_id: decision for decision in decisions}
        rewritten: list[FlowDecision] = []
        for spec, obs in candidate_list:
            decision = by_id.get(spec.flow_id)
            if decision is None:
                continue
            intent = self._control_intent_candidate(spec, obs, link, decision)
            if intent is not None:
                rewritten.append(
                    self._intent_decision(
                        spec,
                        decision,
                        intent,
                        profile_label,
                    )
                )
            else:
                rewritten.append(decision)
        return sorted(rewritten, key=lambda item: item.flow_id)

    def _control_intent_candidate(
        self,
        spec: FlowSpec,
        obs: FlowObservation,
        link: NetworkLink,
        decision: FlowDecision,
    ) -> TransformCandidate | None:
        if spec.flow_class is not FlowClass.CONTROL:
            return None
        if decision.action in _SENT_ACTIONS:
            return None
        candidates = transform_candidates(
            spec,
            obs,
            link,
            min_intent_bytes=self.min_intent_bytes,
            intent_ratio=self.intent_ratio,
        )
        raw = next(
            (item for item in candidates if item.transform.kind is TransformKind.RAW),
            None,
        )
        intent = next(
            (item for item in candidates if item.transform.kind is TransformKind.CONTROL_INTENT),
            None,
        )
        if raw is None or intent is None:
            return None
        if raw.certificate.feasible:
            return None
        if intent.certificate.predicted_arrival_age_ms > spec.qos.lifespan_ms:
            return None
        if intent.certificate.slack_after_wire_ms <= raw.certificate.slack_after_wire_ms:
            return None
        return intent

    def _intent_decision(
        self,
        spec: FlowSpec,
        decision: FlowDecision,
        intent: TransformCandidate,
        profile_label: str,
    ) -> FlowDecision:
        allocated_bytes = intent.allocated_bytes
        certificate = intent.certificate
        horizon_ms = float(certificate.transform.effective_deadline_ms)
        return replace(
            decision,
            action="send_intent",
            priority=max(decision.priority, 0.0) + 1.0,
            allocated_bytes=allocated_bytes,
            reason=(
                f"profile={profile_label}; control intent horizon: "
                f"source_deadline_ms={spec.qos.deadline_ms:.1f}; "
                f"effective_deadline_ms={horizon_ms:.1f}; "
                f"certificate={certificate.reason}; "
                f"risk={certificate.deadline_risk:.3f}; {decision.reason}"
            ),
            degraded=False,
            reliability=str(certificate.transform.reliability),
            wire_mode=str(certificate.transform.wire_mode),
            predicted_slack_ms=float(certificate.slack_after_wire_ms),
        )


def default_lagrangian_profiles() -> dict[str, LinkProfile]:
    """Return conservative profile-specific Lagrangian operating points."""

    return {
        "lan": LinkProfile(
            label="lan",
            config=LagrangianAdmissionConfig(
                deadline_risk_budget=0.12,
                initial_deadline_lambda=0.8,
                risk_barrier_start=0.85,
                risk_barrier_scale=8.0,
                deadline_drop_risk=0.90,
                safety_margin=0.96,
                pressure_compaction_threshold=0.95,
            ),
        ),
        "wifi": LinkProfile(
            label="wifi",
            config=LagrangianAdmissionConfig(
                deadline_risk_budget=0.08108907959603916,
                initial_deadline_lambda=2.5791431516549297,
                risk_barrier_start=0.5873597131456887,
                risk_barrier_scale=12.578351757284267,
                deadline_drop_risk=0.42910490748345237,
            ),
        ),
        "wan": LinkProfile(
            label="wan",
            config=LagrangianAdmissionConfig(
                deadline_risk_budget=0.035,
                initial_deadline_lambda=6.0,
                risk_barrier_start=0.50,
                risk_barrier_scale=18.0,
                deadline_drop_risk=0.34,
                safety_margin=0.68,
                pressure_compaction_threshold=0.58,
                semantic_compaction_ratio=0.45,
                opportunistic_degradation_ratio=0.10,
            ),
        ),
        "roaming": LinkProfile(
            label="roaming",
            config=LagrangianAdmissionConfig(
                deadline_risk_budget=0.02,
                initial_deadline_lambda=8.0,
                risk_barrier_start=0.45,
                risk_barrier_scale=20.0,
                deadline_drop_risk=0.30,
                safety_margin=0.55,
                pressure_compaction_threshold=0.45,
                semantic_compaction_ratio=0.40,
                opportunistic_degradation_ratio=0.08,
            ),
        ),
    }


def default_contextual_lagrangian_envelopes() -> dict[str, tuple[ProfileEnvelope, ...]]:
    """Return shielded safe/balanced/utility envelopes for each link profile."""

    base = default_lagrangian_profiles()
    budgets = {
        "lan": (0.06, 0.04, 0.03),
        "wifi": (0.28, 0.22, 0.16),
        "wan": (0.72, 0.58, 0.44),
        "roaming": (0.88, 0.76, 0.62),
    }
    result: dict[str, tuple[ProfileEnvelope, ...]] = {}
    for label, profile in base.items():
        safe_budget, balanced_budget, utility_budget = budgets[label]
        result[label] = (
            ProfileEnvelope(
                label="safe",
                config=profile.config,
                critical_non_delivery_budget=safe_budget,
            ),
            ProfileEnvelope(
                label="balanced",
                config=_relax_lagrangian_config(profile.config, level=1),
                critical_non_delivery_budget=balanced_budget,
            ),
            ProfileEnvelope(
                label="utility",
                config=_relax_lagrangian_config(profile.config, level=2),
                critical_non_delivery_budget=utility_budget,
            ),
        )
    return result


def _relax_lagrangian_config(
    config: LagrangianAdmissionConfig,
    *,
    level: int,
) -> LagrangianAdmissionConfig:
    """Move a safe profile envelope toward higher utility within bounded risk."""

    if level == 1:
        return replace(
            config,
            deadline_risk_budget=min(0.14, config.deadline_risk_budget * 1.35 + 0.006),
            initial_deadline_lambda=max(0.5, config.initial_deadline_lambda * 0.74),
            risk_barrier_start=min(0.88, config.risk_barrier_start + 0.07),
            risk_barrier_scale=max(7.0, config.risk_barrier_scale * 0.82),
            deadline_drop_risk=min(0.92, config.deadline_drop_risk + 0.10),
            safety_margin=min(0.97, config.safety_margin + 0.10),
            pressure_compaction_threshold=min(
                0.96,
                config.pressure_compaction_threshold + 0.08,
            ),
            semantic_compaction_ratio=min(0.62, config.semantic_compaction_ratio + 0.05),
            opportunistic_degradation_ratio=min(
                0.20,
                config.opportunistic_degradation_ratio + 0.025,
            ),
        )
    if level == 2:
        return replace(
            config,
            deadline_risk_budget=min(0.16, config.deadline_risk_budget * 1.75 + 0.012),
            initial_deadline_lambda=max(0.5, config.initial_deadline_lambda * 0.55),
            risk_barrier_start=min(0.90, config.risk_barrier_start + 0.13),
            risk_barrier_scale=max(6.0, config.risk_barrier_scale * 0.65),
            deadline_drop_risk=min(0.94, config.deadline_drop_risk + 0.18),
            safety_margin=min(0.99, config.safety_margin + 0.16),
            pressure_compaction_threshold=min(
                0.98,
                config.pressure_compaction_threshold + 0.14,
            ),
            semantic_compaction_ratio=min(0.68, config.semantic_compaction_ratio + 0.10),
            opportunistic_degradation_ratio=min(
                0.24,
                config.opportunistic_degradation_ratio + 0.05,
            ),
        )
    raise ValueError(f"unsupported relaxation level: {level}")


def classify_link_profile(link: NetworkLink) -> str:
    """Classify an IP path from the scheduler-visible link estimate."""

    link.validates()
    one_way_tail_ms = 0.5 * link.rtt_ms + 1.35 * link.jitter_ms
    if (
        one_way_tail_ms >= 95.0
        or link.rtt_ms >= 150.0
        or link.jitter_ms >= 24.0
        or link.loss >= 0.025
        or link.capacity_bytes_per_tick < 1_600
    ):
        return "roaming"
    if (
        one_way_tail_ms >= 55.0
        or link.rtt_ms >= 80.0
        or link.jitter_ms >= 12.0
        or link.loss >= 0.012
        or link.capacity_bytes_per_tick < 2_200
    ):
        return "wan"
    if (
        one_way_tail_ms <= 12.0
        and link.rtt_ms <= 18.0
        and link.jitter_ms <= 3.0
        and link.loss <= 0.005
        and link.capacity_bytes_per_tick >= 3_000
    ):
        return "lan"
    return "wifi"
