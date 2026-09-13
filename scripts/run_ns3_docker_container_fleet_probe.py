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
