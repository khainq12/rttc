"""Fresh, fair, same-harness LAN N=16 comparison: FleetRMW / Fast DDS /
CycloneDDS / Zenoh.

Run AFTER the two benchmark-harness bugs were fixed (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "HARNESS IDENTITY FIX VALIDATED" and
"RED -> MINIMAL FIX -> GREEN: idle-timeout shutdown"):

  - commit a6dffd1: receiver no longer closes its socket while peers are
    still mid-stream sending to it (drain_deadline computed from every
    peer's schedule, not just this endpoint's own).
  - commit 1544008: FLEETQOX_RMW_ROBOT_ID wired into launch_endpoints(),
    fixing a publisher_id/robot_id collision across robots that had been
    misclassifying legitimate messages as duplicates.

Historical LAN N=16 numbers measured before both fixes are NOT valid for
ranking and must not be mixed with this pass's results -- see the
"OLD RESULTS STATUS" section this script's report feeds into.

This script performs NO optimization, NO parameter tuning, and NO
production RMW code changes -- it only orchestrates existing
run_lan_probe() calls and computes descriptive metrics from their output.

Discovery-mode-per-middleware reuses the SAME convention already
established (and justified) in the earlier "Bang IV/V" comparison work,
decided well before this pass and NOT tuned based on today's results:
  - FleetRMW: default (its own static mode)
  - Fast DDS: discovery_server (its own out-of-the-box multicast
    discovery was previously found to degrade badly at this endpoint
    count; a technically-necessary launch fix, not a performance tune)
  - CycloneDDS: static_peers (same reasoning; previously found to work
    around a real ParticipantIndex bug under default multicast)
  - Zenoh: default (already static via its own router process)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    endpoint_list,
    run_lan_probe,
)

MIDDLEWARE: list[tuple[str, str, str]] = [
    ("FleetRMW", "rmw_fleetqox_cpp", "default"),
    ("Fast DDS", "rmw_fastrtps_cpp", "discovery_server"),
    ("CycloneDDS", "rmw_cyclonedds_cpp", "static_peers"),
    ("Zenoh", "rmw_zenoh_cpp", "default"),
]
SEEDS = [7, 13, 29]
FLOW_CLASSES = ["control", "state", "perception", "coordination", "debug", "human_qoe"]


def load_flow_class_map(trace_path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with trace_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            mapping[row["event_id"]] = row["flow_class"]
    return mapping


def per_flow_breakdown(
    trace_path: Path, endpoint_results: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    flow_map = load_flow_class_map(trace_path)
    intended = {fc: 0 for fc in FLOW_CLASSES}
    for flow_class in flow_map.values():
        intended[flow_class] = intended.get(flow_class, 0) + 1
    delivered = {fc: 0 for fc in FLOW_CLASSES}
    for result in endpoint_results.values():
        if not result:
            continue
        for msg in result.get("received", []):
            flow_class = flow_map.get(msg["event_id"])
            if flow_class is not None:
                delivered[flow_class] = delivered.get(flow_class, 0) + 1
    breakdown: dict[str, dict[str, Any]] = {}
    for fc in FLOW_CLASSES:
        i = intended.get(fc, 0)
        d = delivered.get(fc, 0)
        breakdown[fc] = {
            "intended": i,
            "delivered": d,
            "lost": i - d,
            "delivery_pct": (100.0 * d / i) if i else None,
        }
    return breakdown


def total_intended_delivered(
    trace_path: Path, endpoint_results: dict[str, Any]
) -> tuple[int, int]:
    flow_map = load_flow_class_map(trace_path)
    intended = len(flow_map)
    delivered = 0
    for result in endpoint_results.values():
        if not result:
            continue
        delivered += len(result.get("received", []))
    return intended, delivered


def fresh_success_pct(endpoints: list[str], endpoint_results: dict[str, Any]) -> float:
    # "fresh" here maps onto the harness's own freshness guarantee: each
    # run_lan_probe() call uses a brand-new output_dir (never reused
    # across (middleware, seed) pairs in this script), and run_lan_probe()
    # itself rmtree()s any stale container_results before launching -- so
    # every non-None endpoint_results[endpoint] entry was necessarily
    # produced by THIS run, not a leftover. "success" = that endpoint's
    # result file exists at all (endpoint_results[endpoint] is not None).
    if not endpoints:
        return 0.0
    ok = sum(1 for ep in endpoints if endpoint_results.get(ep) is not None)
    return 100.0 * ok / len(endpoints)


def run_one(
    name: str,
    rmw_impl: str,
    discovery_mode: str,
    num_robots: int,
    seed: int,
    output_root: Path,
    image: str,
    seconds: int = 3,
    policy: str = "fifo",
) -> dict[str, Any]:
    output_dir = (output_root / f"{rmw_impl}_{discovery_mode}_n{num_robots}_seed{seed}").resolve()
    result = run_lan_probe(
        image=image,
        output_dir=output_dir,
        num_robots=num_robots,
        policy=policy,
        seconds=seconds,
        seed=seed,
        rmw_implementation=rmw_impl,
        discovery_mode=discovery_mode,
    )
    endpoints = endpoint_list(num_robots)
    trace_path = ROOT / result["trace"]
    intended, delivered = total_intended_delivered(trace_path, result["endpoint_results"])
    lost = intended - delivered
    delivery_pct = (100.0 * delivered / intended) if intended else None
    fresh_pct = fresh_success_pct(endpoints, result["endpoint_results"])
    jitter_stats = result["jitter_stale_repair_stats"]
    latency = result["latency_stats_ms"] or {}
    per_flow = per_flow_breakdown(trace_path, result["endpoint_results"])
    valid = result["status"] == "ok" and result["endpoint_results_complete"] and intended > 0
    invalid_reason = None
    if not valid:
        if result["status"] != "ok":
            invalid_reason = f"status={result['status']} error={result['error']!r}"
        elif not result["endpoint_results_complete"]:
            missing = [ep for ep in endpoints if result["endpoint_results"].get(ep) is None]
            invalid_reason = f"endpoint_results incomplete, missing={missing}"
        elif intended == 0:
            invalid_reason = "intended==0 (trace generation problem)"
    return {
        "middleware": name,
        "rmw_implementation": rmw_impl,
        "discovery_mode": discovery_mode,
        "num_robots": num_robots,
        "seed": seed,
        "valid": valid,
        "invalid_reason": invalid_reason,
        "status": result["status"],
        "error": result["error"],
        "endpoint_results_complete": result["endpoint_results_complete"],
        "intended": intended,
        "delivered": delivered,
        "lost": lost,
        "delivery_pct": delivery_pct,
        "fresh_success_pct": fresh_pct,
        "stale_pct": (
            jitter_stats["stale_ratio"] * 100.0
            if jitter_stats.get("stale_ratio") is not None
            else None
        ),
        "jitter_ms": jitter_stats.get("jitter_ms"),
        "repair_amp": jitter_stats.get("repair_amp"),
        "repair_amp_available": jitter_stats.get("repair_amp_available"),
        "p50_ms": latency.get("p50_ms"),
        "p95_ms": latency.get("p95_ms"),
        "p99_ms": latency.get("p99_ms"),
        "latency_n": latency.get("n"),
        "cpu_pct_mean": result.get("cpu_pct_mean"),
        "rss_mb_mean": result.get("rss_mb_mean"),
        "graph_join_failures": result.get("graph_join_failures"),
        "per_flow": per_flow,
        "raw_output_dir": str(output_dir.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "results_rmw_socket" / "lan_n16_fresh_baseline",
    )
    parser.add_argument("--sanity-only", action="store_true")
    parser.add_argument("--main-only", action="store_true")
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    args.output_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, list[dict[str, Any]]] = {"sanity": [], "main": []}

    if not args.main_only:
        for name, rmw_impl, discovery_mode in MIDDLEWARE:
            print(f"=== SANITY N=2 {name} ===", flush=True)
            r = run_one(
                name, rmw_impl, discovery_mode, 2, 13,
                args.output_root / "sanity", args.image,
            )
            report["sanity"].append(r)
            print(json.dumps({
                k: r[k] for k in (
                    "middleware", "valid", "status", "intended", "delivered",
                    "delivery_pct", "endpoint_results_complete",
                )
            }), flush=True)

    if not args.sanity_only:
        # Counterbalanced order: rotate which middleware runs first for
        # each seed, so machine-warming/order effects don't systematically
        # favor one middleware over the whole experiment.
        for seed_idx, seed in enumerate(SEEDS):
            shift = seed_idx % len(MIDDLEWARE)
            order = MIDDLEWARE[shift:] + MIDDLEWARE[:shift]
            for name, rmw_impl, discovery_mode in order:
                print(f"=== N=16 {name} seed={seed} ===", flush=True)
                r = run_one(
                    name, rmw_impl, discovery_mode, 16, seed,
                    args.output_root / "main", args.image,
                )
                report["main"].append(r)
                print(json.dumps({
                    k: r[k] for k in (
                        "middleware", "seed", "valid", "status", "intended",
                        "delivered", "delivery_pct", "endpoint_results_complete",
                    )
                }), flush=True)

    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
