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


def diagnose_run(result: dict[str, Any]) -> dict[str, Any]:
    """Build the fragment->NACK->repair->delivery causal chain for one run."""

    metrics = result.get("relay_fragment_repair_metrics") or {}
    control_ratio = result.get("control_delivery_ratio")
    state_ratio = result.get("state_delivery_ratio")
    passed = bool(
        result.get("status") == "ok"
        and control_ratio == 1.0
        and state_ratio == 1.0
    )

    nacks_sent = int(metrics.get("fragment_nacks_sent", 0))
    # fragment_nacks_received / fragments_selectively_retransmitted count
    # this relay hop acting as a REPAIR SOURCE for whatever is downstream
    # of it (subscriber) -- a completely different leg from nacks_sent
    # (this hop, as a receiver, requesting repair from whatever is
    # upstream of it -- publisher). Only the relay probe exposes these
    # metrics at all (publisher/subscriber don't), so there is no
    # visibility into whether the publisher actually received/responded
    # to a NACK the relay sent it -- don't use these two counters to
    # classify a miss on the publisher->relay leg, only report them.
    nacks_received = int(metrics.get("fragment_nacks_received", 0))
    retransmitted = int(metrics.get("fragments_selectively_retransmitted", 0))
    ttl_expirations = int(metrics.get("fragment_assembly_ttl_expirations", 0))
    nack_exhausted = int(metrics.get("fragment_nack_exhausted_assemblies", 0))
    oversize_drops = int(metrics.get("fragment_assembly_oversize_drops", 0))
    metadata_mismatch_drops = int(
        metrics.get("fragment_assembly_metadata_mismatch_drops", 0)
    )
    active_missing = int(metrics.get("fragment_active_missing_indexes", 0))
    fragment_loss_observed = nacks_sent > 0 or active_missing > 0

    reason = "no_miss"
    if not passed:
        if oversize_drops > 0 or metadata_mismatch_drops > 0:
            reason = "reassembly_failure"
        elif not fragment_loss_observed:
            # A miss happened but this hop's OWN loss detector (watching
            # what it receives from upstream) never engaged at all --
            # either the miss is on a leg this probe can't see (relay->
            # subscriber), or it isn't fragment-loss-related at all (e.g.
            # an application-layer bug).
            reason = "miss_without_detected_fragment_loss"
        elif nack_exhausted > 0:
            reason = "nack_budget_exhausted"
        elif nacks_sent > 0 and ttl_expirations > 0:
            # This hop detected loss and requested repair from upstream,
            # but its own fragment-assembly TTL expired before a complete
            # repair arrived. Can't attribute further (publisher-side
            # response isn't observable from here) without adding
            # equivalent metrics to the publisher/subscriber probes too.
            reason = "repair_requested_but_ttl_expired_before_completion"
        else:
            reason = "unclassified_miss"

    return {
        "passed": passed,
        "control_delivery_ratio": control_ratio,
        "state_delivery_ratio": state_ratio,
        "fragment_loss_observed": fragment_loss_observed,
        "fragment_nacks_sent": nacks_sent,
        "fragment_nacks_received": nacks_received,
        "fragments_selectively_retransmitted": retransmitted,
        "fragment_assembly_ttl_expirations": ttl_expirations,
        "fragment_nack_exhausted_assemblies": nack_exhausted,
        "fragment_assembly_oversize_drops": oversize_drops,
        "fragment_assembly_metadata_mismatch_drops": metadata_mismatch_drops,
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
            print(
                f"  seed={d['seed']} passed={d['passed']} reason={d['reason']} "
                f"control={d['control_delivery_ratio']} state={d['state_delivery_ratio']} "
                f"nacks_sent={d['fragment_nacks_sent']} nacks_received={d['fragment_nacks_received']} "
                f"retransmitted={d['fragments_selectively_retransmitted']} "
                f"ttl_expirations={d['fragment_assembly_ttl_expirations']}",
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
