"""Smoke test for the new ns-3 Wi-Fi MAC/PHY instrumentation added to
external/ns3/fleetqox_trace_replay_tap.cc (see docs/AUDIT_ACCEPTANCE_TRACKING.md,
"N=8 REMAINING WI-FI LOSS AFTER DIRECTED-REPLY"). N=2, short duration,
directed_reply=True (the frozen config this investigation uses) --
confirms the binary builds/links/runs (all the new TraceConnectWithoutContext
calls resolve at runtime -- an invalid trace source name is an ns-3
FATAL_ERROR, not a silent no-op) and produces the new FLEETQOX_WIFI_STATS
fields + at least one FLEETQOX_QUEUE_BACKLOG line, before committing to a
full N=8 run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    run_coordination_probe,
)


def main() -> int:
    output_dir = ROOT / "results_rmw_socket" / "wifi_mac_phy_instrumentation_smoke_test" / "fleetrmw_n2_seed7"
    result = run_coordination_probe(
        image=DEFAULT_IMAGE,
        output_dir=output_dir,
        num_robots=2,
        seed=7,
        num_crossings=5,
        scenario_timeout_s=40.0,
        discovery_timeout_s=5.0,
        rmw_implementation="rmw_fleetqox_cpp",
        discovery_mode="default",
        directed_reply=True,
    )
    print(json.dumps({"status": result["status"], "error": result["error"]}), flush=True)
    if result["status"] != "ok":
        print("SMOKE TEST FAILED: run did not complete ok", flush=True)
        return 1

    log_text = result.get("ns3_log", "") or ""
    if not log_text:
        print("SMOKE TEST WARNING: result['ns3_log'] is empty", flush=True)
    (output_dir.parent / "ns3_full.log").write_text(log_text, encoding="utf-8")
    debug_lines = [l for l in log_text.splitlines() if "FLEETQOX_MAC_DEBUG_PAYLOAD" in l]
    print(f"debug payload dump lines: {len(debug_lines)}", flush=True)
    for l in debug_lines[:10]:
        print(l[:400], flush=True)

    has_wifi_stats = "FLEETQOX_WIFI_STATS" in log_text
    has_new_fields = "dropped_mpdu_failed_enqueue" in log_text and "backoff_value_max" in log_text
    has_queue_backlog = "FLEETQOX_QUEUE_BACKLOG" in log_text
    has_who_field = '"who":"' in log_text

    print(f"has_wifi_stats={has_wifi_stats}", flush=True)
    print(f"has_new_fields={has_new_fields}", flush=True)
    print(f"has_queue_backlog={has_queue_backlog}", flush=True)
    print(f"has_who_field={has_who_field}", flush=True)

    last_stats_line = ""
    for line in log_text.splitlines():
        if line.startswith("FLEETQOX_WIFI_STATS"):
            last_stats_line = line
    print("last FLEETQOX_WIFI_STATS line:", last_stats_line, flush=True)

    ok = has_wifi_stats and has_new_fields and has_queue_backlog and has_who_field
    print(f"SMOKE TEST: {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
