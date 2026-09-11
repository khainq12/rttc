"""Core data model for FleetQoX.

The model is deliberately independent from ROS 2 so it can be validated before
the full RMW implementation exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class FlowClass(str, Enum):
    """Semantic class of a communication flow."""

    SAFETY = "safety"
    CONTROL = "control"
    COORDINATION = "coordination"
    STATE = "state"
    PERCEPTION = "perception"
    HUMAN_QOE = "human_qoe"
    DEBUG = "debug"
    BULK = "bulk"


@dataclass(frozen=True)
class QoSProfile:
    """ROS-like QoS profile extended with freshness constraints."""

    reliability: str = "best_effort"
    durability: str = "volatile"
    depth: int = 1
    deadline_ms: float = 100.0
    lifespan_ms: float = 250.0
    liveliness_lease_ms: float = 500.0

    def validates(self) -> None:
        if self.reliability not in {"best_effort", "reliable"}:
            raise ValueError(f"unknown reliability: {self.reliability}")
        if self.durability not in {"volatile", "transient_local"}:
            raise ValueError(f"unknown durability: {self.durability}")
        if self.depth < 1:
            raise ValueError("depth must be positive")
        if self.deadline_ms <= 0 or self.lifespan_ms <= 0:
            raise ValueError("deadline and lifespan must be positive")


@dataclass(frozen=True)
class QoEProfile:
    """Human/operator sensitivity profile."""

    operator_visible: bool = False
    smoothness_weight: float = 0.0
    freeze_penalty: float = 0.0
    visual_confidence_weight: float = 0.0


@dataclass(frozen=True)
class TaskContext:
    """Task-level context used by the scheduler."""

    task_id: str
    robot_id: str
    task_criticality: float
    collision_risk: float
    operator_attention: float
    coordination_pressure: float

    def clipped(self) -> "TaskContext":
        return TaskContext(
            task_id=self.task_id,
            robot_id=self.robot_id,
            task_criticality=_clip01(self.task_criticality),
            collision_risk=_clip01(self.collision_risk),
            operator_attention=_clip01(self.operator_attention),
            coordination_pressure=_clip01(self.coordination_pressure),
        )


@dataclass(frozen=True)
class FlowSpec:
    """A schedulable ROS/FleetQoX flow."""

    flow_id: str
    robot_id: str
    topic: str
    flow_class: FlowClass
    qos: QoSProfile
    qoe: QoEProfile
    nominal_size_bytes: int
    nominal_rate_hz: float
    causal_task_gain: float
    redundancy: float = 0.0
    semantic_delta_ratio: float = 1.0
    tags: Mapping[str, str] = field(default_factory=dict)

    def validates(self) -> None:
        self.qos.validates()
        if self.nominal_size_bytes <= 0:
            raise ValueError("nominal_size_bytes must be positive")
        if self.nominal_rate_hz <= 0:
            raise ValueError("nominal_rate_hz must be positive")
        if self.semantic_delta_ratio <= 0:
            raise ValueError("semantic_delta_ratio must be positive")


@dataclass(frozen=True)
class FlowObservation:
    """Current runtime state of a flow."""

    age_ms: float
    queue_depth: int
    measured_loss: float
    measured_rtt_ms: float
    observed_jitter_ms: float
    task: TaskContext


@dataclass(frozen=True)
class NetworkLink:
    """Network budget visible to the scheduler for the current tick."""

    capacity_bytes_per_tick: int
    # Optional packet-count ceiling for the tick, separate from the byte
    # budget above. Real 802.11 airtime is dominated by a mostly
    # size-independent per-packet overhead (DIFS/backoff/preamble/ACK), so a
    # byte-only budget under-prices high-frequency small packets (e.g. 50Hz
    # control) relative to real channel capacity. None means no packet-rate
    # constraint (all existing callers keep their current behavior).
    capacity_packets_per_tick: int | None = None
    loss: float = 0.0
    jitter_ms: float = 0.0
    rtt_ms: float = 20.0

    def validates(self) -> None:
        if self.capacity_bytes_per_tick < 0:
            raise ValueError("capacity must be non-negative")
        if self.capacity_packets_per_tick is not None and self.capacity_packets_per_tick < 0:
            raise ValueError("capacity_packets_per_tick must be non-negative")
        if not 0 <= self.loss <= 1:
            raise ValueError("loss must be in [0, 1]")
        if self.jitter_ms < 0 or self.rtt_ms < 0:
            raise ValueError("jitter and rtt must be non-negative")


@dataclass(frozen=True)
class FlowDecision:
    """Scheduler output for a flow."""

    flow_id: str
    action: str
    priority: float
    allocated_bytes: int
    reason: str
    degraded: bool = False
    reliability: str = ""
    wire_mode: str = ""
    predicted_slack_ms: float = 0.0


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))
