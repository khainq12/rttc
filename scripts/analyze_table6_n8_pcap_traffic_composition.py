"""Table VI N=8 seed=7 wire-traffic COMPOSITION (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI N=8 TRAFFIC COMPOSITION
AND ACK/NACK CORRELATION"). Reuses the pcaps already captured by
scripts/investigate_table6_n8_network_loss.py (no rerun, no new
traffic, no harness/production/protocol change) and classifies EVERY
UDP packet on port 9100 by its FleetRMW wire "kind" field (see
rmw_pubsub.cpp/data_frame.cpp's own literal encode strings):
  sidecar_packet_frame        -> DATA
  source_sequence_ack_nack    -> ACK  (empty missing_sequence_ranges)
                              -> NACK (non-empty missing_sequence_ranges)
  source_sequence_unrecoverable, graph_advertisement,
  route_advertisement, service_frame, action_frame
                              -> OTHER_CONTROL
  anything else (encrypted/fragment/unparseable)
                              -> UNKNOWN

Parses the pcap files directly (minimal hand-rolled libpcap + Ethernet/
IPv4/UDP header reader -- no external dependency) rather than through
tcpdump's text output, so the UDP payload bytes are available for the
"kind" substring search this classification needs (the packet-count-
only investigation in the prior pass only used tcpdump's summary
line, which has no payload content).

READ-ONLY analysis: parses existing files on disk, runs no containers,
sends no traffic, changes nothing.
"""

from __future__ import annotations

import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import endpoint_list  # noqa: E402

RUN_DIR = ROOT / "results_rmw_socket" / "table6_n8_network_loss_investigation" / "fleetrmw_n8_seed7"
NUM_ROBOTS = 8


def parse_pcap(path: Path):
    """Yields (wall_s, src_ip, dst_ip, src_port, dst_port, length, payload_bytes)
    for every IPv4/UDP packet in an Ethernet-linktype pcap file."""
    data = path.read_bytes()
    if len(data) < 24:
        return
    magic = data[0:4]
    if magic == b"\xd4\xc3\xb2\xa1":
        endian = "<"
    elif magic == b"\xa1\xb2\xc3\xd4":
        endian = ">"
    else:
        raise ValueError(f"unrecognized pcap magic in {path}: {magic!r}")
    linktype = struct.unpack_from(endian + "I", data, 20)[0]
    if linktype != 1:
        raise ValueError(f"expected Ethernet linktype (1), got {linktype} in {path}")

    offset = 24
    n = len(data)
    while offset + 16 <= n:
        ts_sec, ts_usec, incl_len, _orig_len = struct.unpack_from(endian + "IIII", data, offset)
        offset += 16
        if offset + incl_len > n:
            break
        frame = data[offset:offset + incl_len]
        offset += incl_len
        if len(frame) < 14:
            continue
        ethertype = struct.unpack_from(">H", frame, 12)[0]
        if ethertype != 0x0800:  # IPv4 only
            continue
        ip_start = 14
        if len(frame) < ip_start + 20:
            continue
        version_ihl = frame[ip_start]
        ihl_words = version_ihl & 0x0F
        ip_header_len = ihl_words * 4
        protocol = frame[ip_start + 9]
        if protocol != 17:  # UDP only
            continue
        src_ip = ".".join(str(b) for b in frame[ip_start + 12:ip_start + 16])
        dst_ip = ".".join(str(b) for b in frame[ip_start + 16:ip_start + 20])
        udp_start = ip_start + ip_header_len
        if len(frame) < udp_start + 8:
            continue
        src_port, dst_port, udp_len, _checksum = struct.unpack_from(">HHHH", frame, udp_start)
        payload = frame[udp_start + 8:udp_start + udp_len] if udp_len >= 8 else b""
        wall_s = ts_sec + ts_usec / 1_000_000.0
        yield {
            "wall_s": wall_s, "src_ip": src_ip, "dst_ip": dst_ip,
            "src_port": src_port, "dst_port": dst_port,
            "length": incl_len, "payload": payload,
        }


def classify(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "UNKNOWN"
    if '"kind":"sidecar_packet_frame"' in text:
        return "DATA"
    if '"kind":"source_sequence_ack_nack"' in text:
        # ACK vs NACK isn't a separate "kind" -- inferred from whether
        # any missing range was reported (see encode_ack_nack() /
        # AckNackFeedback.missing_sequence_ranges in data_frame.cpp).
        if '"missing_sequence_ranges":[]' in text:
            return "ACK"
        return "NACK"
    for other_kind in (
        "source_sequence_unrecoverable", "graph_advertisement",
        "route_advertisement", "service_frame", "action_frame",
    ):
        if f'"kind":"{other_kind}"' in text:
            return "OTHER_CONTROL"
    return "UNKNOWN"


def main() -> int:
    endpoints = endpoint_list(NUM_ROBOTS)
    ip_of = {e: f"10.60.0.{i + 2}" for i, e in enumerate(endpoints)}
    ip_to_ep = {v: k for k, v in ip_of.items()}
    rmw_port = 9100

    print("Parsing pcaps and classifying packets (this may take a while)...", flush=True)
    all_packets: dict[str, list[dict[str, Any]]] = {}
    for i, endpoint in enumerate(endpoints):
        pcap_path = RUN_DIR / "container_results" / f"capture_{i}.pcap"
        pkts = []
        for pkt in parse_pcap(pcap_path):
            if pkt["src_port"] != rmw_port and pkt["dst_port"] != rmw_port:
                continue
            pkt["class"] = classify(pkt["payload"])
            pkt["src_ep"] = ip_to_ep.get(pkt["src_ip"])
            pkt["dst_ep"] = ip_to_ep.get(pkt["dst_ip"])
            del pkt["payload"]
            pkts.append(pkt)
        all_packets[endpoint] = pkts
        print(f"  {endpoint}: {len(pkts)} packets classified", flush=True)

    out_path = RUN_DIR / "pcap_classified_packets.json"
    out_path.write_text(json.dumps(all_packets), encoding="utf-8")
    print(f"Wrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
