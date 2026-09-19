"""Table VI N=8 sendto()->raw_recvfrom() loss -- PACKET-LEVEL LOCALIZATION
(see docs/AUDIT_ACCEPTANCE_TRACKING.md, "TABLE VI N=8 NETWORK-LOSS
LOCALIZATION"). Scope: FleetRMW N=8 seed=7 ONLY. Measurement-only: adds
live packet capture (portable tcpdump, already built by an earlier
session at .tcpdump_portable/ -- see that investigation's own notes in
AUDIT_ACCEPTANCE_TRACKING.md for why a portable binary is needed
instead of `apt-get install tcpdump` inside these --network=none
containers) on every endpoint's own `eth0` (the veth wire_network()
creates for that container -- NOT the ns-3 `ftapN` tap, so this
captures exactly what enters/leaves EACH ENDPOINT CONTAINER's own
network stack, both directions, for the whole run.

This directly answers: for a given sender->receiver pair, did the
packet ever leave the sender's own interface (independent of whatever
happens inside the ns-3 wifi simulation), and did it ever arrive at
the receiver's own interface (independent of whether the RMW process's
own recvfrom() call ever picked it up)? Comparing "arrived at receiver
eth0" against the ALREADY-EXISTING raw_recvfrom loss-funnel trace
(FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING=1, kept enabled here too for
correlation) localizes whether loss happens INSIDE the ns-3 wifi
simulation (packet never reaches receiver's eth0 at all) or AFTER
arrival but before the RMW's own UDP socket picks it up (arrives at
eth0, recvfrom() never sees it -- a kernel-socket-buffer-level cause,
not a wifi-PHY one).

No harness/production/QoS/timeout/retransmission/broadcast/Ricart-
Agrawala/Docker-network-config change. This script drives
ReferenceTopologyProbe's existing public methods in the exact same
order run_coordination_probe() already does (see that function) --
tcpdump start/stop are the ONLY additions, both no-ops with respect to
what the harness itself does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    READY_DEADLINE_S,
    RMW_PORT,
    ReadinessFailure,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
)

NUM_ROBOTS = 8
SEED = 7
TCPDUMP_PORTABLE_HOST_DIR = ROOT / ".tcpdump_portable"


def deploy_portable_tcpdump(container_name: str) -> None:
    docker("cp", str(TCPDUMP_PORTABLE_HOST_DIR), f"{container_name}:/tmp/tcpdump_portable")


def start_capture(container_name: str, pcap_path_container: str, pid_file_container: str) -> None:
    # -Z root: this minimal image has no "tcpdump" system user for
    # tcpdump's default privilege-drop target -- confirmed necessary by
    # the earlier session's own notes (without it, tcpdump silently
    # exits with "Couldn't change to 'tcpdump'", easily mistaken for
    # "no traffic"). -i eth0: this container's OWN interface (the veth
    # wire_network() renamed to eth0), not the ns-3 ftapN tap -- sees
    # exactly what this one container's network stack sends/receives.
    cmd = (
        f"LD_LIBRARY_PATH=/tmp/tcpdump_portable/lib "
        f"/tmp/tcpdump_portable/bin/tcpdump -Z root -i eth0 -n "
        f"udp port {RMW_PORT} -w /work/{pcap_path_container} "
        f"> /work/{pcap_path_container}.log 2>&1 & "
        f"echo $! > /work/{pid_file_container}"
    )
    docker("exec", "-d", container_name, "bash", "-lc", cmd)


def stop_capture(container_name: str, pid_file_host: Path) -> None:
    if not pid_file_host.exists():
        return
    pid = pid_file_host.read_text().strip()
    if not pid:
        return
    docker("exec", container_name, "bash", "-lc", f"kill -INT {pid} 2>/dev/null || true")


def read_pcap_packets(pcap_path: Path) -> list[dict[str, Any]]:
    """Parse with the HOST's own tcpdump (already installed, confirmed
    via `which tcpdump`) -- reading a pcap file needs no container/
    network-namespace access at all, unlike capturing it live."""
    if not pcap_path.exists() or pcap_path.stat().st_size == 0:
        return []
    result = subprocess.run(
        ["tcpdump", "-r", str(pcap_path), "-tt", "-n", "-q"],
        capture_output=True, text=True, check=False,
    )
    packets = []
    for line in result.stdout.splitlines():
        # Example: "1789800063.057143 IP 10.60.0.2.9100 > 10.60.0.3.9100: UDP, length 210"
        parts = line.split()
        if len(parts) < 6 or parts[1] != "IP":
            continue
        try:
            ts = float(parts[0])
            src = parts[2]
            dst = parts[4].rstrip(":")
            length = int(parts[-1])
        except (ValueError, IndexError):
            continue
        src_ip = src.rsplit(".", 1)[0]
        dst_ip = dst.rsplit(".", 1)[0]
        packets.append({"wall_s": ts, "src_ip": src_ip, "dst_ip": dst_ip, "length": length})
    return packets


def main() -> int:
    output_dir = (ROOT / "results_rmw_socket" / "table6_n8_network_loss_investigation" / f"fleetrmw_n{NUM_ROBOTS}_seed{SEED}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    endpoints = endpoint_list(NUM_ROBOTS)
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    shutil.rmtree(ROOT / results_dir_container, ignore_errors=True)
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=NUM_ROBOTS, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(15.0) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    pcap_files: dict[str, Path] = {}
    pid_files: dict[str, Path] = {}

    try:
        probe.start_containers()
        probe.build_ns3_binary()
        probe.wire_network()

        print("Deploying portable tcpdump to every endpoint container...", flush=True)
        for i, endpoint in enumerate(endpoints):
            container_name = probe.endpoint_container_names[i]
            deploy_portable_tcpdump(container_name)
            pcap_path_container = f"{results_dir_container}/capture_{i}.pcap"
            pid_file_container = f"{results_dir_container}/tcpdump_{i}.pid"
            pcap_files[endpoint] = ROOT / pcap_path_container
            pid_files[endpoint] = ROOT / pid_file_container
            start_capture(container_name, pcap_path_container, pid_file_container)
        time.sleep(2.0)  # let every tcpdump attach before any RMW traffic starts

        probe.start_ns3(sim_duration_s=60.0, num_aps=1, layout="circle", circle_radius=7.5,
                         path_loss_exponent=2.7, tx_power_dbm=15.0, rx_sensitivity_dbm=-82.0,
                         mobility_speed=0.0, ns3_seed=1, ns3_run=1)
        probe.launch_coordination_endpoints(
            num_crossings=5, crossing_duration_ms=300.0, reply_timeout_s=5.0,
            defer_release_timeout_s=8.0, priority_mode="lamport", seed=SEED,
            start_offset_ms=2000.0, discovery_timeout_s=15.0,
            start_wait_timeout_s=start_wait_timeout_s, scenario_timeout_s=120.0,
            results_dir_container=results_dir_container, rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default",
            extra_rmw_env={"FLEETQOX_RMW_LOSS_FUNNEL_TRACE_PROFILING": "1"},
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        probe.wait_for_completion(timeout_s=2.0 + 120.0 + 60.0, results_dir_container=results_dir_container)
        endpoint_results = probe.collect_results(results_dir_container)
    except ReadinessFailure as exc:
        status = "invalid_readiness"
        error_text = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_text = str(exc)
    finally:
        print("Stopping tcpdump captures...", flush=True)
        for i, endpoint in enumerate(endpoints):
            container_name = probe.endpoint_container_names[i]
            stop_capture(container_name, pid_files[endpoint])
        time.sleep(2.0)  # let tcpdump flush its pcap buffer to disk
        probe.teardown()

    print(json.dumps({"status": status, "error": error_text}), flush=True)

    print("Parsing captured pcaps...", flush=True)
    packets_by_endpoint = {endpoint: read_pcap_packets(pcap_files[endpoint]) for endpoint in endpoints}
    summary = {
        "status": status,
        "error": error_text,
        "endpoints": endpoints,
        "packet_counts_by_capture_point": {e: len(p) for e, p in packets_by_endpoint.items()},
    }
    summary_path = output_dir / "pcap_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary["packet_counts_by_capture_point"], indent=2), flush=True)

    # Save raw per-endpoint packet lists too (needed for the pair matrix
    # this investigation requires -- kept as a separate file since it
    # can be large).
    raw_path = output_dir / "pcap_packets.json"
    raw_path.write_text(json.dumps(packets_by_endpoint), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
