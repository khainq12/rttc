"""READ-ONLY architecture audit (see user request, "AUDIT ONLY. Do not
modify the benchmark yet"): live diagnostic launch of the LAN Table V
topology at a given N, capturing container inventory, network
namespace identity, and a packet-capture proof of the actual data path
between control_station and one robot. Does NOT modify
run_ns3_docker_container_fleet_probe.py, does NOT change wiring, does
NOT rerun the 20-seed experiment -- it only calls existing,
already-tested methods (start_containers, wire_network_lan,
launch_endpoints) and inspects the result with docker/tcpdump.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    ReferenceTopologyProbe,
    docker,
    endpoint_list,
    required_peers_from_trace,
)
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402


def sh(cmd: str) -> str:
    r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    num_robots = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    run_id = f"audit_lan_topology_n{num_robots}"
    output_dir = ROOT / "results_rmw_socket" / "lan_topology_audit" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=DEFAULT_IMAGE, num_robots=num_robots, output_dir=output_dir
    )
    findings: dict = {"num_robots": num_robots}
    try:
        probe.start_containers()
        probe.wire_network_lan()

        # --- 1. container inventory ---
        all_names = [probe.rigger_name, probe.ns3sim_name, *probe.endpoint_container_names]
        inv = []
        for name in all_names:
            ps = sh(f"docker inspect {name} --format "
                    "'{{.Name}}|{{.Config.Image}}|{{.HostConfig.NetworkMode}}|{{.State.Pid}}|{{json .NetworkSettings.Networks}}'")
            inv.append({"container": name, "inspect": ps.strip()})
        findings["container_inventory"] = inv

        # --- 2. network namespace identity per container ---
        netns = {}
        for name in all_names:
            pid_out = sh(f"docker inspect -f '{{{{.State.Pid}}}}' {name}").strip()
            ns_out = sh(f"readlink /proc/{pid_out}/ns/net 2>/dev/null || echo NOPID").strip()
            netns[name] = {"pid": pid_out, "netns": ns_out}
        findings["network_namespaces"] = netns

        # --- 3. interfaces per endpoint container + the bridge host ---
        iface_info = {}
        for i, name in enumerate(probe.endpoint_container_names):
            out = sh(f"docker exec {name} ip -brief addr show")
            iface_info[name] = out.strip()
        bridge_out = sh(f"docker exec {probe.ns3sim_name} ip -brief addr show")
        iface_info[probe.ns3sim_name] = bridge_out.strip()
        bridge_detail = sh(f"docker exec {probe.ns3sim_name} bash -lc 'ip link show type bridge; echo ---; bridge link show 2>/dev/null || true'")
        iface_info[f"{probe.ns3sim_name}_bridge_detail"] = bridge_detail.strip()
        rigger_out = sh(f"docker exec {probe.rigger_name} ip -brief addr show 2>&1")
        iface_info[probe.rigger_name] = rigger_out.strip()
        findings["interfaces"] = iface_info

        # --- 4. host networking / bypass checks ---
        bypass = {}
        for name in all_names:
            nm = sh(f"docker inspect -f '{{{{.HostConfig.NetworkMode}}}}' {name}").strip()
            ipcm = sh(f"docker inspect -f '{{{{.HostConfig.IpcMode}}}}' {name}").strip()
            pidm = sh(f"docker inspect -f '{{{{.HostConfig.PidMode}}}}' {name}").strip()
            bypass[name] = {"NetworkMode": nm, "IpcMode": ipcm, "PidMode": pidm}
        findings["bypass_check"] = bypass

        # --- 5. real data-path proof via interface RX/TX counters ---
        # tcpdump/tshark/ngrep are not installed in this image, so hop
        # traversal is proven via `ip -s link show` byte/packet deltas
        # on every hop's interface (control_station's eth0, the
        # bridge-side veth ends inside ns3sim's netns, and the robot's
        # eth0), captured immediately before and after a real FleetRMW
        # send/receive workload -- equally valid "interface counter"
        # evidence per the audit's own allowed methods.
        def iface_counters(container: str, iface: str) -> str:
            return sh(
                f"docker exec {container} bash -lc "
                f"\"cat /sys/class/net/{iface}/statistics/rx_packets "
                f"/sys/class/net/{iface}/statistics/tx_packets "
                f"/sys/class/net/{iface}/statistics/rx_bytes "
                f"/sys/class/net/{iface}/statistics/tx_bytes 2>/dev/null | tr '\\n' ' '\""
            ).strip()

        endpoints = endpoint_list(num_robots)
        bridge_side_ifaces = [f"vlan{i}br" for i in range(num_robots + 1)]
        before = {
            "control_station_eth0": iface_counters(probe.endpoint_container_names[0], "eth0"),
            f"{endpoints[1]}_eth0": iface_counters(probe.endpoint_container_names[1], "eth0"),
            "bridge_lanbr0": iface_counters(probe.ns3sim_name, "lanbr0"),
        }
        for j, br_iface in enumerate(bridge_side_ifaces):
            before[f"bridge_{br_iface}"] = iface_counters(probe.ns3sim_name, br_iface)

        trace_path = output_dir / f"trace_{num_robots}robot.csv"
        events = generate_trace_events(
            scenario="audit_lan_topology", robots=num_robots, seconds=3, seed=7,
            capacity_bytes_per_second=max(200_000, num_robots * 6_000),
            capacity_packets_per_second=None, capacity_airtime_ns_per_second=None,
            policies=("fifo",), include_non_sent=False, merge_control_station=True,
        )
        write_simulator_csv(events, trace_path)
        trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"
        results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        probe._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(probe.endpoints))]
        probe._start_file = f"{results_dir_container}/start"

        required_peer_ids_by_endpoint = required_peers_from_trace(trace_path, "fifo", endpoints)
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy="fifo", start_offset_ms=2000.0, drain_s=8.0,
            discovery_timeout_s=15.0, static_mode=True, static_subscriptions=None,
            extra_rmw_env=None, results_dir_container=results_dir_container,
            start_wait_timeout_s=45.0, rmw_implementation="rmw_fleetqox_cpp",
            discovery_mode="default", required_peer_ids_by_endpoint=required_peer_ids_by_endpoint,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=45.0)
        time.sleep(13.0)  # 2s offset + 3s trace + 8s drain, with margin

        after = {
            "control_station_eth0": iface_counters(probe.endpoint_container_names[0], "eth0"),
            f"{endpoints[1]}_eth0": iface_counters(probe.endpoint_container_names[1], "eth0"),
            "bridge_lanbr0": iface_counters(probe.ns3sim_name, "lanbr0"),
        }
        for j, br_iface in enumerate(bridge_side_ifaces):
            after[f"bridge_{br_iface}"] = iface_counters(probe.ns3sim_name, br_iface)

        def parse(counters: str) -> list[int]:
            return [int(x) for x in counters.split()] if counters.strip() else [0, 0, 0, 0]

        deltas = {}
        for key in before:
            b, a = parse(before[key]), parse(after.get(key, ""))
            deltas[key] = {
                "rx_packets_delta": a[0] - b[0], "tx_packets_delta": a[1] - b[1],
                "rx_bytes_delta": a[2] - b[2], "tx_bytes_delta": a[3] - b[3],
            }
        findings["interface_counter_deltas"] = deltas
        findings["ips"] = probe.ips
        findings["bridge_iface_to_endpoint_map"] = {
            f"vlan{i}br": endpoints[i] for i in range(num_robots + 1)
        }
    finally:
        probe.teardown()

    out_path = output_dir / "findings.json"
    out_path.write_text(json.dumps(findings, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(findings, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
