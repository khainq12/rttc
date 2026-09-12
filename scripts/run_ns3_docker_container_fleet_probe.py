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
            env_prefix += "".join(f"{key}={value} " for key, value in (extra_rmw_env or {}).items())
            # Same ready/start double-gate as run_ns3_docker_wifi_tap_rmw_probe.py's
            # build_shell_script -- every endpoint sets its own wall-clock
            # "t=0" right after its own discovery finishes, so without a
            # shared gate, endpoints that discover at different real times
            # would replay the trace against different origins. All
            # containers mount the SAME host directory at /work, so a
            # plain shared file under results_dir_container is visible
            # across every container -- no extra IPC mechanism needed.
            inner = (
                "source /opt/ros/jazzy/setup.bash && "
                f"source /work/{FLEETQOX_RMW_INSTALL}/setup.bash && "
                f"export {env_prefix}&& "
                f"python3 /work/scripts/fleetqox_rmw_trace_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} "
                f"--endpoint={shlex.quote(endpoint)} "
                f"--policy={shlex.quote(policy)} "
                f"--start-offset-ms={start_offset_ms:.12g} "
                f"--drain-s={drain_s:.12g} "
                f"--discovery-timeout-s={discovery_timeout_s:.12g} "
                f"--start-wait-timeout-s={start_wait_timeout_s} "
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
    static_subscriptions = (
        build_static_subscriptions(trace_path, policy, endpoints) if static_mode else None
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
        probe.launch_endpoints(
            trace_container_path=trace_container_path,
            policy=policy,
            start_offset_ms=start_offset_ms,
            drain_s=drain_s,
            discovery_timeout_s=discovery_timeout_s,
            static_mode=static_mode,
            static_subscriptions=static_subscriptions,
            extra_rmw_env=extra_rmw_env,
            results_dir_container=results_dir_container,
            start_wait_timeout_s=start_wait_timeout_s,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
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
    )
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(summary, indent=2))
    print(json.dumps({"status": summary["status"], "error": summary["error"]}))
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
