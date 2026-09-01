"""Live telemetry-to-path-plan controller for FleetRMW.

This module consumes router/subscriber telemetry records, aggregates them into
per-path observations, and updates the file-backed `fleet_plan` consumed by the
C++ RMW transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import os
from pathlib import Path
from statistics import NormalDist, variance
import tempfile
from typing import Iterable, Mapping, Sequence

from .fleet_optimizer import FleetFlowDemand, FleetOptimizerConfig, RobotQoEState
from .online_fleet_planner import (
    FleetTopicDemand,
    OnlineFleetPathPlan,
    OnlineFleetPathPlanner,
    OnlineFleetPlannerConfig,
    PathObservation,
)


ROUTER_TELEMETRY_SCHEMA_VERSION = "fleetrmw.router_path_telemetry.v1"
SUBSCRIBER_TELEMETRY_SCHEMA_VERSION = "fleetrmw.subscriber_delivery_telemetry.v1"
CONTROLLER_SCHEMA_VERSION = "fleetrmw.live_path_plan_controller.v1"


@dataclass(frozen=True)
class QoESequentialStoppingConfig:
    min_samples_per_robot: int = 3
    max_samples_per_robot: int = 5
    confidence_level: float = 0.95
    min_sample_stddev: float = 0.005
    separation_margin: float = 0.01
    migration_hysteresis: float = 0.01


@dataclass(frozen=True)
class RobotQoEEstimate:
    robot_id: str
    sample_count: int
    mean_qoe: float
    sample_variance: float
    confidence_radius: float
    lower_bound: float
    upper_bound: float

    def as_dict(self) -> dict[str, object]:
        return {
            "robot_id": self.robot_id,
            "sample_count": self.sample_count,
            "mean_qoe": self.mean_qoe,
            "sample_variance": self.sample_variance,
            "confidence_radius": self.confidence_radius,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
        }


@dataclass(frozen=True)
class QoESequentialStoppingDecision:
    should_stop: bool
    confidence_separated: bool
    reason: str
    candidate_protected_robots: tuple[str, ...]
    previous_protected_robots: tuple[str, ...]
    boundary_gap: float
    required_gap: float
    min_sample_count: int
    max_sample_count: int
    estimates: tuple[RobotQoEEstimate, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "should_stop": self.should_stop,
            "confidence_separated": self.confidence_separated,
            "reason": self.reason,
            "candidate_protected_robots": list(self.candidate_protected_robots),
            "previous_protected_robots": list(self.previous_protected_robots),
            "boundary_gap": self.boundary_gap,
            "required_gap": self.required_gap,
            "min_sample_count": self.min_sample_count,
            "max_sample_count": self.max_sample_count,
            "estimates": [estimate.as_dict() for estimate in self.estimates],
        }


@dataclass(frozen=True)
class QoEConfidenceFallbackConfig:
    max_extra_protected_robots: int = 0
    prefer_previous_robots: bool = True
    protected_delivery_ratio: float = 0.0
    protected_deadline_miss_ratio: float = 1.0
    protected_qoe_score: float = 0.0
    healthy_delivery_ratio: float = 1.0
    healthy_deadline_miss_ratio: float = 0.0
    healthy_qoe_score: float = 1.0


@dataclass(frozen=True)
class QoEConfidenceFallbackActuation:
    applied: bool
    reason: str
    protected_robots: tuple[str, ...]
    extra_protected_robot_count: int
    plan: OnlineFleetPathPlan

    def as_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "protected_robots": list(self.protected_robots),
            "extra_protected_robot_count": self.extra_protected_robot_count,
            "plan": self.plan.as_dict(),
        }


@dataclass(frozen=True)
class RouterTelemetryRecord:
    path_id: str
    topic: str
    sequence_number: int
    event: str = "data_frame"
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    sent_frames: int = 1
    delivered_frames: int = 1
    nack_frames: int = 0
    bytes_sent: int = 0
    capacity_bytes: int = 0
    loss: float | None = None
    nack_rate: float | None = None
    deadline_miss_ratio: float | None = None
    bandwidth_utilization: float | None = None
    failure_domain: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "RouterTelemetryRecord":
        return cls(
            path_id=str(data.get("path_id", data.get("path", ""))),
            topic=str(data.get("topic", "")),
            sequence_number=_int_value(
                data.get("source_sequence_number", data.get("sequence_number", 0))
            ),
            event=str(data.get("event", "data_frame")),
            latency_ms=_float_value(data.get("latency_ms", 0.0)),
            jitter_ms=_float_value(data.get("jitter_ms", 0.0)),
            sent_frames=max(0, _int_value(data.get("sent_frames", 1))),
            delivered_frames=max(0, _int_value(data.get("delivered_frames", 1))),
            nack_frames=max(0, _int_value(data.get("nack_frames", 0))),
            bytes_sent=max(0, _int_value(data.get("bytes_sent", data.get("payload_bytes", 0)))),
            capacity_bytes=max(0, _int_value(data.get("capacity_bytes", 0))),
            loss=_optional_fraction(data.get("loss")),
            nack_rate=_optional_fraction(data.get("nack_rate")),
            deadline_miss_ratio=_optional_fraction(data.get("deadline_miss_ratio")),
            bandwidth_utilization=_optional_fraction(data.get("bandwidth_utilization")),
            failure_domain=str(data.get("failure_domain", "")),
        )

    def as_path_observation(self) -> PathObservation:
        return PathObservation(
            path_id=self.path_id,
            latency_ms=self.latency_ms,
            jitter_ms=self.jitter_ms,
            sent_frames=self.sent_frames,
            delivered_frames=self.delivered_frames,
            nack_frames=self.nack_frames,
            bytes_sent=self.bytes_sent,
            capacity_bytes=self.capacity_bytes,
            loss=self.loss,
            nack_rate=self.nack_rate,
            deadline_miss_ratio=self.deadline_miss_ratio,
            bandwidth_utilization=self.bandwidth_utilization,
            failure_domain=self.failure_domain,
        )


@dataclass(frozen=True)
class SubscriberDeliveryTelemetryRecord:
    robot_id: str
    topic: str
    sequence_number: int
    event: str = "take"
    latency_ms: float = 0.0
    deadline_ms: float = 0.0
    deadline_missed: bool = False
    delivered: bool = True
    duplicate: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "SubscriberDeliveryTelemetryRecord":
        return cls(
            robot_id=str(data.get("robot_id", "")),
            topic=str(data.get("topic", "")),
            sequence_number=_int_value(
                data.get("source_sequence_number", data.get("sequence_number", 0))
            ),
            event=str(data.get("event", "take")),
            latency_ms=max(0.0, _float_value(data.get("latency_ms", 0.0))),
            deadline_ms=max(0.0, _float_value(data.get("deadline_ms", 0.0))),
            deadline_missed=_bool_value(data.get("deadline_missed", False)),
            delivered=_bool_value(data.get("delivered", True)),
            duplicate=_bool_value(data.get("duplicate", False)),
        )


@dataclass
class PathObservationAccumulator:
    path_id: str
    latency_values: list[float] = field(default_factory=list)
    jitter_values: list[float] = field(default_factory=list)
    sent_frames: int = 0
    delivered_frames: int = 0
    nack_frames: int = 0
    bytes_sent: int = 0
    capacity_bytes: int = 0
    loss_values: list[float] = field(default_factory=list)
    nack_rate_values: list[float] = field(default_factory=list)
    deadline_miss_values: list[float] = field(default_factory=list)
    utilization_values: list[float] = field(default_factory=list)
    failure_domain: str = ""

    def add(self, record: RouterTelemetryRecord) -> None:
        self.latency_values.append(record.latency_ms)
        self.jitter_values.append(record.jitter_ms)
        self.sent_frames += record.sent_frames
        self.delivered_frames += record.delivered_frames
        self.nack_frames += record.nack_frames
        self.bytes_sent += record.bytes_sent
        self.capacity_bytes = max(self.capacity_bytes, record.capacity_bytes)
        if record.loss is not None:
            self.loss_values.append(record.loss)
        if record.nack_rate is not None:
            self.nack_rate_values.append(record.nack_rate)
        if record.deadline_miss_ratio is not None:
            self.deadline_miss_values.append(record.deadline_miss_ratio)
        if record.bandwidth_utilization is not None:
            self.utilization_values.append(record.bandwidth_utilization)
        if record.failure_domain:
            self.failure_domain = record.failure_domain

    def observation(self) -> PathObservation:
        return PathObservation(
            path_id=self.path_id,
            latency_ms=_mean(self.latency_values),
            jitter_ms=_mean(self.jitter_values),
            sent_frames=self.sent_frames,
            delivered_frames=self.delivered_frames,
            nack_frames=self.nack_frames,
            bytes_sent=self.bytes_sent,
            capacity_bytes=self.capacity_bytes,
            loss=_mean(self.loss_values) if self.loss_values else None,
            nack_rate=_mean(self.nack_rate_values) if self.nack_rate_values else None,
            deadline_miss_ratio=_mean(self.deadline_miss_values)
            if self.deadline_miss_values
            else None,
            bandwidth_utilization=_mean(self.utilization_values)
            if self.utilization_values
            else None,
            failure_domain=self.failure_domain,
        )


class RouterTelemetryAggregator:
    """Accumulate router telemetry records into current path observations."""

    def __init__(self, seed_observations: Iterable[PathObservation] = ()) -> None:
        self._seed_observations = {item.path_id: item for item in seed_observations}
        self._accumulators: dict[str, PathObservationAccumulator] = {}
        self._record_count = 0

    @property
    def record_count(self) -> int:
        return self._record_count

    def ingest(self, record: RouterTelemetryRecord) -> None:
        if not record.path_id:
            return
        accumulator = self._accumulators.setdefault(
            record.path_id,
            PathObservationAccumulator(record.path_id),
        )
        accumulator.add(record)
        self._record_count += 1

    def observations(self) -> list[PathObservation]:
        observations = dict(self._seed_observations)
        for path_id, accumulator in self._accumulators.items():
            observation = accumulator.observation()
            seed = self._seed_observations.get(path_id)
            if not observation.failure_domain and seed is not None:
                observation = replace(observation, failure_domain=seed.failure_domain)
            observations[path_id] = observation
        return sorted(observations.values(), key=lambda item: item.path_id)

    def start_new_epoch(
        self,
        seed_observations: Iterable[PathObservation] | None = None,
    ) -> None:
        self._accumulators.clear()
        if seed_observations is not None:
            self._seed_observations = {
                item.path_id: item for item in seed_observations
            }


@dataclass
class SubscriberRobotAccumulator:
    robot_id: str
    delivered: int = 0
    total: int = 0
    deadline_misses: int = 0
    latency_values: list[float] = field(default_factory=list)
    deadline_values: list[float] = field(default_factory=list)
    qoe_samples: list[float] = field(default_factory=list)

    def add(self, record: SubscriberDeliveryTelemetryRecord) -> None:
        if record.duplicate:
            return
        self.total += 1
        if record.delivered:
            self.delivered += 1
        if record.deadline_missed:
            self.deadline_misses += 1
        self.latency_values.append(record.latency_ms)
        if record.deadline_ms > 0:
            self.deadline_values.append(record.deadline_ms)
        self.qoe_samples.append(_subscriber_qoe_sample(record))

    def state(self) -> RobotQoEState:
        total = max(1, self.total)
        return RobotQoEState(
            robot_id=self.robot_id,
            control_delivery_ratio=min(1.0, max(0.0, self.delivered / total)),
            deadline_miss_ratio=min(1.0, max(0.0, self.deadline_misses / total)),
            qoe_score=_mean(self.qoe_samples) if self.qoe_samples else 1.0,
        )

    def estimate(
        self,
        *,
        z_value: float,
        min_sample_stddev: float,
    ) -> RobotQoEEstimate:
        sample_count = len(self.qoe_samples)
        mean_qoe = _mean(self.qoe_samples) if self.qoe_samples else 1.0
        sample_variance = variance(self.qoe_samples) if sample_count > 1 else 0.0
        effective_variance = max(sample_variance, min_sample_stddev ** 2)
        radius = (
            z_value * math.sqrt(effective_variance / sample_count)
            if sample_count > 0 else 1.0
        )
        return RobotQoEEstimate(
            robot_id=self.robot_id,
            sample_count=sample_count,
            mean_qoe=mean_qoe,
            sample_variance=sample_variance,
            confidence_radius=radius,
            lower_bound=max(0.0, mean_qoe - radius),
            upper_bound=min(1.0, mean_qoe + radius),
        )


class SubscriberDeliveryAggregator:
    """Aggregate subscriber-visible delivery telemetry into robot QoE states."""

    def __init__(self, seed_states: Iterable[RobotQoEState] = ()) -> None:
        self._seed_states = {state.robot_id: state for state in seed_states}
        self._accumulators: dict[str, SubscriberRobotAccumulator] = {}
        self._record_count = 0

    @property
    def record_count(self) -> int:
        return self._record_count

    def ingest(self, record: SubscriberDeliveryTelemetryRecord) -> None:
        if not record.robot_id:
            return
        accumulator = self._accumulators.setdefault(
            record.robot_id,
            SubscriberRobotAccumulator(record.robot_id),
        )
        accumulator.add(record)
        self._record_count += 1

    def robot_states(self) -> list[RobotQoEState]:
        states = dict(self._seed_states)
        for robot_id, accumulator in self._accumulators.items():
            states[robot_id] = accumulator.state()
        return sorted(states.values(), key=lambda item: item.robot_id)

    def start_new_epoch(self) -> None:
        self._accumulators.clear()

    def sequential_stopping_decision(
        self,
        *,
        robot_ids: Sequence[str],
        protected_robot_budget: int,
        config: QoESequentialStoppingConfig,
        previous_protected_robots: Iterable[str] = (),
    ) -> QoESequentialStoppingDecision:
        if config.min_samples_per_robot <= 0:
            raise ValueError("min_samples_per_robot must be positive")
        if config.max_samples_per_robot < config.min_samples_per_robot:
            raise ValueError("max_samples_per_robot must be at least the minimum")
        if not 0.0 < config.confidence_level < 1.0:
            raise ValueError("confidence_level must be between zero and one")
        unique_robot_ids = tuple(dict.fromkeys(robot_ids))
        protected_count = min(max(protected_robot_budget, 0), len(unique_robot_ids))
        sequential_tests = max(1, len(unique_robot_ids) * config.max_samples_per_robot)
        tail_probability = (1.0 - config.confidence_level) / (2.0 * sequential_tests)
        z_value = NormalDist().inv_cdf(1.0 - tail_probability)
        estimates = tuple(
            self._accumulators.get(robot_id, SubscriberRobotAccumulator(robot_id)).estimate(
                z_value=z_value,
                min_sample_stddev=max(0.0, config.min_sample_stddev),
            )
            for robot_id in unique_robot_ids
        )
        ranked = tuple(sorted(estimates, key=lambda item: (item.mean_qoe, item.robot_id)))
        candidate = tuple(sorted(item.robot_id for item in ranked[:protected_count]))
        previous = tuple(sorted(set(previous_protected_robots)))
        min_samples = min((item.sample_count for item in estimates), default=0)
        max_samples = max((item.sample_count for item in estimates), default=0)
        required_gap = max(0.0, config.separation_margin)
        if previous and set(previous) != set(candidate):
            required_gap += max(0.0, config.migration_hysteresis)
        if protected_count == 0 or protected_count == len(ranked):
            boundary_gap = math.inf
            confidence_separated = min_samples >= config.min_samples_per_robot
        else:
            protected_upper = max(item.upper_bound for item in ranked[:protected_count])
            unprotected_lower = min(item.lower_bound for item in ranked[protected_count:])
            boundary_gap = unprotected_lower - protected_upper
            confidence_separated = (
                min_samples >= config.min_samples_per_robot
                and boundary_gap >= required_gap
            )
        reached_limit = min_samples >= config.max_samples_per_robot
        if confidence_separated:
            reason = "confidence boundary separated"
        elif reached_limit:
            reason = "maximum samples reached without confidence separation"
        else:
            reason = "collect more subscriber QoE samples"
        return QoESequentialStoppingDecision(
            should_stop=confidence_separated or reached_limit,
            confidence_separated=confidence_separated,
            reason=reason,
            candidate_protected_robots=candidate,
            previous_protected_robots=previous,
            boundary_gap=boundary_gap,
            required_gap=required_gap,
            min_sample_count=min_samples,
            max_sample_count=max_samples,
            estimates=tuple(sorted(estimates, key=lambda item: item.robot_id)),
        )


def qoe_confidence_fallback_protected_robots(
    decision: QoESequentialStoppingDecision,
    *,
    protected_robot_budget: int,
    config: QoEConfidenceFallbackConfig,
) -> tuple[str, ...]:
    """Choose a conservative protected set when QoE confidence does not separate."""

    estimates_by_robot = {estimate.robot_id: estimate for estimate in decision.estimates}
    max_protected = min(
        len(estimates_by_robot),
        max(0, int(protected_robot_budget))
        + max(0, int(config.max_extra_protected_robots)),
    )
    if max_protected <= 0:
        return ()

    def risk_key(robot_id: str) -> tuple[int, float, float, int, str]:
        estimate = estimates_by_robot[robot_id]
        sample_gap = max(0, decision.max_sample_count - estimate.sample_count)
        return (
            0 if sample_gap > 0 else 1,
            estimate.mean_qoe,
            estimate.upper_bound,
            -estimate.sample_count,
            robot_id,
        )

    previous = [
        robot_id
        for robot_id in decision.previous_protected_robots
        if robot_id in estimates_by_robot
    ]
    selected: list[str] = []
    seen: set[str] = set()

    def add_robot(robot_id: str) -> None:
        if robot_id in seen or len(selected) >= max_protected:
            return
        selected.append(robot_id)
        seen.add(robot_id)

    if config.prefer_previous_robots:
        for robot_id in sorted(previous, key=risk_key):
            add_robot(robot_id)
    for robot_id in sorted(estimates_by_robot, key=risk_key):
        add_robot(robot_id)

    return tuple(sorted(selected))


class JsonlTelemetryTailer:
    """Read newly appended JSONL records from one or more telemetry files."""

    def __init__(self, paths: Sequence[Path | str], *, kind: str = "router") -> None:
        self.paths = [Path(path) for path in paths]
        self.kind = kind
        self._offsets = {path: 0 for path in self.paths}

    def poll(self) -> list[RouterTelemetryRecord | SubscriberDeliveryTelemetryRecord]:
        records: list[RouterTelemetryRecord | SubscriberDeliveryTelemetryRecord] = []
        for path in self.paths:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self._offsets.get(path, 0))
                for line in handle:
                    record = (
                        parse_subscriber_telemetry_line(line)
                        if self.kind == "subscriber"
                        else parse_router_telemetry_line(line)
                    )
                    if record is not None:
                        records.append(record)
                self._offsets[path] = handle.tell()
        return records


@dataclass(frozen=True)
class LivePathPlanControllerConfig:
    plan_file: Path
    telemetry_files: tuple[Path, ...]
    demands: tuple[FleetTopicDemand, ...]
    repair_plan_file: Path | None = None
    subscriber_telemetry_files: tuple[Path, ...] = ()
    seed_observations: tuple[PathObservation, ...] = ()
    robot_states: tuple[RobotQoEState, ...] = ()
    optimizer: FleetOptimizerConfig = field(default_factory=FleetOptimizerConfig)
    telemetry_alpha: float = 0.65
    min_dwell_ticks: int = 0
    switch_score_margin: float = 0.20


class LivePathPlanController:
    """Tail router telemetry and refresh an RMW fleet-plan file."""

    def __init__(self, config: LivePathPlanControllerConfig) -> None:
        self.config = config
        self._tailer = JsonlTelemetryTailer(config.telemetry_files)
        self._subscriber_tailer = JsonlTelemetryTailer(
            config.subscriber_telemetry_files,
            kind="subscriber",
        )
        self._aggregator = RouterTelemetryAggregator(config.seed_observations)
        self._subscriber_aggregator = SubscriberDeliveryAggregator(config.robot_states)
        self._planner = OnlineFleetPathPlanner(
            OnlineFleetPlannerConfig(
                optimizer=config.optimizer,
                telemetry_alpha=config.telemetry_alpha,
                min_dwell_ticks=config.min_dwell_ticks,
                switch_score_margin=config.switch_score_margin,
            )
        )
        self._tick = 0
        self._last_plan: OnlineFleetPathPlan | None = None

    @property
    def last_plan(self) -> OnlineFleetPathPlan | None:
        return self._last_plan

    @property
    def record_count(self) -> int:
        return self._aggregator.record_count

    @property
    def subscriber_record_count(self) -> int:
        return self._subscriber_aggregator.record_count

    def poll_once(self) -> OnlineFleetPathPlan:
        return self._poll_once()

    def poll_once_with_robot_states(
        self,
        robot_states: Iterable[RobotQoEState],
        *,
        optimizer: FleetOptimizerConfig | None = None,
    ) -> OnlineFleetPathPlan:
        return self._poll_once(robot_states=robot_states, optimizer=optimizer)

    def _poll_once(
        self,
        *,
        robot_states: Iterable[RobotQoEState] | None = None,
        optimizer: FleetOptimizerConfig | None = None,
    ) -> OnlineFleetPathPlan:
        self.poll_telemetry()
        observations = self._aggregator.observations()
        effective_robot_states = (
            self._subscriber_aggregator.robot_states()
            if robot_states is None else
            list(robot_states)
        )
        planner = self._planner
        if optimizer is not None:
            planner = OnlineFleetPathPlanner(
                replace(
                    self._planner.config,
                    optimizer=optimizer,
                    min_dwell_ticks=0,
                )
            )
        plan = planner.update(
            tick=self._tick,
            observations=observations,
            demands=self.config.demands,
            robot_states=effective_robot_states,
        )
        self._tick += 1
        _write_live_plan_text(self.config.plan_file, plan.path_plan_env + "\n")
        if self.config.repair_plan_file is not None:
            _write_live_plan_text(self.config.repair_plan_file, plan.path_plan_env + "\n")
        self._last_plan = plan
        return plan

    def apply_qoe_confidence_fallback(
        self,
        *,
        decision: QoESequentialStoppingDecision,
        protected_robot_budget: int,
        config: QoEConfidenceFallbackConfig,
    ) -> QoEConfidenceFallbackActuation:
        fallback_robots = qoe_confidence_fallback_protected_robots(
            decision,
            protected_robot_budget=protected_robot_budget,
            config=config,
        )
        protected = set(fallback_robots)
        robot_ids = tuple(dict.fromkeys(demand.demand.robot_id for demand in self.config.demands))
        robot_states = tuple(
            RobotQoEState(
                robot_id=robot_id,
                control_delivery_ratio=(
                    config.protected_delivery_ratio
                    if robot_id in protected else
                    config.healthy_delivery_ratio
                ),
                deadline_miss_ratio=(
                    config.protected_deadline_miss_ratio
                    if robot_id in protected else
                    config.healthy_deadline_miss_ratio
                ),
                qoe_score=(
                    config.protected_qoe_score
                    if robot_id in protected else
                    config.healthy_qoe_score
                ),
            )
            for robot_id in robot_ids
        )
        payload_by_robot = {
            demand.demand.robot_id: max(1, int(demand.demand.payload_bytes))
            for demand in self.config.demands
        }
        base_payload_bytes = sum(payload_by_robot.values())
        redundancy_budget = sum(payload_by_robot.get(robot_id, 0) for robot_id in fallback_robots)
        optimizer = replace(
            self.config.optimizer,
            capacity_bytes_per_tick=max(
                self.config.optimizer.capacity_bytes_per_tick,
                base_payload_bytes + redundancy_budget,
            ),
            redundancy_budget_bytes_per_tick=redundancy_budget,
            redundancy_risk_threshold=0.0,
        )
        plan = self.poll_once_with_robot_states(robot_states, optimizer=optimizer)
        return QoEConfidenceFallbackActuation(
            applied=not decision.confidence_separated,
            reason=(
                "confidence fallback: conservative protected set"
                if not decision.confidence_separated else
                "confidence separated: fallback not required"
            ),
            protected_robots=fallback_robots,
            extra_protected_robot_count=max(
                0,
                len(fallback_robots) - max(0, int(protected_robot_budget)),
            ),
            plan=plan,
        )

    def poll_telemetry(self) -> None:
        for record in self._tailer.poll():
            if isinstance(record, RouterTelemetryRecord):
                self._aggregator.ingest(record)
        for record in self._subscriber_tailer.poll():
            if isinstance(record, SubscriberDeliveryTelemetryRecord):
                self._subscriber_aggregator.ingest(record)

    def qoe_sequential_stopping_decision(
        self,
        *,
        protected_robot_budget: int,
        config: QoESequentialStoppingConfig,
        previous_protected_robots: Iterable[str] = (),
    ) -> QoESequentialStoppingDecision:
        self.poll_telemetry()
        robot_ids = [demand.demand.robot_id for demand in self.config.demands]
        return self._subscriber_aggregator.sequential_stopping_decision(
            robot_ids=robot_ids,
            protected_robot_budget=protected_robot_budget,
            config=config,
            previous_protected_robots=previous_protected_robots,
        )

    def start_new_epoch(
        self,
        *,
        seed_observations: Iterable[PathObservation] | None = None,
    ) -> None:
        self._aggregator.start_new_epoch(seed_observations)
        self._subscriber_aggregator.start_new_epoch()

    def summary(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLER_SCHEMA_VERSION,
            "record_count": self.record_count,
            "subscriber_record_count": self.subscriber_record_count,
            "last_plan": None if self._last_plan is None else self._last_plan.as_dict(),
            "plan_file": str(self.config.plan_file),
            "repair_plan_file": (
                None
                if self.config.repair_plan_file is None
                else str(self.config.repair_plan_file)
            ),
            "telemetry_files": [str(path) for path in self.config.telemetry_files],
            "subscriber_telemetry_files": [
                str(path) for path in self.config.subscriber_telemetry_files
            ],
            "robot_states": [
                {
                    "robot_id": state.robot_id,
                    "control_delivery_ratio": state.control_delivery_ratio,
                    "deadline_miss_ratio": state.deadline_miss_ratio,
                    "qoe_score": state.qoe_score,
                }
                for state in self._subscriber_aggregator.robot_states()
            ],
        }


def parse_router_telemetry_line(line: str) -> RouterTelemetryRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    schema = str(data.get("schema_version", ""))
    if schema and schema != ROUTER_TELEMETRY_SCHEMA_VERSION:
        return None
    return RouterTelemetryRecord.from_mapping(data)


def parse_subscriber_telemetry_line(line: str) -> SubscriberDeliveryTelemetryRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    schema = str(data.get("schema_version", ""))
    if schema and schema != SUBSCRIBER_TELEMETRY_SCHEMA_VERSION:
        return None
    return SubscriberDeliveryTelemetryRecord.from_mapping(data)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.tmp-{os.getpid()}-",
        delete=False,
    ) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    os.replace(tmp, path)


def _write_live_plan_text(path: Path, text: str) -> None:
    # Unlike atomic_write_text, this rewrites the existing inode in place
    # (open+truncate, no rename-over). A long-running reader process inside
    # a Docker bind-mounted container can keep seeing pre-rename content
    # indefinitely on some Docker Desktop virtiofs mounts even though a
    # freshly opened reader (e.g. `docker exec cat`) sees the new content
    # immediately -- renaming a new file over the path apparently doesn't
    # invalidate that reader's cached view of the path, while an in-place
    # write to the same inode does.
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _subscriber_qoe_sample(record: SubscriberDeliveryTelemetryRecord) -> float:
    if not record.delivered:
        return 0.0
    if record.deadline_ms <= 0:
        return 1.0
    latency_ratio = record.latency_ms / max(1.0, record.deadline_ms)
    return min(1.0, max(0.0, 1.0 - 0.5 * latency_ratio))


def _int_value(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _float_value(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _optional_fraction(value: object) -> float | None:
    if value is None:
        return None
    parsed = _float_value(value)
    if parsed > 1.0:
        parsed /= 100.0
    return min(1.0, max(0.0, parsed))


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
