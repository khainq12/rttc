import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fleetqox.sidecar_contract import validate_event
from fleetqox.trace import TRACE_SCHEMA_VERSION, generate_trace_events, write_simulator_csv


class TraceExportTest(unittest.TestCase):
    def test_trace_contains_packet_events(self) -> None:
        events = generate_trace_events(
            scenario="test",
            robots=3,
            seconds=1,
            seed=7,
            capacity_bytes_per_second=120_000,
            policies=["fleetqox_csds"],
        )

        self.assertTrue(events)
        first = events[0]
        self.assertEqual(first["schema_version"], TRACE_SCHEMA_VERSION)
        self.assertEqual(first["event_type"], "packet")
        self.assertIn(first["flow_class"], {"control", "state", "coordination", "perception", "human_qoe", "debug"})
        self.assertGreater(first["bytes"], 0)
        self.assertIn("wire_mode", first)
        self.assertIn("predicted_slack_ms", first)
        validate_event(first)

    def test_trace_can_include_non_sent_decisions(self) -> None:
        events = generate_trace_events(
            scenario="test",
            robots=5,
            seconds=1,
            seed=11,
            capacity_bytes_per_second=40_000,
            policies=["fifo"],
            include_non_sent=True,
        )

        event_types = {event["event_type"] for event in events}
        self.assertIn("packet", event_types)
        self.assertIn("decision", event_types)

    def test_trace_can_be_written_as_simulator_csv(self) -> None:
        events = generate_trace_events(
            scenario="test",
            robots=3,
            seconds=1,
            seed=7,
            capacity_bytes_per_second=120_000,
            policies=["fleetqox_csds"],
        )

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "trace.csv"
            count = write_simulator_csv(events, output)
            lines = output.read_text(encoding="utf-8").splitlines()

        self.assertGreater(count, 0)
        self.assertTrue(lines[0].startswith("event_id,timestamp_ms,policy"))
        self.assertEqual(count, len(lines) - 1)
        self.assertIn("wire_mode", lines[0])

    def test_predictive_trace_exposes_sidecar_decisions(self) -> None:
        events = generate_trace_events(
            scenario="test",
            robots=5,
            seconds=1,
            seed=7,
            capacity_bytes_per_second=40_000,
            policies=["fleetqox_predictive"],
            include_non_sent=True,
        )

        self.assertTrue(any(event["action"] == "send_compacted" for event in events))
        self.assertTrue(any(event["wire_mode"] == "semantic_delta" for event in events))
        self.assertTrue(all(event["schema_version"] == TRACE_SCHEMA_VERSION for event in events))

    def test_predictive_does_not_admit_more_than_fifo_at_low_load(self) -> None:
        """At N=2 (well below any real contention limit -- confirmed
        degraded=False via a live Docker+ns-3 run), fifo_policy's naive
        per-tick byte-budget FIFO admits 440/467 candidates. Predictive
        admission, despite being "smarter" about density-based byte
        packing, was found (see docs/AUDIT_ACCEPTANCE_TRACKING.md
        16/09/2026 "Bước 5: message-level diff") to admit MORE total
        candidates (444) at this exact scenario -- 7 extra low-value
        (debug/human_qoe/perception) messages fifo's simpler logic
        leaves unsent. A live A/B confirmed this modest excess causes
        collateral MAC-layer collision loss for 41 OTHER, unrelated,
        natively-sized messages that fifo delivers successfully --
        collapsing real delivery from 96.8% to 86.5% even though byte
        budget was never actually exceeded by either policy."""
        fifo_events = generate_trace_events(
            scenario="test", robots=2, seconds=3, seed=13,
            capacity_bytes_per_second=max(200_000, 2 * 6_000),
            policies=["fifo"], include_non_sent=True, merge_control_station=True,
        )
        predictive_events = generate_trace_events(
            scenario="test", robots=2, seconds=3, seed=13,
            capacity_bytes_per_second=max(200_000, 2 * 6_000),
            policies=["fleetqox_predictive"], include_non_sent=True, merge_control_station=True,
        )
        fifo_admitted = sum(1 for e in fifo_events if e["event_type"] == "packet")
        predictive_admitted = sum(1 for e in predictive_events if e["event_type"] == "packet")
        self.assertLessEqual(
            predictive_admitted, fifo_admitted,
            "predictive admitted more candidates than fifo's naive per-tick byte "
            "budget at a scenario with abundant real capacity -- the extra "
            "low-value traffic measurably collapses real delivery via collateral "
            "MAC contention (see docs/AUDIT_ACCEPTANCE_TRACKING.md 16/09/2026)",
        )


if __name__ == "__main__":
    unittest.main()
