"""Bridge the REAL rmw_fleetqox_cpp transport through a simulated ns-3
802.11 wifi network, instead of Group 6's existing wifi-parity test (which
sends raw single-shot UDP with no recovery beyond the 802.11 MAC's own
retry limit -- see external/ns3/fleetqox_trace_replay.cc /
external/omnetpp/TraceDrivenUdpApp.cc). This is the ns-3 half of that
effort: external/ns3/fleetqox_trace_replay_tap.cc provides the simulated
topology bridged via TapBridge, and scripts/fleetqox_rmw_trace_endpoint.py
replays one endpoint's trace rows through the real transport in each
per-station network namespace this script sets up.

Everything runs inside ONE Docker container (not N, unlike the existing
netem probes) because the tap/bridge/netns tree ns-3's TapBridge needs to
attach to has to live in the same network-namespace hierarchy as the
process running ns-3 itself.

Per-station networking, so a real process's traffic is FORCED through the
simulated wifi channel instead of bypassing it via ordinary same-host
kernel routing (which would otherwise silently "deliver" same-subnet
traffic locally without ever touching ns-3):

    real process (its own netns, eth0) --veth pair-- bridge brI --- tapI (ns-3 TapBridge, UseLocal mode)

FIRST ATTEMPT -- nothing here has been run yet. Timing constants (how long
to wait for ns-3 to attach to the tap devices before starting real
processes, etc.) are best-effort guesses documented as such; expect to
need to tune them once this actually runs. Defaults to the smallest
possible scenario (1 robot => 4 total stations) rather than
production-scale robot counts, matching the "start minimal, escalate"
methodology already used for the fragment-repair investigation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402
from scripts.run_ros2_relay_rmw_netem_probe import (  # noqa: E402
    DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES,
)

DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
# Reuses the colcon install space run_ros2_relay_rmw_netem_probe.py's own
# SERIALIZED_RELAY_INSTALL builds/leaves behind under /work (volume-mounted,
# so it survives across --rm containers) -- rclpy and rmw_fleetqox_cpp
# aren't found by a plain `python3 ...` in this image without both this and
# /opt/ros/jazzy/setup.bash sourced first, confirmed by the first real run
# failing with ModuleNotFoundError: No module named 'rclpy'.
FLEETQOX_RMW_INSTALL = ".tmp_fleetrmw_matched_v2_install"
RMW_PORT = 9100
BASE_IP_PREFIX = "10.50.0."  # .0/.1 reserved; stations start at .2
# ns-3's TapBridge (Mode=UseLocal) needs the tap device to already exist
# and be up before it tries to attach; this is how long we wait after
# starting the ns-3 process before assuming it has attached to every tap
# and it's safe to start sending real traffic. UNVERIFIED GUESS -- if
# real runs show endpoints failing to reach each other early on, this is
# the first thing to increase.
NS3_ATTACH_WAIT_S = 3
# How long the outer shell script polls for every endpoint's --ready-file
# before giving up (see build_shell_script's READY_DEADLINE). Each
# endpoint's own --start-wait-timeout-s (how long IT waits for --start-file
# after touching its own --ready-file) must be at least this large plus
# margin -- otherwise a fast-discovering endpoint can time itself out
# before a slower sibling ever finishes discovery and releases the shared
# start gate. Confirmed as a real bug: with both timeouts equal to the
# endpoint script's old 15s discovery_timeout_s default (vs. this 30s
# ready-poll deadline), 2 of 4 endpoints crashed with "timed out waiting
# for data-plane start gate" despite the other 2 succeeding.
READY_DEADLINE_S = 30
START_WAIT_TIMEOUT_S = READY_DEADLINE_S + 30


def endpoint_list(num_robots: int) -> list[str]:
    endpoints = ["fleet_controller", "fleet_router", "operator_ui"]
    endpoints.extend(f"robot_{i:04d}" for i in range(num_robots))
    return endpoints


def _station_mac(index: int) -> str:
    """Deterministic MAC for station `index`, matching fleetqox_trace_replay_tap.cc's
    stationMacs formula EXACTLY (02:00:00:00:<index>) -- must be set on
    each netns's own eth0 too, since TapBridge's UseLocal mode does not
    copy the tap's real MAC onto the ns-3 WifiNetDevice it bridges (the
    device keeps whatever address ns-3 assigns it). Without this, a real
    process's ARP replies -- which Linux populates using its OWN
    interface's address, not anything ns-3-aware -- carry a MAC the AP's
    association table has never heard of and are silently dropped:
    confirmed by a real run where broadcast ARP requests reached the far
    station's tap but unicast replies never made it back. Giving the real
    interface and its simulated station the SAME address closes that gap.
    """
    return f"02:00:00:00:{(index >> 8) & 0xFF:02x}:{index & 0xFF:02x}"


def _container_path(path: Path) -> str:
    return f"/work/{path.relative_to(ROOT)}"


def build_shell_script(
    *,
    trace_container_path: str,
    endpoints: list[str],
    policy: str,
    num_robots: int,
    sim_duration_s: float,
    start_offset_ms: float,
    drain_s: float,
    results_dir_container: str,
    fragment_chunk_bytes: int = DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    udp_datagram_budget_bytes: int = DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES,
    graph_renew_interval_ms: int | None = None,
    num_aps: int = 1,
    isolate_controller: bool = False,
    subscription_aware: bool = False,
    discovery_timeout_s: float = 15.0,
    rmw_implementation: str = "rmw_fleetqox_cpp",
) -> str:
    ips = {endpoint: f"{BASE_IP_PREFIX}{i + 2}" for i, endpoint in enumerate(endpoints)}
    # ChatGPT-flagged bootstrap-feedback-loop hypothesis (see
    # docs/AUDIT_ACCEPTANCE_TRACKING.md "discovery-convergence experiment"):
    # subscription-aware routing's fail-open fallback (broadcast when no
    # subscriber is known YET) means a discovery timeout that's too short
    # for 19-endpoint wifi contention could itself be causing a self-
    # sustaining overload -- discovery doesn't converge in time -> every
    # publish stays a full broadcast -> more contention -> discovery
    # converges even later. Deriving the orchestrator's own ready/start
    # deadlines FROM discovery_timeout_s (instead of the fixed constants)
    # lets a much longer discovery_timeout_s actually be given the room to
    # matter, rather than being cut short by an unrelated outer timeout.
    # +15/+30 margins match the original fixed constants exactly at the
    # default discovery_timeout_s=15.0 (ready_deadline_s=30,
    # start_wait_timeout_s=60), so nothing changes for any existing call
    # site that doesn't pass a longer discovery_timeout_s explicitly.
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30

    lines: list[str] = [
        "set -e",
        f"mkdir -p {shlex.quote(results_dir_container)}",
    ]
    if rmw_implementation == "rmw_fleetqox_cpp":
        lines.append(
            f"test -f /work/{FLEETQOX_RMW_INSTALL}/setup.bash || "
            f"(echo 'missing {FLEETQOX_RMW_INSTALL}/setup.bash -- run "
            "scripts/run_ros2_relay_rmw_netem_probe.py once first to build "
            "the rmw_fleetqox_cpp colcon install this reuses' >&2 && exit 1)"
        )
    lines += [
        # Build the ns-3 tap-bridge program fresh every run, matching the
        # existing wifi-parity scripts' compile-on-each-invocation pattern
        # (ns3-tap-bridge is confirmed present in this image already, no
        # image rebuild needed -- see docs/AUDIT_ACCEPTANCE_TRACKING.md).
        (
            "g++ -std=c++17 external/ns3/fleetqox_trace_replay_tap.cc "
            "-o /tmp/fleetqox_tap_bridge "
            "$(pkg-config --cflags --libs ns3-core ns3-network ns3-mobility "
            "ns3-wifi ns3-tap-bridge)"
        ),
        # libns3-tap-bridge.so has the tap-creator helper's absolute path
        # baked in at whatever location it was built from (confirmed via
        # `strings` on the .so) rather than where it's actually installed
        # at runtime (/usr/libexec/ns3/ns3.<ver>-tap-creator) -- first
        # seen with the apt package's own build-time path
        # (/build/ns3-*/.../build/src/tap-bridge/...), and reconfirmed
        # with a DIFFERENT baked path once this image switched to
        # building ns-3 from source (/tmp/ns3-src/build/src/tap-bridge/...,
        # matching the Dockerfile's own build location, which gets
        # deleted after `cmake --install` to keep the image small) -- see
        # docs/AUDIT_ACCEPTANCE_TRACKING.md. Extract the baked-in path
        # dynamically via `strings` instead of hardcoding it, since it's
        # tied to wherever ns-3 happened to be built and has already
        # changed once.
        (
            "TAPCREATOR_REAL=$(find /usr -iname '*tap-creator*' -type f 2>/dev/null | head -1) && "
            "TAPCREATOR_SO=$(find /usr -iname 'libns3*tap-bridge*' 2>/dev/null | head -1) && "
            "TAPCREATOR_BAKED=$(strings \"$TAPCREATOR_SO\" 2>/dev/null | "
            "grep -E '/.*tap-creator$' | head -1) && "
            "if [ -n \"$TAPCREATOR_BAKED\" ] && [ ! -e \"$TAPCREATOR_BAKED\" ]; then "
            "mkdir -p \"$(dirname \"$TAPCREATOR_BAKED\")\" && "
            "ln -sf \"$TAPCREATOR_REAL\" \"$TAPCREATOR_BAKED\"; fi"
        ),
    ]

    for i, endpoint in enumerate(endpoints):
        lines.extend(
            [
                f"# --- station {i}: {endpoint} ---",
                f"ip link add name br{i} type bridge",
                f"ip link set br{i} up",
                f"ip tuntap add dev ftap{i} mode tap",
                f"ip link set ftap{i} up",
                f"ip link set ftap{i} master br{i}",
                f"ip link add v{i}br type veth peer name v{i}ns",
                f"ip link set v{i}br up",
                f"ip link set v{i}br master br{i}",
                f"ip netns add ns{i}",
                f"ip link set v{i}ns netns ns{i}",
                f"ip netns exec ns{i} ip link set v{i}ns name eth0",
                # Must match fleetqox_trace_replay_tap.cc's stationMacs[i]
                # exactly -- see _station_mac's docstring for why.
                f"ip netns exec ns{i} ip link set eth0 address {_station_mac(i)}",
                f"ip netns exec ns{i} ip addr add {ips[endpoint]}/24 dev eth0",
                f"ip netns exec ns{i} ip link set eth0 up",
                f"ip netns exec ns{i} ip link set lo up",
            ]
        )

    lines.extend(
        [
            "# --- start the simulated wifi network ---",
            (
                f"/tmp/fleetqox_tap_bridge --numRobots={num_robots} --tapPrefix=ftap "
                f"--simDuration={sim_duration_s:.12g} --numAps={num_aps} "
                + ("--isolateController=1 " if isolate_controller else "")
                + f"> {results_dir_container}/ns3_tap.log 2>&1 &"
            ),
            "NS3_PID=$!",
            f"sleep {NS3_ATTACH_WAIT_S}",
            "if ! kill -0 $NS3_PID 2>/dev/null; then",
            f"  echo 'ns-3 tap-bridge process exited early, see {results_dir_container}/ns3_tap.log' >&2",
            f"  cat {results_dir_container}/ns3_tap.log >&2",
            "  exit 1",
            "fi",
            "# --- start every station's real RMW process ---",
            "ENDPOINT_PIDS=()",
        ]
    )

    start_file = f"{results_dir_container}/start"
    ready_files = [f"{results_dir_container}/ready_{i}" for i in range(len(endpoints))]
    for i, endpoint in enumerate(endpoints):
        peers = ",".join(
            f"{ips[other]}:{RMW_PORT}" for other in endpoints if other != endpoint
        )
        result_json = f"{results_dir_container}/result_{i}.json"
        log_file = f"{results_dir_container}/endpoint_{i}.log"
        # ip netns exec runs a single command, not a login shell -- rclpy
        # and rmw_fleetqox_cpp aren't importable/loadable without both
        # /opt/ros/jazzy/setup.bash and the rmw_fleetqox_cpp colcon
        # install's setup.bash sourced first (confirmed by the first real
        # run failing with ModuleNotFoundError: No module named 'rclpy'),
        # so wrap everything in an inner `bash -c` to source them.
        if rmw_implementation == "rmw_fleetqox_cpp":
            rmw_setup = (
                f"source /work/{FLEETQOX_RMW_INSTALL}/setup.bash && "
                "export RMW_IMPLEMENTATION=rmw_fleetqox_cpp "
                f"FLEETQOX_RMW_BIND=0.0.0.0:{RMW_PORT} FLEETQOX_RMW_PEERS={peers} "
                # Without these, a first real run showed sends for
                # oversized payloads (>1472B, e.g. the 2200B perception /
                # 3500-9000B human_qoe flows) failing outright with
                # "FleetRMW UDP payload exceeds automatically discovered
                # path MTU" -- this synthetic bridged-L2 topology has no
                # real IP router to generate the ICMP "fragmentation
                # needed" feedback real PMTU discovery relies on, so
                # explicitly enabling loss-resilient chunking (rather than
                # relying on reactive PMTU discovery to eventually trigger
                # it) is required, not just an optimization.
                f"FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES={fragment_chunk_bytes} "
                f"FLEETQOX_RMW_UDP_DATAGRAM_BUDGET_BYTES={udp_datagram_budget_bytes} "
                + (
                    # Diagnostic knob for the 16-robot-scale delivery-collapse
                    # investigation (see docs/AUDIT_ACCEPTANCE_TRACKING.md):
                    # rmw_fleetqox_cpp's own graph-advertisement renewal loop
                    # re-broadcasts every publisher/subscription to every peer
                    # on this interval (500ms default), an O(publishers x
                    # peers) cost per tick and O(N^2) system-wide as peer
                    # count grows -- unset leaves the RMW's own default.
                    f"FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS={graph_renew_interval_ms} "
                    if graph_renew_interval_ms is not None else ""
                )
                + (
                    # Opt-in fix for a ChatGPT-flagged gap (see
                    # docs/AUDIT_ACCEPTANCE_TRACKING.md "data-plane fanout"):
                    # every OTHER peer_policy_ value, including the RMW's
                    # default ("all"), sends every published message to every
                    # configured peer regardless of subscription interest --
                    # a real O(peers) fanout on every single publish(), not
                    # just the O(N^2) graph-discovery traffic already reduced.
                    # A new named policy rather than a changed default, since
                    # this RMW is shared by many other probes/tests this
                    # investigation hasn't audited.
                    "FLEETQOX_RMW_PEER_POLICY=subscription_aware "
                    if subscription_aware else ""
                )
            )
        else:
            # Standard ROS2 RMW (e.g. rmw_fastrtps_cpp/Fast DDS,
            # rmw_cyclonedds_cpp/Cyclone DDS) -- comparison baseline for the
            # 16-robot-scale wifi investigation (see
            # docs/AUDIT_ACCEPTANCE_TRACKING.md "DDS comparison"). Already
            # present in this base ROS2 image, no colcon build needed.
            # Discovers peers via its own DDS discovery protocol (typically
            # multicast) instead of a static FLEETQOX_RMW_PEERS list --
            # this synthetic bridged-L2 TapBridge topology relays multicast
            # the same way it relays any other broadcast traffic, already
            # confirmed working for ARP earlier in this investigation.
            rmw_setup = f"export RMW_IMPLEMENTATION={rmw_implementation} "
        inner = (
            "source /opt/ros/jazzy/setup.bash && "
            + rmw_setup
            + "&& "
            f"python3 {_container_path(ROOT / 'scripts' / 'fleetqox_rmw_trace_endpoint.py')} "
            f"--trace={shlex.quote(trace_container_path)} "
            f"--endpoint={shlex.quote(endpoint)} "
            f"--policy={shlex.quote(policy)} "
            f"--start-offset-ms={start_offset_ms:.12g} "
            f"--drain-s={drain_s:.12g} "
            f"--discovery-timeout-s={discovery_timeout_s:.12g} "
            f"--start-wait-timeout-s={start_wait_timeout_s} "
            f"--summary-json={shlex.quote(result_json)} "
            f"--ready-file={shlex.quote(ready_files[i])} "
            f"--start-file={shlex.quote(start_file)}"
        )
        cmd = (
            f"ip netns exec ns{i} bash -c {shlex.quote(inner)} "
            f"> {shlex.quote(log_file)} 2>&1 &"
        )
        lines.append(cmd)
        lines.append("ENDPOINT_PIDS+=($!)")

    lines.extend(
        [
            # Every endpoint independently sets its OWN wall-clock "t=0"
            # right after its own discovery finishes -- without a shared
            # gate, endpoints that discover peers at different real times
            # would replay the trace against different time origins,
            # skewing the intended cross-endpoint schedule. Block every
            # process on --start-file until all of them have signalled
            # --ready-file, then release them together.
            "# --- wait for every endpoint to finish discovery, then release them together ---",
            f"READY_DEADLINE=$(( $(date +%s) + {ready_deadline_s} ))",
            "READY_TIMED_OUT=0",
            "while true; do",
            "  MISSING=0",
        ]
        + [f"  [ -f {shlex.quote(f)} ] || MISSING=1" for f in ready_files]
        + [
            "  [ $MISSING -eq 0 ] && break",
            "  if [ $(date +%s) -ge $READY_DEADLINE ]; then",
            "    READY_TIMED_OUT=1",
            "    break",
            "  fi",
            "  sleep 0.2",
            "done",
            "if [ $READY_TIMED_OUT -eq 1 ]; then",
            "  echo 'timed out waiting for every endpoint to become ready -- dumping logs' >&2",
        ]
        + [
            f"  echo '--- {name} ---' >&2; cat {shlex.quote(path)} >&2 2>/dev/null || true"
            for name, path in [("ns3_tap.log", f"{results_dir_container}/ns3_tap.log")]
            + [
                (f"endpoint_{i}.log ({endpoints[i]})", f"{results_dir_container}/endpoint_{i}.log")
                for i in range(len(endpoints))
            ]
        ]
        + [
            "  kill $NS3_PID 2>/dev/null || true",
            '  kill "${ENDPOINT_PIDS[@]}" 2>/dev/null || true',
            "  exit 1",
            "fi",
            f"touch {shlex.quote(start_file)}",
            # --- wait for every real process, then tear down the network ---
            # `wait` returns the exit status of the (last) awaited process,
            # and under `set -e` a failing endpoint here would abort the
            # WHOLE script on this line -- before any of the log/result
            # dumping below ever runs. That is exactly backwards: an
            # endpoint process failing is precisely the case the dump below
            # exists to explain, so this must not itself trigger -e. `||
            # true` here does NOT hide the failure -- ENDPOINT_EXIT below
            # still records it and the trailing check still exits 1 after
            # every diagnostic has been printed.
            # `cmd1; cmd2` does NOT protect cmd2 from -e if cmd1 fails --
            # only an explicit set +e/set -e bracket (or `||`) does, so
            # `wait ...; ENDPOINT_EXIT=$?` would abort on the wait line
            # itself before the assignment ever ran.
            "set +e",
            'wait "${ENDPOINT_PIDS[@]}"',
            "ENDPOINT_EXIT=$?",
            "set -e",
            "kill $NS3_PID 2>/dev/null || true",
            "wait $NS3_PID 2>/dev/null || true",
        ]
    )
    # The container runs with --rm, so its filesystem (including every
    # endpoint's result JSON and log under results_dir_container)
    # disappears the moment it exits -- print each one to stdout,
    # bracketed by a marker containing its endpoint name, so run_probe()
    # can pull them back out of the captured subprocess output instead of
    # needing `docker cp` before removal. Every cat is followed by
    # `|| true`, not chained with &&: under `set -e`, a plain failing
    # command aborts the WHOLE script immediately even when followed by
    # `; next_command` (";" does not protect against -e the way "||"
    # does) -- a missing result file must not abort the script before its
    # END marker (or any later endpoint's output) ever gets printed, which
    # is exactly the failure this section exists to surface, not hide.
    lines.append("echo '=== ns-3 tap-bridge log ==='")
    lines.append(f"cat {shlex.quote(results_dir_container)}/ns3_tap.log 2>/dev/null || true")
    for i, endpoint in enumerate(endpoints):
        result_json = f"{results_dir_container}/result_{i}.json"
        log_file = f"{results_dir_container}/endpoint_{i}.log"
        lines.append(f"echo \"=== endpoint log: {endpoint} ===\"")
        lines.append(f"cat {shlex.quote(log_file)} 2>/dev/null || true")
        lines.append(f"echo FLEETQOX_TAP_RESULT_BEGIN:{endpoint}")
        lines.append(f"cat {shlex.quote(result_json)} 2>/dev/null || true")
        lines.append(f"echo; echo FLEETQOX_TAP_RESULT_END:{endpoint}")
    lines.append("echo FLEETQOX_TAP_PROBE_DONE")
    lines.append("exit $ENDPOINT_EXIT")
    return "\n".join(lines)


def parse_endpoint_results(stdout: str, endpoints: list[str]) -> dict[str, Any]:
    """Pull each endpoint's JSON summary back out of the captured stdout
    (see build_shell_script's FLEETQOX_TAP_RESULT_BEGIN/END markers)."""

    results: dict[str, Any] = {}
    for endpoint in endpoints:
        begin = f"FLEETQOX_TAP_RESULT_BEGIN:{endpoint}"
        end = f"FLEETQOX_TAP_RESULT_END:{endpoint}"
        begin_index = stdout.find(begin)
        end_index = stdout.find(end)
        if begin_index == -1 or end_index == -1 or end_index < begin_index:
            results[endpoint] = None
            continue
        blob = stdout[begin_index + len(begin) : end_index].strip()
        try:
            results[endpoint] = json.loads(blob) if blob else None
        except json.JSONDecodeError:
            results[endpoint] = None
    return results


def run_probe(
    *,
    image: str,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    sim_duration_s: float,
    start_offset_ms: float,
    drain_s: float,
    fragment_chunk_bytes: int = DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    udp_datagram_budget_bytes: int = DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES,
    graph_renew_interval_ms: int | None = None,
    num_aps: int = 1,
    isolate_controller: bool = False,
    subscription_aware: bool = False,
    discovery_timeout_s: float = 15.0,
    rmw_implementation: str = "rmw_fleetqox_cpp",
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = output_dir / f"trace_tap_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"ns3_wifi_tap_rmw_probe_{num_robots}robot",
        robots=num_robots,
        seconds=seconds,
        seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        policies=(policy,),
        include_non_sent=False,
    )
    packet_rows = write_simulator_csv(events, trace_path)

    endpoints = endpoint_list(num_robots)
    results_dir_container = "/tmp/fleetqox_tap_results"
    script = build_shell_script(
        trace_container_path=_container_path(trace_path),
        endpoints=endpoints,
        policy=policy,
        num_robots=num_robots,
        sim_duration_s=sim_duration_s,
        start_offset_ms=start_offset_ms,
        drain_s=drain_s,
        results_dir_container=results_dir_container,
        fragment_chunk_bytes=fragment_chunk_bytes,
        udp_datagram_budget_bytes=udp_datagram_budget_bytes,
        graph_renew_interval_ms=graph_renew_interval_ms,
        num_aps=num_aps,
        isolate_controller=isolate_controller,
        subscription_aware=subscription_aware,
        discovery_timeout_s=discovery_timeout_s,
        rmw_implementation=rmw_implementation,
    )

    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cap-add",
            "NET_ADMIN",
            # `ip netns add` bind-mounts the new namespace under
            # /run/netns for persistent by-name reference, which needs
            # CAP_SYS_ADMIN (NET_ADMIN alone isn't enough) -- confirmed by
            # the first real run failing with "mount --make-shared
            # /run/netns failed: Operation not permitted".
            "--cap-add",
            "SYS_ADMIN",
            "--device",
            "/dev/net/tun",
            "--entrypoint",
            "bash",
            "-v",
            f"{ROOT}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            script,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=max(120.0, sim_duration_s + 60.0),
    )

    endpoint_results = parse_endpoint_results(completed.stdout, endpoints)

    return {
        "schema_version": "fleetqox.ns3_wifi_tap_rmw_probe.v1",
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "trace": str(trace_path.relative_to(ROOT)),
        "packet_rows": packet_rows,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "policy": policy,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--num-robots", type=int, default=1)
    parser.add_argument("--policy", default="fifo")
    parser.add_argument("--seconds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sim-duration-s", type=float, default=30.0)
    parser.add_argument("--start-offset-ms", type=float, default=2000.0)
    parser.add_argument("--drain-s", type=float, default=10.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results_rmw_socket/ns3_wifi_tap_rmw_probe"),
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_rmw_socket/ns3_wifi_tap_rmw_probe_summary.json"),
    )
    parser.add_argument(
        "--graph-renew-interval-ms",
        type=int,
        default=None,
        help=(
            "Override FLEETQOX_RMW_GRAPH_RENEW_INTERVAL_MS (rmw_fleetqox_cpp "
            "default: 500ms) -- diagnostic knob for the 16-robot-scale "
            "delivery-collapse investigation, see "
            "docs/AUDIT_ACCEPTANCE_TRACKING.md. Unset leaves the RMW's own "
            "default."
        ),
    )
    parser.add_argument(
        "--num-aps",
        type=int,
        default=1,
        help=(
            "Split stations round-robin across this many APs, each on its "
            "own fully non-interfering ns-3 wifi channel -- diagnostic for "
            "the 16-robot-scale delivery-collapse investigation, see "
            "docs/AUDIT_ACCEPTANCE_TRACKING.md. Default 1 matches the "
            "original single-AP topology."
        ),
    )
    parser.add_argument(
        "--isolate-controller",
        action="store_true",
        help=(
            "Reserve AP group 0 exclusively for fleet_controller "
            "(station 0), round-robining every other station across the "
            "remaining num-aps-1 groups. Added after a --num-aps=4 run "
            "still barely delivered anything: fleet_controller alone "
            "generates 71 percent of the fleet's traffic and, confined "
            "to one channel like everyone else under plain round-robin, "
            "that one channel stayed the bottleneck regardless of "
            "--num-aps -- see docs/AUDIT_ACCEPTANCE_TRACKING.md. No-op "
            "when --num-aps is 1."
        ),
    )
    parser.add_argument(
        "--subscription-aware",
        action="store_true",
        help=(
            "Sets FLEETQOX_RMW_PEER_POLICY=subscription_aware -- ChatGPT-"
            "flagged gap: every OTHER peer_policy_ value, including the "
            "RMW's own \"all\" default, sends every published message to "
            "every configured peer regardless of subscription interest. "
            "See docs/AUDIT_ACCEPTANCE_TRACKING.md 'data-plane fanout'."
        ),
    )
    parser.add_argument(
        "--discovery-timeout-s",
        type=float,
        default=15.0,
        help=(
            "Per-endpoint max wait for its own subscription discovery to "
            "converge before publishing starts regardless (fleetqox_rmw_"
            "trace_endpoint.py's own flag). The orchestrator's ready/start "
            "deadlines scale up with this automatically. Added to test a "
            "ChatGPT-flagged bootstrap-feedback-loop hypothesis: does "
            "delivery recover if discovery is given long enough to fully "
            "converge before any application data is sent? See "
            "docs/AUDIT_ACCEPTANCE_TRACKING.md."
        ),
    )
    parser.add_argument(
        "--rmw-implementation",
        default="rmw_fleetqox_cpp",
        help=(
            "RMW_IMPLEMENTATION to run inside every endpoint. Default is "
            "this project's own custom RMW; pass rmw_fastrtps_cpp (Fast "
            "DDS) or rmw_cyclonedds_cpp (Cyclone DDS) to compare a "
            "traditional DDS implementation's discovery/data-plane "
            "behavior against the same real ns-3 802.11g TapBridge "
            "topology and traffic this investigation used throughout --"
            "see docs/AUDIT_ACCEPTANCE_TRACKING.md 'DDS comparison'. "
            "Any value other than rmw_fleetqox_cpp skips the "
            "FLEETQOX_RMW_* colcon-install/env-var setup entirely and "
            "relies on the RMW's own discovery (typically multicast)."
        ),
    )
    args = parser.parse_args()

    summary = run_probe(
        image=args.image,
        output_dir=ROOT / args.output_dir,
        num_robots=max(args.num_robots, 1),
        policy=args.policy,
        seconds=max(args.seconds, 1),
        seed=args.seed,
        sim_duration_s=max(args.sim_duration_s, 1.0),
        start_offset_ms=max(args.start_offset_ms, 0.0),
        drain_s=max(args.drain_s, 1.0),
        graph_renew_interval_ms=args.graph_renew_interval_ms,
        num_aps=max(args.num_aps, 1),
        isolate_controller=args.isolate_controller,
        subscription_aware=args.subscription_aware,
        discovery_timeout_s=max(args.discovery_timeout_s, 0.1),
        rmw_implementation=args.rmw_implementation,
    )
    summary_path = ROOT / args.summary_json
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"status={summary['status']} returncode={summary['returncode']}")
    return 0 if summary["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
