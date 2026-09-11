"""Scale/loss escalation diagnostic for FleetRMW's fragment NACK/repair path.

Runs a fixed ladder starting from an established-good baseline (1 robot,
32 KiB payload, 0% network loss -- the only configuration this project has
actually exercised before, per
results_rmw_socket/docker_loss_resilient_large_sample_fragment_5run_summary.json)
up through increasing loss, then increasing robot count, STOPPING at the
first step that misses. The point is to find the exact boundary where
delivery starts failing rather than burying it in a wide sweep.

For every step, prints a causal-chain diagnosis using counters the relay
probe binary already computes (generic_serialized_relay_probe.cpp's
fleetqox_transport_metrics, surfaced by run_ros2_relay_rmw_netem_probe.py
as relay_fragment_repair_metrics):

    fragment lost? -> NACK sent? -> NACK received by sender? ->
    repair (retransmit) sent? -> arrived before TTL? -> delivered?

If even the 1-robot/0%-loss baseline misses, that points at a
fragmentation/reassembly code bug (no network loss to blame). If only a
higher robot count or loss rate misses, that points at contention/
bandwidth/repair-amplification under load, not a code bug.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_large_scale_rmw_comparison import parse_csv_int  # noqa: E402
from scripts.run_rmw_docker_loss_resilient_fragment_campaign import (  # noqa: E402
    run_campaign,
)
from scripts.run_ros2_relay_rmw_netem_probe import (  # noqa: E402
    DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS,
    DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    DEFAULT_FLEETQOX_RELIABLE_MAX_RETRANSMISSIONS,
    DEFAULT_IMAGE,
)


SCHEMA_VERSION = "fleetrmw.fragment_repair_escalation.v1"

# netem_loss_scale is a multiplier on the profile's base loss:
# loss_percent = min(100, profile.primary_loss * 100 * loss_scale). The
# "roaming" profile's primary_loss is 0.28 (28%) -- see
# scripts/run_rmw_docker_multi_robot_live_telemetry_plan_probe.py's
# ROUTER_PATH_TELEMETRY_PROFILES. Solve for round loss percentages against
# that profile rather than guessing a scale value.
_ROAMING_PRIMARY_LOSS = 0.28


def _loss_scale_for_percent(percent: float) -> float:
    return percent / (_ROAMING_PRIMARY_LOSS * 100.0)


def _min_timeout_s_for_robot_count(robot_count: int) -> float:
    """Floor for the probe's overall wait timeout at a given robot count.

    DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS (60s) is the fragment-repair
    mechanism's OWN internal give-up point per message -- longer than the
    25s --timeout-s default this script (and the campaign it wraps)
    otherwise inherits. If the harness's overall wait window is shorter
    than that TTL, it can declare a run "failed" by giving up before the
    mechanism's own retry logic would have finished, misclassifying "test
    harness didn't wait long enough" as a delivery miss. Scale a floor off
    the real TTL plus margin per robot for repair-queue contention instead
    of trusting a flat default at every ladder step.
    """
    ttl_s = DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS / 1000.0
    return ttl_s + 10.0 + 2.0 * robot_count


DEFAULT_LADDER: list[tuple[str, int, float]] = [
    ("1 robot, 0% loss (established-good baseline)", 1, 0.0),
    ("1 robot, ~5% loss", 1, _loss_scale_for_percent(5.0)),
    ("4 robots, ~5% loss", 4, _loss_scale_for_percent(5.0)),
    ("8 robots, ~5% loss", 8, _loss_scale_for_percent(5.0)),
    ("16 robots, ~5% loss", 16, _loss_scale_for_percent(5.0)),
    ("32 robots, ~5% loss", 32, _loss_scale_for_percent(5.0)),
]


def _leg_chain(
    *,
    receiver_metrics: dict[str, Any],
    source_metrics: dict[str, Any],
) -> dict[str, Any]:
    """One directed leg's causal chain: receiver detects loss -> source
    receives the NACK -> source retransmits -> receiver's own reassembly
    completes before its TTL. `receiver_metrics`/`source_metrics` are each
    hop's OWN fleetqox_transport_metrics (a hop is a "receiver" for
    traffic it accepts from upstream, a "source" for traffic it forwards
    downstream -- the same physical process is a source on one leg and a
    receiver on the other).
    """
    return {
        "loss_detected": int(receiver_metrics.get("fragment_nacks_sent", 0)),
        "nack_received_by_source": int(source_metrics.get("fragment_nacks_received", 0)),
        "repair_sent_by_source": int(
            source_metrics.get("fragments_selectively_retransmitted", 0)
        ),
        "receiver_assembly_ttl_expirations": int(
            receiver_metrics.get("fragment_assembly_ttl_expirations", 0)
        ),
        "receiver_nack_budget_exhausted": int(
            receiver_metrics.get("fragment_nack_exhausted_assemblies", 0)
        ),
    }


def _leg_reason(leg: dict[str, Any]) -> str | None:
    """None means this leg's chain looks clean (no TTL expiration to
    explain); the caller only calls this for a leg whose TTL expired.
    """
    if leg["receiver_assembly_ttl_expirations"] == 0:
        return None
    if leg["receiver_nack_budget_exhausted"] > 0:
        return "nack_budget_exhausted"
    if leg["loss_detected"] == 0:
        # TTL expired but the receiver's own detector never logged loss --
        # possible if the whole datagram (not a mid-fragment loss) never
        # arrived, so there was nothing to fragment-NACK about.
        return "ttl_expired_without_detected_fragment_loss"
    if leg["nack_received_by_source"] == 0:
        return "nack_sent_but_never_reached_source"
    if leg["repair_sent_by_source"] == 0:
        return "source_received_nack_but_never_sent_repair"
    return "repair_sent_but_ttl_expired_before_arrival"


def diagnose_run(result: dict[str, Any]) -> dict[str, Any]:
    """Build the full 2-leg fragment->NACK->repair->delivery causal chain:
    publisher -> relay (the lossy netem leg) -> subscriber. Requires
    publisher_fragment_repair_metrics / relay_fragment_repair_metrics /
    subscriber_fragment_repair_metrics on `result` (all three hops'
    fleetqox_transport_metrics, surfaced by run_ros2_relay_rmw_netem_probe.py).
    """

    pub_m = result.get("publisher_fragment_repair_metrics") or {}
    relay_m = result.get("relay_fragment_repair_metrics") or {}
    sub_m = result.get("subscriber_fragment_repair_metrics") or {}
    control_ratio = result.get("control_delivery_ratio")
    state_ratio = result.get("state_delivery_ratio")
    passed = bool(
        result.get("status") == "ok"
        and control_ratio == 1.0
        and state_ratio == 1.0
    )

    # leg1: publisher -> relay (the netem-lossy "primary_wifi" path).
    # relay is the receiver; publisher is the repair source.
    leg1 = _leg_chain(receiver_metrics=relay_m, source_metrics=pub_m)
    # leg2: relay -> subscriber (not netem-lossy in the "roaming" profile,
    # but tracked regardless in case contention alone causes loss here).
    # subscriber is the receiver; relay is the repair source.
    leg2 = _leg_chain(receiver_metrics=sub_m, source_metrics=relay_m)

    oversize_drops = int(relay_m.get("fragment_assembly_oversize_drops", 0)) + int(
        sub_m.get("fragment_assembly_oversize_drops", 0)
    )
    metadata_mismatch_drops = int(
        relay_m.get("fragment_assembly_metadata_mismatch_drops", 0)
    ) + int(sub_m.get("fragment_assembly_metadata_mismatch_drops", 0))

    reason = "no_miss"
    if not passed:
        if oversize_drops > 0 or metadata_mismatch_drops > 0:
            reason = "reassembly_failure"
        else:
            leg1_reason = _leg_reason(leg1)
            leg2_reason = _leg_reason(leg2)
            if leg1_reason is not None:
                reason = f"leg1_publisher_to_relay:{leg1_reason}"
            elif leg2_reason is not None:
                reason = f"leg2_relay_to_subscriber:{leg2_reason}"
            elif leg1["loss_detected"] == 0 and leg2["loss_detected"] == 0:
                reason = "miss_without_detected_fragment_loss"
            else:
                reason = "unclassified_miss"

    return {
        "passed": passed,
        "control_delivery_ratio": control_ratio,
        "state_delivery_ratio": state_ratio,
        "leg1_publisher_to_relay": leg1,
        "leg2_relay_to_subscriber": leg2,
        "reason": reason,
    }


def run_escalation(
    *,
    root: Path,
    image: str,
    profile: str,
    ladder: list[tuple[str, int, float]],
    seeds: list[int],
    samples: int,
    payload_bytes: int,
    publish_interval_ms: int,
    timeout_s: float,
    fragment_chunk_bytes: int,
    max_retransmissions: int,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    stopped_at: str | None = None
    for label, robot_count, loss_scale in ladder:
        step_timeout_s = max(timeout_s, _min_timeout_s_for_robot_count(robot_count))
        print(
            f"=== step: {label} (timeout_s={step_timeout_s:.0f}) ===",
            file=sys.stderr,
            flush=True,
        )
        campaign = run_campaign(
            root=root,
            image=image,
            profile=profile,
            netem_loss_scale=loss_scale,
            seeds=seeds,
            samples=samples,
            robot_count=robot_count,
            payload_bytes=payload_bytes,
            publish_interval_ms=publish_interval_ms,
            timeout_s=step_timeout_s,
            fragment_chunk_bytes=fragment_chunk_bytes,
            max_retransmissions=max_retransmissions,
        )
        diagnoses = [
            {"seed": row["seed"], **diagnose_run(row.get("result") or {})}
            for row in campaign["runs"]
        ]
        step_passed = bool(diagnoses) and all(d["passed"] for d in diagnoses)
        step = {
            "label": label,
            "robot_count": robot_count,
            "netem_loss_scale": loss_scale,
            "loss_percent": min(100.0, _ROAMING_PRIMARY_LOSS * 100.0 * loss_scale),
            "timeout_s": step_timeout_s,
            "passed": step_passed,
            "diagnoses": diagnoses,
            # Full per-seed result (status, any error text, every raw
            # counter) so a miss with an otherwise-empty diagnosis (all
            # counters zero -- no fragment loss/NACK/repair activity at
            # all) can be told apart from a harness/infra-level failure
            # (docker setup race, container didn't come up in time, etc.)
            # instead of being misread as a fragmentation code bug.
            "raw_runs": campaign["runs"],
        }
        steps.append(step)
        for d in diagnoses:
            l1, l2 = d["leg1_publisher_to_relay"], d["leg2_relay_to_subscriber"]
            print(
                f"  seed={d['seed']} passed={d['passed']} reason={d['reason']} "
                f"control={d['control_delivery_ratio']} state={d['state_delivery_ratio']} "
                f"leg1(pub->relay) loss_detected={l1['loss_detected']} "
                f"nack_received_by_pub={l1['nack_received_by_source']} "
                f"repair_sent_by_pub={l1['repair_sent_by_source']} "
                f"relay_ttl_exp={l1['receiver_assembly_ttl_expirations']} | "
                f"leg2(relay->sub) loss_detected={l2['loss_detected']} "
                f"nack_received_by_relay={l2['nack_received_by_source']} "
                f"repair_sent_by_relay={l2['repair_sent_by_source']} "
                f"sub_ttl_exp={l2['receiver_assembly_ttl_expirations']}",
                file=sys.stderr,
                flush=True,
            )
        print(
            f"  step result: {'PASS' if step_passed else 'MISS'}",
            file=sys.stderr,
            flush=True,
        )
        if not step_passed:
            stopped_at = label
            break
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "found_miss_boundary" if stopped_at else "all_steps_passed",
        "stopped_at": stopped_at,
        "profile": profile,
        "payload_bytes": payload_bytes,
        "seeds": seeds,
        "samples": samples,
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--profile", default="roaming")
    parser.add_argument("--seeds", default="7,13,29")
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--payload-bytes", type=int, default=32768)
    parser.add_argument("--publish-interval-ms", type=int, default=2000)
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=25.0,
        help=(
            "Floor for the probe's overall wait timeout, per step raised to "
            "at least _min_timeout_s_for_robot_count(robot_count) (based on "
            "the 60s fragment-assembly TTL plus per-robot margin) so a "
            "slow-but-successful repair at higher robot counts isn't "
            "misread as a miss."
        ),
    )
    parser.add_argument(
        "--fragment-chunk-bytes",
        type=int,
        default=DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    )
    parser.add_argument(
        "--max-retransmissions",
        type=int,
        default=DEFAULT_FLEETQOX_RELIABLE_MAX_RETRANSMISSIONS,
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path(
            "results_rmw_socket/fragment_repair_escalation_summary.json"
        ),
    )
    args = parser.parse_args()
    summary = run_escalation(
        root=ROOT,
        image=args.image,
        profile=args.profile,
        ladder=DEFAULT_LADDER,
        seeds=parse_csv_int(args.seeds, minimum=0),
        samples=max(args.samples, 1),
        payload_bytes=max(args.payload_bytes, 1),
        publish_interval_ms=max(args.publish_interval_ms, 1),
        timeout_s=max(args.timeout_s, 1.0),
        fragment_chunk_bytes=max(min(args.fragment_chunk_bytes, 60000), 1),
        max_retransmissions=max(min(args.max_retransmissions, 100), 1),
    )
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"status={summary['status']} stopped_at={summary['stopped_at']!r}")
    return 0 if summary["status"] == "all_steps_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
