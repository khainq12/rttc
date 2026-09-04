"""Bounded state engine for the FleetRMW QUIC/H3 gateway service."""

from __future__ import annotations

import base64
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .repair_scheduler import (
    FleetRepairScheduler,
    FleetRepairSchedulerConfig,
    RepairDemand,
    RepairPath,
)
from .task_outcome import TASK_KINDS, TERMINAL_STATUSES


DATA_FRAME_MAGIC = b"FRMW1\n"
DATA_FRAME_SCHEMA_VERSION = "fleetrmw.data_frame.v1"
ADMISSION_POLICY_SCHEMA_VERSION = "fleetrmw.quic_gateway_admission_policy.v1"
GATEWAY_API_PATH = "/fleetrmw/v1/frames"
GATEWAY_BATCH_API_PATH = "/fleetrmw/v1/frame-batches"
OBSERVATION_API_PATH = "/fleetrmw/v1/observations"
APPLICATION_OUTCOME_API_PATH = "/fleetrmw/v1/application-outcomes"
METRICS_API_PATH = "/fleetrmw/v1/metrics"
HEALTH_API_PATH = "/healthz"
FRAME_BATCH_SCHEMA_VERSION = "fleetrmw.quic_gateway_frame_batch.v1"
OBSERVATION_SCHEMA_VERSION = "fleetrmw.quic_gateway_observation.v1"
APPLICATION_OUTCOME_SCHEMA_VERSION = "fleetrmw.quic_gateway_application_outcome.v1"


class FrameValidationError(ValueError):
    """Raised when a POST body is not a valid FleetRMW data frame."""


class FrameAdmissionError(RuntimeError):
    """Raised when a valid frame is rejected by fleet gateway admission."""

    def __init__(
        self,
        *,
        status: int,
        reason_code: str,
        reason: str,
        traffic_class: str,
    ) -> None:
        super().__init__(reason)
        self.status = status
        self.reason_code = reason_code
        self.reason = reason
        self.traffic_class = traffic_class


class FramePersistenceError(RuntimeError):
    """Raised when durable gateway state cannot be committed safely."""


class FramePersistenceUnavailableError(FramePersistenceError):
    """Raised when a durable backend is temporarily unreachable."""


@dataclass(frozen=True)
class FrameMetadata:
    domain_id: int
    topic: str
    publisher_id: str
    source_sequence_number: int
    robot_id: str = ""
    traffic_class: str = ""
    deadline_ms: float = 0.0
    age_ms: float = 0.0
    qoe_debt: float = 0.0
    criticality: float = 0.0
    repair_requested: bool = False
    prior_repair_attempts: int = 0
    frame_bytes: int = 0

    @property
    def stream_key(self) -> tuple[int, str]:
        return self.domain_id, self.topic

    @property
    def dedup_key(self) -> tuple[str, int]:
        return self.publisher_id, self.source_sequence_number

    @property
    def remaining_deadline_ms(self) -> float:
        if self.deadline_ms <= 0.0:
            return 1000.0
        return max(1.0, self.deadline_ms - self.age_ms)

    @property
    def admission_score(self) -> float:
        urgency = (
            min(1.0, self.age_ms / self.deadline_ms)
            if self.deadline_ms > 0.0
            else 0.0
        )
        return 0.45 * self.criticality + 0.35 * self.qoe_debt + 0.20 * urgency


@dataclass(frozen=True)
class GatewayAdmissionRule:
    domain_id: int
    topic: str
    traffic_class: str
    max_accepted_frames: int
    allowed_publishers: frozenset[str]
    min_admission_score: float = 0.0


@dataclass(frozen=True)
class GatewayObservation:
    domain_id: int
    topic: str
    publisher_id: str
    qoe_debt: float
    measured_loss: float
    measured_rtt_ms: float
    measured_jitter_ms: float
    source: str
    qoe_debt_source: str
    updated_at: float


class GatewayAdmissionPolicy:
    """Deterministic per-topic fleet admission quotas for gateway frames."""

    def __init__(
        self,
        *,
        rules: tuple[GatewayAdmissionRule, ...],
        default_action: str,
        max_accepted_frames: int | None,
        epoch_ms: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        repair_capacity_bytes: int = 0,
        repair_max_admitted: int | None = None,
        repair_paths: tuple[RepairPath, ...] = (),
        observation_ttl_ms: int = 5000,
        native_qoe_debt_enabled: bool = False,
        native_qoe_debt_ewma_alpha: float = 0.5,
        native_qoe_loss_saturation: float = 0.05,
        native_qoe_rtt_deadline_ratio_saturation: float = 1.0,
        native_qoe_jitter_deadline_ratio_saturation: float = 0.25,
        application_outcome_qoe_debt_enabled: bool = False,
        application_outcome_qoe_debt_ewma_alpha: float = 0.5,
    ) -> None:
        if default_action not in {"allow", "deny"}:
            raise ValueError("admission default_action must be allow or deny")
        if max_accepted_frames is not None and max_accepted_frames <= 0:
            raise ValueError("admission max_accepted_frames must be positive")
        if epoch_ms is not None and epoch_ms <= 0:
            raise ValueError("admission epoch_ms must be positive")
        if repair_capacity_bytes < 0:
            raise ValueError("repair capacity must be non-negative")
        if repair_max_admitted is not None and repair_max_admitted <= 0:
            raise ValueError("repair max_admitted must be positive")
        if repair_capacity_bytes > 0 and not repair_paths:
            raise ValueError("repair paths are required with repair capacity")
        if observation_ttl_ms <= 0:
            raise ValueError("observation_ttl_ms must be positive")
        if not 0.0 < native_qoe_debt_ewma_alpha <= 1.0:
            raise ValueError("native QoE debt EWMA alpha must be in (0, 1]")
        if not 0.0 < native_qoe_loss_saturation <= 1.0:
            raise ValueError("native QoE loss saturation must be in (0, 1]")
        if (
            native_qoe_rtt_deadline_ratio_saturation <= 0.0
            or native_qoe_jitter_deadline_ratio_saturation <= 0.0
        ):
            raise ValueError("native QoE deadline-ratio saturations must be positive")
        if not 0.0 < application_outcome_qoe_debt_ewma_alpha <= 1.0:
            raise ValueError(
                "application outcome QoE debt EWMA alpha must be in (0, 1]"
            )
        self.rules = rules
        self.default_action = default_action
        self.max_accepted_frames = max_accepted_frames
        self.epoch_ms = epoch_ms
        self._clock = clock
        self._epoch_started = clock()
        self._epoch_reset_count = 0
        self.repair_capacity_bytes = repair_capacity_bytes
        self.repair_max_admitted = repair_max_admitted
        self.repair_paths = repair_paths
        self.observation_ttl_ms = observation_ttl_ms
        self.native_qoe_debt_enabled = native_qoe_debt_enabled
        self.native_qoe_debt_ewma_alpha = native_qoe_debt_ewma_alpha
        self.native_qoe_loss_saturation = native_qoe_loss_saturation
        self.native_qoe_rtt_deadline_ratio_saturation = (
            native_qoe_rtt_deadline_ratio_saturation
        )
        self.native_qoe_jitter_deadline_ratio_saturation = (
            native_qoe_jitter_deadline_ratio_saturation
        )
        self.application_outcome_qoe_debt_enabled = (
            application_outcome_qoe_debt_enabled
        )
        self.application_outcome_qoe_debt_ewma_alpha = (
            application_outcome_qoe_debt_ewma_alpha
        )
        self._rules_by_stream: dict[tuple[int, str], GatewayAdmissionRule] = {}
        for rule in rules:
            if rule.domain_id < 0 or not rule.topic.startswith("/"):
                raise ValueError("admission rule requires valid domain_id and topic")
            if not rule.traffic_class or rule.max_accepted_frames <= 0:
                raise ValueError("admission rule requires class and positive quota")
            if not 0.0 <= rule.min_admission_score <= 1.0:
                raise ValueError("admission score threshold must be in [0, 1]")
            key = (rule.domain_id, rule.topic)
            if key in self._rules_by_stream:
                raise ValueError("duplicate admission rule for domain/topic")
            self._rules_by_stream[key] = rule
        self._accepted_total = 0
        self._accepted_cumulative = 0
        self._accepted_by_stream: dict[tuple[int, str], int] = {}
        self._accepted_by_class: dict[str, int] = {}
        self._rejected_by_reason: dict[str, int] = {}
        self._repair_allocated_bytes = 0
        self._repair_admitted_count = 0
        self._repair_deferred_count = 0
        self._repair_decisions: list[dict[str, object]] = []
        self._observations: dict[tuple[int, str, str], GatewayObservation] = {}
        self._observation_updates = 0
        self._observation_updates_by_source: dict[str, int] = {}
        self._observation_expirations = 0
        self._observation_score_uses = 0
        self._native_qoe_debt_updates = 0
        self._application_outcome_qoe_debt_updates = 0
        self._application_task_outcome_updates = 0
        self._application_task_outcome_failures = 0

    @property
    def fingerprint(self) -> str:
        document = {
            "schema_version": ADMISSION_POLICY_SCHEMA_VERSION,
            "default_action": self.default_action,
            "max_accepted_frames": self.max_accepted_frames,
            "epoch_ms": self.epoch_ms,
            "repair_capacity_bytes": self.repair_capacity_bytes,
            "repair_max_admitted": self.repair_max_admitted,
            "observation_ttl_ms": self.observation_ttl_ms,
            "native_qoe_debt": {
                "enabled": self.native_qoe_debt_enabled,
                "ewma_alpha": self.native_qoe_debt_ewma_alpha,
                "loss_saturation": self.native_qoe_loss_saturation,
                "rtt_deadline_ratio_saturation": (
                    self.native_qoe_rtt_deadline_ratio_saturation
                ),
                "jitter_deadline_ratio_saturation": (
                    self.native_qoe_jitter_deadline_ratio_saturation
                ),
            },
            "application_outcome_qoe_debt": {
                "enabled": self.application_outcome_qoe_debt_enabled,
                "ewma_alpha": self.application_outcome_qoe_debt_ewma_alpha,
            },
            "rules": [
                {
                    "domain_id": rule.domain_id,
                    "topic": rule.topic,
                    "traffic_class": rule.traffic_class,
                    "max_accepted_frames": rule.max_accepted_frames,
                    "allowed_publishers": sorted(rule.allowed_publishers),
                    "min_admission_score": rule.min_admission_score,
                }
                for rule in sorted(
                    self.rules, key=lambda value: (value.domain_id, value.topic)
                )
            ],
            "repair_paths": [
                {
                    "path_id": path.path_id,
                    "latency_ms": path.latency_ms,
                    "loss": path.loss,
                    "failure_domain": path.failure_domain,
                    "bandwidth_utilization": path.bandwidth_utilization,
                }
                for path in sorted(self.repair_paths, key=lambda value: value.path_id)
            ],
        }
        encoded = json.dumps(
            document, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_document(
        cls,
        document: dict[str, Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "GatewayAdmissionPolicy":
        if document.get("schema_version") != ADMISSION_POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported QUIC gateway admission policy schema")
        raw_rules = document.get("rules")
        if not isinstance(raw_rules, list):
            raise ValueError("admission policy rules must be a list")
        rules: list[GatewayAdmissionRule] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                raise ValueError("admission rule must be an object")
            publishers = raw_rule.get("allowed_publishers", [])
            if not isinstance(publishers, list) or not all(
                isinstance(value, str) and value for value in publishers
            ):
                raise ValueError("allowed_publishers must contain non-empty strings")
            domain_id = raw_rule.get("domain_id")
            quota = raw_rule.get("max_accepted_frames")
            if not isinstance(domain_id, int) or isinstance(domain_id, bool):
                raise ValueError("admission rule domain_id must be an integer")
            if not isinstance(quota, int) or isinstance(quota, bool):
                raise ValueError("admission rule quota must be an integer")
            rules.append(
                GatewayAdmissionRule(
                    domain_id=domain_id,
                    topic=str(raw_rule.get("topic", "")),
                    traffic_class=str(raw_rule.get("traffic_class", "")),
                    max_accepted_frames=quota,
                    allowed_publishers=frozenset(publishers),
                    min_admission_score=float(raw_rule.get("min_admission_score", 0.0)),
                )
            )
        global_quota = document.get("max_accepted_frames")
        if global_quota is not None and (
            not isinstance(global_quota, int) or isinstance(global_quota, bool)
        ):
            raise ValueError("admission policy max_accepted_frames must be an integer")
        epoch_ms = document.get("epoch_ms")
        if epoch_ms is not None and (
            not isinstance(epoch_ms, int) or isinstance(epoch_ms, bool)
        ):
            raise ValueError("admission policy epoch_ms must be an integer")
        observation_ttl_ms = document.get("observation_ttl_ms", 5000)
        if (
            not isinstance(observation_ttl_ms, int)
            or isinstance(observation_ttl_ms, bool)
        ):
            raise ValueError("observation_ttl_ms must be an integer")
        native_qoe = document.get("native_qoe_debt", {})
        if not isinstance(native_qoe, dict):
            raise ValueError("native_qoe_debt must be an object")
        native_qoe_enabled = native_qoe.get("enabled", False)
        if not isinstance(native_qoe_enabled, bool):
            raise ValueError("native_qoe_debt enabled must be boolean")

        def native_qoe_number(name: str, default: float) -> float:
            value = native_qoe.get(name, default)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"native_qoe_debt {name} must be finite numeric")
            return float(value)

        native_qoe_alpha = native_qoe_number("ewma_alpha", 0.5)
        native_qoe_loss_saturation = native_qoe_number("loss_saturation", 0.05)
        native_qoe_rtt_ratio = native_qoe_number(
            "rtt_deadline_ratio_saturation", 1.0
        )
        native_qoe_jitter_ratio = native_qoe_number(
            "jitter_deadline_ratio_saturation", 0.25
        )
        application_outcome_qoe = document.get(
            "application_outcome_qoe_debt", {}
        )
        if not isinstance(application_outcome_qoe, dict):
            raise ValueError("application_outcome_qoe_debt must be an object")
        application_outcome_enabled = application_outcome_qoe.get(
            "enabled", False
        )
        if not isinstance(application_outcome_enabled, bool):
            raise ValueError(
                "application_outcome_qoe_debt enabled must be boolean"
            )
        application_outcome_alpha = application_outcome_qoe.get(
            "ewma_alpha", 0.5
        )
        if (
            not isinstance(application_outcome_alpha, (int, float))
            or isinstance(application_outcome_alpha, bool)
            or not math.isfinite(float(application_outcome_alpha))
        ):
            raise ValueError(
                "application_outcome_qoe_debt ewma_alpha must be finite numeric"
            )
        repair = document.get("repair", {})
        if not isinstance(repair, dict):
            raise ValueError("admission repair configuration must be an object")
        repair_capacity = repair.get("capacity_bytes", 0)
        repair_max_admitted = repair.get("max_admitted")
        if not isinstance(repair_capacity, int) or isinstance(repair_capacity, bool):
            raise ValueError("repair capacity_bytes must be an integer")
        if repair_max_admitted is not None and (
            not isinstance(repair_max_admitted, int)
            or isinstance(repair_max_admitted, bool)
        ):
            raise ValueError("repair max_admitted must be an integer")
        raw_paths = repair.get("paths", [])
        if not isinstance(raw_paths, list):
            raise ValueError("repair paths must be a list")
        repair_paths: list[RepairPath] = []
        for raw_path in raw_paths:
            if not isinstance(raw_path, dict):
                raise ValueError("repair path must be an object")
            repair_paths.append(
                RepairPath(
                    path_id=str(raw_path.get("path_id", "")),
                    latency_ms=float(raw_path.get("latency_ms", 0.0)),
                    loss=float(raw_path.get("loss", 0.0)),
                    failure_domain=str(raw_path.get("failure_domain", "")),
                    bandwidth_utilization=float(
                        raw_path.get("bandwidth_utilization", 0.0)
                    ),
                )
            )
        if any(
            not path.path_id
            or path.latency_ms < 0.0
            or not 0.0 <= path.loss <= 1.0
            for path in repair_paths
        ):
            raise ValueError("repair path values are invalid")
        return cls(
            rules=tuple(rules),
            default_action=str(document.get("default_action", "deny")),
            max_accepted_frames=global_quota,
            epoch_ms=epoch_ms,
            clock=clock,
            repair_capacity_bytes=repair_capacity,
            repair_max_admitted=repair_max_admitted,
            repair_paths=tuple(repair_paths),
            observation_ttl_ms=observation_ttl_ms,
            native_qoe_debt_enabled=native_qoe_enabled,
            native_qoe_debt_ewma_alpha=native_qoe_alpha,
            native_qoe_loss_saturation=native_qoe_loss_saturation,
            native_qoe_rtt_deadline_ratio_saturation=native_qoe_rtt_ratio,
            native_qoe_jitter_deadline_ratio_saturation=native_qoe_jitter_ratio,
            application_outcome_qoe_debt_enabled=application_outcome_enabled,
            application_outcome_qoe_debt_ewma_alpha=float(
                application_outcome_alpha
            ),
        )

    def admit(self, metadata: FrameMetadata) -> str:
        self._refresh_epoch()
        rule = self._rules_by_stream.get(metadata.stream_key)
        if rule is None:
            if self.default_action == "deny":
                self._reject("no_matching_rule")
                raise FrameAdmissionError(
                    status=403,
                    reason_code="no_matching_rule",
                    reason="frame is not covered by fleet admission policy",
                    traffic_class="unclassified",
                )
            traffic_class = "default"
        else:
            traffic_class = rule.traffic_class
            if metadata.traffic_class and metadata.traffic_class != traffic_class:
                self._reject("traffic_class_mismatch")
                raise FrameAdmissionError(
                    status=403,
                    reason_code="traffic_class_mismatch",
                    reason="frame traffic class does not match admission policy",
                    traffic_class=traffic_class,
                )
            if rule.allowed_publishers and metadata.publisher_id not in rule.allowed_publishers:
                self._reject("publisher_not_allowed")
                raise FrameAdmissionError(
                    status=403,
                    reason_code="publisher_not_allowed",
                    reason="publisher is not allowed by fleet admission policy",
                    traffic_class=traffic_class,
                )
            if self.effective_admission_score(metadata) < rule.min_admission_score:
                if self._try_repair(metadata, traffic_class):
                    return "repair"
                self._reject("qox_score_below_threshold")
                raise FrameAdmissionError(
                    status=429,
                    reason_code="qox_score_below_threshold",
                    reason="QoS/QoE admission score is below the stream threshold",
                    traffic_class=traffic_class,
                )
            accepted = self._accepted_by_stream.get(metadata.stream_key, 0)
            if accepted >= rule.max_accepted_frames:
                if self._try_repair(metadata, traffic_class):
                    return "repair"
                self._reject("stream_quota_exhausted")
                raise FrameAdmissionError(
                    status=429,
                    reason_code="stream_quota_exhausted",
                    reason="stream admission quota is exhausted",
                    traffic_class=traffic_class,
                )
        if (
            self.max_accepted_frames is not None
            and self._accepted_total >= self.max_accepted_frames
        ):
            if self._try_repair(metadata, traffic_class):
                return "repair"
            self._reject("fleet_quota_exhausted")
            raise FrameAdmissionError(
                status=429,
                reason_code="fleet_quota_exhausted",
                reason="fleet admission quota is exhausted",
                traffic_class=traffic_class,
            )
        self._accepted_total += 1
        self._accepted_cumulative += 1
        self._accepted_by_stream[metadata.stream_key] = (
            self._accepted_by_stream.get(metadata.stream_key, 0) + 1
        )
        self._accepted_by_class[traffic_class] = (
            self._accepted_by_class.get(traffic_class, 0) + 1
        )
        return "normal"

    def snapshot(self) -> dict[str, Any]:
        self._refresh_epoch()
        self._expire_observations()
        active_observations_by_source: dict[str, int] = {}
        active_observations_by_qoe_debt_source: dict[str, int] = {}
        for observation in self._observations.values():
            active_observations_by_source[observation.source] = (
                active_observations_by_source.get(observation.source, 0) + 1
            )
            active_observations_by_qoe_debt_source[observation.qoe_debt_source] = (
                active_observations_by_qoe_debt_source.get(
                    observation.qoe_debt_source, 0
                )
                + 1
            )
        return {
            "schema_version": ADMISSION_POLICY_SCHEMA_VERSION,
            "default_action": self.default_action,
            "max_accepted_frames": self.max_accepted_frames,
            "epoch_ms": self.epoch_ms,
            "epoch_reset_count": self._epoch_reset_count,
            "accepted_total": self._accepted_total,
            "accepted_cumulative": self._accepted_cumulative,
            "accepted_by_class": dict(sorted(self._accepted_by_class.items())),
            "rejected_by_reason": dict(sorted(self._rejected_by_reason.items())),
            "rule_count": len(self.rules),
            "repair_capacity_bytes": self.repair_capacity_bytes,
            "repair_allocated_bytes": self._repair_allocated_bytes,
            "repair_admitted_count": self._repair_admitted_count,
            "repair_deferred_count": self._repair_deferred_count,
            "repair_decisions": list(self._repair_decisions),
            "observation_ttl_ms": self.observation_ttl_ms,
            "native_qoe_debt_enabled": self.native_qoe_debt_enabled,
            "native_qoe_debt_ewma_alpha": self.native_qoe_debt_ewma_alpha,
            "application_outcome_qoe_debt_enabled": (
                self.application_outcome_qoe_debt_enabled
            ),
            "application_outcome_qoe_debt_ewma_alpha": (
                self.application_outcome_qoe_debt_ewma_alpha
            ),
            "active_observation_count": len(self._observations),
            "active_observations_by_source": dict(
                sorted(active_observations_by_source.items())
            ),
            "active_observations_by_qoe_debt_source": dict(
                sorted(active_observations_by_qoe_debt_source.items())
            ),
            "observation_updates": self._observation_updates,
            "observation_updates_by_source": dict(
                sorted(self._observation_updates_by_source.items())
            ),
            "observation_expirations": self._observation_expirations,
            "observation_score_uses": self._observation_score_uses,
            "native_qoe_debt_updates": self._native_qoe_debt_updates,
            "application_outcome_qoe_debt_updates": (
                self._application_outcome_qoe_debt_updates
            ),
            "application_task_outcome_updates": self._application_task_outcome_updates,
            "application_task_outcome_failures": (
                self._application_task_outcome_failures
            ),
        }

    def export_durable_state(self) -> dict[str, Any]:
        """Export quota, repair, and live observation state for one policy."""

        self._refresh_epoch()
        self._expire_observations()
        now = self._clock()
        return {
            "schema_version": "fleetrmw.quic_gateway_admission_state.v1",
            "policy_fingerprint": self.fingerprint,
            "epoch_elapsed_ms": max(0.0, (now - self._epoch_started) * 1000.0),
            "epoch_reset_count": self._epoch_reset_count,
            "accepted_total": self._accepted_total,
            "accepted_cumulative": self._accepted_cumulative,
            "accepted_by_stream": [
                {"domain_id": key[0], "topic": key[1], "count": count}
                for key, count in sorted(self._accepted_by_stream.items())
            ],
            "accepted_by_class": dict(sorted(self._accepted_by_class.items())),
            "rejected_by_reason": dict(sorted(self._rejected_by_reason.items())),
            "repair_allocated_bytes": self._repair_allocated_bytes,
            "repair_admitted_count": self._repair_admitted_count,
            "repair_deferred_count": self._repair_deferred_count,
            "repair_decisions": list(self._repair_decisions),
            "observations": [
                {
                    "domain_id": observation.domain_id,
                    "topic": observation.topic,
                    "publisher_id": observation.publisher_id,
                    "qoe_debt": observation.qoe_debt,
                    "measured_loss": observation.measured_loss,
                    "measured_rtt_ms": observation.measured_rtt_ms,
                    "measured_jitter_ms": observation.measured_jitter_ms,
                    "source": observation.source,
                    "qoe_debt_source": observation.qoe_debt_source,
                    "age_ms": max(0.0, (now - observation.updated_at) * 1000.0),
                }
                for observation in sorted(
                    self._observations.values(),
                    key=lambda value: (
                        value.domain_id,
                        value.topic,
                        value.publisher_id,
                    ),
                )
            ],
            "observation_updates": self._observation_updates,
            "observation_updates_by_source": dict(
                sorted(self._observation_updates_by_source.items())
            ),
            "observation_expirations": self._observation_expirations,
            "observation_score_uses": self._observation_score_uses,
            "native_qoe_debt_updates": self._native_qoe_debt_updates,
            "application_outcome_qoe_debt_updates": (
                self._application_outcome_qoe_debt_updates
            ),
            "application_task_outcome_updates": self._application_task_outcome_updates,
            "application_task_outcome_failures": (
                self._application_task_outcome_failures
            ),
        }

    def restore_durable_state(self, document: dict[str, Any]) -> None:
        """Restore state only when schema, policy fingerprint, and ranges match."""

        if document.get("schema_version") != "fleetrmw.quic_gateway_admission_state.v1":
            raise ValueError("unsupported durable admission state schema")
        if document.get("policy_fingerprint") != self.fingerprint:
            raise ValueError("durable admission policy fingerprint mismatch")

        def count(name: str) -> int:
            value = document.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"durable admission {name} is invalid")
            return value

        epoch_elapsed_ms = document.get("epoch_elapsed_ms")
        if (
            not isinstance(epoch_elapsed_ms, (int, float))
            or isinstance(epoch_elapsed_ms, bool)
            or not math.isfinite(float(epoch_elapsed_ms))
            or epoch_elapsed_ms < 0
        ):
            raise ValueError("durable admission epoch_elapsed_ms is invalid")
        accepted_by_stream: dict[tuple[int, str], int] = {}
        raw_streams = document.get("accepted_by_stream")
        if not isinstance(raw_streams, list):
            raise ValueError("durable admission accepted_by_stream is invalid")
        for row in raw_streams:
            if not isinstance(row, dict):
                raise ValueError("durable admission stream row is invalid")
            domain_id, topic, stream_count = (
                row.get("domain_id"),
                row.get("topic"),
                row.get("count"),
            )
            if (
                not isinstance(domain_id, int)
                or isinstance(domain_id, bool)
                or domain_id < 0
                or not isinstance(topic, str)
                or not topic.startswith("/")
                or not isinstance(stream_count, int)
                or isinstance(stream_count, bool)
                or stream_count < 0
                or (domain_id, topic) in accepted_by_stream
            ):
                raise ValueError("durable admission stream row is invalid")
            rule = self._rules_by_stream.get((domain_id, topic))
            if rule is not None and stream_count > rule.max_accepted_frames:
                raise ValueError("durable admission stream quota is exceeded")
            accepted_by_stream[(domain_id, topic)] = stream_count

        def count_map(name: str) -> dict[str, int]:
            value = document.get(name)
            if not isinstance(value, dict) or any(
                not isinstance(key, str)
                or not key
                or not isinstance(item, int)
                or isinstance(item, bool)
                or item < 0
                for key, item in value.items()
            ):
                raise ValueError(f"durable admission {name} is invalid")
            return dict(value)

        accepted_total = count("accepted_total")
        accepted_cumulative = count("accepted_cumulative")
        if accepted_cumulative < accepted_total or (
            self.max_accepted_frames is not None
            and accepted_total > self.max_accepted_frames
        ):
            raise ValueError("durable admission accepted counters are inconsistent")
        repair_allocated = count("repair_allocated_bytes")
        repair_admitted = count("repair_admitted_count")
        if repair_allocated > self.repair_capacity_bytes or (
            self.repair_max_admitted is not None
            and repair_admitted > self.repair_max_admitted
        ):
            raise ValueError("durable repair allocation exceeds policy")
        repair_decisions = document.get("repair_decisions")
        if not isinstance(repair_decisions, list) or not all(
            isinstance(value, dict) for value in repair_decisions
        ):
            raise ValueError("durable repair decisions are invalid")

        now = self._clock()
        observations: dict[tuple[int, str, str], GatewayObservation] = {}
        raw_observations = document.get("observations")
        if not isinstance(raw_observations, list):
            raise ValueError("durable observations are invalid")
        for row in raw_observations:
            if not isinstance(row, dict):
                raise ValueError("durable observation row is invalid")
            age_ms = row.get("age_ms")
            numeric = (
                row.get("qoe_debt"),
                row.get("measured_loss"),
                row.get("measured_rtt_ms"),
                row.get("measured_jitter_ms"),
                age_ms,
            )
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                for value in numeric
            ):
                raise ValueError("durable observation metrics are invalid")
            if age_ms < 0 or age_ms >= self.observation_ttl_ms:
                continue
            domain_id = row.get("domain_id")
            topic = row.get("topic")
            publisher_id = row.get("publisher_id")
            source = row.get("source")
            qoe_debt_source = row.get(
                "qoe_debt_source",
                "external_api" if source == "external_api" else "publisher_metadata",
            )
            self.update_observation(
                domain_id=domain_id,
                topic=topic,
                publisher_id=publisher_id,
                qoe_debt=float(row["qoe_debt"]),
                measured_loss=float(row["measured_loss"]),
                measured_rtt_ms=float(row["measured_rtt_ms"]),
                measured_jitter_ms=float(row["measured_jitter_ms"]),
                source=source,
                qoe_debt_source=qoe_debt_source,
            )
            observation = self._observations[(domain_id, topic, publisher_id)]
            observations[(domain_id, topic, publisher_id)] = GatewayObservation(
                **{
                    **observation.__dict__,
                    "updated_at": now - float(age_ms) / 1000.0,
                }
            )

        self._epoch_started = now - float(epoch_elapsed_ms) / 1000.0
        self._epoch_reset_count = count("epoch_reset_count")
        self._accepted_total = accepted_total
        self._accepted_cumulative = accepted_cumulative
        self._accepted_by_stream = accepted_by_stream
        self._accepted_by_class = count_map("accepted_by_class")
        self._rejected_by_reason = count_map("rejected_by_reason")
        self._repair_allocated_bytes = repair_allocated
        self._repair_admitted_count = repair_admitted
        self._repair_deferred_count = count("repair_deferred_count")
        self._repair_decisions = list(repair_decisions)
        self._observations = observations
        self._observation_updates = count("observation_updates")
        self._observation_updates_by_source = count_map(
            "observation_updates_by_source"
        )
        self._observation_expirations = count("observation_expirations")
        self._observation_score_uses = count("observation_score_uses")
        self._native_qoe_debt_updates = (
            count("native_qoe_debt_updates")
            if "native_qoe_debt_updates" in document
            else 0
        )
        self._application_outcome_qoe_debt_updates = (
            count("application_outcome_qoe_debt_updates")
            if "application_outcome_qoe_debt_updates" in document
            else 0
        )
        self._application_task_outcome_updates = (
            count("application_task_outcome_updates")
            if "application_task_outcome_updates" in document
            else 0
        )
        self._application_task_outcome_failures = (
            count("application_task_outcome_failures")
            if "application_task_outcome_failures" in document
            else 0
        )
        self._refresh_epoch()

    def _reject(self, reason: str) -> None:
        self._rejected_by_reason[reason] = self._rejected_by_reason.get(reason, 0) + 1

    def _refresh_epoch(self) -> None:
        if self.epoch_ms is None:
            return
        elapsed_ms = (self._clock() - self._epoch_started) * 1000.0
        if elapsed_ms < self.epoch_ms:
            return
        elapsed_epochs = max(1, int(elapsed_ms // self.epoch_ms))
        self._epoch_started += elapsed_epochs * self.epoch_ms / 1000.0
        self._epoch_reset_count += elapsed_epochs
        self._accepted_total = 0
        self._accepted_by_stream.clear()
        self._repair_allocated_bytes = 0
        self._repair_admitted_count = 0

    def _try_repair(self, metadata: FrameMetadata, traffic_class: str) -> bool:
        if not metadata.repair_requested or self.repair_capacity_bytes <= 0:
            return False
        if (
            self.repair_max_admitted is not None
            and self._repair_admitted_count >= self.repair_max_admitted
        ):
            self._repair_deferred_count += 1
            return False
        remaining_capacity = self.repair_capacity_bytes - self._repair_allocated_bytes
        if remaining_capacity <= 0:
            self._repair_deferred_count += 1
            return False
        scheduler = FleetRepairScheduler(
            FleetRepairSchedulerConfig(
                capacity_bytes=remaining_capacity,
                max_admitted_repairs=1,
                max_paths_per_repair=1,
            )
        )
        schedule = scheduler.schedule(
            [
                RepairDemand(
                    topic=metadata.topic,
                    robot_id=metadata.robot_id or metadata.publisher_id,
                    publisher_id=metadata.publisher_id,
                    source_sequence_number=metadata.source_sequence_number,
                    payload_bytes=max(1, metadata.frame_bytes),
                    remaining_deadline_ms=metadata.remaining_deadline_ms,
                    qoe_debt=metadata.qoe_debt,
                    criticality=metadata.criticality,
                    age_ms=metadata.age_ms,
                    prior_attempts=metadata.prior_repair_attempts,
                )
            ],
            self.repair_paths,
        )
        decision = schedule.decisions[0]
        self._repair_decisions.append(decision.as_dict())
        if decision.action != "repair":
            self._repair_deferred_count += 1
            return False
        self._repair_allocated_bytes += decision.allocated_bytes
        self._repair_admitted_count += 1
        self._accepted_cumulative += 1
        self._accepted_by_class[traffic_class] = (
            self._accepted_by_class.get(traffic_class, 0) + 1
        )
        return True

    def update_observation(
        self,
        *,
        domain_id: int,
        topic: str,
        publisher_id: str,
        qoe_debt: float,
        measured_loss: float,
        measured_rtt_ms: float,
        measured_jitter_ms: float,
        source: str = "external_api",
        qoe_debt_source: str | None = None,
    ) -> None:
        if domain_id < 0 or not topic.startswith("/") or not publisher_id:
            raise ValueError("observation identity is invalid")
        if not all(
            math.isfinite(value)
            for value in (qoe_debt, measured_loss, measured_rtt_ms, measured_jitter_ms)
        ):
            raise ValueError("observation values must be finite")
        if not 0.0 <= qoe_debt <= 1.0 or not 0.0 <= measured_loss <= 1.0:
            raise ValueError("observation debt/loss must be in [0, 1]")
        if measured_rtt_ms < 0.0 or measured_jitter_ms < 0.0:
            raise ValueError("observation RTT/jitter must be non-negative")
        if source not in {
            "external_api",
            "quic_session_native",
            "ngtcp2_public_api",
            "application_outcome",
        }:
            raise ValueError("observation source is unsupported")
        if qoe_debt_source is None:
            qoe_debt_source = (
                "external_api" if source == "external_api" else "publisher_metadata"
            )
        if qoe_debt_source not in {
            "external_api",
            "publisher_metadata",
            "gateway_derived_path",
            "gateway_derived_outcome",
        }:
            raise ValueError("observation QoE debt source is unsupported")
        key = (domain_id, topic, publisher_id)
        self._observations[key] = GatewayObservation(
            domain_id=domain_id,
            topic=topic,
            publisher_id=publisher_id,
            qoe_debt=qoe_debt,
            measured_loss=measured_loss,
            measured_rtt_ms=measured_rtt_ms,
            measured_jitter_ms=measured_jitter_ms,
            source=source,
            qoe_debt_source=qoe_debt_source,
            updated_at=self._clock(),
        )
        self._observation_updates += 1
        self._observation_updates_by_source[source] = (
            self._observation_updates_by_source.get(source, 0) + 1
        )

    def update_native_path_observation(
        self,
        *,
        metadata: FrameMetadata,
        measured_loss: float,
        measured_rtt_ms: float,
        measured_jitter_ms: float,
    ) -> float:
        """Publish authenticated path metrics and optionally derive QoE debt."""

        qoe_debt = metadata.qoe_debt
        qoe_debt_source = "publisher_metadata"
        if self.native_qoe_debt_enabled:
            loss_pressure = min(
                1.0, measured_loss / self.native_qoe_loss_saturation
            )
            if metadata.deadline_ms > 0.0:
                rtt_pressure = min(
                    1.0,
                    (measured_rtt_ms / metadata.deadline_ms)
                    / self.native_qoe_rtt_deadline_ratio_saturation,
                )
                jitter_pressure = min(
                    1.0,
                    (measured_jitter_ms / metadata.deadline_ms)
                    / self.native_qoe_jitter_deadline_ratio_saturation,
                )
            else:
                rtt_pressure = min(1.0, measured_rtt_ms / 1000.0)
                jitter_pressure = min(1.0, measured_jitter_ms / 100.0)
            instantaneous_debt = min(
                1.0,
                0.50 * loss_pressure
                + 0.35 * rtt_pressure
                + 0.15 * jitter_pressure,
            )
            previous = self._observations.get(
                (metadata.domain_id, metadata.topic, metadata.publisher_id)
            )
            if previous is not None and previous.qoe_debt_source == "gateway_derived_path":
                alpha = self.native_qoe_debt_ewma_alpha
                qoe_debt = min(
                    1.0,
                    alpha * instantaneous_debt + (1.0 - alpha) * previous.qoe_debt,
                )
            else:
                qoe_debt = instantaneous_debt
            qoe_debt_source = "gateway_derived_path"
            self._native_qoe_debt_updates += 1
        self.update_observation(
            domain_id=metadata.domain_id,
            topic=metadata.topic,
            publisher_id=metadata.publisher_id,
            qoe_debt=qoe_debt,
            measured_loss=measured_loss,
            measured_rtt_ms=measured_rtt_ms,
            measured_jitter_ms=measured_jitter_ms,
            source="quic_session_native",
            qoe_debt_source=qoe_debt_source,
        )
        return qoe_debt

    def update_application_outcome(
        self,
        *,
        domain_id: int,
        topic: str,
        publisher_id: str,
        delivered: bool,
        deadline_met: bool,
        observed_latency_ms: float,
        deadline_ms: float,
        task_succeeded: bool | None = None,
    ) -> float:
        """Derive bounded QoE debt from an authenticated application outcome."""

        if not self.application_outcome_qoe_debt_enabled:
            raise ValueError("application outcome QoE debt is disabled")
        if not isinstance(delivered, bool) or not isinstance(deadline_met, bool):
            raise ValueError("application outcome delivery flags must be boolean")
        if task_succeeded is not None and not isinstance(task_succeeded, bool):
            raise ValueError("application outcome task_succeeded must be boolean")
        if not all(
            math.isfinite(value) for value in (observed_latency_ms, deadline_ms)
        ):
            raise ValueError("application outcome latency values must be finite")
        if observed_latency_ms < 0.0 or deadline_ms <= 0.0:
            raise ValueError("application outcome latency/deadline is invalid")
        delivery_pressure = 0.0 if delivered else 1.0
        deadline_pressure = 0.0 if deadline_met else 1.0
        latency_pressure = min(1.0, observed_latency_ms / deadline_ms)
        if task_succeeded is None:
            instantaneous_debt = min(
                1.0,
                0.50 * delivery_pressure
                + 0.30 * deadline_pressure
                + 0.20 * latency_pressure,
            )
        else:
            task_pressure = 0.0 if task_succeeded else 1.0
            instantaneous_debt = min(
                1.0,
                0.35 * delivery_pressure
                + 0.25 * deadline_pressure
                + 0.15 * latency_pressure
                + 0.25 * task_pressure,
            )
        previous = self._observations.get((domain_id, topic, publisher_id))
        if (
            previous is not None
            and previous.qoe_debt_source == "gateway_derived_outcome"
        ):
            alpha = self.application_outcome_qoe_debt_ewma_alpha
            qoe_debt = min(
                1.0,
                alpha * instantaneous_debt
                + (1.0 - alpha) * previous.qoe_debt,
            )
        else:
            qoe_debt = instantaneous_debt
        self.update_observation(
            domain_id=domain_id,
            topic=topic,
            publisher_id=publisher_id,
            qoe_debt=qoe_debt,
            measured_loss=delivery_pressure,
            measured_rtt_ms=observed_latency_ms,
            measured_jitter_ms=0.0,
            source="application_outcome",
            qoe_debt_source="gateway_derived_outcome",
        )
        self._application_outcome_qoe_debt_updates += 1
        if task_succeeded is not None:
            self._application_task_outcome_updates += 1
            if not task_succeeded:
                self._application_task_outcome_failures += 1
        return qoe_debt

    def effective_admission_score(self, metadata: FrameMetadata) -> float:
        self._expire_observations()
        observation = self._observations.get(
            (metadata.domain_id, metadata.topic, metadata.publisher_id)
        )
        if observation is None:
            return metadata.admission_score
        self._observation_score_uses += 1
        effective_debt = max(metadata.qoe_debt, observation.qoe_debt)
        urgency = (
            min(1.0, metadata.age_ms / metadata.deadline_ms)
            if metadata.deadline_ms > 0.0
            else 0.0
        )
        deadline_rtt_pressure = (
            min(1.0, observation.measured_rtt_ms / metadata.deadline_ms)
            if metadata.deadline_ms > 0.0
            else min(1.0, observation.measured_rtt_ms / 1000.0)
        )
        jitter_pressure = min(1.0, observation.measured_jitter_ms / 100.0)
        path_pressure = min(
            1.0,
            0.5 * observation.measured_loss
            + 0.35 * deadline_rtt_pressure
            + 0.15 * jitter_pressure,
        )
        return min(
            1.0,
            0.40 * metadata.criticality
            + 0.25 * effective_debt
            + 0.20 * urgency
            + 0.15 * path_pressure,
        )

    def _expire_observations(self) -> None:
        threshold = self._clock() - self.observation_ttl_ms / 1000.0
        expired = [
            key
            for key, observation in self._observations.items()
            if observation.updated_at < threshold
        ]
        for key in expired:
            self._observations.pop(key, None)
        self._observation_expirations += len(expired)


@dataclass(frozen=True)
class GatewayResponse:
    status: int
    body: bytes
    content_type: str = "application/json"


@dataclass(frozen=True)
class PublishResult:
    accepted: bool
    duplicate: bool
    offset: int
    metadata: FrameMetadata
    admission_action: str = "normal"


@dataclass
class StoredFrame:
    offset: int
    payload: bytes
    metadata: FrameMetadata


@dataclass
class TopicHistory:
    records: deque[StoredFrame] = field(default_factory=deque)
    recent_keys: OrderedDict[tuple[str, int], None] = field(
        default_factory=OrderedDict
    )
    application_outcome_keys: OrderedDict[tuple[str, int], None] = field(
        default_factory=OrderedDict
    )
    next_offset: int = 0

    @property
    def base_offset(self) -> int:
        return self.records[0].offset if self.records else self.next_offset


class GatewayDurableStore:
    """SQLite WAL store for active/passive frame, dedup, and cursor recovery."""

    SCHEMA_VERSION = "fleetrmw.quic_gateway_durable_state.v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._writer_lease_id: str | None = None
        self._writer_fence_token: int | None = None
        self._writer_lease_ms: int | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = sqlite3.connect(str(self.path), timeout=10.0)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS durable_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS frames (
                  domain_id INTEGER NOT NULL,
                  topic TEXT NOT NULL,
                  frame_offset INTEGER NOT NULL,
                  payload BLOB NOT NULL,
                  publisher_id TEXT NOT NULL,
                  source_sequence_number INTEGER NOT NULL,
                  PRIMARY KEY (domain_id, topic, frame_offset)
                );
                CREATE TABLE IF NOT EXISTS dedup_keys (
                  ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                  domain_id INTEGER NOT NULL,
                  topic TEXT NOT NULL,
                  publisher_id TEXT NOT NULL,
                  source_sequence_number INTEGER NOT NULL,
                  UNIQUE (domain_id, topic, publisher_id, source_sequence_number)
                );
                CREATE TABLE IF NOT EXISTS application_outcomes (
                  ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                  domain_id INTEGER NOT NULL,
                  topic TEXT NOT NULL,
                  publisher_id TEXT NOT NULL,
                  source_sequence_number INTEGER NOT NULL,
                  UNIQUE (domain_id, topic, publisher_id, source_sequence_number),
                  FOREIGN KEY (domain_id, topic, publisher_id, source_sequence_number)
                    REFERENCES dedup_keys
                      (domain_id, topic, publisher_id, source_sequence_number)
                    ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS consumer_cursors (
                  domain_id INTEGER NOT NULL,
                  topic TEXT NOT NULL,
                  consumer_id TEXT NOT NULL,
                  next_offset INTEGER NOT NULL,
                  PRIMARY KEY (domain_id, topic, consumer_id)
                );
                CREATE TABLE IF NOT EXISTS admission_state (
                  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                  policy_fingerprint TEXT NOT NULL,
                  state_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS writer_lease (
                  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                  holder_id TEXT NOT NULL,
                  fence_token INTEGER NOT NULL,
                  expires_unix_ms INTEGER NOT NULL
                );
                """
            )
            existing = self._connection.execute(
                "SELECT value FROM durable_metadata WHERE key='schema_version'"
            ).fetchone()
            if existing is not None and existing[0] != self.SCHEMA_VERSION:
                raise FramePersistenceError(
                    f"unsupported durable state schema {existing[0]!r}"
                )
            self._connection.execute(
                "INSERT OR REPLACE INTO durable_metadata(key, value) VALUES(?, ?)",
                ("schema_version", self.SCHEMA_VERSION),
            )
            self._connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise FramePersistenceError(
                f"could not initialize durable gateway state: {exc}"
            ) from exc

    def recover(
        self,
        *,
        max_frame_bytes: int,
        max_frames_per_topic: int,
        dedup_capacity_per_topic: int,
    ) -> tuple[
        dict[tuple[int, str], TopicHistory],
        dict[tuple[int, str, str], int],
        int,
        int,
        int,
    ]:
        topics: dict[tuple[int, str], TopicHistory] = {}
        try:
            rows = self._connection.execute(
                "SELECT domain_id, topic, frame_offset, payload, publisher_id, "
                "source_sequence_number FROM frames "
                "ORDER BY domain_id, topic, frame_offset"
            ).fetchall()
            for domain_id, topic, offset, raw_payload, publisher_id, sequence in rows:
                payload = bytes(raw_payload)
                metadata = parse_data_frame(payload, max_frame_bytes=max_frame_bytes)
                if (
                    metadata.domain_id != domain_id
                    or metadata.topic != topic
                    or metadata.publisher_id != publisher_id
                    or metadata.source_sequence_number != sequence
                    or offset < 0
                ):
                    raise FramePersistenceError(
                        "durable frame index does not match its FleetRMW payload"
                    )
                history = topics.setdefault((domain_id, topic), TopicHistory())
                history.records.append(StoredFrame(offset, payload, metadata))
                history.next_offset = max(history.next_offset, offset + 1)

            dedup_count = 0
            dedup_rows = self._connection.execute(
                "SELECT domain_id, topic, publisher_id, source_sequence_number "
                "FROM dedup_keys ORDER BY ordinal"
            ).fetchall()
            for domain_id, topic, publisher_id, sequence in dedup_rows:
                history = topics.setdefault((domain_id, topic), TopicHistory())
                history.recent_keys[(publisher_id, sequence)] = None
                dedup_count += 1

            for stream_key, history in topics.items():
                while len(history.records) > max_frames_per_topic:
                    stale = history.records.popleft()
                    self._connection.execute(
                        "DELETE FROM frames WHERE domain_id=? AND topic=? AND frame_offset=?",
                        (*stream_key, stale.offset),
                    )
                while len(history.recent_keys) > dedup_capacity_per_topic:
                    publisher_id, sequence = history.recent_keys.popitem(last=False)[0]
                    self._connection.execute(
                        "DELETE FROM dedup_keys WHERE domain_id=? AND topic=? "
                        "AND publisher_id=? AND source_sequence_number=?",
                        (*stream_key, publisher_id, sequence),
                    )
                    dedup_count -= 1

            outcome_count = 0
            outcome_rows = self._connection.execute(
                "SELECT domain_id, topic, publisher_id, source_sequence_number "
                "FROM application_outcomes ORDER BY ordinal"
            ).fetchall()
            for domain_id, topic, publisher_id, sequence in outcome_rows:
                history = topics.setdefault((domain_id, topic), TopicHistory())
                if (publisher_id, sequence) not in history.recent_keys:
                    raise FramePersistenceError(
                        "durable application outcome lacks its accepted frame key"
                    )
                history.application_outcome_keys[(publisher_id, sequence)] = None
                outcome_count += 1

            cursors = {
                (domain_id, topic, consumer_id): next_offset
                for domain_id, topic, consumer_id, next_offset in self._connection.execute(
                    "SELECT domain_id, topic, consumer_id, next_offset "
                    "FROM consumer_cursors"
                ).fetchall()
                if next_offset >= 0
            }
            self._connection.commit()
        except FramePersistenceError:
            raise
        except (sqlite3.Error, FrameValidationError, TypeError, ValueError) as exc:
            raise FramePersistenceError(
                f"could not recover durable gateway state: {exc}"
            ) from exc
        frame_count = sum(len(history.records) for history in topics.values())
        return topics, cursors, frame_count, dedup_count, outcome_count

    def acquire_writer_lease(
        self, *, holder_id: str, lease_ms: int, now_unix_ms: int
    ) -> int:
        if not holder_id or lease_ms <= 0 or now_unix_ms < 0:
            raise ValueError("durable writer lease configuration is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT holder_id, fence_token, expires_unix_ms FROM writer_lease "
                "WHERE singleton_id=1"
            ).fetchone()
            if row is not None and row[0] != holder_id and row[2] > now_unix_ms:
                raise FramePersistenceError(
                    f"durable writer lease is held by {row[0]!r}"
                )
            if row is not None and row[0] == holder_id and row[2] > now_unix_ms:
                fence_token = int(row[1])
            else:
                fence_token = (int(row[1]) if row is not None else 0) + 1
            self._connection.execute(
                "INSERT INTO writer_lease(singleton_id, holder_id, fence_token, "
                "expires_unix_ms) VALUES(1, ?, ?, ?) "
                "ON CONFLICT(singleton_id) DO UPDATE SET "
                "holder_id=excluded.holder_id, fence_token=excluded.fence_token, "
                "expires_unix_ms=excluded.expires_unix_ms",
                (holder_id, fence_token, now_unix_ms + lease_ms),
            )
            self._connection.commit()
        except FramePersistenceError:
            self._connection.rollback()
            raise
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise FramePersistenceError(
                f"could not acquire durable writer lease: {exc}"
            ) from exc
        self._writer_lease_id = holder_id
        self._writer_fence_token = fence_token
        self._writer_lease_ms = lease_ms
        return fence_token

    def renew_writer_lease(self, *, now_unix_ms: int) -> int:
        if (
            self._writer_lease_id is None
            or self._writer_fence_token is None
            or self._writer_lease_ms is None
        ):
            raise FramePersistenceError("durable writer lease is not configured")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT holder_id, fence_token, expires_unix_ms FROM writer_lease "
                "WHERE singleton_id=1"
            ).fetchone()
            if (
                row is None
                or row[0] != self._writer_lease_id
                or int(row[1]) != self._writer_fence_token
                or int(row[2]) <= now_unix_ms
            ):
                raise FramePersistenceError("durable writer lease was lost or expired")
            expires = now_unix_ms + self._writer_lease_ms
            self._connection.execute(
                "UPDATE writer_lease SET expires_unix_ms=? WHERE singleton_id=1",
                (expires,),
            )
            self._connection.commit()
            return expires
        except FramePersistenceError:
            self._connection.rollback()
            raise
        except sqlite3.Error as exc:
            self._connection.rollback()
            raise FramePersistenceError(
                f"could not renew durable writer lease: {exc}"
            ) from exc

    def release_writer_lease(self, *, now_unix_ms: int) -> None:
        if self._writer_lease_id is None or self._writer_fence_token is None:
            return
        try:
            with self._connection:
                self._connection.execute(
                    "UPDATE writer_lease SET expires_unix_ms=? WHERE singleton_id=1 "
                    "AND holder_id=? AND fence_token=?",
                    (
                        now_unix_ms,
                        self._writer_lease_id,
                        self._writer_fence_token,
                    ),
                )
        except sqlite3.Error as exc:
            raise FramePersistenceError(
                f"could not release durable writer lease: {exc}"
            ) from exc
        finally:
            self._writer_lease_id = None
            self._writer_fence_token = None
            self._writer_lease_ms = None

    def _verify_writer_lease(self, *, now_unix_ms: int) -> None:
        if self._writer_lease_id is None or self._writer_fence_token is None:
            return
        row = self._connection.execute(
            "SELECT holder_id, fence_token, expires_unix_ms FROM writer_lease "
            "WHERE singleton_id=1"
        ).fetchone()
        if (
            row is None
            or row[0] != self._writer_lease_id
            or int(row[1]) != self._writer_fence_token
            or int(row[2]) <= now_unix_ms
        ):
            raise FramePersistenceError(
                "durable writer fence rejected a stale or expired writer"
            )

    def load_admission_state(
        self, *, policy_fingerprint: str
    ) -> dict[str, Any] | None:
        try:
            row = self._connection.execute(
                "SELECT policy_fingerprint, state_json FROM admission_state "
                "WHERE singleton_id=1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise FramePersistenceError(
                f"could not recover durable admission state: {exc}"
            ) from exc
        if row is None:
            return None
        stored_fingerprint, encoded = row
        if stored_fingerprint != policy_fingerprint:
            raise FramePersistenceError(
                "durable admission policy fingerprint does not match configuration"
            )
        try:
            document = json.loads(encoded)
        except (TypeError, json.JSONDecodeError) as exc:
            raise FramePersistenceError(
                f"durable admission state is invalid: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise FramePersistenceError("durable admission state must be an object")
        return document

    def append_frame(
        self,
        *,
        metadata: FrameMetadata,
        offset: int,
        payload: bytes,
        evict_offset: int | None,
        dedup_capacity_per_topic: int,
        admission_state: dict[str, Any] | None = None,
        now_unix_ms: int | None = None,
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            with self._connection:
                if self._writer_lease_id is not None:
                    if now_unix_ms is None:
                        raise FramePersistenceError(
                            "durable writer fence requires a wall-clock timestamp"
                        )
                    self._verify_writer_lease(now_unix_ms=now_unix_ms)
                self._connection.execute(
                    "INSERT INTO frames(domain_id, topic, frame_offset, payload, "
                    "publisher_id, source_sequence_number) VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        metadata.domain_id,
                        metadata.topic,
                        offset,
                        sqlite3.Binary(payload),
                        metadata.publisher_id,
                        metadata.source_sequence_number,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO dedup_keys(domain_id, topic, publisher_id, "
                    "source_sequence_number) VALUES(?, ?, ?, ?)",
                    (
                        metadata.domain_id,
                        metadata.topic,
                        metadata.publisher_id,
                        metadata.source_sequence_number,
                    ),
                )
                if evict_offset is not None:
                    self._connection.execute(
                        "DELETE FROM frames WHERE domain_id=? AND topic=? "
                        "AND frame_offset=?",
                        (metadata.domain_id, metadata.topic, evict_offset),
                    )
                stale_ordinals = self._connection.execute(
                    "SELECT ordinal FROM dedup_keys WHERE domain_id=? AND topic=? "
                    "ORDER BY ordinal DESC LIMIT -1 OFFSET ?",
                    (
                        metadata.domain_id,
                        metadata.topic,
                        dedup_capacity_per_topic,
                    ),
                ).fetchall()
                if stale_ordinals:
                    self._connection.executemany(
                        "DELETE FROM dedup_keys WHERE ordinal=?", stale_ordinals
                    )
                if admission_state is not None:
                    fingerprint = admission_state.get("policy_fingerprint")
                    if not isinstance(fingerprint, str) or not fingerprint:
                        raise FramePersistenceError(
                            "durable admission state lacks a policy fingerprint"
                        )
                    encoded = json.dumps(
                        admission_state,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    self._connection.execute(
                        "INSERT INTO admission_state(singleton_id, "
                        "policy_fingerprint, state_json) VALUES(1, ?, ?) "
                        "ON CONFLICT(singleton_id) DO UPDATE SET "
                        "policy_fingerprint=excluded.policy_fingerprint, "
                        "state_json=excluded.state_json",
                        (fingerprint, encoded),
                    )
        except FramePersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise FramePersistenceError(
                f"could not commit durable gateway frame: {exc}"
            ) from exc

    def set_cursor(
        self,
        *,
        domain_id: int,
        topic: str,
        consumer_id: str,
        next_offset: int,
        now_unix_ms: int | None = None,
    ) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            with self._connection:
                if self._writer_lease_id is not None:
                    if now_unix_ms is None:
                        raise FramePersistenceError(
                            "durable writer fence requires a wall-clock timestamp"
                        )
                    self._verify_writer_lease(now_unix_ms=now_unix_ms)
                self._connection.execute(
                    "INSERT INTO consumer_cursors(domain_id, topic, consumer_id, "
                    "next_offset) VALUES(?, ?, ?, ?) "
                    "ON CONFLICT(domain_id, topic, consumer_id) DO UPDATE SET "
                    "next_offset=excluded.next_offset",
                    (domain_id, topic, consumer_id, next_offset),
                )
        except FramePersistenceError:
            raise
        except sqlite3.Error as exc:
            raise FramePersistenceError(
                f"could not commit durable gateway cursor: {exc}"
            ) from exc

    def commit_application_outcome(
        self,
        *,
        domain_id: int,
        topic: str,
        publisher_id: str,
        source_sequence_number: int,
        capacity_per_topic: int,
        admission_state: dict[str, Any],
        now_unix_ms: int | None = None,
    ) -> bool:
        """Atomically store one outcome key and its post-outcome admission state."""

        try:
            self._connection.execute("BEGIN IMMEDIATE")
            with self._connection:
                if self._writer_lease_id is not None:
                    if now_unix_ms is None:
                        raise FramePersistenceError(
                            "durable writer fence requires a wall-clock timestamp"
                        )
                    self._verify_writer_lease(now_unix_ms=now_unix_ms)
                known = self._connection.execute(
                    "SELECT 1 FROM dedup_keys WHERE domain_id=? AND topic=? "
                    "AND publisher_id=? AND source_sequence_number=?",
                    (domain_id, topic, publisher_id, source_sequence_number),
                ).fetchone()
                if known is None:
                    raise FramePersistenceError(
                        "durable application outcome references an unknown frame"
                    )
                inserted = self._connection.execute(
                    "INSERT OR IGNORE INTO application_outcomes(domain_id, topic, "
                    "publisher_id, source_sequence_number) VALUES(?, ?, ?, ?)",
                    (domain_id, topic, publisher_id, source_sequence_number),
                ).rowcount == 1
                if not inserted:
                    return False
                stale_ordinals = self._connection.execute(
                    "SELECT ordinal FROM application_outcomes WHERE domain_id=? "
                    "AND topic=? ORDER BY ordinal DESC LIMIT -1 OFFSET ?",
                    (domain_id, topic, capacity_per_topic),
                ).fetchall()
                if stale_ordinals:
                    self._connection.executemany(
                        "DELETE FROM application_outcomes WHERE ordinal=?",
                        stale_ordinals,
                    )
                fingerprint = admission_state.get("policy_fingerprint")
                if not isinstance(fingerprint, str) or not fingerprint:
                    raise FramePersistenceError(
                        "durable admission state lacks a policy fingerprint"
                    )
                encoded = json.dumps(
                    admission_state,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                self._connection.execute(
                    "INSERT INTO admission_state(singleton_id, policy_fingerprint, "
                    "state_json) VALUES(1, ?, ?) "
                    "ON CONFLICT(singleton_id) DO UPDATE SET "
                    "policy_fingerprint=excluded.policy_fingerprint, "
                    "state_json=excluded.state_json",
                    (fingerprint, encoded),
                )
                return True
        except FramePersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise FramePersistenceError(
                f"could not commit durable application outcome: {exc}"
            ) from exc

    def snapshot(self) -> dict[str, Any]:
        try:
            frame_count = self._connection.execute(
                "SELECT COUNT(*) FROM frames"
            ).fetchone()[0]
            dedup_count = self._connection.execute(
                "SELECT COUNT(*) FROM dedup_keys"
            ).fetchone()[0]
            consumer_count = self._connection.execute(
                "SELECT COUNT(*) FROM consumer_cursors"
            ).fetchone()[0]
            admission_state_count = self._connection.execute(
                "SELECT COUNT(*) FROM admission_state"
            ).fetchone()[0]
            application_outcome_count = self._connection.execute(
                "SELECT COUNT(*) FROM application_outcomes"
            ).fetchone()[0]
            writer_lease = self._connection.execute(
                "SELECT holder_id, fence_token, expires_unix_ms FROM writer_lease "
                "WHERE singleton_id=1"
            ).fetchone()
            journal_mode = self._connection.execute("PRAGMA journal_mode").fetchone()[0]
        except sqlite3.Error as exc:
            raise FramePersistenceError(
                f"could not inspect durable gateway state: {exc}"
            ) from exc
        return {
            "schema_version": self.SCHEMA_VERSION,
            "path": str(self.path),
            "journal_mode": str(journal_mode),
            "synchronous": "full",
            "retained_frame_count": frame_count,
            "dedup_key_count": dedup_count,
            "consumer_cursor_count": consumer_count,
            "admission_state_count": admission_state_count,
            "application_outcome_count": application_outcome_count,
            "writer_lease": (
                {
                    "holder_id": writer_lease[0],
                    "fence_token": writer_lease[1],
                    "expires_unix_ms": writer_lease[2],
                }
                if writer_lease is not None
                else None
            ),
        }

    def close(self) -> None:
        self._connection.close()


def parse_data_frame(payload: bytes, *, max_frame_bytes: int) -> FrameMetadata:
    if not payload or len(payload) > max_frame_bytes:
        raise FrameValidationError("frame body is empty or exceeds max_frame_bytes")
    if not payload.startswith(DATA_FRAME_MAGIC):
        raise FrameValidationError("frame body is missing FRMW1 magic")
    try:
        document = json.loads(payload[len(DATA_FRAME_MAGIC) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameValidationError("frame body is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise FrameValidationError("frame document must be an object")
    if document.get("schema_version") != DATA_FRAME_SCHEMA_VERSION:
        raise FrameValidationError("unsupported FleetRMW data-frame schema")
    route = document.get("route")
    envelope = document.get("sample_envelope")
    if not isinstance(route, dict) or not isinstance(envelope, dict):
        raise FrameValidationError("route and sample_envelope objects are required")
    topic = route.get("topic")
    publisher_id = envelope.get("publisher_id")
    sequence = envelope.get("source_sequence_number")
    domain_id = document.get("domain_id", 0)
    if not isinstance(topic, str) or not topic.startswith("/"):
        raise FrameValidationError("route.topic must be an absolute ROS topic")
    if envelope.get("topic") != topic:
        raise FrameValidationError("route and sample_envelope topics differ")
    if not isinstance(publisher_id, str) or not publisher_id:
        raise FrameValidationError("sample_envelope.publisher_id is required")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise FrameValidationError("source_sequence_number must be positive")
    if not isinstance(domain_id, int) or isinstance(domain_id, bool) or domain_id < 0:
        raise FrameValidationError("domain_id must be non-negative")
    serialized = document.get("serialized_payload")
    if serialized is not None:
        if not isinstance(serialized, dict):
            raise FrameValidationError("serialized_payload must be an object")
        encoded = serialized.get("data")
        size = serialized.get("size")
        encoding = serialized.get("encoding")
        if encoding not in ("hex", "base64") or not isinstance(encoded, str):
            raise FrameValidationError(
                "serialized payload must use hex or base64 encoding"
            )
        try:
            decoded = (
                base64.b64decode(encoded, validate=True)
                if encoding == "base64"
                else bytes.fromhex(encoded)
            )
        except ValueError as exc:
            raise FrameValidationError(
                "serialized payload contains invalid encoded data"
            ) from exc
        if not isinstance(size, int) or isinstance(size, bool) or size != len(decoded):
            raise FrameValidationError("serialized payload size does not match data")
    flow_class = route.get("flow_class", "")
    robot_id = route.get("robot_id", envelope.get("robot_id", ""))
    if not isinstance(flow_class, str) or not isinstance(robot_id, str):
        raise FrameValidationError("route flow_class and robot_id must be strings")
    delivery = document.get("delivery", {})
    timing = document.get("timing", {})
    qox = document.get("qox", {})
    repair = document.get("repair", {})
    if not all(isinstance(value, dict) for value in (delivery, timing, qox, repair)):
        raise FrameValidationError("delivery, timing, qox, and repair must be objects")

    def number(
        source: dict[str, Any],
        key: str,
        *,
        minimum: float,
        maximum: float | None = None,
    ) -> float:
        value = source.get(key, 0.0)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise FrameValidationError(f"{key} must be numeric")
        result = float(value)
        if not math.isfinite(result) or result < minimum:
            raise FrameValidationError(f"{key} is outside its valid range")
        if maximum is not None and result > maximum:
            raise FrameValidationError(f"{key} is outside its valid range")
        return result

    deadline_ms = number(delivery, "deadline_ms", minimum=0.0)
    age_ms = number(timing, "age_ms", minimum=0.0)
    qoe_debt = number(qox, "qoe_debt", minimum=0.0, maximum=1.0)
    criticality = number(qox, "task_criticality", minimum=0.0, maximum=1.0)
    repair_requested = repair.get("requested", False)
    prior_attempts = repair.get("prior_attempts", 0)
    if not isinstance(repair_requested, bool):
        raise FrameValidationError("repair.requested must be boolean")
    if (
        not isinstance(prior_attempts, int)
        or isinstance(prior_attempts, bool)
        or prior_attempts < 0
    ):
        raise FrameValidationError("repair.prior_attempts must be non-negative")
    return FrameMetadata(
        domain_id=domain_id,
        topic=topic,
        publisher_id=publisher_id,
        source_sequence_number=sequence,
        robot_id=robot_id,
        traffic_class=flow_class,
        deadline_ms=deadline_ms,
        age_ms=age_ms,
        qoe_debt=qoe_debt,
        criticality=criticality,
        repair_requested=repair_requested,
        prior_repair_attempts=prior_attempts,
        frame_bytes=len(payload),
    )


class FleetQoxGatewayState:
    def __init__(
        self,
        *,
        max_frames_per_topic: int = 1024,
        max_frame_bytes: int = 1_048_576,
        dedup_capacity_per_topic: int | None = None,
        admission_policy: GatewayAdmissionPolicy | None = None,
        max_batch_frames: int = 64,
        durable_state_path: str | Path | None = None,
        durable_writer_id: str | None = None,
        durable_writer_lease_ms: int = 5000,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if max_frames_per_topic <= 0 or max_frame_bytes <= 0 or max_batch_frames <= 0:
            raise ValueError("gateway frame limits must be positive")
        self.max_frames_per_topic = max_frames_per_topic
        self.max_frame_bytes = max_frame_bytes
        self.dedup_capacity_per_topic = (
            dedup_capacity_per_topic
            if dedup_capacity_per_topic is not None
            else max_frames_per_topic * 4
        )
        if self.dedup_capacity_per_topic < max_frames_per_topic:
            raise ValueError("dedup capacity must cover the retained history")
        if durable_writer_id is not None and (
            not durable_writer_id or durable_state_path is None
        ):
            raise ValueError("durable writer id requires durable state")
        if durable_writer_lease_ms <= 0:
            raise ValueError("durable writer lease must be positive")
        self._topics: dict[tuple[int, str], TopicHistory] = {}
        self._consumer_offsets: dict[tuple[int, str, str], int] = {}
        self.admission_policy = admission_policy
        self.max_batch_frames = max_batch_frames
        self._wall_clock = wall_clock
        self._durable_writer_id = durable_writer_id
        self._durable_writer_lease_ms = durable_writer_lease_ms
        self._durable_store: Any = None
        if durable_state_path is not None:
            durable_location = str(durable_state_path)
            if durable_location.startswith(("postgresql://", "postgres://")):
                from .quic_gateway_postgres import PostgresGatewayDurableStore

                self._durable_store = PostgresGatewayDurableStore(durable_location)
            else:
                self._durable_store = GatewayDurableStore(durable_state_path)
        self._metrics: dict[str, int] = {
            "requests_total": 0,
            "post_requests": 0,
            "get_requests": 0,
            "accepted_frames": 0,
            "duplicate_frames": 0,
            "invalid_frames": 0,
            "dequeued_frames": 0,
            "empty_takes": 0,
            "evicted_frames": 0,
            "dedup_key_evictions": 0,
            "consumer_overruns": 0,
            "observation_requests": 0,
            "application_outcome_requests": 0,
            "application_outcome_updates": 0,
            "application_outcome_duplicates": 0,
            "application_outcome_unknown_frames": 0,
            "invalid_application_outcomes": 0,
            "application_task_outcome_updates": 0,
            "application_task_outcome_failures": 0,
            "batch_requests": 0,
            "batch_frames": 0,
            "batch_accepted_frames": 0,
            "batch_rejected_frames": 0,
            "durable_frame_commits": 0,
            "durable_cursor_commits": 0,
            "durable_admission_commits": 0,
            "durable_application_outcome_commits": 0,
            "durable_persistence_failures": 0,
            "recovered_frames": 0,
            "recovered_dedup_keys": 0,
            "recovered_application_outcomes": 0,
            "recovered_consumers": 0,
            "recovered_admission_state": 0,
            "durable_writer_lease_acquires": 0,
            "durable_writer_lease_renewals": 0,
            "durable_writer_lease_failures": 0,
        }
        if self._durable_store is not None:
            lease_acquired = False
            try:
                if self._durable_writer_id is not None:
                    self._durable_store.acquire_writer_lease(
                        holder_id=self._durable_writer_id,
                        lease_ms=self._durable_writer_lease_ms,
                        now_unix_ms=self._now_unix_ms(),
                    )
                    lease_acquired = True
                    self._metrics["durable_writer_lease_acquires"] = 1
                (
                    self._topics,
                    self._consumer_offsets,
                    self._metrics["recovered_frames"],
                    self._metrics["recovered_dedup_keys"],
                    self._metrics["recovered_application_outcomes"],
                ) = self._durable_store.recover(
                    max_frame_bytes=self.max_frame_bytes,
                    max_frames_per_topic=self.max_frames_per_topic,
                    dedup_capacity_per_topic=self.dedup_capacity_per_topic,
                )
                self._metrics["recovered_consumers"] = len(self._consumer_offsets)
                durable_snapshot = self._durable_store.snapshot()
                if self.admission_policy is None:
                    if durable_snapshot["admission_state_count"] > 0:
                        raise ValueError(
                            "durable admission state exists but no admission policy "
                            "was configured"
                        )
                else:
                    admission_state = self._durable_store.load_admission_state(
                        policy_fingerprint=self.admission_policy.fingerprint
                    )
                    if admission_state is None:
                        if self._metrics["recovered_frames"] > 0:
                            raise ValueError(
                                "retained frames lack durable admission state; "
                                "refusing to reset fleet quota/repair state"
                            )
                    else:
                        self.admission_policy.restore_durable_state(admission_state)
                        self._metrics["recovered_admission_state"] = 1
            except (FramePersistenceError, ValueError):
                if lease_acquired:
                    try:
                        self._durable_store.release_writer_lease(
                            now_unix_ms=self._now_unix_ms()
                        )
                    except FramePersistenceError:
                        pass
                self._durable_store.close()
                self._durable_store = None
                raise

    def publish(
        self,
        payload: bytes,
        *,
        expected_domain_id: int | None = None,
        expected_topic: str | None = None,
    ) -> PublishResult:
        metadata = parse_data_frame(payload, max_frame_bytes=self.max_frame_bytes)
        if expected_domain_id is not None and metadata.domain_id != expected_domain_id:
            raise FrameValidationError("frame domain_id does not match request path")
        if expected_topic is not None and metadata.topic != expected_topic:
            raise FrameValidationError("frame topic does not match request path")
        history = self._topics.get(metadata.stream_key)
        if history is not None and metadata.dedup_key in history.recent_keys:
            history.recent_keys.move_to_end(metadata.dedup_key)
            self._metrics["duplicate_frames"] += 1
            return PublishResult(
                accepted=False,
                duplicate=True,
                offset=history.next_offset,
                metadata=metadata,
                admission_action="duplicate",
            )
        admission_action = "normal"
        admission_state_before = None
        if self.admission_policy is not None:
            if self._durable_store is not None:
                admission_state_before = (
                    self.admission_policy.export_durable_state()
                )
            admission_action = self.admission_policy.admit(metadata)
        admission_state_after = (
            self.admission_policy.export_durable_state()
            if self.admission_policy is not None and self._durable_store is not None
            else None
        )
        history = self._topics.setdefault(metadata.stream_key, TopicHistory())
        offset = history.next_offset
        evict_offset = (
            history.records[0].offset
            if len(history.records) >= self.max_frames_per_topic
            else None
        )
        if self._durable_store is not None:
            try:
                self._durable_store.append_frame(
                    metadata=metadata,
                    offset=offset,
                    payload=bytes(payload),
                    evict_offset=evict_offset,
                    dedup_capacity_per_topic=self.dedup_capacity_per_topic,
                    admission_state=admission_state_after,
                    now_unix_ms=self._now_unix_ms(),
                )
            except FramePersistenceError:
                if (
                    self.admission_policy is not None
                    and admission_state_before is not None
                ):
                    self.admission_policy.restore_durable_state(
                        admission_state_before
                    )
                self._metrics["durable_persistence_failures"] += 1
                raise
            self._metrics["durable_frame_commits"] += 1
            if admission_state_after is not None:
                self._metrics["durable_admission_commits"] += 1
        history.next_offset += 1
        history.records.append(StoredFrame(offset, bytes(payload), metadata))
        history.recent_keys[metadata.dedup_key] = None
        self._metrics["accepted_frames"] += 1
        while len(history.records) > self.max_frames_per_topic:
            history.records.popleft()
            self._metrics["evicted_frames"] += 1
        while len(history.recent_keys) > self.dedup_capacity_per_topic:
            history.recent_keys.popitem(last=False)
            self._metrics["dedup_key_evictions"] += 1
        return PublishResult(
            accepted=True,
            duplicate=False,
            offset=offset,
            metadata=metadata,
            admission_action=admission_action,
        )

    def publish_batch(self, payloads: list[bytes]) -> list[dict[str, Any]]:
        if not payloads or len(payloads) > self.max_batch_frames:
            raise FrameValidationError("frame batch size is outside configured limits")
        metadata_rows = [
            parse_data_frame(payload, max_frame_bytes=self.max_frame_bytes)
            for payload in payloads
        ]
        scores = [
            (
                self.admission_policy.effective_admission_score(metadata)
                if self.admission_policy is not None
                else metadata.admission_score
            )
            for metadata in metadata_rows
        ]
        order = sorted(
            range(len(payloads)),
            key=lambda index: (
                -scores[index],
                metadata_rows[index].publisher_id,
                metadata_rows[index].source_sequence_number,
            ),
        )
        results: list[dict[str, Any] | None] = [None] * len(payloads)
        for index in order:
            try:
                result = self.publish(payloads[index])
            except FrameAdmissionError as exc:
                results[index] = {
                    "accepted": False,
                    "status": exc.status,
                    "reason": exc.reason_code,
                    "message": exc.reason,
                    "traffic_class": exc.traffic_class,
                    "score": scores[index],
                }
                self._metrics["batch_rejected_frames"] += 1
            else:
                results[index] = {
                    "accepted": result.accepted,
                    "duplicate": result.duplicate,
                    "status": 200,
                    "offset": result.offset,
                    "admission_action": result.admission_action,
                    "score": scores[index],
                }
                if result.accepted:
                    self._metrics["batch_accepted_frames"] += 1
        return [result for result in results if result is not None]

    def record_application_outcome(
        self, document: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.admission_policy is None:
            raise FrameValidationError("admission policy is disabled")
        if not self.admission_policy.application_outcome_qoe_debt_enabled:
            raise FrameValidationError("application outcome QoE debt is disabled")
        if document.get("schema_version") != APPLICATION_OUTCOME_SCHEMA_VERSION:
            raise FrameValidationError("unsupported application outcome schema")
        domain_id = document.get("domain_id")
        topic = document.get("topic")
        publisher_id = document.get("publisher_id")
        sequence = document.get("source_sequence_number")
        delivered = document.get("delivered")
        deadline_met = document.get("deadline_met")
        observed_latency_ms = document.get("observed_latency_ms")
        deadline_ms = document.get("deadline_ms")
        task_fields_present = any(
            name in document
            for name in ("task_kind", "terminal_status", "task_succeeded")
        )
        task_kind = document.get("task_kind")
        terminal_status = document.get("terminal_status")
        task_succeeded = document.get("task_succeeded")
        if (
            not isinstance(domain_id, int)
            or isinstance(domain_id, bool)
            or domain_id < 0
            or not isinstance(topic, str)
            or not topic.startswith("/")
            or not isinstance(publisher_id, str)
            or not publisher_id
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= 0
        ):
            raise FrameValidationError("application outcome identity is invalid")
        if not isinstance(delivered, bool) or not isinstance(deadline_met, bool):
            raise FrameValidationError(
                "application outcome delivery flags must be boolean"
            )
        if not delivered and deadline_met:
            raise FrameValidationError(
                "undelivered application outcome cannot meet its deadline"
            )
        if task_fields_present:
            if (
                not isinstance(task_kind, str)
                or task_kind not in TASK_KINDS
                or not isinstance(terminal_status, str)
                or terminal_status not in TERMINAL_STATUSES
                or not isinstance(task_succeeded, bool)
            ):
                raise FrameValidationError(
                    "application task outcome fields are invalid or incomplete"
                )
            if task_succeeded != (terminal_status == "succeeded"):
                raise FrameValidationError(
                    "application task success contradicts terminal status"
                )
            if task_succeeded and not delivered:
                raise FrameValidationError(
                    "an undelivered application task cannot succeed"
                )
        if (
            not isinstance(observed_latency_ms, (int, float))
            or isinstance(observed_latency_ms, bool)
            or not isinstance(deadline_ms, (int, float))
            or isinstance(deadline_ms, bool)
        ):
            raise FrameValidationError(
                "application outcome latency/deadline must be numeric"
            )
        stream_key = (domain_id, topic)
        dedup_key = (publisher_id, sequence)
        history = self._topics.get(stream_key)
        if history is None or dedup_key not in history.recent_keys:
            self._metrics["application_outcome_unknown_frames"] += 1
            return None
        if dedup_key in history.application_outcome_keys:
            history.application_outcome_keys.move_to_end(dedup_key)
            self._metrics["application_outcome_duplicates"] += 1
            return {"accepted": True, "duplicate": True}
        admission_state_before = (
            self.admission_policy.export_durable_state()
            if self._durable_store is not None
            else None
        )
        try:
            qoe_debt = self.admission_policy.update_application_outcome(
                domain_id=domain_id,
                topic=topic,
                publisher_id=publisher_id,
                delivered=delivered,
                deadline_met=deadline_met,
                observed_latency_ms=float(observed_latency_ms),
                deadline_ms=float(deadline_ms),
                task_succeeded=(task_succeeded if task_fields_present else None),
            )
        except ValueError as exc:
            raise FrameValidationError(str(exc)) from exc
        if self._durable_store is not None:
            admission_state_after = self.admission_policy.export_durable_state()
            try:
                inserted = self._durable_store.commit_application_outcome(
                    domain_id=domain_id,
                    topic=topic,
                    publisher_id=publisher_id,
                    source_sequence_number=sequence,
                    capacity_per_topic=self.dedup_capacity_per_topic,
                    admission_state=admission_state_after,
                    now_unix_ms=self._now_unix_ms(),
                )
            except FramePersistenceError:
                assert admission_state_before is not None
                self.admission_policy.restore_durable_state(admission_state_before)
                self._metrics["durable_persistence_failures"] += 1
                raise
            if not inserted:
                assert admission_state_before is not None
                self.admission_policy.restore_durable_state(admission_state_before)
                history.application_outcome_keys[dedup_key] = None
                self._metrics["application_outcome_duplicates"] += 1
                return {"accepted": True, "duplicate": True}
            self._metrics["durable_application_outcome_commits"] += 1
            self._metrics["durable_admission_commits"] += 1
        history.application_outcome_keys[dedup_key] = None
        while len(history.application_outcome_keys) > self.dedup_capacity_per_topic:
            history.application_outcome_keys.popitem(last=False)
        self._metrics["application_outcome_updates"] += 1
        if task_fields_present:
            self._metrics["application_task_outcome_updates"] += 1
            if not task_succeeded:
                self._metrics["application_task_outcome_failures"] += 1
        return {"accepted": True, "duplicate": False, "qoe_debt": qoe_debt}

    def take(self, *, domain_id: int, topic: str, consumer_id: str) -> bytes | None:
        history = self._topics.get((domain_id, topic))
        if history is None:
            self._metrics["empty_takes"] += 1
            return None
        consumer_key = (domain_id, topic, consumer_id)
        cursor = self._consumer_offsets.get(consumer_key, history.base_offset)
        if cursor < history.base_offset:
            cursor = history.base_offset
            self._metrics["consumer_overruns"] += 1
        for record in history.records:
            if record.offset < cursor:
                continue
            self._commit_cursor(
                domain_id=domain_id,
                topic=topic,
                consumer_id=consumer_id,
                next_offset=record.offset + 1,
            )
            self._consumer_offsets[consumer_key] = record.offset + 1
            self._metrics["dequeued_frames"] += 1
            return record.payload
        next_offset = max(cursor, history.next_offset)
        self._commit_cursor(
            domain_id=domain_id,
            topic=topic,
            consumer_id=consumer_id,
            next_offset=next_offset,
        )
        self._consumer_offsets[consumer_key] = next_offset
        self._metrics["empty_takes"] += 1
        return None

    def _commit_cursor(
        self, *, domain_id: int, topic: str, consumer_id: str, next_offset: int
    ) -> None:
        if self._durable_store is None:
            return
        try:
            self._durable_store.set_cursor(
                domain_id=domain_id,
                topic=topic,
                consumer_id=consumer_id,
                next_offset=next_offset,
                now_unix_ms=self._now_unix_ms(),
            )
        except FramePersistenceError:
            self._metrics["durable_persistence_failures"] += 1
            raise
        self._metrics["durable_cursor_commits"] += 1

    def close(self) -> None:
        if self._durable_store is not None:
            try:
                if self._durable_writer_id is not None:
                    try:
                        self._durable_store.release_writer_lease(
                            now_unix_ms=self._now_unix_ms()
                        )
                    except FramePersistenceUnavailableError:
                        # A lost database cannot acknowledge lease release. The
                        # persisted expiry and fence token still fail closed.
                        self._metrics["durable_persistence_failures"] += 1
            finally:
                self._durable_store.close()
                self._durable_store = None

    def renew_writer_lease(self) -> int:
        if self._durable_store is None or self._durable_writer_id is None:
            raise FramePersistenceError("durable writer lease is not configured")
        try:
            expires = self._durable_store.renew_writer_lease(
                now_unix_ms=self._now_unix_ms()
            )
        except FramePersistenceError:
            self._metrics["durable_writer_lease_failures"] += 1
            raise
        self._metrics["durable_writer_lease_renewals"] += 1
        return expires

    def _now_unix_ms(self) -> int:
        return max(0, int(self._wall_clock() * 1000.0))

    def snapshot(self) -> dict[str, Any]:
        retained_frames = sum(len(history.records) for history in self._topics.values())
        application_outcome_key_count = sum(
            len(history.application_outcome_keys)
            for history in self._topics.values()
        )
        return {
            "schema_version": "fleetrmw.quic_gateway_state.v1",
            **self._metrics,
            "topic_count": len(self._topics),
            "consumer_count": len(self._consumer_offsets),
            "retained_frames": retained_frames,
            "application_outcome_key_count": application_outcome_key_count,
            "max_frames_per_topic": self.max_frames_per_topic,
            "max_frame_bytes": self.max_frame_bytes,
            "dedup_capacity_per_topic": self.dedup_capacity_per_topic,
            "max_batch_frames": self.max_batch_frames,
            "durable_state_enabled": self._durable_store is not None,
            "durable_state": (
                self._durable_store.snapshot()
                if self._durable_store is not None
                else None
            ),
            "admission_policy_enabled": self.admission_policy is not None,
            "admission": (
                self.admission_policy.snapshot()
                if self.admission_policy is not None
                else None
            ),
        }

    def handle_request(
        self, method: str, raw_path: str, body: bytes = b""
    ) -> GatewayResponse:
        self._metrics["requests_total"] += 1
        parsed = urlsplit(raw_path)
        query = parse_qs(parsed.query, keep_blank_values=False)
        if parsed.path == HEALTH_API_PATH and method == "GET":
            return self._json_response(200, {"status": "ok"})
        if parsed.path == METRICS_API_PATH and method == "GET":
            return self._json_response(200, self.snapshot())
        if parsed.path == OBSERVATION_API_PATH and method == "POST":
            self._metrics["observation_requests"] += 1
            if self.admission_policy is None:
                return self._json_response(409, {"error": "admission_policy_disabled"})
            try:
                document = json.loads(body.decode("utf-8"))
                if not isinstance(document, dict):
                    raise ValueError("observation must be an object")
                if document.get("schema_version") != OBSERVATION_SCHEMA_VERSION:
                    raise ValueError("unsupported observation schema")
                domain_id = document.get("domain_id")
                topic = document.get("topic")
                publisher_id = document.get("publisher_id")
                numeric_keys = (
                    "qoe_debt",
                    "measured_loss",
                    "measured_rtt_ms",
                    "measured_jitter_ms",
                )
                if not isinstance(domain_id, int) or isinstance(domain_id, bool):
                    raise ValueError("observation domain_id must be an integer")
                if not isinstance(topic, str) or not isinstance(publisher_id, str):
                    raise ValueError("observation topic/publisher must be strings")
                if any(
                    not isinstance(document.get(key), (int, float))
                    or isinstance(document.get(key), bool)
                    for key in numeric_keys
                ):
                    raise ValueError("observation metrics must be numeric")
                self.admission_policy.update_observation(
                    domain_id=domain_id,
                    topic=topic,
                    publisher_id=publisher_id,
                    qoe_debt=float(document["qoe_debt"]),
                    measured_loss=float(document["measured_loss"]),
                    measured_rtt_ms=float(document["measured_rtt_ms"]),
                    measured_jitter_ms=float(document["measured_jitter_ms"]),
                    source="external_api",
                )
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                return self._json_response(400, {"error": str(exc)})
            return self._json_response(200, {"accepted": True})
        if parsed.path == APPLICATION_OUTCOME_API_PATH and method == "POST":
            self._metrics["application_outcome_requests"] += 1
            if (
                self.admission_policy is None
                or not self.admission_policy.application_outcome_qoe_debt_enabled
            ):
                return self._json_response(
                    409, {"error": "application_outcome_qoe_debt_disabled"}
                )
            try:
                document = json.loads(body.decode("utf-8"))
                if not isinstance(document, dict):
                    raise FrameValidationError(
                        "application outcome must be an object"
                    )
                outcome = self.record_application_outcome(document)
            except (
                FrameValidationError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                self._metrics["invalid_application_outcomes"] += 1
                return self._json_response(400, {"error": str(exc)})
            except FramePersistenceError as exc:
                return self._json_response(503, {"error": str(exc)})
            if outcome is None:
                return self._json_response(
                    404, {"error": "application_outcome_frame_not_found"}
                )
            return self._json_response(200, outcome)
        if parsed.path == GATEWAY_BATCH_API_PATH and method == "POST":
            self._metrics["batch_requests"] += 1
            try:
                document = json.loads(body.decode("utf-8"))
                if not isinstance(document, dict):
                    raise FrameValidationError("frame batch must be an object")
                if document.get("schema_version") != FRAME_BATCH_SCHEMA_VERSION:
                    raise FrameValidationError("unsupported frame batch schema")
                encoded_frames = document.get("frames")
                if not isinstance(encoded_frames, list):
                    raise FrameValidationError("frame batch frames must be a list")
                payloads = []
                for encoded in encoded_frames:
                    if not isinstance(encoded, str):
                        raise FrameValidationError("batch frame must be hex text")
                    try:
                        payloads.append(bytes.fromhex(encoded))
                    except ValueError as exc:
                        raise FrameValidationError("batch frame contains invalid hex") from exc
                self._metrics["batch_frames"] += len(payloads)
                results = self.publish_batch(payloads)
            except FramePersistenceError as exc:
                return self._json_response(503, {"error": str(exc)})
            except (FrameValidationError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                return self._json_response(400, {"error": str(exc)})
            return self._json_response(200, {"results": results})
        if parsed.path != GATEWAY_API_PATH:
            return self._json_response(404, {"error": "unknown_path"})
        if method == "POST":
            self._metrics["post_requests"] += 1
            expected_domain = self._query_domain(query, required=False)
            expected_topic = self._query_one(query, "topic", required=False)
            try:
                result = self.publish(
                    body,
                    expected_domain_id=expected_domain,
                    expected_topic=expected_topic,
                )
            except FrameValidationError as exc:
                self._metrics["invalid_frames"] += 1
                return self._json_response(400, {"error": str(exc)})
            except FrameAdmissionError as exc:
                return self._json_response(
                    exc.status,
                    {
                        "error": exc.reason,
                        "reason": exc.reason_code,
                        "traffic_class": exc.traffic_class,
                    },
                )
            except FramePersistenceError as exc:
                return self._json_response(503, {"error": str(exc)})
            return self._json_response(
                200,
                {
                    "accepted": result.accepted,
                    "duplicate": result.duplicate,
                    "offset": result.offset,
                },
            )
        if method == "GET":
            self._metrics["get_requests"] += 1
            try:
                domain_id = self._query_domain(query, required=True)
                topic = self._query_one(query, "topic", required=True)
                consumer_id = self._query_one(query, "consumer_id", required=True)
            except FrameValidationError as exc:
                return self._json_response(400, {"error": str(exc)})
            assert domain_id is not None and topic is not None and consumer_id is not None
            try:
                payload = self.take(
                    domain_id=domain_id, topic=topic, consumer_id=consumer_id
                )
            except FramePersistenceError as exc:
                return self._json_response(503, {"error": str(exc)})
            if payload is None:
                return GatewayResponse(status=204, body=b"", content_type="")
            return GatewayResponse(
                status=200,
                body=payload,
                content_type="application/vnd.fleetrmw.frame",
            )
        return self._json_response(405, {"error": "method_not_allowed"})

    @staticmethod
    def _query_one(
        query: dict[str, list[str]], key: str, *, required: bool
    ) -> str | None:
        values = query.get(key, [])
        if not values:
            if required:
                raise FrameValidationError(f"missing query parameter: {key}")
            return None
        if len(values) != 1 or not values[0]:
            raise FrameValidationError(f"query parameter must have one value: {key}")
        return values[0]

    @classmethod
    def _query_domain(
        cls, query: dict[str, list[str]], *, required: bool
    ) -> int | None:
        value = cls._query_one(query, "domain_id", required=required)
        if value is None:
            return None
        try:
            domain_id = int(value)
        except ValueError as exc:
            raise FrameValidationError("domain_id query parameter is not an integer") from exc
        if domain_id < 0:
            raise FrameValidationError("domain_id query parameter must be non-negative")
        return domain_id

    @staticmethod
    def _json_response(status: int, document: dict[str, Any]) -> GatewayResponse:
        return GatewayResponse(
            status=status,
            body=json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),
        )
