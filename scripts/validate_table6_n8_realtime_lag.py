"""Validate the "N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY" pass's
realtime-lag finding (see docs/AUDIT_ACCEPTANCE_TRACKING.md). That pass
found ns-3's own Simulator::Now() reaching only ~60s of simulated time
across a 120s real run, but could not separate three explanations:
  A) that pass's OWN heavy per-packet instrumentation slowing ns-3 down
     enough to cause the lag itself,
  B) genuine FleetRMW/network event load independent of this program's
     own tracing,
  C) a more fundamental N=8 realtime-simulator CPU ceiling.

STEP 1 (isolate A): external/ns3/fleetqox_trace_replay_tap.cc's
--heavyTracing flag now defaults to FALSE -- with it off, the program's
trace connections and per-event work are IDENTICAL to what this
investigation used before that pass (see g_heavyTracing's own doc
comment). This script's default run (no extra_rmw_env, directed_reply
=True, N=8 seed=7, all other config frozen at this investigation's own
defaults) uses that default. Records ONLY wall_elapsed_s, sim_time_s,
sim_lag_s, self_cpu_s, self_rss_kb, and the pre-existing cheap
(unconditional, O(1)-per-packet) mac_tx/mac_rx counts+bytes -- nothing
per-packet-extraction-based.

STEP 2 (load A/B): reruns with FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT
=0 (the only already-existing, already-proven-safe knob this
investigation is allowed to touch) vs the production default (10,
achieved by simply not setting the env var), otherwise identical config,
to see whether reducing FleetRMW's own wire volume changes sim_lag_s.

Explicitly does NOT change: radio conditions, workload, coordination
timeout, QoS, retransmission code, forced-entry rules, directed-REPLY.
Does NOT run N=16. forced_entry/crossings are recorded for completeness
only -- per this task's own instruction, they are not used to judge
network performance from a run whose sim_lag_s exceeds the 10s validity
gate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# fleetqox_trace_replay_tap.cc's PrintWifiStats() leaves a trailing comma
# before phy_rx_drop_by_reason's closing "]" -- invalid JSON, pre-existing,
# unrelated to this script (see investigate_table6_n8_wifi_mac_phy_loss.py's
# same tolerance for the original discovery of this).
_TRAILING_COMMA_RE = re.compile(r",(\s*[\]}])")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    endpoint_list,
    run_coordination_probe,
)

SEED = 7
VALIDITY_GATE_S = 10.0


def parse_wifi_stats(ns3_log: str) -> list[dict[str, Any]]:
    out = []
    for line in ns3_log.splitlines():
        if not line.startswith("FLEETQOX_WIFI_STATS "):
            continue
        try:
            out.append(json.loads(_TRAILING_COMMA_RE.sub(r"\1", line[len("FLEETQOX_WIFI_STATS "):])))
        except json.JSONDecodeError:
            continue
    return out


def run_one(label: str, num_robots: int, redundant_count: str | None, scenario_timeout_s: float = 120.0) -> dict[str, Any]:
    endpoints = endpoint_list(num_robots)
    output_dir = ROOT / "results_rmw_socket" / "table6_n8_realtime_lag_validation" / f"n{num_robots}_seed{SEED}_{label}"
    extra_rmw_env: dict[str, str] = {}
    if redundant_count is not None:
        extra_rmw_env["FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT"] = redundant_count
    print(f"=== N={num_robots} seed={SEED} label={label} redundant_count={redundant_count} ===", flush=True)
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=num_robots,
        seed=SEED,
        scenario_timeout_s=scenario_timeout_s,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        extra_rmw_env=extra_rmw_env or None,
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        return {"label": label, "num_robots": num_robots, "status": result["status"], "error": result["error"]}

    ns3_log = result.get("ns3_log", "") or ""
    (output_dir.parent / f"ns3_{label}_n{num_robots}.log").write_text(ns3_log, encoding="utf-8")
    stats_lines = parse_wifi_stats(ns3_log)
    last = stats_lines[-1] if stats_lines else {}

    results_dir = output_dir / "container_results"
    task_completion_s = {}
    crossings_completed = {}
    forced_entry = {}
    for i, ep in enumerate(endpoints):
        try:
            d = json.loads((results_dir / f"result_{i}.json").read_text())
            task_completion_s[ep] = d["task_completion_s"]
            crossings_completed[ep] = d["num_crossings_completed"]
            forced_entry[ep] = any(c["forced_entry"] for c in d["crossings"])
        except FileNotFoundError:
            pass

    wall_elapsed_s = last.get("wall_elapsed_s")
    sim_time_s = last.get("sim_time_s")
    sim_lag_s = last.get("sim_lag_s")
    out = {
        "label": label,
        "num_robots": num_robots,
        "status": "ok",
        "redundant_count": redundant_count,
        "wall_elapsed_s": wall_elapsed_s,
        "sim_time_s": sim_time_s,
        "sim_lag_s": sim_lag_s,
        "self_cpu_s": last.get("self_cpu_s"),
        "self_rss_kb": last.get("self_rss_kb"),
        "heavy_tracing": last.get("heavy_tracing"),
        "mac_tx_total": last.get("mac_tx_total"),
        "mac_tx_bytes": last.get("mac_tx_bytes"),
        "mac_rx_total": last.get("mac_rx_total"),
        "mac_rx_bytes": last.get("mac_rx_bytes"),
        "valid": (sim_lag_s is not None and sim_lag_s <= VALIDITY_GATE_S),
        "task_completion_s_mean": (
            round(sum(task_completion_s.values()) / len(task_completion_s), 2) if task_completion_s else None
        ),
        "crossings_completed": crossings_completed,
        "any_forced_entry": any(forced_entry.values()) if forced_entry else None,
        "num_wifi_stats_lines": len(stats_lines),
    }
    print(json.dumps({k: v for k, v in out.items() if k not in ("crossings_completed",)}, indent=2), flush=True)
    return out


def main() -> int:
    all_results: list[dict[str, Any]] = []

    # STEP 1: N=8, default redundancy (10, i.e. env var unset), lightweight
    # (heavyTracing default false) -- isolates whether the PRIOR pass's own
    # instrumentation caused the lag.
    step1 = run_one("step1_default_redundancy10", num_robots=8, redundant_count=None)
    all_results.append(step1)

    if step1["status"] == "ok" and not step1["valid"]:
        print("\nSTEP 1: lag REMAINS with heavyTracing=false -- instrumentation is NOT the (sole) cause. Continuing to STEP 2.", flush=True)
    elif step1["status"] == "ok":
        print("\nSTEP 1: lag DISAPPEARED with heavyTracing=false -- prior instrumentation was the cause. Still running STEP 2 for completeness.", flush=True)

    # STEP 2: N=8, redundancy=0 vs default(10), everything else identical.
    step2_zero = run_one("step2_redundancy0", num_robots=8, redundant_count="0")
    all_results.append(step2_zero)

    out_path = ROOT / "results_rmw_socket" / "table6_n8_realtime_lag_validation" / "lag_validation_findings.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_results, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)

    print("\n=== SUMMARY ===", flush=True)
    for r in all_results:
        if r.get("status") != "ok" or r.get("wall_elapsed_s") is None:
            print(f"{r['label']}: FAILED/NO_STATS ({r.get('status')})", flush=True)
            continue
        print(
            f"{r['label']}: wall={r['wall_elapsed_s']:.2f}s sim={r['sim_time_s']:.2f}s "
            f"lag={r['sim_lag_s']:.2f}s valid={r['valid']} cpu={r['self_cpu_s']:.2f}s "
            f"rss={r['self_rss_kb']}kb mac_tx={r['mac_tx_total']} mac_tx_bytes={r['mac_tx_bytes']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
