"""Table VI N=8 seed=7 -- does reducing ACK/NACK redundant resends also
reduce FleetRMW's own DATA-frame retransmissions, or are the two
mechanisms independent? (See docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE
VI N=8 POST-RECVFROM DUPLICATE-DROP LOCALIZATION", whose "exactly one
next step" this implements.)

Measurement-only. Single controlled A/B (not A1/B/A2 -- this pass does
not test reversion, only the one comparison asked for):
  A = FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT unset (default 10)
  B = FLEETQOX_RMW_ACK_NACK_REDUNDANT_RESEND_COUNT=0

Reuses run_table6_n8_acknack_ab_experiment.py's run_one()/summarize()
verbatim (same tcpdump capture, same FLEETQOX_RMW_LOSS_FUNNEL_TRACE_
PROFILING=1, same N=8/seed=7/probe lifecycle) -- no changes to that
script, no source/retransmission/timeout/QoS/broadcast/ns-3/protocol
change of any kind.

Two DISTINCT retransmission counters are read back per endpoint (both
already existed in rmw_pubsub.cpp; only newly EXPOSED to
fleetqox_coordination_endpoint.py's fleetqox_stream_identity_diagnostics()
in this same change, mirroring what fleetqox_rmw_trace_endpoint.py
already exposed for Table IV/V):
  - nack_retransmissions: socket_transport().send_retransmission_frame()
    call count -- fires when an incoming ACK/NACK's
    missing_sequence_ranges names a sequence this sender still holds in
    g_retransmit_ledger. THIS is the transport-retry path actually
    active in this scenario (NACK-driven, not timeout-driven).
  - reliable_timeout_retransmissions: the SEPARATE periodic
    reliable_retransmit_loop() path, gated by
    FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS (default 0 = the loop returns
    immediately and never runs) -- read back to CONFIRM this stays 0
    in both A and B, not assumed from the env var default alone.

"Unique DATA frames" = total application-level publish() calls (each
sent_log entry is one NEW source_sequence assigned by the RMW layer --
an APPLICATION retry, e.g. the coordination logic re-publishing a
request after a reply timeout, shows up here as a genuinely NEW unique
frame, correctly distinguished from a TRANSPORT retry of the same
frame). "DATA send attempts" = unique DATA frames + both
retransmission-counter sums (every retransmission is, by definition,
one more attempt to send an already-assigned sequence number, not a
new one).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_table6_n8_acknack_ab_experiment import (  # noqa: E402
    ENDPOINTS,
    SEED,
    run_one,
    summarize,
)

RUNS = [("A", None), ("B", "0")]


def extended_summary(run: dict[str, Any]) -> dict[str, Any]:
    base = summarize(run)
    endpoint_results = run["endpoint_results"]

    unique_data_frames = 0
    nack_retransmissions_sum = 0
    reliable_timeout_retransmissions_sum = 0
    per_endpoint: dict[str, Any] = {}
    for ep in ENDPOINTS:
        d = endpoint_results.get(ep)
        if d is None:
            per_endpoint[ep] = None
            continue
        sent = [x for x in d["sent_log"] if x["type"] in ("request", "reply", "release")]
        diag = d.get("fleetqox_stream_identity_diagnostics", {})
        nack_rt = diag.get("nack_retransmissions", 0)
        timeout_rt = diag.get("reliable_timeout_retransmissions", 0)
        unique_data_frames += len(sent)
        nack_retransmissions_sum += nack_rt
        reliable_timeout_retransmissions_sum += timeout_rt
        per_endpoint[ep] = {
            "unique_data_frames_sent": len(sent),
            "nack_retransmissions": nack_rt,
            "reliable_timeout_retransmissions": timeout_rt,
        }

    data_retransmissions = nack_retransmissions_sum + reliable_timeout_retransmissions_sum
    data_send_attempts = unique_data_frames + data_retransmissions

    base["per_endpoint_retransmission_diagnostics"] = per_endpoint
    base["unique_data_frames"] = unique_data_frames
    base["nack_retransmissions_sum"] = nack_retransmissions_sum
    base["reliable_timeout_retransmissions_sum"] = reliable_timeout_retransmissions_sum
    base["data_retransmissions_total"] = data_retransmissions
    base["data_send_attempts_total"] = data_send_attempts
    base["retransmissions_per_unique_data"] = (
        round(data_retransmissions / unique_data_frames, 4) if unique_data_frames else None
    )
    return base


def main() -> int:
    output_root = ROOT / "results_rmw_socket" / "table6_n8_ack_nack_vs_data_retransmission"
    summaries = []
    for label, redundant_count_env in RUNS:
        run = run_one(label, redundant_count_env, output_root, seed=SEED)
        summary = extended_summary(run)
        summaries.append(summary)
        print(
            json.dumps({
                k: summary[k] for k in (
                    "label", "status", "unique_data_frames", "nack_retransmissions_sum",
                    "reliable_timeout_retransmissions_sum", "data_retransmissions_total",
                    "data_send_attempts_total", "retransmissions_per_unique_data",
                )
            }),
            flush=True,
        )

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
