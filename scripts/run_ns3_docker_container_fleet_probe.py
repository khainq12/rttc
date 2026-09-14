"""Reference-topology probe: 1 control_station + N robots, EACH IN ITS
OWN SEPARATE DOCKER CONTAINER, connected through a single ns-3-simulated
802.11g wifi network (1 AP + N+1 STA, circular layout) -- matching the
reference simulation diagram (see docs/AUDIT_ACCEPTANCE_TRACKING.md
"kịch bản mô phỏng theo sơ đồ tham chiếu").

Architecture (validated via a manual 2-container/1-ns3-container proof of
concept before this script was written -- see that doc section for the
full design discussion and the debugging trail):

  - 1 Docker container per endpoint (control_station, robot_0000..N-1),
    --network=none, running the real FleetRMW/ROS2 stack -- NOT network
    namespaces manufactured inside one shared container the way
    run_ns3_docker_wifi_tap_rmw_probe.py does it. Each container gets its
    own netns automatically (that's what a container IS), so "1 container
    per endpoint" is exactly "1 network namespace per endpoint" plus
    filesystem/process isolation on top.

  - 1 additional "ns3sim" container running the compiled ns-3 TapBridge
    program (external/ns3/fleetqox_trace_replay_tap.cc), simulating the
    shared wifi channel + AP -- identical role to the single-container
    harness's ns-3 process, just isolated in its own container instead of
    sharing the orchestrator's.

  - 1 additional privileged "rigger" container (--pid=host, NET_ADMIN,
    SYS_ADMIN, /dev/net/tun) used ONLY to run the cross-container network
    setup commands (veth creation, moving interface ends between
    containers' network namespaces, tap+bridge creation inside ns3sim's
    namespace). This exists because manipulating network namespaces
    requires CAP_NET_ADMIN/CAP_SYS_ADMIN, which the orchestrating Python
    process does NOT have on the bare host (confirmed: `ip netns add`
    fails with "mount --make-shared /run/netns failed: Operation not
    permitted" when run as a normal user) -- those capabilities are only
    available inside a container that requests them via --cap-add.
    --pid=host lets this one container see every OTHER container's PID
    (hence /proc/<pid>/ns/net) even though they're all separate
    containers, letting `nsenter -t <PID> -n -- <cmd>` reach into any of
    them from one place instead of needing --pid=host on every container.

  - For each endpoint container: a veth pair connects its network
    namespace to ns3sim's network namespace, where a bridge+tap device
    (same setup as the single-container harness, just executed via the
    rigger's nsenter commands instead of `ip netns exec`) feeds the ns-3
    TapBridge for that station.

Known pitfall fixed during development: ns-3's LogDistancePropagationLossModel
defaults to a ReferenceLoss calibrated for ~5.15 GHz, not 802.11g's actual
2.4 GHz carrier -- left at that default, the reference diagram's own
circleRadius (7.5m, meaning up to 15m worst-case station separation)
delivered ZERO packets end to end. Fixed in fleetqox_trace_replay_tap.cc
by passing an explicit 2.4 GHz ReferenceLoss (~40.05 dB); re-verified
working after the fix. If this script is ever adapted to a very different
--circle-radius/--path-loss-exponent combination, re-check the link
budget the same way (a 2-station probe with just the reference and the
farthest robot is enough) before trusting a larger run's delivery numbers.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
FLEETQOX_RMW_INSTALL = ".tmp_fleetrmw_matched_v2_install"
RMW_PORT = 9100
# Distinct /24 from run_ns3_docker_wifi_tap_rmw_probe.py's 10.50.0.0/24 --
# these are separate Docker networks/namespaces, but keeping the ranges
# visually distinct avoids any confusion when both harnesses' logs are
# read side by side.
BASE_IP_PREFIX = "10.60.0."
STATIC_SUBSCRIPTION_TYPE_NAME = "std_msgs/msg/String"
# Fast DDS's own default discovery-server port (matches server id 0's
# conventional port in its own CLI examples) -- arbitrary otherwise, just
# needs to not collide with RMW_PORT/Zenoh's 7447 on the same station.
FASTDDS_DISCOVERY_SERVER_PORT = 11811
NS3_ATTACH_WAIT_S = 3
# Confirmed empirically (see docs/AUDIT_ACCEPTANCE_TRACKING.md "kịch bản
# mô phỏng theo sơ đồ tham chiếu") that run_ns3_docker_wifi_tap_rmw_probe.py's
# READY_DEADLINE_S=30 is NOT enough here: each endpoint is a full separate
# `docker exec` into its own container (cold rclpy import, cold ROS2
# setup.bash sourcing per container) instead of a lightweight background
# process sharing one already-warm container, so per-endpoint ready-gate
# latency is meaningfully higher and more variable across runs.
READY_DEADLINE_S = 60
CONTAINER_PREFIX = "fleetqox_ref"


def endpoint_list(num_robots: int) -> list[str]:
    """Reference-topology endpoint set: ONE control_station (merges the
    old fleet_controller/operator_ui/fleet_router roles) plus num_robots
    robots -- matches fleetqox_trace_replay_tap.cc's stationEndpointLabels
    exactly (index 0 = control_station, 1..num_robots = robot_0000..N-1)."""
    endpoints = ["control_station"]
    endpoints.extend(f"robot_{i:04d}" for i in range(num_robots))
    return endpoints


def station_mac(index: int) -> str:
    """MUST match fleetqox_trace_replay_tap.cc's stationMacs formula
    exactly -- see run_ns3_docker_wifi_tap_rmw_probe.py's _station_mac()
    for the full rationale (TapBridge doesn't copy a tap's real MAC onto
    the ns-3 WifiNetDevice it bridges)."""
    return f"02:00:00:00:{(index >> 8) & 0xFF:02x}:{index & 0xFF:02x}"


def topic_for(destination: str, flow_class: str) -> str:
    """MUST match scripts/fleetqox_rmw_trace_endpoint.py's _topic_for()."""
    safe_dst = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in destination)
    safe_class = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in flow_class)
    return f"/fleetqox_trace/{safe_dst}/{safe_class}"


def build_static_subscriptions(
    trace_path: Path, policy: str, endpoints: list[str]
) -> dict[str, list[tuple[str, str]]]:
    """Same design as run_ns3_docker_wifi_tap_rmw_probe.py's function of
    the same name -- see its docstring for the full rationale."""
    by_publisher: dict[str, set[tuple[str, str]]] = {endpoint: set() for endpoint in endpoints}
    with trace_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] != policy:
                continue
            src = row["src"]
            if src not in by_publisher:
                continue
            by_publisher[src].add((row["dst"], row["flow_class"]))
    return {endpoint: sorted(pairs) for endpoint, pairs in by_publisher.items()}


def compute_latency_stats_ms(endpoint_results: dict[str, Any]) -> dict[str, Any] | None:
    """End-to-end latency percentiles (p50/p95/p99) across every delivered
    message, aggregated over ALL endpoints. Needs no new instrumentation --
    fleetqox_rmw_trace_endpoint.py's on_message() callback already records
    both sent_wall_ns (embedded in the payload by the sender, RMW-agnostic
    since it's just JSON the app itself writes/reads) and recv_wall_ns
    (stamped locally on receipt) for every entry in "received". This was
    sitting unused in the result JSON -- see docs/AUDIT_ACCEPTANCE_TRACKING.md
    "Bổ sung metrics còn thiếu: latency percentile" for why it wasn't
    reported until now. Returns None if nothing was delivered (avoids
    dividing by zero / indexing an empty list -- e.g. CycloneDDS's 0%
    collapse at 17 endpoints)."""
    samples_ns: list[int] = []
    for result in endpoint_results.values():
        if not result:
            continue
        for msg in result.get("received", []):
            samples_ns.append(msg["recv_wall_ns"] - msg["sent_wall_ns"])
    if not samples_ns:
        return None
    samples_ns.sort()

    def _percentile(p: float) -> float:
        index = min(len(samples_ns) - 1, int(round(p / 100.0 * (len(samples_ns) - 1))))
        return samples_ns[index] / 1e6

    return {
        "n": len(samples_ns),
        "p50_ms": _percentile(50),
        "p95_ms": _percentile(95),
        "p99_ms": _percentile(99),
        "mean_ms": sum(samples_ns) / len(samples_ns) / 1e6,
        "max_ms": samples_ns[-1] / 1e6,
    }


def compute_jitter_stale_repair_stats(endpoint_results: dict[str, Any]) -> dict[str, Any]:
    """Bảng V's Jitter / Stale ratio / Repair amp. columns.

    - jitter_ms: stdev of end-to-end latency across every delivered
      message (a standard networking-paper proxy for jitter -- NOT the
      RFC 3550 running-average formula, which needs strict per-flow
      packet ORDER that this aggregate cross-endpoint view doesn't
      preserve; stdev over the same recv_wall_ns-sent_wall_ns samples
      compute_latency_stats_ms already uses is the defensible choice
      here). None if fewer than 2 messages delivered.
    - stale_ratio: fraction of DELIVERED messages whose end-to-end
      latency exceeded their own deadline_ms (embedded in the payload by
      the sender -- RMW-agnostic, same field compute_latency_stats_ms
      draws sent_wall_ns/recv_wall_ns from). Computable for all 4 RMWs
      from data already being collected, no new instrumentation needed.
    - repair_amp: FleetRMW-ONLY, via fleetqox_transport_metrics' own
      NACK/retransmission counters (nack_retransmissions +
      fragments_selectively_retransmitted + reliable_timeout_retransmissions,
      as a fraction of frames_sent). CycloneDDS/Zenoh/FastDDS are
      black-box RMWs with no equivalent introspection reachable through
      this harness -- estimating their repair amplification would need
      packet-capture-based counting of duplicate/retransmitted sequence
      numbers, a separate, much larger undertaking not attempted here.
      repair_amp_available is False (repair_amp is None) whenever no
      endpoint in this run carries fleetqox_transport_metrics at all.
    """
    latency_samples_ms: list[float] = []
    stale_count = 0
    for result in endpoint_results.values():
        if not result:
            continue
        for msg in result.get("received", []):
            latency_ms = (msg["recv_wall_ns"] - msg["sent_wall_ns"]) / 1e6
            latency_samples_ms.append(latency_ms)
            if latency_ms > msg["deadline_ms"]:
                stale_count += 1

    jitter_ms = statistics.stdev(latency_samples_ms) if len(latency_samples_ms) > 1 else None
    stale_ratio = (stale_count / len(latency_samples_ms)) if latency_samples_ms else None

    repair_numerator = 0
    repair_denominator = 0
    repair_amp_available = False
    for result in endpoint_results.values():
        if not result:
            continue
        metrics = result.get("fleetqox_transport_metrics")
        if not metrics:
            continue
        repair_amp_available = True
        repair_numerator += (
            metrics.get("nack_retransmissions", 0)
            + metrics.get("fragments_selectively_retransmitted", 0)
            + metrics.get("reliable_timeout_retransmissions", 0)
        )
        repair_denominator += metrics.get("frames_sent", 0)
    repair_amp = (
        (repair_numerator / repair_denominator if repair_denominator else 0.0)
        if repair_amp_available
        else None
    )

    return {
        "jitter_ms": jitter_ms,
        "stale_ratio": stale_ratio,
        "repair_amp": repair_amp,
        "repair_amp_available": repair_amp_available,
    }


def compute_graph_join_failures(endpoint_results: dict[str, Any]) -> dict[str, Any] | None:
    """Count endpoints whose discovery beacon (see
    fleetqox_rmw_trace_endpoint.py's --expected-peer-count) did NOT reach
    its full expected peer count within --discovery-timeout-s -- i.e. the
    paper's "Graph/Join failures" column (Bảng IV). Returns None when no
    endpoint ran the beacon at all (discovery_expected_peers is 0/absent
    for every one, e.g. an all-rmw_fleetqox_cpp run -- static mode has no
    discovery step by design, so "join failure" isn't a meaningful concept
    there, not simply zero of them)."""
    total_with_beacon = 0
    failures = 0
    per_endpoint: dict[str, Any] = {}
    for endpoint, result in endpoint_results.items():
        if not result:
            continue
        expected = result.get("discovery_expected_peers") or 0
        if expected <= 0:
            continue
        total_with_beacon += 1
        seen = result.get("discovery_peers_seen") or 0
        failed = seen < expected
        failures += int(failed)
        per_endpoint[endpoint] = {"peers_seen": seen, "expected_peers": expected, "failed": failed}
    if total_with_beacon == 0:
        return None
    return {
        "total_endpoints": total_with_beacon,
        "failures": failures,
        "failure_rate": failures / total_with_beacon,
        "per_endpoint": per_endpoint,
    }


def compute_coordination_metrics(endpoint_results: dict[str, Any]) -> dict[str, Any]:
    """Bảng VI's 4 columns, computed from fleetqox_coordination_endpoint.py's
    per-endpoint result JSON (see that file's module docstring for the
    Ricart-Agrawala zone-mutex protocol this aggregates).

    - coordination_update_age_ms: mean age (recv - declared wall time) of
      every REQUEST/REPLY message actually received, across all
      endpoints -- how stale the fleet's shared coordination state is
      by the time it's acted on.
    - conflict_resolution_delay_ms: mean, across every crossing that
      reached a GENUINE consensus (forced_entry == False -- see that
      field's own comment for why a forced entry, i.e. giving up on
      collecting every peer's reply, must not be averaged in as if it
      were an equally valid mutex acquisition), of
      entered_wall_ns - declared_wall_ns.
    - coordination_retry_count (renamed 14/09/2026 from
      navigation_recovery_count -- the old name was misleading, see
      fleetqox_coordination_endpoint.py's module docstring): SUM across
      all endpoints of retry counts (a REQUEST that didn't collect
      every reply within --reply-timeout-s and had to be re-broadcast)
      -- see fleetqox_coordination_endpoint.py's module docstring for
      why this coordination-layer retry is the closest available
      stand-in for a
      real navigation-stack recovery in a harness with no actual motion
      planner.
    - task_completion_s: MAX across endpoints of task_completion_s --
      the scenario isn't done until the SLOWEST endpoint finishes its
      assigned crossings, same "whole-fleet is only as fast as its
      slowest member" reasoning as discovery_convergence_max_s
      elsewhere in this file.
    - forced_entry_rate: fraction of ALL crossings (across all
      endpoints) that were forced entries -- reported alongside the
      other 4 so a reader can tell whether conflict_resolution_delay_ms
      reflects a harness that mostly reached genuine consensus, or one
      where forced entries were so common the "clean" delay figure
      covers only a small, possibly unrepresentative minority of
      crossings.

    Returns None values for whichever field has no eligible samples
    (e.g. every crossing at every endpoint was forced -- see the 5G
    profile's N=16/32 collapse) rather than raising or silently
    defaulting to 0, which would misleadingly read as "instant".
    """
    message_ages_ms: list[float] = []
    resolution_delays_ms: list[float] = []
    total_crossings = 0
    forced_crossings = 0
    total_recovery_count = 0
    completion_times_s: list[float] = []

    for result in endpoint_results.values():
        if not result:
            continue
        message_ages_ms.extend(result.get("coordination_message_ages_ms", []))
        total_recovery_count += result.get("coordination_retry_count", 0)
        completion_times_s.append(result.get("task_completion_s", 0.0))
        for crossing in result.get("crossings", []):
            total_crossings += 1
            if crossing.get("forced_entry"):
                forced_crossings += 1
            else:
                resolution_delays_ms.append(crossing["conflict_resolution_delay_ms"])

    return {
        "coordination_update_age_ms": (
            sum(message_ages_ms) / len(message_ages_ms) if message_ages_ms else None
        ),
        "conflict_resolution_delay_ms": (
            sum(resolution_delays_ms) / len(resolution_delays_ms) if resolution_delays_ms else None
        ),
        "coordination_retry_count": total_recovery_count,
        "task_completion_s": max(completion_times_s) if completion_times_s else None,
        "total_crossings": total_crossings,
        "forced_crossings": forced_crossings,
        "forced_entry_rate": (forced_crossings / total_crossings) if total_crossings else None,
    }


def parse_docker_mem_usage_mb(mem_usage: str) -> float:
    """Parse docker stats' "{{.MemUsage}}" field, e.g. "45.2MiB / 3.678GiB",
    into the "used" side as decimal MB. Docker reports binary units (Ki/Mi/Gi
    = 1024^n bytes) but the paper's Bảng IV column is just labeled "RSS (MB)"
    -- converting to decimal MB (1e6 bytes) rather than leaving it in MiB
    keeps that column's units unambiguous regardless of which convention a
    reader assumes "MB" means."""
    used = mem_usage.split("/")[0].strip()
    match = re.match(r"([\d.]+)\s*([KMGT]?i?B)", used)
    if not match:
        raise ValueError(f"unrecognized docker MemUsage format: {mem_usage!r}")
    value, unit = float(match.group(1)), match.group(2)
    multipliers_binary = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    bytes_used = value * multipliers_binary[unit]
    return bytes_used / 1e6


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"docker {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result


def container_pid(name: str) -> int:
    result = docker("inspect", "--format", "{{.State.Pid}}", name)
    pid_text = result.stdout.strip()
    if not pid_text.isdigit() or pid_text == "0":
        raise RuntimeError(f"container {name} has no running PID (got {pid_text!r})")
    return int(pid_text)


def rigger_run(rigger_name: str, script: str, *, check: bool = True) -> subprocess.CompletedProcess:
    """Run `script` as root inside the rigger container. Every network-
    namespace-touching command in this file goes through here, NEVER
    directly via subprocess on the host -- see the module docstring for
    why the host process itself can't do this."""
    return docker("exec", rigger_name, "bash", "-lc", script, check=check)


class ReferenceTopologyProbe:
    """Owns the full container/network lifecycle for one probe run.
    Always call teardown() when done (a context manager would silently
    swallow the traceback on a mid-setup failure that's exactly when you
    most want to see which container/step failed, so this is deliberately
    NOT one -- callers are expected to wrap run() in their own try/finally
    if they want guaranteed cleanup)."""

    def __init__(
        self,
        *,
        run_id: str,
        image: str,
        num_robots: int,
        output_dir: Path,
    ) -> None:
        self.run_id = run_id
        self.image = image
        self.num_robots = num_robots
        self.endpoints = endpoint_list(num_robots)
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rigger_name = f"{CONTAINER_PREFIX}_{run_id}_rigger"
        self.ns3sim_name = f"{CONTAINER_PREFIX}_{run_id}_ns3sim"
        self.endpoint_container_names = [
            f"{CONTAINER_PREFIX}_{run_id}_{endpoint}" for endpoint in self.endpoints
        ]
        self.ips = {
            endpoint: f"{BASE_IP_PREFIX}{i + 2}" for i, endpoint in enumerate(self.endpoints)
        }

    def _mount_args(self) -> list[str]:
        return ["-v", f"{ROOT}:/work", "-w", "/work"]

    def start_containers(self) -> None:
        docker("rm", "-f", self.rigger_name, self.ns3sim_name, *self.endpoint_container_names, check=False)
        docker(
            "run", "-d", "--name", self.rigger_name, "--pid=host", "--network=none",
            "--cap-add=NET_ADMIN", "--cap-add=SYS_ADMIN", "--device=/dev/net/tun",
            *self._mount_args(), self.image, "sleep infinity",
        )
        docker(
            "run", "-d", "--name", self.ns3sim_name, "--network=none", "--init",
            "--cap-add=NET_ADMIN", "--cap-add=SYS_ADMIN", "--device=/dev/net/tun",
            *self._mount_args(), self.image, "sleep infinity",
        )
        # --init (Docker's built-in tini as PID 1): without it, "sleep
        # infinity" as PID 1 never reaps its exited `docker exec -d`
        # children, leaving zombies behind after each endpoint finishes --
        # not itself the cause of the pgrep-based completion check bug
        # documented on wait_for_completion() (that was pgrep matching its
        # OWN invoking shell's command line), but proper process hygiene
        # regardless, and confirmed via `ps -eo pid,ppid,stat,cmd` that
        # without --init the exited python3 process stayed listed.
        for name in self.endpoint_container_names:
            docker(
                "run", "-d", "--name", name, "--network=none", "--init", "--cap-add=NET_ADMIN",
                *self._mount_args(), self.image, "sleep infinity",
            )

    def build_ns3_binary(self, extra_ns3_source_lines: str = "") -> None:
        build_cmd = (
            "set -e\n"
            "g++ -std=c++17 external/ns3/fleetqox_trace_replay_tap.cc "
            "-o /tmp/fleetqox_tap_bridge "
            "$(pkg-config --cflags --libs ns3-core ns3-network ns3-mobility "
            "ns3-wifi ns3-tap-bridge)\n"
            # Same tap-creator baked-path symlink fix as
            # run_ns3_docker_wifi_tap_rmw_probe.py's build_shell_script --
            # see that file's comment for the full history of why this is
            # needed (the .so's baked-in helper-binary path doesn't match
            # where it's actually installed in this image).
            "TAPCREATOR_REAL=$(find /usr -iname '*tap-creator*' -type f 2>/dev/null | head -1)\n"
            "TAPCREATOR_SO=$(find /usr -iname 'libns3*tap-bridge*' 2>/dev/null | head -1)\n"
            "TAPCREATOR_BAKED=$(strings \"$TAPCREATOR_SO\" 2>/dev/null | "
            "grep -E '/.*tap-creator$' | head -1)\n"
            "if [ -n \"$TAPCREATOR_BAKED\" ] && [ ! -e \"$TAPCREATOR_BAKED\" ]; then\n"
            "  mkdir -p \"$(dirname \"$TAPCREATOR_BAKED\")\" && "
            "ln -sf \"$TAPCREATOR_REAL\" \"$TAPCREATOR_BAKED\"\n"
            "fi\n"
        )
        result = docker("exec", self.ns3sim_name, "bash", "-lc", build_cmd, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ns-3 binary build failed:\n{result.stdout}\n{result.stderr}")

    def build_ns3_nr_binary(self) -> None:
        """NR counterpart to build_ns3_binary(), for Bảng V's "5G" profile
        (external/ns3/fleetqox_trace_replay_nr.cc) -- links against the
        jazzy-nr image's additional ns3-nr/ns3-antenna pkg-config modules
        (see external/rmw-netem/Dockerfile.nr) instead of ns3-wifi: the
        ghost-node architecture never touches a WifiNetDevice at all, only
        Csma (ghost<->tap), point-to-point (ghost<->UE), and the nr
        module's own gNB/UE/EPC devices."""
        build_cmd = (
            "set -e\n"
            "g++ -std=c++17 external/ns3/fleetqox_trace_replay_nr.cc "
            "-o /tmp/fleetqox_nr_bridge "
            "$(pkg-config --cflags --libs ns3-core ns3-network ns3-internet "
            "ns3-point-to-point ns3-csma ns3-mobility ns3-antenna "
            "ns3-tap-bridge ns3-nr)\n"
            # Same tap-creator baked-path symlink fix as build_ns3_binary()
            # -- see that method's comment for the full history.
            "TAPCREATOR_REAL=$(find /usr -iname '*tap-creator*' -type f 2>/dev/null | head -1)\n"
            "TAPCREATOR_SO=$(find /usr -iname 'libns3*tap-bridge*' 2>/dev/null | head -1)\n"
            "TAPCREATOR_BAKED=$(strings \"$TAPCREATOR_SO\" 2>/dev/null | "
            "grep -E '/.*tap-creator$' | head -1)\n"
            "if [ -n \"$TAPCREATOR_BAKED\" ] && [ ! -e \"$TAPCREATOR_BAKED\" ]; then\n"
            "  mkdir -p \"$(dirname \"$TAPCREATOR_BAKED\")\" && "
            "ln -sf \"$TAPCREATOR_REAL\" \"$TAPCREATOR_BAKED\"\n"
            "fi\n"
        )
        result = docker("exec", self.ns3sim_name, "bash", "-lc", build_cmd, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"ns-3 NR binary build failed:\n{result.stdout}\n{result.stderr}")

    def wire_network(self) -> None:
        """Cross-container equivalent of run_ns3_docker_wifi_tap_rmw_probe.py's
        build_shell_script per-station netns/tap/bridge loop -- same
        sequence of operations, executed via the rigger's nsenter instead
        of `ip netns exec` (see module docstring)."""
        ns3_pid = container_pid(self.ns3sim_name)
        commands: list[str] = ["set -e"]
        for i, endpoint in enumerate(self.endpoints):
            endpoint_pid = container_pid(self.endpoint_container_names[i])
            veth_ns3_side = f"v{i}ns3"
            veth_endpoint_side = f"v{i}ep"
            commands.extend(
                [
                    f"nsenter -t {ns3_pid} -n -- ip link add name br{i} type bridge",
                    f"nsenter -t {ns3_pid} -n -- ip link set br{i} up",
                    f"nsenter -t {ns3_pid} -n -- ip tuntap add dev ftap{i} mode tap",
                    f"nsenter -t {ns3_pid} -n -- ip link set ftap{i} up",
                    f"nsenter -t {ns3_pid} -n -- ip link set ftap{i} master br{i}",
                    f"ip link add {veth_ns3_side} type veth peer name {veth_endpoint_side}",
                    f"ip link set {veth_ns3_side} netns {ns3_pid}",
                    f"ip link set {veth_endpoint_side} netns {endpoint_pid}",
                    f"nsenter -t {ns3_pid} -n -- ip link set {veth_ns3_side} up",
                    f"nsenter -t {ns3_pid} -n -- ip link set {veth_ns3_side} master br{i}",
                    f"nsenter -t {endpoint_pid} -n -- ip link set {veth_endpoint_side} name eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set eth0 address {station_mac(i)}",
                    f"nsenter -t {endpoint_pid} -n -- ip addr add {self.ips[endpoint]}/24 dev eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set eth0 up",
                    f"nsenter -t {endpoint_pid} -n -- ip link set lo up",
                    # `ip addr add` only auto-creates a route for the
                    # local /24 subnet, NOT for the multicast range --
                    # confirmed via a real 2-container proof of concept:
                    # sendto(('239.255.0.1', port)) failed with "Network
                    # is unreachable" without this route, and succeeded
                    # once added. Standard DDS/Zenoh discovery relies on
                    # multicast (e.g. CycloneDDS's SPDP), so without this
                    # route every non-fleetqox rmw_implementation would
                    # silently fail ALL discovery -- not a wifi-congestion
                    # finding, just a missing route (see
                    # docs/AUDIT_ACCEPTANCE_TRACKING.md "so sánh baseline
                    # DDS truyền thống" for the full trail: a first
                    # CycloneDDS run measured a suspicious clean 0%
                    # delivery, traced back to exactly this).
                    f"nsenter -t {endpoint_pid} -n -- ip route add 224.0.0.0/4 dev eth0",
                ]
            )
        commands.append("echo WIRE_NETWORK_OK")
        result = rigger_run(self.rigger_name, "\n".join(commands))
        if "WIRE_NETWORK_OK" not in result.stdout:
            raise RuntimeError(f"network wiring failed:\n{result.stdout}\n{result.stderr}")

    def wire_network_lan(self) -> None:
        """Bảng V's "LAN" network profile -- the paper's own framing is
        "network control: độ trễ thấp và ít mất gói" (low latency,
        minimal loss), i.e. an IDEAL switched network, not a wifi/
        cellular impairment model at all. Reuses ns3sim purely as a
        bridge host (no ns-3 process ever runs for this profile -- no
        tap devices, no per-station bridges, no propagation-loss model)
        -- ONE shared Linux bridge, every endpoint's veth plugged
        straight into it. This is real kernel-bridged Ethernet between
        containers on the same host: negligible latency (sub-ms) and
        zero loss barring genuine congestion, exactly the "control"
        condition the paper wants LAN to represent."""
        bridge_host_pid = container_pid(self.ns3sim_name)
        commands: list[str] = [
            "set -e",
            f"nsenter -t {bridge_host_pid} -n -- ip link add name lanbr0 type bridge",
            f"nsenter -t {bridge_host_pid} -n -- ip link set lanbr0 up",
        ]
        for i, endpoint in enumerate(self.endpoints):
            endpoint_pid = container_pid(self.endpoint_container_names[i])
            veth_bridge_side = f"vlan{i}br"
            veth_endpoint_side = f"vlan{i}ep"
            commands.extend(
                [
                    f"ip link add {veth_bridge_side} type veth peer name {veth_endpoint_side}",
                    f"ip link set {veth_bridge_side} netns {bridge_host_pid}",
                    f"ip link set {veth_endpoint_side} netns {endpoint_pid}",
                    f"nsenter -t {bridge_host_pid} -n -- ip link set {veth_bridge_side} up",
                    f"nsenter -t {bridge_host_pid} -n -- ip link set {veth_bridge_side} master lanbr0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set {veth_endpoint_side} name eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set eth0 address {station_mac(i)}",
                    f"nsenter -t {endpoint_pid} -n -- ip addr add {self.ips[endpoint]}/24 dev eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set eth0 up",
                    f"nsenter -t {endpoint_pid} -n -- ip link set lo up",
                    # Same multicast-route fix as wire_network() -- needed
                    # regardless of profile for any non-fleetqox RMW's
                    # discovery (see that method's comment for the full
                    # "Network is unreachable" trail).
                    f"nsenter -t {endpoint_pid} -n -- ip route add 224.0.0.0/4 dev eth0",
                ]
            )
        commands.append("echo WIRE_NETWORK_LAN_OK")
        result = rigger_run(self.rigger_name, "\n".join(commands))
        if "WIRE_NETWORK_LAN_OK" not in result.stdout:
            raise RuntimeError(f"LAN network wiring failed:\n{result.stdout}\n{result.stderr}")

    def wire_network_nr_l2(self) -> None:
        """Bảng V's "5G" network profile, L2-only half of the wiring --
        see fleetqox_trace_replay_nr.cc's module docstring for the full
        ghost-node architecture. Same per-endpoint tap+bridge+veth
        sequence as wire_network() (ns-3's TapBridge, Mode=UseLocal,
        attaches to a PRE-created persistent tap by name rather than
        creating an ephemeral one itself -- see wire_network()'s comment
        and fleetqox_trace_replay_tap.cc's docstring for why), just with
        an "n" prefix (nbr{i}/ntap{i}) to keep this profile's interface
        names visually distinct from wire_network()'s wifi-profile
        br{i}/ftap{i} in case both are ever debugged side by side.

        Deliberately does NOT set each container's real 5G identity here
        (no station_mac()-style MAC sync is needed either -- unlike
        WifiNetDevice, CsmaNetDevice supports SendFrom/promiscuous mode
        natively, so TapBridge's UseLocal mode works with NO MAC
        synchronization trick at all, matching ns-3 core's own
        examples/tap-csma-virtual-machine.cc reference pattern). Only
        brings each container's eth0 up with a LINK-LOCAL address
        (172.16.<i>.2/24, matching the ghost's own tap-facing CSMA
        address 172.16.<i>.1/24 assigned by fleetqox_trace_replay_nr.cc's
        ghostTapAddressHelper) -- enough for the container to reach its
        own ghost and nothing else yet. The container's actual overlay
        (EPC-assigned) IP is only known once ns-3 has actually run its
        UE address-assignment step and printed FLEETQOX_NR_MAPPING (see
        start_ns3_nr()), which necessarily happens AFTER this method,
        so finishing the container's routing is a separate method
        (finish_wire_network_nr()) called after that point."""
        ns3_pid = container_pid(self.ns3sim_name)
        commands: list[str] = ["set -e"]
        for i, endpoint in enumerate(self.endpoints):
            endpoint_pid = container_pid(self.endpoint_container_names[i])
            veth_ns3_side = f"v{i}nr3"
            veth_endpoint_side = f"v{i}nrep"
            link_local_ip = f"172.16.{i}.2"
            commands.extend(
                [
                    f"nsenter -t {ns3_pid} -n -- ip link add name nbr{i} type bridge",
                    f"nsenter -t {ns3_pid} -n -- ip link set nbr{i} up",
                    f"nsenter -t {ns3_pid} -n -- ip tuntap add dev ntap{i} mode tap",
                    f"nsenter -t {ns3_pid} -n -- ip link set ntap{i} up",
                    f"nsenter -t {ns3_pid} -n -- ip link set ntap{i} master nbr{i}",
                    f"ip link add {veth_ns3_side} type veth peer name {veth_endpoint_side}",
                    f"ip link set {veth_ns3_side} netns {ns3_pid}",
                    f"ip link set {veth_endpoint_side} netns {endpoint_pid}",
                    f"nsenter -t {ns3_pid} -n -- ip link set {veth_ns3_side} up",
                    f"nsenter -t {ns3_pid} -n -- ip link set {veth_ns3_side} master nbr{i}",
                    f"nsenter -t {endpoint_pid} -n -- ip link set {veth_endpoint_side} name eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip addr add {link_local_ip}/24 dev eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip link set eth0 up",
                    f"nsenter -t {endpoint_pid} -n -- ip link set lo up",
                ]
            )
        commands.append("echo WIRE_NETWORK_NR_L2_OK")
        result = rigger_run(self.rigger_name, "\n".join(commands))
        if "WIRE_NETWORK_NR_L2_OK" not in result.stdout:
            raise RuntimeError(f"NR L2 network wiring failed:\n{result.stdout}\n{result.stderr}")

    def start_ns3(
        self,
        *,
        sim_duration_s: float,
        num_aps: int = 1,
        layout: str = "circle",
        circle_radius: float = 7.5,
        path_loss_exponent: float = 2.7,
        tx_power_dbm: float = 15.0,
        rx_sensitivity_dbm: float = -82.0,
        mobility_speed: float = 0.0,
        ns3_seed: int = 1,
        ns3_run: int = 1,
        log_path: str = "/tmp/ns3.log",
    ) -> None:
        cmd = (
            f"/tmp/fleetqox_tap_bridge --numRobots={self.num_robots} --tapPrefix=ftap "
            f"--simDuration={sim_duration_s:.12g} --numAps={num_aps} "
            f"--layout={shlex.quote(layout)} --circleRadius={circle_radius:.12g} "
            f"--pathLossExponent={path_loss_exponent:.12g} --txPowerDbm={tx_power_dbm:.12g} "
            f"--rxSensitivityDbm={rx_sensitivity_dbm:.12g} --mobilitySpeed={mobility_speed:.12g} "
            f"--seed={ns3_seed} --run={ns3_run} > {log_path} 2>&1"
        )
        docker("exec", "-d", self.ns3sim_name, "bash", "-lc", cmd)
        time.sleep(NS3_ATTACH_WAIT_S)
        check = docker("exec", self.ns3sim_name, "bash", "-lc", "pgrep -f fleetqox_tap_bridge", check=False)
        if check.returncode != 0:
            log = docker("exec", self.ns3sim_name, "cat", log_path, check=False)
            raise RuntimeError(f"ns-3 tap-bridge process exited early:\n{log.stdout}\n{log.stderr}")

    @staticmethod
    def _parse_nr_mapping(log_text: str) -> dict[str, dict[str, str]]:
        """Parse fleetqox_trace_replay_nr.cc's
        "FLEETQOX_NR_MAPPING station_index,endpoint,tap_device,
        ue_overlay_ip,ghost_link_local_ip" lines (one header + one per
        endpoint)."""
        mapping: dict[str, dict[str, str]] = {}
        for line in log_text.splitlines():
            if not line.startswith("FLEETQOX_NR_MAPPING "):
                continue
            fields = line[len("FLEETQOX_NR_MAPPING "):].split(",")
            if len(fields) != 5 or fields[0] == "station_index":
                continue
            _, endpoint, tap_device, ue_overlay_ip, ghost_link_local_ip = fields
            mapping[endpoint] = {
                "tap_device": tap_device,
                "ue_overlay_ip": ue_overlay_ip,
                "ghost_link_local_ip": ghost_link_local_ip,
            }
        return mapping

    def start_ns3_nr(
        self,
        *,
        sim_duration_s: float,
        circle_radius: float = 50.0,
        numerology: int = 1,
        central_frequency: float = 3.5e9,
        bandwidth: float = 20e6,
        gnb_tx_power_dbm: float = 35.0,
        ue_tx_power_dbm: float = 23.0,
        ns3_seed: int = 1,
        ns3_run: int = 1,
        log_path: str = "/tmp/ns3_nr.log",
        mapping_wait_s: float = 60.0,
    ) -> dict[str, dict[str, str]]:
        """Starts fleetqox_trace_replay_nr.cc's compiled binary and blocks
        until it has printed every endpoint's FLEETQOX_NR_MAPPING line.
        Those lines are printed BEFORE Simulator::Run() is ever called
        (EPC UE-address assignment and every ghost's TapBridge::Install()
        both happen during setup, in that C++ file's main()), so this
        returns well before sim_duration_s elapses -- mapping_wait_s=60 is
        a generous ceiling for the one-time NR channel/spectrum model
        initialization cost, not a reflection of how long that setup is
        expected to actually take.

        Returns {endpoint: {"tap_device", "ue_overlay_ip",
        "ghost_link_local_ip"}}, consumed by finish_wire_network_nr() to
        configure each real container's routing with its actual
        EPC-assigned identity -- there is no way to precompute this IP;
        it's assigned internally by NrPointToPointEpcHelper."""
        cmd = (
            f"/tmp/fleetqox_nr_bridge --numRobots={self.num_robots} --tapPrefix=ntap "
            f"--simDuration={sim_duration_s:.12g} --circleRadius={circle_radius:.12g} "
            f"--numerology={numerology} --centralFrequency={central_frequency:.12g} "
            f"--bandwidth={bandwidth:.12g} --gnbTxPowerDbm={gnb_tx_power_dbm:.12g} "
            f"--ueTxPowerDbm={ue_tx_power_dbm:.12g} "
            f"--seed={ns3_seed} --run={ns3_run} > {log_path} 2>&1"
        )
        docker("exec", "-d", self.ns3sim_name, "bash", "-lc", cmd)
        deadline = time.monotonic() + mapping_wait_s
        mapping: dict[str, dict[str, str]] = {}
        while time.monotonic() < deadline:
            check = docker(
                "exec", self.ns3sim_name, "bash", "-lc", "pgrep -f fleetqox_nr_bridge", check=False
            )
            if check.returncode != 0:
                log = docker("exec", self.ns3sim_name, "cat", log_path, check=False)
                raise RuntimeError(
                    f"ns-3 NR bridge process exited early:\n{log.stdout}\n{log.stderr}"
                )
            log = docker("exec", self.ns3sim_name, "cat", log_path, check=False)
            mapping = self._parse_nr_mapping(log.stdout)
            if len(mapping) == len(self.endpoints):
                return mapping
            time.sleep(0.5)
        raise TimeoutError(
            f"timed out after {mapping_wait_s}s waiting for FLEETQOX_NR_MAPPING "
            f"(got {len(mapping)}/{len(self.endpoints)} endpoints)"
        )

    def finish_wire_network_nr(self, mapping: dict[str, dict[str, str]]) -> None:
        """Second half of the NR profile's container-side IP
        configuration -- see wire_network_nr_l2()'s docstring for why
        this can't happen until AFTER ns-3 has assigned + printed each
        UE's real EPC overlay IP. For endpoint i: adds that overlay IP as
        a /32 alias on eth0 (so Linux accepts it as a valid explicit
        route "src"), then an explicit src-routed default route via the
        ghost's link-local IP -- this forces all of this endpoint's
        OUTBOUND traffic to carry its true 5G identity as source
        regardless of eth0's own "natural" (link-local) address, without
        needing NAT (confirmed unavailable -- no ns-3 NAT module found in
        this image via `pkg-config --list-all`) or any subnet-matching
        trick on the ghost's side (the ghost's own AddHostRouteTo,
        already set up in the C++ file, routes by exact destination IP
        regardless of subnet).

        Also overwrites self.ips with each endpoint's real overlay IP --
        every downstream call that reads self.ips (FLEETQOX_RMW_PEERS,
        zenoh_router_endpoint(), fastdds_discovery_server_endpoint(), the
        CycloneDDS static-peers XML) then transparently uses each
        endpoint's true 5G address, no profile-specific branching needed
        in launch_endpoints()."""
        commands: list[str] = ["set -e"]
        for i, endpoint in enumerate(self.endpoints):
            endpoint_pid = container_pid(self.endpoint_container_names[i])
            info = mapping[endpoint]
            overlay_ip = info["ue_overlay_ip"]
            ghost_ip = info["ghost_link_local_ip"]
            commands.extend(
                [
                    f"nsenter -t {endpoint_pid} -n -- ip addr add {overlay_ip}/32 dev eth0",
                    f"nsenter -t {endpoint_pid} -n -- ip route replace default via {ghost_ip} "
                    f"dev eth0 src {overlay_ip}",
                    # Same multicast-route fix as wire_network()/
                    # wire_network_lan() -- needed for any non-fleetqox
                    # RMW's discovery.
                    f"nsenter -t {endpoint_pid} -n -- ip route add 224.0.0.0/4 dev eth0",
                ]
            )
        commands.append("echo WIRE_NETWORK_NR_IP_OK")
        result = rigger_run(self.rigger_name, "\n".join(commands))
        if "WIRE_NETWORK_NR_IP_OK" not in result.stdout:
            raise RuntimeError(f"NR IP routing setup failed:\n{result.stdout}\n{result.stderr}")
        self.ips = {endpoint: mapping[endpoint]["ue_overlay_ip"] for endpoint in self.endpoints}

    def zenoh_router_endpoint(self) -> str:
        """control_station (endpoint index 0) is where start_zenoh_router()
        runs the router -- its IP:7447 is what every other endpoint's
        session config points at explicitly (see start_zenoh_router() and
        the ZENOH_SESSION_CONFIG_URI wiring in launch_endpoints())."""
        return f"tcp/{self.ips[self.endpoints[0]]}:7447"

    def start_zenoh_router(self, log_path: str = "/tmp/zenohd.log") -> None:
        """rmw_zenoh_cpp requires a separate rmw_zenohd router process --
        confirmed via a real run's own warning: "Unable to connect to a
        Zenoh router. Have you started a router with 'ros2 run
        rmw_zenoh_cpp rmw_zenohd'?" (see
        docs/AUDIT_ACCEPTANCE_TRACKING.md "so sánh baseline DDS truyền
        thống"). Run it INSIDE the control_station container (endpoint
        index 0) rather than adding an 18th wifi station slot just for
        this -- Zenoh peers reach it over the same already-wired network
        the control_station itself uses, no separate tap/veth/bridge
        needed. Started before any endpoint launches so the router is
        already listening by the time peers start scouting for it.

        Uses an explicit IPv4 listen config (tcp/0.0.0.0:7447) instead of
        the default DEFAULT_RMW_ZENOH_ROUTER_CONFIG.json5's `tcp/[::]:7447`
        -- these netns interfaces are IPv4-only (no IPv6 address was ever
        configured on them, see wire_network()), so an IPv6-wildcard
        listen risks not being reachable via the IPv4 addresses every
        other endpoint actually connects through.
        """
        control_station_name = self.endpoint_container_names[0]
        router_config = (
            '{ listen: { endpoints: ["tcp/0.0.0.0:7447"] } }'
        )
        docker(
            "exec", control_station_name, "bash", "-lc",
            f"echo {shlex.quote(router_config)} > /tmp/zenoh_router_config.json5",
        )
        cmd = (
            "source /opt/ros/jazzy/setup.bash && "
            "export ZENOH_ROUTER_CONFIG_URI=/tmp/zenoh_router_config.json5 && "
            f"ros2 run rmw_zenoh_cpp rmw_zenohd > {log_path} 2>&1"
        )
        docker("exec", "-d", control_station_name, "bash", "-lc", cmd)
        time.sleep(3)
        check = docker("exec", control_station_name, "bash", "-lc", "pgrep -f rmw_zenohd", check=False)
        if check.returncode != 0:
            log = docker("exec", control_station_name, "cat", log_path, check=False)
            raise RuntimeError(f"rmw_zenohd exited early:\n{log.stdout}\n{log.stderr}")

    def fastdds_discovery_server_endpoint(self) -> str:
        """control_station (endpoint index 0) also hosts the Fast DDS
        discovery server -- same "run it on control_station instead of a
        separate 18th station" reasoning as zenoh_router_endpoint()."""
        return f"{self.ips[self.endpoints[0]]}:{FASTDDS_DISCOVERY_SERVER_PORT}"

    def start_fastdds_discovery_server(self, log_path: str = "/tmp/fastdds_discovery_server.log") -> None:
        """Fast DDS's static-config alternative to its default multicast
        Simple Discovery Protocol -- a standalone `fastdds discovery`
        server process every client points at via ROS_DISCOVERY_SERVER
        instead of relying on multicast SPDP-equivalent announcements.
        Confirmed present in this image via `fastdds discovery --help`
        (part of the fastdds-tools package bundled with ROS 2 Jazzy's
        Fast DDS). Run inside control_station like start_zenoh_router(),
        started before any endpoint launches so it's already listening by
        the time clients connect."""
        control_station_name = self.endpoint_container_names[0]
        server_ip = self.ips[self.endpoints[0]]
        cmd = (
            "source /opt/ros/jazzy/setup.bash && "
            f"fastdds discovery -i 0 -l {server_ip} -p {FASTDDS_DISCOVERY_SERVER_PORT} "
            f"> {log_path} 2>&1"
        )
        docker("exec", "-d", control_station_name, "bash", "-lc", cmd)
        time.sleep(3)
        check = docker("exec", control_station_name, "bash", "-lc", "pgrep -f 'fastdds discovery'", check=False)
        if check.returncode != 0:
            log = docker("exec", control_station_name, "cat", log_path, check=False)
            raise RuntimeError(f"fastdds discovery server exited early:\n{log.stdout}\n{log.stderr}")

    def launch_endpoints(
        self,
        *,
        trace_container_path: str,
        policy: str,
        start_offset_ms: float,
        drain_s: float,
        discovery_timeout_s: float,
        static_mode: bool,
        static_subscriptions: dict[str, list[tuple[str, str]]] | None,
        extra_rmw_env: dict[str, str] | None,
        results_dir_container: str,
        start_wait_timeout_s: float,
        rmw_implementation: str = "rmw_fleetqox_cpp",
        discovery_mode: str = "default",
    ) -> None:
        docker("exec", self.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        self._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(self.endpoints))]
        self._start_file = f"{results_dir_container}/start"
        for i, endpoint in enumerate(self.endpoints):
            peers = ",".join(
                f"{self.ips[other]}:{RMW_PORT}" for other in self.endpoints if other != endpoint
            )
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            if rmw_implementation == "rmw_fleetqox_cpp":
                static_subscription_entries = [
                    f"{self.ips[dst]}:{RMW_PORT}|0|{topic_for(dst, flow_class)}|"
                    f"{STATIC_SUBSCRIPTION_TYPE_NAME}"
                    for dst, flow_class in (static_subscriptions or {}).get(endpoint, [])
                ]
                env_prefix = (
                    f"RMW_IMPLEMENTATION=rmw_fleetqox_cpp FLEETQOX_RMW_BIND=0.0.0.0:{RMW_PORT} "
                    f"FLEETQOX_RMW_PEERS={peers} "
                )
                if static_mode:
                    env_prefix += (
                        "FLEETQOX_RMW_PEER_POLICY=subscription_aware FLEETQOX_RMW_STATIC_MODE=1 "
                        f"FLEETQOX_RMW_STATIC_SUBSCRIPTIONS="
                        f"{shlex.quote(','.join(static_subscription_entries))} "
                    )
                env_prefix += "".join(
                    f"{key}={value} " for key, value in (extra_rmw_env or {}).items()
                )
                rmw_setup = f"source /work/{FLEETQOX_RMW_INSTALL}/setup.bash && export {env_prefix}"
            else:
                # Standard ROS2 RMW (rmw_cyclonedds_cpp, rmw_zenoh_cpp, ...)
                # -- already present in this base ROS2 image, no colcon
                # build needed. Discovers peers via its own protocol
                # (typically multicast for DDS; Zenoh's own discovery for
                # rmw_zenoh_cpp) instead of a static FLEETQOX_RMW_PEERS
                # list -- the per-station bridge/tap topology this probe
                # sets up already relays broadcast/multicast the same way
                # run_ns3_docker_wifi_tap_rmw_probe.py's confirmed working
                # for ARP, so this should reach every station the same way.
                env_prefix = f"RMW_IMPLEMENTATION={rmw_implementation} "
                if rmw_implementation == "rmw_zenoh_cpp":
                    # Multicast-based scouting for the router did NOT
                    # converge reliably in this topology (confirmed: even
                    # small-scale runs failed with the default config) --
                    # point every non-router endpoint at the router's
                    # known address explicitly instead, same "sidestep
                    # unreliable discovery with static config" approach
                    # already used for rmw_fleetqox_cpp's static mode.
                    # Skipped for the router's own container (endpoint 0
                    # == control_station -- see start_zenoh_router()) so
                    # it doesn't try to connect to itself.
                    if i != 0:
                        session_config = (
                            '{ connect: { endpoints: ["'
                            + self.zenoh_router_endpoint()
                            + '"] } }'
                        )
                        session_config_path = f"/tmp/zenoh_session_config_{i}.json5"
                        docker(
                            "exec", self.endpoint_container_names[i], "bash", "-lc",
                            f"echo {shlex.quote(session_config)} > {session_config_path}",
                        )
                        env_prefix += f"ZENOH_SESSION_CONFIG_URI={session_config_path} "
                if rmw_implementation == "rmw_cyclonedds_cpp" and discovery_mode == "static_peers":
                    # Same "sidestep unreliable multicast discovery with a
                    # static config" approach as rmw_fleetqox_cpp's static
                    # mode and Zenoh's router+session-config above --
                    # needed for a FAIR comparison (see
                    # docs/AUDIT_ACCEPTANCE_TRACKING.md "so sánh cùng mode
                    # discovery"): the earlier CycloneDDS/FastDDS baselines
                    # ran with completely default multicast SPDP discovery
                    # while FleetRMW and Zenoh both already got a static-
                    # config variant, which isn't apples-to-apples.
                    # AllowMulticast=false forces SPDP to rely SOLELY on
                    # the explicit unicast Peers list below -- every
                    # endpoint lists every OTHER endpoint's IP (including
                    # itself is harmless, CycloneDDS just ignores a peer
                    # that resolves to its own address).
                    peer_xml = "".join(
                        f'<Peer address="{self.ips[other]}"/>'
                        for other in self.endpoints
                        if other != endpoint
                    )
                    cyclonedds_config = (
                        '<?xml version="1.0" encoding="UTF-8" ?>'
                        '<CycloneDDS xmlns="https://cdds.io/config">'
                        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
                        f"<Discovery><Peers>{peer_xml}</Peers>"
                        # Fixed 0, NOT "auto" -- confirmed via a real run
                        # that "auto" causes TOTAL cross-participant
                        # isolation (every endpoint only ever received its
                        # own beacon loopback, 0 messages from anyone else,
                        # tx=1066/rx=0 -- not a scale/capacity issue, a
                        # config bug). A bare "address=<ip>" Peer entry
                        # (no port) tells CycloneDDS to assume that peer is
                        # listening at participant-index-0's SPDP port; if
                        # each container's own participant also resolved
                        # "auto" to index 0 that assumption should hold, but
                        # empirically it didn't -- pinning every container
                        # to the SAME explicit index removes the ambiguity
                        # "auto" left open. See
                        # docs/AUDIT_ACCEPTANCE_TRACKING.md "CycloneDDS
                        # static-peers total isolation bug".
                        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
                        "</Domain></CycloneDDS>"
                    )
                    cyclonedds_config_path = f"/tmp/cyclonedds_static_peers_{i}.xml"
                    docker(
                        "exec", self.endpoint_container_names[i], "bash", "-lc",
                        f"echo {shlex.quote(cyclonedds_config)} > {cyclonedds_config_path}",
                    )
                    env_prefix += f"CYCLONEDDS_URI={cyclonedds_config_path} "
                if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
                    # Fast DDS's own static-config answer to multicast
                    # discovery -- a separate "fastdds discovery" server
                    # process (see start_fastdds_discovery_server()) that
                    # every client points at explicitly via
                    # ROS_DISCOVERY_SERVER, instead of Simple Discovery
                    # Protocol's default multicast announcements. Set for
                    # EVERY endpoint including control_station itself --
                    # the server is a separate OS process, not a ROS 2
                    # node, so control_station's own endpoint process
                    # still needs this env var to use it rather than
                    # falling back to default multicast discovery.
                    env_prefix += f"ROS_DISCOVERY_SERVER={self.fastdds_discovery_server_endpoint()} "
                env_prefix += "".join(
                    f"{key}={value} " for key, value in (extra_rmw_env or {}).items()
                )
                rmw_setup = f"export {env_prefix}"
            # Same ready/start double-gate as run_ns3_docker_wifi_tap_rmw_probe.py's
            # build_shell_script -- every endpoint sets its own wall-clock
            # "t=0" right after its own discovery finishes, so without a
            # shared gate, endpoints that discover at different real times
            # would replay the trace against different origins. All
            # containers mount the SAME host directory at /work, so a
            # plain shared file under results_dir_container is visible
            # across every container -- no extra IPC mechanism needed.
            # expected-peer-count only for standard RMWs -- fleetqox's
            # static mode has no discovery step by design (see
            # FLEETQOX_RMW_STATIC_MODE above), so forcing the beacon there
            # would add NEW traffic to an already-validated code path and
            # change results already committed to
            # docs/AUDIT_ACCEPTANCE_TRACKING.md for no benefit (its
            # discovery_convergence_s is definitionally ~0 by construction,
            # not something that needs measuring).
            expected_peer_count = 0 if rmw_implementation == "rmw_fleetqox_cpp" else len(self.endpoints) - 1
            # Static mode has no discovery step by design -- skip the
            # get_subscription_count()-based fallback loop entirely rather
            # than let it silently burn the full --discovery-timeout-s
            # (FleetRMW's custom transport doesn't populate that API
            # meaningfully, so the loop never broke out early; see
            # docs/AUDIT_ACCEPTANCE_TRACKING.md "FleetRMW N/A" for the
            # ~15.1s artifact this replaces with a real near-zero number).
            skip_discovery_wait_flag = " --skip-discovery-wait" if static_mode else ""
            inner = (
                "source /opt/ros/jazzy/setup.bash && "
                f"{rmw_setup}&& "
                f"python3 /work/scripts/fleetqox_rmw_trace_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} "
                f"--endpoint={shlex.quote(endpoint)} "
                f"--policy={shlex.quote(policy)} "
                f"--start-offset-ms={start_offset_ms:.12g} "
                f"--drain-s={drain_s:.12g} "
                f"--discovery-timeout-s={discovery_timeout_s:.12g} "
                f"--start-wait-timeout-s={start_wait_timeout_s} "
                f"--expected-peer-count={expected_peer_count}"
                f"{skip_discovery_wait_flag} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{self._ready_files[i]} "
                f"--start-file=/work/{self._start_file}"
            )
            cmd = f"{inner} > /work/{log_file} 2>&1"
            docker("exec", "-d", self.endpoint_container_names[i], "bash", "-lc", cmd)

    def launch_coordination_endpoints(
        self,
        *,
        num_crossings: int,
        crossing_duration_ms: float,
        reply_timeout_s: float,
        defer_release_timeout_s: float = 8.0,
        seed: int,
        start_offset_ms: float,
        discovery_timeout_s: float,
        start_wait_timeout_s: float,
        scenario_timeout_s: float,
        results_dir_container: str,
        rmw_implementation: str = "rmw_fleetqox_cpp",
        discovery_mode: str = "default",
        extra_rmw_env: dict[str, str] | None = None,
        launch_order: list[int] | None = None,
    ) -> None:
        """Bảng VI ("Chỉ số điều phối và hoàn thành nhiệm vụ") launcher --
        runs fleetqox_coordination_endpoint.py (the Ricart-Agrawala zone-
        mutex simulation, see that file's module docstring) instead of
        fleetqox_rmw_trace_endpoint.py, but reuses the EXACT SAME RMW
        environment setup (peers list, static-subscription config for
        FleetRMW, CycloneDDS static-peers XML, Fast DDS discovery-server,
        Zenoh router session config) as launch_endpoints() -- the network/
        RMW plumbing is identical, only the application workload differs.

        launch_order: a permutation of range(len(self.endpoints))
        controlling the ORDER `docker exec -d` calls fire in, for
        diagnosing whether a stuck endpoint is tied to launch-ORDER
        (whichever one starts last) vs. that specific endpoint's own
        name/IP -- see docs/AUDIT_ACCEPTANCE_TRACKING.md "endpoint cuối
        cùng bị cô lập". Every index/file/container-name association
        stays keyed by the endpoint's ORIGINAL position in self.endpoints
        (self.endpoint_container_names[i], result_i.json, ready_i, etc.)
        regardless of this launch order -- only the wall-clock SEQUENCE
        of the docker exec calls themselves changes. None (default)
        launches in the normal 0..N-1 order.
        """
        docker("exec", self.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        self._ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(self.endpoints))]
        self._start_file = f"{results_dir_container}/start"
        order = launch_order if launch_order is not None else list(range(len(self.endpoints)))
        for i in order:
            endpoint = self.endpoints[i]
            peers_env = ",".join(other for other in self.endpoints if other != endpoint)
            rmw_peers = ",".join(
                f"{self.ips[other]}:{RMW_PORT}" for other in self.endpoints if other != endpoint
            )
            result_json = f"{results_dir_container}/result_{i}.json"
            log_file = f"{results_dir_container}/endpoint_{i}.log"
            if rmw_implementation == "rmw_fleetqox_cpp":
                # STATIC_MODE=1 without PEER_POLICY (leaving it at its own
                # default, "all") -- confirmed via a real run that omitting
                # STATIC_MODE entirely fails almost completely: without it,
                # rmw_pubsub.cpp starts a background "graph renewal" thread
                # that periodically sends heartbeat/advertisement traffic
                # to converge a real discovery graph (see
                # ensure_pubsub_graph_renewal_thread()'s own comment) --
                # the SAME kind of slow, periodic convergence as DDS's own
                # SPDP, just via a different wire format. Since this
                # endpoint script passes --skip-discovery-wait (coordination
                # traffic needs to start on the shared gate, not wait out an
                # arbitrary timeout), messages sent before that background
                # graph converges were silently going nowhere -- a real
                # 2-endpoint smoke test with STATIC_MODE unset got 100%
                # forced-entry crossings (zero successful mutex acquisitions
                # in 60s). STATIC_MODE=1 skips starting that thread entirely
                # and treats FLEETQOX_RMW_PEERS as already fully connected,
                # matching what makes FleetRMW's "default" mode reliable
                # everywhere else in this investigation. peer_policy_'s own
                # default ("all" -- broadcast every frame to every known
                # peer, no subscription registry needed) is exactly the
                # broadcast-to-everyone shape this scenario wants, so
                # subscription_aware mode/a static subscriptions map isn't
                # needed here the way it is for the trace-replay endpoint.
                env_prefix = (
                    f"RMW_IMPLEMENTATION=rmw_fleetqox_cpp FLEETQOX_RMW_BIND=0.0.0.0:{RMW_PORT} "
                    f"FLEETQOX_RMW_PEERS={rmw_peers} FLEETQOX_RMW_STATIC_MODE=1 "
                )
                env_prefix += "".join(
                    f"{key}={value} " for key, value in (extra_rmw_env or {}).items()
                )
                rmw_setup = f"source /work/{FLEETQOX_RMW_INSTALL}/setup.bash && export {env_prefix}"
            else:
                env_prefix = f"RMW_IMPLEMENTATION={rmw_implementation} "
                if rmw_implementation == "rmw_zenoh_cpp":
                    if i != 0:
                        session_config = (
                            '{ connect: { endpoints: ["' + self.zenoh_router_endpoint() + '"] } }'
                        )
                        session_config_path = f"/tmp/zenoh_session_config_coord_{i}.json5"
                        docker(
                            "exec", self.endpoint_container_names[i], "bash", "-lc",
                            f"echo {shlex.quote(session_config)} > {session_config_path}",
                        )
                        env_prefix += f"ZENOH_SESSION_CONFIG_URI={session_config_path} "
                if rmw_implementation == "rmw_cyclonedds_cpp" and discovery_mode == "static_peers":
                    peer_xml = "".join(
                        f'<Peer address="{self.ips[other]}"/>'
                        for other in self.endpoints
                        if other != endpoint
                    )
                    cyclonedds_config = (
                        '<?xml version="1.0" encoding="UTF-8" ?>'
                        '<CycloneDDS xmlns="https://cdds.io/config">'
                        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
                        f"<Discovery><Peers>{peer_xml}</Peers>"
                        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
                        "</Domain></CycloneDDS>"
                    )
                    cyclonedds_config_path = f"/tmp/cyclonedds_static_peers_coord_{i}.xml"
                    docker(
                        "exec", self.endpoint_container_names[i], "bash", "-lc",
                        f"echo {shlex.quote(cyclonedds_config)} > {cyclonedds_config_path}",
                    )
                    env_prefix += f"CYCLONEDDS_URI={cyclonedds_config_path} "
                if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
                    env_prefix += f"ROS_DISCOVERY_SERVER={self.fastdds_discovery_server_endpoint()} "
                env_prefix += "".join(
                    f"{key}={value} " for key, value in (extra_rmw_env or {}).items()
                )
                rmw_setup = f"export {env_prefix}"
            expected_peer_count = 0 if rmw_implementation == "rmw_fleetqox_cpp" else len(self.endpoints) - 1
            skip_discovery_wait_flag = " --skip-discovery-wait" if rmw_implementation == "rmw_fleetqox_cpp" else ""
            inner = (
                "source /opt/ros/jazzy/setup.bash && "
                f"{rmw_setup}&& "
                f"python3 /work/scripts/fleetqox_coordination_endpoint.py "
                f"--endpoint={shlex.quote(endpoint)} "
                f"--peers={shlex.quote(peers_env)} "
                f"--num-crossings={num_crossings} "
                f"--crossing-duration-ms={crossing_duration_ms:.12g} "
                f"--reply-timeout-s={reply_timeout_s:.12g} "
                f"--defer-release-timeout-s={defer_release_timeout_s:.12g} "
                f"--seed={seed} "
                f"--start-offset-ms={start_offset_ms:.12g} "
                f"--discovery-timeout-s={discovery_timeout_s:.12g} "
                f"--start-wait-timeout-s={start_wait_timeout_s} "
                f"--scenario-timeout-s={scenario_timeout_s:.12g} "
                f"--expected-peer-count={expected_peer_count}"
                f"{skip_discovery_wait_flag} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{self._ready_files[i]} "
                f"--start-file=/work/{self._start_file}"
            )
            cmd = f"{inner} > /work/{log_file} 2>&1"
            docker("exec", "-d", self.endpoint_container_names[i], "bash", "-lc", cmd)

    def wait_for_ready_then_start(self, ready_deadline_s: float) -> None:
        """Poll (via the rigger, which already has /work mounted) until
        every endpoint's ready-file exists, then touch the shared start
        file to release them all together."""
        deadline = time.monotonic() + ready_deadline_s
        checks = " && ".join(f"[ -f /work/{f} ]" for f in self._ready_files)
        while time.monotonic() < deadline:
            result = rigger_run(self.rigger_name, f"{checks} && echo ALL_READY", check=False)
            if "ALL_READY" in result.stdout:
                rigger_run(self.rigger_name, f"touch /work/{self._start_file}")
                return
            time.sleep(0.2)
        missing = []
        for f in self._ready_files:
            check = docker("exec", self.rigger_name, "test", "-f", f"/work/{f}", check=False)
            if check.returncode != 0:
                missing.append(f)
        raise TimeoutError(
            f"timed out waiting for every endpoint to become ready "
            f"(missing: {missing}) after {ready_deadline_s}s"
        )

    def wait_for_completion(self, timeout_s: float, results_dir_container: str) -> None:
        """Poll for every endpoint's --summary-json file to appear, via
        the rigger (already has /work mounted -- one docker exec per poll
        instead of one per endpoint per poll). NOT a `pgrep -f
        fleetqox_rmw_trace_endpoint.py`-based check -- that was tried
        first and is a real, confirmed pgrep footgun: the CHECKING
        command itself (`bash -lc "pgrep -f fleetqox_rmw_trace_endpoint.py
        ..."`) contains that exact string in its own argv, so `pgrep -f`
        matches its own invoking shell every single time regardless of
        whether the real target process is still running -- reproduced
        directly against a container running something completely
        unrelated (a 2-second sleep) and confirmed it still printed
        "RUNNING". Checking for the actual completion artifact (the
        summary JSON every endpoint writes as its last action) sidesteps
        process-matching entirely.
        """
        result_files = [
            f"/work/{results_dir_container}/result_{i}.json" for i in range(len(self.endpoints))
        ]
        deadline = time.monotonic() + timeout_s
        checks = " && ".join(f"[ -s {f} ]" for f in result_files)
        while time.monotonic() < deadline:
            result = rigger_run(self.rigger_name, f"{checks} && echo ALL_DONE", check=False)
            if "ALL_DONE" in result.stdout:
                return
            time.sleep(1.0)
        missing = []
        for f in result_files:
            check = docker("exec", self.rigger_name, "test", "-s", f, check=False)
            if check.returncode != 0:
                missing.append(f)
        raise TimeoutError(f"endpoints still running after {timeout_s}s (missing: {missing})")

    def collect_results(self, results_dir_container: str) -> dict[str, Any]:
        endpoint_results: dict[str, Any] = {}
        for i, endpoint in enumerate(self.endpoints):
            result_json = f"{results_dir_container}/result_{i}.json"
            local_path = ROOT / result_json
            endpoint_results[endpoint] = (
                json.loads(local_path.read_text()) if local_path.exists() else None
            )
        return endpoint_results

    def ns3_log(self, log_path: str = "/tmp/ns3.log") -> str:
        result = docker("exec", self.ns3sim_name, "cat", log_path, check=False)
        return result.stdout

    def tap_byte_counter(self, iface: str = "ftap0") -> int:
        """RX+TX byte counter for one station's tap device inside ns3sim,
        read straight from `ip -s link show` -- no tcpdump/pcap needed.
        Used to measure discovery_bytes as a simple before/after delta
        bracketing wait_for_ready_then_start() (see run_probe()): this is
        cheaper and more robust than parsing a pcap capture, at the cost of
        only covering control_station's tap (ftap0 == endpoints[0]), same
        representative-station scope as the packet-size pcap measurements
        (docs/AUDIT_ACCEPTANCE_TRACKING.md "đo thật kích thước gói"). Counts
        whatever crosses that tap during the window, including any pre-app
        ARP/ND noise -- an honest inclusion, since that traffic is itself
        part of the real control-plane cost paid before data starts
        flowing, not an artifact to filter out."""
        result = docker("exec", self.ns3sim_name, "bash", "-lc", f"ip -s link show {iface}", check=False)
        rx_bytes = tx_bytes = 0
        lines = result.stdout.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("RX:") and idx + 1 < len(lines):
                rx_bytes = int(lines[idx + 1].split()[0])
            elif stripped.startswith("TX:") and idx + 1 < len(lines):
                tx_bytes = int(lines[idx + 1].split()[0])
        return rx_bytes + tx_bytes

    def sample_resource_usage(self) -> dict[str, dict[str, float]]:
        """One-shot `docker stats --no-stream` snapshot of CPU%/RSS for
        EVERY endpoint container at once (Bảng IV's "CPU (%)"/"RSS (MB)"
        columns) -- container-level, not per-process, since this
        architecture is already 1 container == 1 endpoint, so a
        container's total footprint IS that endpoint's footprint (no
        separate process to isolate inside it the way a shared-container
        harness would need). `--no-stream` makes docker itself take one
        quick internal before/after CPU-time sample rather than requiring
        this method to bracket two calls itself. Call mid-run (see
        run_probe()) rather than after the process has gone idle in the
        drain phase, or CPU% would read near-zero and understate real
        load."""
        result = docker(
            "stats", *self.endpoint_container_names, "--no-stream",
            "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
            check=False,
        )
        usage: dict[str, dict[str, float]] = {}
        name_to_endpoint = dict(zip(self.endpoint_container_names, self.endpoints))
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            container_name, cpu_str, mem_str = parts
            endpoint = name_to_endpoint.get(container_name)
            if endpoint is None:
                continue
            try:
                cpu_pct = float(cpu_str.rstrip("%"))
                rss_mb = parse_docker_mem_usage_mb(mem_str)
            except ValueError:
                continue
            usage[endpoint] = {"cpu_pct": cpu_pct, "rss_mb": rss_mb}
        return usage

    def teardown(self) -> None:
        docker(
            "rm", "-f", self.rigger_name, self.ns3sim_name, *self.endpoint_container_names,
            check=False,
        )


def run_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    sim_duration_s: float,
    start_offset_ms: float = 2000.0,
    drain_s: float = 10.0,
    discovery_timeout_s: float = 15.0,
    static_mode: bool = True,
    extra_rmw_env: dict[str, str] | None = None,
    ns3_seed: int = 1,
    ns3_run: int = 1,
    layout: str = "circle",
    circle_radius: float = 7.5,
    path_loss_exponent: float = 2.7,
    tx_power_dbm: float = 15.0,
    rx_sensitivity_dbm: float = -82.0,
    mobility_speed: float = 0.0,
    num_aps: int = 1,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    trace_path = output_dir / f"trace_ref_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"ns3_docker_container_fleet_probe_{num_robots}robot",
        robots=num_robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        policies=(policy,),
        include_non_sent=False,
        merge_control_station=True,
    )
    packet_rows = write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"

    endpoints = endpoint_list(num_robots)
    # static_mode/FLEETQOX_RMW_STATIC_SUBSCRIPTIONS is a rmw_fleetqox_cpp-
    # specific mechanism -- meaningless (and not read) by standard DDS/
    # Zenoh RMWs, which discover peers via their own protocol instead.
    effective_static_mode = static_mode and rmw_implementation == "rmw_fleetqox_cpp"
    static_subscriptions = (
        build_static_subscriptions(trace_path, policy, endpoints) if effective_static_mode else None
    )
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    ns3_log_text = ""
    discovery_bytes_ftap0: int | None = None
    resource_usage: dict[str, dict[str, float]] = {}
    try:
        probe.start_containers()
        probe.build_ns3_binary()
        probe.wire_network()
        probe.start_ns3(
            sim_duration_s=sim_duration_s,
            num_aps=num_aps,
            layout=layout,
            circle_radius=circle_radius,
            path_loss_exponent=path_loss_exponent,
            tx_power_dbm=tx_power_dbm,
            rx_sensitivity_dbm=rx_sensitivity_dbm,
            mobility_speed=mobility_speed,
            ns3_seed=ns3_seed,
            ns3_run=ns3_run,
        )
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()
        # Snapshot BEFORE any endpoint launches -- discovery/control-plane
        # traffic starts as soon as launch_endpoints() spawns the RMW
        # processes, so this is the true zero point for discovery_bytes.
        discovery_bytes_before = probe.tap_byte_counter()
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy=policy,
            start_offset_ms=start_offset_ms,
            drain_s=drain_s,
            discovery_timeout_s=discovery_timeout_s,
            static_mode=effective_static_mode,
            static_subscriptions=static_subscriptions,
            extra_rmw_env=extra_rmw_env,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=start_wait_timeout_s,
            rmw_implementation=rmw_implementation,
            discovery_mode=discovery_mode,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        # Snapshot right as the shared start-gate releases -- by
        # definition every endpoint has finished its own discovery by this
        # point (that's what "ready" means here), so this delta is the
        # control-plane cost paid before any application data flows. Only
        # covers control_station's tap (ftap0) -- see tap_byte_counter().
        discovery_bytes_ftap0 = probe.tap_byte_counter() - discovery_bytes_before
        # Sample CPU%/RSS while endpoints are actively sending, not after
        # the drain phase once they've gone idle (which would read near-
        # zero CPU and understate real load). The real send window is
        # short -- it starts start_offset_ms after this gate releases and
        # spans roughly `seconds` of trace time -- NOT sim_duration_s
        # (that's just how long the background ns-3 process keeps running,
        # unrelated to how long the trace replay itself takes), so this
        # sleep targets the middle of that actual window instead of
        # scaling with sim_duration_s, which would otherwise add tens of
        # seconds of pure dead time to every run for no benefit.
        time.sleep(start_offset_ms / 1000.0 + max(seconds, 1) / 2.0)
        resource_usage = probe.sample_resource_usage()
        probe.wait_for_completion(
            timeout_s=sim_duration_s + drain_s + start_offset_ms / 1000.0 + 60.0,
            results_dir_container=results_dir_container,
        )
        endpoint_results = probe.collect_results(results_dir_container)
        ns3_log_text = probe.ns3_log()
    except Exception as exc:  # noqa: BLE001 -- report to caller, don't hide the traceback
        status = "failed"
        error_text = str(exc)
        try:
            ns3_log_text = probe.ns3_log()
        except Exception:  # noqa: BLE001
            pass
    finally:
        probe.teardown()

    discovery_convergence_samples_s = [
        result["discovery_convergence_s"]
        for result in endpoint_results.values()
        if result is not None and result.get("discovery_convergence_s") is not None
    ]
    cpu_samples = [v["cpu_pct"] for v in resource_usage.values()]
    rss_samples = [v["rss_mb"] for v in resource_usage.values()]

    return {
        "schema_version": "fleetqox.ns3_docker_container_fleet_probe.v1",
        "status": status,
        "error": error_text,
        "trace": str(trace_path.relative_to(ROOT)),
        "packet_rows": packet_rows,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "policy": policy,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
        "ns3_log": ns3_log_text,
        "latency_stats_ms": compute_latency_stats_ms(endpoint_results),
        "jitter_stale_repair_stats": compute_jitter_stale_repair_stats(endpoint_results),
        "discovery_bytes_ftap0": discovery_bytes_ftap0,
        # max, not mean: the paper's own definition ("đến khi graph đạt
        # trạng thái quan sát ổn định") is a whole-fleet property -- the
        # graph isn't stable until its SLOWEST endpoint converges, same
        # reasoning as wait_for_ready_then_start()'s all-endpoints gate.
        "discovery_convergence_max_s": (
            max(discovery_convergence_samples_s) if discovery_convergence_samples_s else None
        ),
        "resource_usage": resource_usage,
        "cpu_pct_mean": (sum(cpu_samples) / len(cpu_samples)) if cpu_samples else None,
        "rss_mb_mean": (sum(rss_samples) / len(rss_samples)) if rss_samples else None,
        "graph_join_failures": compute_graph_join_failures(endpoint_results),
    }


def run_coordination_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    seed: int,
    sim_duration_s: float = 60.0,
    num_crossings: int = 5,
    crossing_duration_ms: float = 300.0,
    reply_timeout_s: float = 5.0,
    defer_release_timeout_s: float = 8.0,
    scenario_timeout_s: float = 120.0,
    start_offset_ms: float = 2000.0,
    discovery_timeout_s: float = 15.0,
    ns3_seed: int = 1,
    ns3_run: int = 1,
    layout: str = "circle",
    circle_radius: float = 7.5,
    path_loss_exponent: float = 2.7,
    tx_power_dbm: float = 15.0,
    rx_sensitivity_dbm: float = -82.0,
    mobility_speed: float = 0.0,
    num_aps: int = 1,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
    extra_rmw_env: dict[str, str] | None = None,
    launch_order: list[int] | None = None,
) -> dict[str, Any]:
    """Bảng VI ("Chỉ số điều phối và hoàn thành nhiệm vụ") -- see
    fleetqox_coordination_endpoint.py's module docstring for the
    Ricart-Agrawala zone-mutex simulation this runs, and
    compute_coordination_metrics()'s docstring for how the 4 columns are
    derived from it. Uses the SAME wifi network setup as run_probe()
    (wire_network()/start_ns3(), the paper's main reference profile,
    matching Bảng IV's own default) -- pass a different image/wire-up if
    Bảng VI ever needs to be measured under LAN/5G too, following the
    same pattern run_lan_probe()/run_nr_probe() already establish.

    No trace CSV here (unlike run_probe()) -- this scenario's workload
    is fully self-contained inside fleetqox_coordination_endpoint.py,
    parameterized only by --num-crossings/--crossing-duration-ms/
    --reply-timeout-s, not by a pre-generated event schedule."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    endpoints = endpoint_list(num_robots)
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    ns3_log_text = ""
    try:
        probe.start_containers()
        probe.build_ns3_binary()
        probe.wire_network()
        probe.start_ns3(
            sim_duration_s=sim_duration_s,
            num_aps=num_aps,
            layout=layout,
            circle_radius=circle_radius,
            path_loss_exponent=path_loss_exponent,
            tx_power_dbm=tx_power_dbm,
            rx_sensitivity_dbm=rx_sensitivity_dbm,
            mobility_speed=mobility_speed,
            ns3_seed=ns3_seed,
            ns3_run=ns3_run,
        )
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()
        probe.launch_coordination_endpoints(
            num_crossings=num_crossings,
            crossing_duration_ms=crossing_duration_ms,
            reply_timeout_s=reply_timeout_s,
            defer_release_timeout_s=defer_release_timeout_s,
            seed=seed,
            start_offset_ms=start_offset_ms,
            discovery_timeout_s=discovery_timeout_s,
            start_wait_timeout_s=start_wait_timeout_s,
            scenario_timeout_s=scenario_timeout_s,
            results_dir_container=results_dir_container,
            rmw_implementation=rmw_implementation,
            discovery_mode=discovery_mode,
            extra_rmw_env=extra_rmw_env,
            launch_order=launch_order,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        probe.wait_for_completion(
            timeout_s=start_offset_ms / 1000.0 + scenario_timeout_s + 60.0,
            results_dir_container=results_dir_container,
        )
        endpoint_results = probe.collect_results(results_dir_container)
        ns3_log_text = probe.ns3_log()
    except Exception as exc:  # noqa: BLE001 -- report to caller, don't hide the traceback
        status = "failed"
        error_text = str(exc)
        try:
            ns3_log_text = probe.ns3_log()
        except Exception:  # noqa: BLE001
            pass
    finally:
        probe.teardown()

    discovery_convergence_samples_s = [
        result["discovery_convergence_s"]
        for result in endpoint_results.values()
        if result is not None and result.get("discovery_convergence_s") is not None
    ]

    return {
        "schema_version": "fleetqox.ns3_docker_container_coordination_probe.v1",
        "status": status,
        "error": error_text,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
        "ns3_log": ns3_log_text,
        "coordination_metrics": compute_coordination_metrics(endpoint_results),
        "discovery_convergence_max_s": (
            max(discovery_convergence_samples_s) if discovery_convergence_samples_s else None
        ),
        "graph_join_failures": compute_graph_join_failures(endpoint_results),
    }


def run_lan_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    start_offset_ms: float = 2000.0,
    drain_s: float = 10.0,
    discovery_timeout_s: float = 15.0,
    static_mode: bool = True,
    extra_rmw_env: dict[str, str] | None = None,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
) -> dict[str, Any]:
    """Bảng V's "LAN" network profile -- see wire_network_lan()'s
    docstring for what this represents (an ideal switched network, no
    wifi/cellular impairment at all). Deliberately a SEPARATE function
    from run_probe() rather than a mode flag threaded through it: LAN
    has no ns-3 process, no sim_duration_s/layout/circle_radius/
    path_loss/tx_power/mobility knobs (none of those concepts apply
    without a wifi PHY simulation), and no discovery_bytes_ftap0/ns3_log
    (no tap device exists in this profile) -- forcing all of run_probe()'s
    wifi-specific parameters to be silently ignored for this profile
    would be more confusing than a parallel, deliberately smaller
    function that only exposes what LAN actually has."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    trace_path = output_dir / f"trace_ref_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"ns3_docker_container_fleet_probe_lan_{num_robots}robot",
        robots=num_robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        policies=(policy,),
        include_non_sent=False,
        merge_control_station=True,
    )
    packet_rows = write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"

    endpoints = endpoint_list(num_robots)
    effective_static_mode = static_mode and rmw_implementation == "rmw_fleetqox_cpp"
    static_subscriptions = (
        build_static_subscriptions(trace_path, policy, endpoints) if effective_static_mode else None
    )
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    resource_usage: dict[str, dict[str, float]] = {}
    try:
        probe.start_containers()
        probe.wire_network_lan()
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy=policy,
            start_offset_ms=start_offset_ms,
            drain_s=drain_s,
            discovery_timeout_s=discovery_timeout_s,
            static_mode=effective_static_mode,
            static_subscriptions=static_subscriptions,
            extra_rmw_env=extra_rmw_env,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=start_wait_timeout_s,
            rmw_implementation=rmw_implementation,
            discovery_mode=discovery_mode,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        # No sim_duration_s to time a mid-run sample against here (no
        # ns-3 process) -- the real send window is the same
        # start_offset_ms + seconds/2 as run_probe() uses, timed off the
        # trace itself rather than any wifi-simulation runtime.
        time.sleep(start_offset_ms / 1000.0 + max(seconds, 1) / 2.0)
        resource_usage = probe.sample_resource_usage()
        probe.wait_for_completion(
            timeout_s=drain_s + start_offset_ms / 1000.0 + seconds + 60.0,
            results_dir_container=results_dir_container,
        )
        endpoint_results = probe.collect_results(results_dir_container)
    except Exception as exc:  # noqa: BLE001 -- report to caller, don't hide the traceback
        status = "failed"
        error_text = str(exc)
    finally:
        probe.teardown()

    discovery_convergence_samples_s = [
        result["discovery_convergence_s"]
        for result in endpoint_results.values()
        if result is not None and result.get("discovery_convergence_s") is not None
    ]
    cpu_samples = [v["cpu_pct"] for v in resource_usage.values()]
    rss_samples = [v["rss_mb"] for v in resource_usage.values()]

    return {
        "schema_version": "fleetqox.ns3_docker_container_fleet_probe_lan.v1",
        "network_profile": "LAN",
        "status": status,
        "error": error_text,
        "trace": str(trace_path.relative_to(ROOT)),
        "packet_rows": packet_rows,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "policy": policy,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
        "latency_stats_ms": compute_latency_stats_ms(endpoint_results),
        "jitter_stale_repair_stats": compute_jitter_stale_repair_stats(endpoint_results),
        "discovery_convergence_max_s": (
            max(discovery_convergence_samples_s) if discovery_convergence_samples_s else None
        ),
        "resource_usage": resource_usage,
        "cpu_pct_mean": (sum(cpu_samples) / len(cpu_samples)) if cpu_samples else None,
        "rss_mb_mean": (sum(rss_samples) / len(rss_samples)) if rss_samples else None,
        "graph_join_failures": compute_graph_join_failures(endpoint_results),
    }


DEFAULT_NR_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy-nr"


def run_nr_probe(
    *,
    image: str = DEFAULT_NR_IMAGE,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    sim_duration_s: float = 30.0,
    start_offset_ms: float = 2000.0,
    drain_s: float = 10.0,
    discovery_timeout_s: float = 15.0,
    static_mode: bool = True,
    extra_rmw_env: dict[str, str] | None = None,
    ns3_seed: int = 1,
    ns3_run: int = 1,
    circle_radius: float = 50.0,
    numerology: int = 1,
    central_frequency: float = 3.5e9,
    bandwidth: float = 20e6,
    gnb_tx_power_dbm: float = 35.0,
    ue_tx_power_dbm: float = 23.0,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
) -> dict[str, Any]:
    """Bảng V's "5G" network profile -- see fleetqox_trace_replay_nr.cc's
    module docstring for the full ghost-node architecture rationale. Uses
    the SEPARATE jazzy-nr image (DEFAULT_NR_IMAGE), not the base :jazzy
    image every other profile uses -- only that image has the nr contrib
    module built in (see external/rmw-netem/Dockerfile.nr).

    Structurally closest to run_probe() (wifi) rather than run_lan_probe():
    like wifi, this profile has a real ns-3 process, a tap device per
    endpoint, and an ns3_log -- but the network wiring is split into TWO
    phases (wire_network_nr_l2() then, only after ns-3 has assigned and
    printed real UE overlay IPs, finish_wire_network_nr()) instead of
    wifi's single wire_network() call before start_ns3(), because unlike
    wifi's scheme (IPs chosen up front by this script itself), each
    endpoint's real 5G address is assigned INTERNALLY by ns-3's EPC
    helper and only known once ns-3 actually starts running."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    trace_path = output_dir / f"trace_ref_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"ns3_docker_container_fleet_probe_nr_{num_robots}robot",
        robots=num_robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        policies=(policy,),
        include_non_sent=False,
        merge_control_station=True,
    )
    packet_rows = write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"

    endpoints = endpoint_list(num_robots)
    effective_static_mode = static_mode and rmw_implementation == "rmw_fleetqox_cpp"
    static_subscriptions = (
        build_static_subscriptions(trace_path, policy, endpoints) if effective_static_mode else None
    )
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = ReferenceTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    ns3_log_text = ""
    resource_usage: dict[str, dict[str, float]] = {}
    nr_mapping: dict[str, dict[str, str]] = {}
    try:
        probe.start_containers()
        probe.build_ns3_nr_binary()
        probe.wire_network_nr_l2()
        nr_mapping = probe.start_ns3_nr(
            sim_duration_s=sim_duration_s,
            circle_radius=circle_radius,
            numerology=numerology,
            central_frequency=central_frequency,
            bandwidth=bandwidth,
            gnb_tx_power_dbm=gnb_tx_power_dbm,
            ue_tx_power_dbm=ue_tx_power_dbm,
            ns3_seed=ns3_seed,
            ns3_run=ns3_run,
        )
        probe.finish_wire_network_nr(nr_mapping)
        # From here on, probe.ips holds each endpoint's REAL EPC overlay
        # IP (finish_wire_network_nr() overwrote it) -- every downstream
        # call below reads probe.ips exactly like the wifi/LAN profiles
        # do, no NR-specific branching needed in launch_endpoints().
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy=policy,
            start_offset_ms=start_offset_ms,
            drain_s=drain_s,
            discovery_timeout_s=discovery_timeout_s,
            static_mode=effective_static_mode,
            static_subscriptions=static_subscriptions,
            extra_rmw_env=extra_rmw_env,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=start_wait_timeout_s,
            rmw_implementation=rmw_implementation,
            discovery_mode=discovery_mode,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        # Same mid-send-window timing rationale as run_probe()/
        # run_lan_probe() -- start_offset_ms + seconds/2 after the shared
        # start gate, NOT scaled by sim_duration_s (unrelated to how long
        # the actual trace replay takes).
        time.sleep(start_offset_ms / 1000.0 + max(seconds, 1) / 2.0)
        resource_usage = probe.sample_resource_usage()
        probe.wait_for_completion(
            timeout_s=sim_duration_s + drain_s + start_offset_ms / 1000.0 + 60.0,
            results_dir_container=results_dir_container,
        )
        endpoint_results = probe.collect_results(results_dir_container)
        ns3_log_text = probe.ns3_log(log_path="/tmp/ns3_nr.log")
    except Exception as exc:  # noqa: BLE001 -- report to caller, don't hide the traceback
        status = "failed"
        error_text = str(exc)
        try:
            ns3_log_text = probe.ns3_log(log_path="/tmp/ns3_nr.log")
        except Exception:  # noqa: BLE001
            pass
    finally:
        probe.teardown()

    discovery_convergence_samples_s = [
        result["discovery_convergence_s"]
        for result in endpoint_results.values()
        if result is not None and result.get("discovery_convergence_s") is not None
    ]
    cpu_samples = [v["cpu_pct"] for v in resource_usage.values()]
    rss_samples = [v["rss_mb"] for v in resource_usage.values()]

    return {
        "schema_version": "fleetqox.ns3_docker_container_fleet_probe_nr.v1",
        "network_profile": "5G",
        "status": status,
        "error": error_text,
        "trace": str(trace_path.relative_to(ROOT)),
        "packet_rows": packet_rows,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "policy": policy,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
        "ns3_log": ns3_log_text,
        "nr_mapping": nr_mapping,
        "latency_stats_ms": compute_latency_stats_ms(endpoint_results),
        "jitter_stale_repair_stats": compute_jitter_stale_repair_stats(endpoint_results),
        "discovery_convergence_max_s": (
            max(discovery_convergence_samples_s) if discovery_convergence_samples_s else None
        ),
        "resource_usage": resource_usage,
        "cpu_pct_mean": (sum(cpu_samples) / len(cpu_samples)) if cpu_samples else None,
        "rss_mb_mean": (sum(rss_samples) / len(rss_samples)) if rss_samples else None,
        "graph_join_failures": compute_graph_join_failures(endpoint_results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--num-robots", type=int, default=16)
    parser.add_argument("--policy", default="fifo")
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--sim-duration-s", type=float, default=30.0)
    parser.add_argument("--ns3-seed", type=int, default=42)
    parser.add_argument("--ns3-run", type=int, default=1)
    parser.add_argument("--layout", choices=("circle", "grid"), default="circle")
    parser.add_argument("--circle-radius", type=float, default=7.5)
    parser.add_argument("--path-loss-exponent", type=float, default=2.7)
    parser.add_argument("--tx-power-dbm", type=float, default=15.0)
    parser.add_argument("--rx-sensitivity-dbm", type=float, default=-82.0)
    parser.add_argument("--mobility-speed", type=float, default=0.0)
    parser.add_argument(
        "--rmw-implementation",
        default="rmw_fleetqox_cpp",
        help="e.g. rmw_fleetqox_cpp (default, uses static_mode), rmw_cyclonedds_cpp, rmw_zenoh_cpp",
    )
    parser.add_argument(
        "--discovery-mode",
        default="default",
        choices=("default", "static_peers", "discovery_server"),
        help=(
            "default: each RMW's own out-of-the-box discovery (multicast "
            "SPDP for Cyclone/FastDDS, Zenoh's own scouting+router as "
            "already wired). static_peers: rmw_cyclonedds_cpp only -- "
            "disables multicast, uses an explicit unicast Peers list "
            "(CYCLONEDDS_URI), matching FleetRMW/Zenoh's own static-config "
            "treatment for a fair comparison. discovery_server: "
            "rmw_fastrtps_cpp only -- runs a `fastdds discovery` server on "
            "control_station and points every client at it via "
            "ROS_DISCOVERY_SERVER instead of default multicast discovery."
        ),
    )
    parser.add_argument("--summary-json", type=Path, default=None)
    args = parser.parse_args()

    summary = run_probe(
        image=args.image,
        output_dir=args.output_dir,
        num_robots=args.num_robots,
        policy=args.policy,
        seconds=args.seconds,
        seed=args.seed,
        sim_duration_s=args.sim_duration_s,
        ns3_seed=args.ns3_seed,
        ns3_run=args.ns3_run,
        layout=args.layout,
        circle_radius=args.circle_radius,
        path_loss_exponent=args.path_loss_exponent,
        tx_power_dbm=args.tx_power_dbm,
        rx_sensitivity_dbm=args.rx_sensitivity_dbm,
        mobility_speed=args.mobility_speed,
        rmw_implementation=args.rmw_implementation,
        discovery_mode=args.discovery_mode,
    )
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2))
    print(json.dumps({"status": summary["status"], "error": summary["error"]}))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
