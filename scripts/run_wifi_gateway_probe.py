"""WiFi-Gateway benchmark: a NEW Table V profile (see
docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK"). Does NOT
modify or replace WiFi-Direct (run_probe()), LAN, 5G, or Table VI --
this is a separate, additive module that reuses ReferenceTopologyProbe
and several of its existing methods without changing them.

TOPOLOGY

    robot_0000 --+
    robot_0001 --+-- Wi-Fi / ns-3 (UNCHANGED mechanism) -- GATEWAY --
    ...          |                                            |
    robot_N   ---+                                   control-side LAN
                                                    (new, small, wired)
                                                            |
                                                     control_station

The gateway takes over station-index-0's OLD position in the existing
Wi-Fi simulation (previously control_station's slot) -- confirmed safe
via fleetqox_trace_replay_tap.cc: with the default --numAps=1 (this
profile's only supported case), station 0 gets no special AP/position
treatment beyond being first in stationEndpointLabels, purely a label
string used for logging. The REAL control_station moves to a brand
new, separate container on a separate wired segment, reachable ONLY
through the gateway's second interface -- so robots and control_station
share no network segment at all; "no bypass" is a physical property of
the topology, not something enforced by application logic.

Every existing per-middleware discovery mechanism (CycloneDDS static
peers, Fast DDS Discovery Server, Zenoh router, FleetRMW static peers)
is reused, just re-addressed: the router/discovery-server process now
runs on the GATEWAY (reusing start_zenoh_router()/
start_fastdds_discovery_server() UNCHANGED, since they already operate
on self.endpoint_container_names[0]/self.endpoints[0], which now IS
the gateway after relabeling) -- robots reach it via its Wi-Fi IP;
control_station reaches the SAME process via the gateway's SEPARATE
wired IP (a second address for the same server, not a second server).
"""

from __future__ import annotations

import shlex
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    BASE_IP_PREFIX,
    DEFAULT_IMAGE,
    FASTDDS_DISCOVERY_SERVER_PORT,
    FLEETQOX_RMW_INSTALL,
    MAX_HEALTHY_SIM_LAG_S,
    READY_DEADLINE_S,
    RMW_PORT,
    STATIC_SUBSCRIPTION_TYPE_NAME,
    ReadinessFailure,
    ReferenceTopologyProbe,
    build_static_subscriptions,
    container_pid,
    corrected_sim_lag_s,
    docker,
    endpoint_list,
    fleetqox_rmw_env_prefix,
    parse_last_wifi_stats,
    parse_wifi_stats,
    rigger_run,
    station_mac,
    topic_for,
    wifi_stats_target_s,
)
from scripts.fleetqox_rmw_trace_endpoint import _topic_for  # noqa: E402
from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402

GATEWAY_CONTROL_IP_PREFIX = "10.61.0."
RELAY_TOPIC_SUFFIX = "__relayed"
CONTROL_STATION_NAME = "control_station"


def wifi_gateway_endpoint_list(num_robots: int) -> list[str]:
    """Same shape as endpoint_list() (index 0 first, robots after) but
    index 0 is named "gateway", not "control_station" -- control_station
    is a SEPARATE container in this profile, not part of the Wi-Fi
    station set at all."""
    endpoints = ["gateway"]
    endpoints.extend(f"robot_{i:04d}" for i in range(num_robots))
    return endpoints


def required_peers_for_wifi_gateway(
    endpoints: list[str],
) -> dict[str, frozenset[str]]:
    """Readiness contract for the gateway topology (see
    docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK" Phase
    2): robots and control_station only need the GATEWAY (the only
    peer they can physically reach); the gateway needs EVERY robot
    plus control_station. Deliberately NOT required_peers_from_trace()
    (which derives peers from workload src/dst -- robot<->control_station
    -- that would be physically unreachable here); this derives peers
    from the PHYSICAL topology instead."""
    robots = [e for e in endpoints if e != "gateway"]
    result: dict[str, frozenset[str]] = {
        robot: frozenset({"gateway"}) for robot in robots
    }
    result["gateway"] = frozenset(robots) | {CONTROL_STATION_NAME}
    result[CONTROL_STATION_NAME] = frozenset({"gateway"})
    return result


def wire_gateway_control_segment(
    rigger_name: str,
    gateway_container: str,
    control_container: str,
) -> tuple[str, str]:
    """Builds the SECOND, wired-only segment: gateway gets a SECOND
    interface (eth1, its Wi-Fi-side eth0 from wire_network() is
    untouched) and control_station gets its ONE interface (eth0). A
    plain point-to-point veth PAIR, not a bridge: a bridge is for
    joining 3+ ports (that's what wire_network_lan() needs, hence a
    real Linux bridge device there); with exactly 2 members here, a
    bridge would need each end's own interface enslaved as a bridge
    PORT, and once an interface is a bridge port its own IP address
    stops working for L3 traffic (all L3 processing moves to the
    bridge device instead) -- confirmed as a genuine bug this way
    (live UDP send/recv timed out) before switching to a bare veth
    pair, which needs no bridge device at all. Different subnet
    (10.61.0.0/24) so it is never confusable with the Wi-Fi segment's
    10.60.0.0/24 addressing. Returns (gateway_wired_ip, control_station_ip)."""
    gateway_pid = container_pid(gateway_container)
    control_pid = container_pid(control_container)
    gateway_wired_ip = f"{GATEWAY_CONTROL_IP_PREFIX}2"
    control_ip = f"{GATEWAY_CONTROL_IP_PREFIX}3"
    commands = [
        "set -e",
        "ip link add gwveth0 type veth peer name gwveth1",
        f"ip link set gwveth0 netns {gateway_pid}",
        f"ip link set gwveth1 netns {control_pid}",
        f"nsenter -t {gateway_pid} -n -- ip link set gwveth0 name eth1",
        f"nsenter -t {gateway_pid} -n -- ip link set eth1 address 02:00:00:01:00:01",
        f"nsenter -t {gateway_pid} -n -- ip addr add {gateway_wired_ip}/24 dev eth1",
        f"nsenter -t {gateway_pid} -n -- ip link set eth1 up",
        f"nsenter -t {control_pid} -n -- ip link set gwveth1 name eth0",
        f"nsenter -t {control_pid} -n -- ip link set eth0 address 02:00:00:01:00:02",
        f"nsenter -t {control_pid} -n -- ip addr add {control_ip}/24 dev eth0",
        f"nsenter -t {control_pid} -n -- ip link set eth0 up",
        f"nsenter -t {control_pid} -n -- ip link set lo up",
        f"nsenter -t {control_pid} -n -- ip route add 224.0.0.0/4 dev eth0",
    ]
    commands.append("echo WIRE_GATEWAY_CONTROL_OK")
    result = rigger_run(rigger_name, "\n".join(commands))
    if "WIRE_GATEWAY_CONTROL_OK" not in result.stdout:
        raise RuntimeError(f"gateway<->control wiring failed:\n{result.stdout}\n{result.stderr}")
    return gateway_wired_ip, control_ip


def _static_entries(
    pairs: list[tuple[str, str]], ip_for_dst, topic_suffix: str = ""
) -> list[str]:
    """Builds FLEETQOX_RMW_STATIC_SUBSCRIPTIONS entries in the EXACT
    same format launch_endpoints() already uses for WiFi-Direct
    (`"{ip}:{port}|0|{topic}|{type}"`), but with the destination IP
    resolved by ip_for_dst(dst) instead of self.ips[dst] directly --
    WiFi-Direct's own build_static_subscriptions() output gives the
    (dst, flow_class) PAIRS (a workload-level fact, unchanged by this
    topology), but the physical IP each pair must route to is NOT
    unchanged: a robot's "control_station" pair must resolve to the
    GATEWAY's address (the only thing it can physically reach), not
    control_station's own real address."""
    return [
        f"{ip_for_dst(dst)}:{RMW_PORT}|0|{topic_for(dst, flow_class) + topic_suffix}|"
        f"{STATIC_SUBSCRIPTION_TYPE_NAME}"
        for dst, flow_class in pairs
    ]


def _cyclonedds_static_peers_config(peer_ips: list[str]) -> str:
    """Mirrors launch_endpoints()'s own CycloneDDS static-peers config
    exactly (same AllowMulticast=false + explicit unicast Peers list +
    ParticipantIndex=0 reasoning, see that method's own comment) --
    duplicated here (not imported) because the ORIGINAL derives its
    peer list from self.endpoints/self.ips, which for this profile
    means physically-unreachable IPs (a robot's Peers list must NOT
    include control_station's IP, which it cannot route to)."""
    peer_xml = "".join(f'<Peer address="{ip}"/>' for ip in peer_ips)
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<CycloneDDS xmlns="https://cdds.io/config">'
        "<Domain><General><AllowMulticast>false</AllowMulticast></General>"
        f"<Discovery><Peers>{peer_xml}</Peers>"
        "<ParticipantIndex>0</ParticipantIndex></Discovery>"
        "</Domain></CycloneDDS>"
    )


def run_wifi_gateway_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    rmw_implementation: str,
    discovery_mode: str = "default",
    start_offset_ms: float = 2000.0,
    drain_s: float = 10.0,
    discovery_timeout_s: float = 15.0,
    disable_gateway: bool = False,
    fleetqox_static_subscriptions: bool = True,
) -> dict[str, Any]:
    """disable_gateway=False (default): normal run. disable_gateway=True
    is the Phase 3 no-bypass-test knob -- everything is wired and
    launched identically EXCEPT the gateway process itself is never
    started, so robot->control delivery must become zero with no other
    change (see STEP 5's A/B/C sequence).

    fleetqox_static_subscriptions=True (default, the fix): FleetRMW
    endpoints use static_mode=True with the topology-correct
    static_subscriptions table built below. False reproduces the
    pre-fix RED case (static_mode=False, no routing table at all) for
    before/after comparison -- kept as an explicit, named parameter
    (same pattern as disable_gateway) rather than a one-off hack, since
    it is exactly the variable STEP 2/STEP 3 of this investigation
    compares."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    trace_path = output_dir / f"trace_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"wifi_gateway_{rmw_implementation}",
        robots=num_robots, seconds=seconds, seed=seed,
        capacity_bytes_per_second=max(200_000, num_robots * 6_000),
        capacity_packets_per_second=None, capacity_airtime_ns_per_second=None,
        policies=(policy,), include_non_sent=False, merge_control_station=True,
    )
    write_simulator_csv(events, trace_path)
    trace_container_path = f"/work/{trace_path.relative_to(ROOT)}"

    # STEP 1 (see docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY
    # BENCHMARK" Phase 3): the (dst, flow_class) PAIRS a publisher
    # sends on are a workload-level fact, unchanged from WiFi-Direct --
    # reuse build_static_subscriptions() verbatim for those. What
    # DIFFERS is the physical IP each pair must resolve to (built
    # per-role below, once container IPs are known).
    workload_endpoints = [CONTROL_STATION_NAME] + [
        f"robot_{i:04d}" for i in range(num_robots)
    ]
    static_pairs_by_publisher = build_static_subscriptions(trace_path, policy, workload_endpoints)

    wifi_endpoints = wifi_gateway_endpoint_list(num_robots)  # ["gateway", robot_0000, ...]
    probe = ReferenceTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir
    )
    # Relabel index 0 from "control_station" to "gateway" -- reuses
    # start_containers()/wire_network()/start_ns3() completely
    # unmodified; they only ever iterate self.endpoints/
    # self.endpoint_container_names/self.ips generically.
    probe.endpoints = wifi_endpoints
    probe.endpoint_container_names = [
        f"{probe.rigger_name.rsplit('_rigger', 1)[0]}_{e}" for e in wifi_endpoints
    ]
    probe.ips = {e: f"{BASE_IP_PREFIX}{i + 2}" for i, e in enumerate(wifi_endpoints)}

    control_container_name = f"{probe.rigger_name.rsplit('_rigger', 1)[0]}_{CONTROL_STATION_NAME}"
    required_peers = required_peers_for_wifi_gateway(wifi_endpoints)

    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    all_logical_endpoints = [CONTROL_STATION_NAME] + [e for e in wifi_endpoints if e != "gateway"]
    gateway_result: dict[str, Any] | None = None
    ns3_log_text = ""
    wifi_stats = None
    ns3_real_elapsed_s_at_log_read = None
    try:
        probe.start_containers()
        docker(
            "run", "-d", "--name", control_container_name, "--network=none", "--init",
            "--cap-add=NET_ADMIN", *probe._mount_args(), probe.image, "sleep infinity",
        )
        probe.wire_network()  # Wi-Fi side: gateway + robots, UNCHANGED mechanism
        gateway_wired_ip, control_ip = wire_gateway_control_segment(
            probe.rigger_name, probe.endpoint_container_names[0], control_container_name
        )
        probe.build_ns3_binary()
        probe.start_ns3(sim_duration_s=float(seconds) + start_offset_ms / 1000.0 + drain_s + 5.0)

        gateway_wifi_ip = probe.ips["gateway"]
        # STEP 3 (continued): FleetRMW's static_mode has NO discovery
        # step by design -- see --skip-discovery-wait's own help text
        # in both endpoint scripts. Applies to all three roles
        # uniformly when the static-subscriptions fix is active;
        # completely absent (empty string) for every other middleware
        # and for the fleetqox_static_subscriptions=False RED case.
        fleetqox_skip_discovery_flag = (
            " --skip-discovery-wait"
            if rmw_implementation == "rmw_fleetqox_cpp" and fleetqox_static_subscriptions
            else ""
        )
        # Router/discovery-server infra runs on the gateway (reusing
        # these UNCHANGED -- they already target
        # self.endpoint_container_names[0]/self.endpoints[0], which is
        # now the gateway after relabeling above).
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()

        results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
        docker("exec", probe.rigger_name, "mkdir", "-p", f"/work/{results_dir_container}")
        ready_files = {
            e: f"{results_dir_container}/ready_{e}.txt" for e in all_logical_endpoints + ["gateway"]
        }
        start_file = f"{results_dir_container}/start"

        def env_prefix_for(
            role: str, own_ip: str, peer_ips: list[str],
            static_subscription_entries: list[str] | None = None,
        ) -> str:
            peers_str = ",".join(f"{ip}:{RMW_PORT}" for ip in peer_ips)
            if rmw_implementation == "rmw_fleetqox_cpp":
                # STEP 3: static_mode=True with a topology-correct
                # static_subscription_entries table -- the SAME
                # subscription-aware routing semantics WiFi-Direct uses
                # (see fleetqox_rmw_env_prefix()'s own docstring),
                # applied to the gateway's 3-role topology instead of
                # the 2-role direct one. Previously this was
                # static_mode=False (no routing table at all, relying
                # solely on FLEETQOX_RMW_PEERS broadcast) -- changed
                # here because a control experiment against the
                # EXISTING, unmodified WiFi-Direct run_probe() showed
                # tx>0/rx=0 for every endpoint when
                # static_subscriptions was omitted, matching this
                # topology's own pre-fix symptom (see
                # docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY
                # BENCHMARK" Phase 3/STEP 1-3).
                prefix = fleetqox_rmw_env_prefix(
                    role, peers_str, fleetqox_static_subscriptions,
                    (static_subscription_entries or []) if fleetqox_static_subscriptions else [],
                    None,
                )
                return f"source /work/{FLEETQOX_RMW_INSTALL}/setup.bash && export {prefix}"
            prefix = f"RMW_IMPLEMENTATION={rmw_implementation} "
            if rmw_implementation == "rmw_zenoh_cpp":
                session_config = ReferenceTopologyProbe.zenoh_session_config_json5(
                    own_ip, f"tcp/{peer_ips[0]}:7447" if peer_ips else "tcp/127.0.0.1:7447", True,
                )
                path = f"/tmp/zenoh_session_config_{role}.json5"
                docker("exec", _container_for(role), "bash", "-lc",
                       f"echo {shlex.quote(session_config)} > {path}")
                prefix += f"ZENOH_SESSION_CONFIG_URI={path} "
            if rmw_implementation == "rmw_cyclonedds_cpp" and discovery_mode == "static_peers":
                cfg = _cyclonedds_static_peers_config(peer_ips)
                path = f"/tmp/cyclonedds_static_peers_{role}.xml"
                docker("exec", _container_for(role), "bash", "-lc",
                       f"echo {shlex.quote(cfg)} > {path}")
                prefix += f"CYCLONEDDS_URI={path} "
            if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
                server_ip = peer_ips[0] if peer_ips else gateway_wifi_ip
                prefix += f"ROS_DISCOVERY_SERVER={server_ip}:{FASTDDS_DISCOVERY_SERVER_PORT} "
            return f"source /opt/ros/jazzy/setup.bash && export {prefix}"

        def _container_for(role: str) -> str:
            if role == CONTROL_STATION_NAME:
                return control_container_name
            return probe.endpoint_container_names[wifi_endpoints.index(role)]

        # --- launch robots (Wi-Fi side, unchanged trace-endpoint script) ---
        for i, robot in enumerate(wifi_endpoints):
            if robot == "gateway":
                continue
            required = required_peers[robot]
            required_ips = [gateway_wifi_ip]
            # STEP 1: robot_i's own publish pairs are UNCHANGED from
            # WiFi-Direct (always (control_station, flow_class)) -- the
            # only thing that differs is the physical destination,
            # which must resolve to the GATEWAY's Wi-Fi IP (the only
            # thing this robot can reach), not control_station's real
            # (physically unreachable) address. Topic name unsuffixed:
            # this is exactly what the robot ALREADY publishes today,
            # zero code/behavior change on the robot's own side.
            robot_static_entries = _static_entries(
                static_pairs_by_publisher.get(robot, []), lambda _dst: gateway_wifi_ip
            )
            rmw_setup = env_prefix_for(
                robot, probe.ips[robot], required_ips, robot_static_entries
            )
            result_json = f"{results_dir_container}/result_{robot}.json"
            log_file = f"{results_dir_container}/endpoint_{robot}.log"
            inner = (
                f"{rmw_setup}&& "
                f"python3 -B /work/scripts/fleetqox_rmw_trace_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} "
                f"--endpoint={shlex.quote(robot)} --policy={shlex.quote(policy)} "
                f"--start-offset-ms={start_offset_ms:.12g} --drain-s={drain_s:.12g} "
                f"--discovery-timeout-s={discovery_timeout_s:.12g} "
                f"--start-wait-timeout-s=90 --expected-peer-count=1 "
                f"--required-peer-ids={shlex.quote(','.join(sorted(required)))} "
                f"--incoming-topic-suffix={RELAY_TOPIC_SUFFIX}"
                f"{fleetqox_skip_discovery_flag} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{ready_files[robot]} --start-file=/work/{start_file}"
            )
            docker("exec", "-d", probe.endpoint_container_names[i], "bash", "-lc",
                   f"{inner} > /work/{log_file} 2>&1")

        # --- launch control_station (wired side, unchanged trace-endpoint script) ---
        required = required_peers[CONTROL_STATION_NAME]
        # STEP 1: control_station's own publish pairs are UNCHANGED
        # from WiFi-Direct (always (robot_i, flow_class)) -- physical
        # destination resolves to the GATEWAY's WIRED IP (the only
        # thing control_station can reach), topic name unsuffixed
        # (control_station's own publish code/behavior is unchanged).
        control_static_entries = _static_entries(
            static_pairs_by_publisher.get(CONTROL_STATION_NAME, []),
            lambda _dst: gateway_wired_ip,
        )
        rmw_setup = env_prefix_for(
            CONTROL_STATION_NAME, control_ip, [gateway_wired_ip], control_static_entries
        )
        result_json = f"{results_dir_container}/result_{CONTROL_STATION_NAME}.json"
        log_file = f"{results_dir_container}/endpoint_{CONTROL_STATION_NAME}.log"
        inner = (
            f"{rmw_setup}&& "
            f"python3 -B /work/scripts/fleetqox_rmw_trace_endpoint.py "
            f"--trace={shlex.quote(trace_container_path)} "
            f"--endpoint={shlex.quote(CONTROL_STATION_NAME)} --policy={shlex.quote(policy)} "
            f"--start-offset-ms={start_offset_ms:.12g} --drain-s={drain_s:.12g} "
            f"--discovery-timeout-s={discovery_timeout_s:.12g} "
            f"--start-wait-timeout-s=90 --expected-peer-count=1 "
            f"--required-peer-ids={shlex.quote(','.join(sorted(required)))} "
            f"--incoming-topic-suffix={RELAY_TOPIC_SUFFIX}"
            f"{fleetqox_skip_discovery_flag} "
            f"--summary-json=/work/{result_json} "
            f"--ready-file=/work/{ready_files[CONTROL_STATION_NAME]} --start-file=/work/{start_file}"
        )
        docker("exec", "-d", control_container_name, "bash", "-lc", f"{inner} > /work/{log_file} 2>&1")

        # --- launch gateway (two-interface relay process) ---
        if not disable_gateway:
            required = required_peers["gateway"]
            peer_ips_for_gateway = [probe.ips[r] for r in wifi_endpoints if r != "gateway"] + [control_ip]
            # STEP 1: the gateway's OWN outbound pairs are exactly what
            # it relays: "uplink" (every robot's pairs, all destined
            # control_station-ward) resolves to the REAL control_ip
            # (physically reachable from the gateway's wired
            # interface); "downlink" (control_station's own pairs,
            # each destined to one specific robot) resolves to THAT
            # robot's own real Wi-Fi IP (individually addressable from
            # the gateway's Wi-Fi interface). Topic name SUFFIXED
            # (RELAY_TOPIC_SUFFIX) for both -- this is what the gateway
            # actually publishes on, matching what robots/
            # control_station now subscribe to instead of the
            # unsuffixed original.
            uplink_pairs: set[tuple[str, str]] = set()
            for r in wifi_endpoints:
                if r != "gateway":
                    uplink_pairs.update(static_pairs_by_publisher.get(r, []))
            downlink_pairs = static_pairs_by_publisher.get(CONTROL_STATION_NAME, [])
            gateway_static_entries = _static_entries(
                sorted(uplink_pairs), lambda _dst: control_ip, RELAY_TOPIC_SUFFIX
            ) + _static_entries(
                downlink_pairs, lambda dst: probe.ips[dst], RELAY_TOPIC_SUFFIX
            )
            rmw_setup = env_prefix_for(
                "gateway", gateway_wifi_ip, peer_ips_for_gateway, gateway_static_entries
            )
            result_json = f"{results_dir_container}/result_gateway.json"
            log_file = f"{results_dir_container}/endpoint_gateway.log"
            inner = (
                f"{rmw_setup}&& "
                f"python3 -B /work/scripts/fleetqox_rmw_gateway_endpoint.py "
                f"--trace={shlex.quote(trace_container_path)} --policy={shlex.quote(policy)} "
                f"--control-station-name={CONTROL_STATION_NAME} "
                f"--relay-topic-suffix={RELAY_TOPIC_SUFFIX} "
                f"--discovery-timeout-s={discovery_timeout_s:.12g} "
                f"--start-wait-timeout-s=90 --drain-s={drain_s:.12g} "
                f"--required-peer-ids={shlex.quote(','.join(sorted(required)))}"
                f"{fleetqox_skip_discovery_flag} "
                f"--summary-json=/work/{result_json} "
                f"--ready-file=/work/{ready_files['gateway']} --start-file=/work/{start_file}"
            )
            docker("exec", "-d", probe.endpoint_container_names[0], "bash", "-lc",
                   f"{inner} > /work/{log_file} 2>&1")

        # --- readiness gate ---
        wait_targets = list(ready_files.values()) if not disable_gateway else [
            v for k, v in ready_files.items() if k != "gateway"
        ]
        deadline = time.monotonic() + READY_DEADLINE_S
        invalid = False
        while time.monotonic() < deadline:
            checks = []
            for f in wait_targets:
                r = docker("exec", probe.rigger_name, "bash", "-lc",
                            f"cat /work/{f} 2>/dev/null || true", check=False)
                checks.append(r.stdout.strip())
            if any("invalid_readiness" in c for c in checks):
                invalid = True
                break
            if all(c == "ready" for c in checks):
                docker("exec", probe.rigger_name, "touch", f"/work/{start_file}")
                break
            time.sleep(0.2)
        else:
            invalid = True
        if invalid and not disable_gateway:
            status = "invalid_readiness"
            error_text = "one or more endpoints reported invalid_readiness or timed out"
        elif disable_gateway:
            # RED test: readiness for robots/control_station is EXPECTED
            # to fail (they require "gateway", which never announces
            # itself) -- this is the correct, documented outcome, not an
            # error. wait_for_completion below still runs so we can prove
            # zero application delivery occurred.
            status = "gateway_disabled_red_test"

        # Poll for result files to actually exist instead of a fixed
        # sleep -- a fixed sleep here was a real harness bug (see
        # docs/AUDIT_ACCEPTANCE_TRACKING.md, "WIFI GATEWAY BENCHMARK"
        # Phase 3/STEP 2-3): it read the gateway's own summary before
        # that process had reached its own drain-then-write, since
        # discovery alone can legitimately take up to
        # discovery_timeout_s before any of these processes even
        # starts its own measured window. Bounded by
        # READY_DEADLINE_S + discovery_timeout_s + the measured window
        # + a fixed margin -- generous, not tuned to any specific
        # observed timing.
        expected_result_files = [
            f"{results_dir_container}/result_{e}.json" for e in all_logical_endpoints
        ]
        if not disable_gateway:
            expected_result_files.append(f"{results_dir_container}/result_gateway.json")
        # +90.0 matches --start-wait-timeout-s used on every launch
        # command below -- in the invalid-readiness case, robots/
        # control_station/gateway all wait that long before giving up,
        # so the collection window must cover it too or results are
        # read before ANY of them could possibly have written anything.
        collection_deadline = time.monotonic() + (
            discovery_timeout_s + float(seconds) + start_offset_ms / 1000.0 + drain_s + 90.0 + 10.0
        )
        while time.monotonic() < collection_deadline:
            check = docker(
                "exec", probe.rigger_name, "bash", "-lc",
                " && ".join(f"test -s /work/{f}" for f in expected_result_files),
                check=False,
            )
            if check.returncode == 0:
                break
            time.sleep(1.0)

        for e in all_logical_endpoints:
            result_json = f"{results_dir_container}/result_{e}.json"
            r = docker("exec", probe.rigger_name, "cat", f"/work/{result_json}", check=False)
            endpoint_results[e] = None
            if r.returncode == 0 and r.stdout.strip():
                import json as _json
                try:
                    endpoint_results[e] = _json.loads(r.stdout)
                except ValueError:
                    endpoint_results[e] = None
        if not disable_gateway:
            r = docker("exec", probe.rigger_name, "cat",
                        f"/work/{results_dir_container}/result_gateway.json", check=False)
            if r.returncode == 0 and r.stdout.strip():
                import json as _json
                try:
                    gateway_result = _json.loads(r.stdout)
                except ValueError:
                    gateway_result = None

        ns3_log_text = probe.ns3_log()
        stats_target_s = wifi_stats_target_s(
            start_offset_ms=start_offset_ms, seconds=seconds, drain_s=drain_s
        )
        wifi_stats = parse_wifi_stats(ns3_log_text, stats_target_s)
        ns3sim_resource_usage = probe.sample_ns3sim_resource_usage()
        if ns3sim_resource_usage is not None:
            ns3_real_elapsed_s_at_log_read = ns3sim_resource_usage.get("elapsed_s")
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_text = str(exc)
        ns3sim_resource_usage = None
    finally:
        docker("rm", "-f", control_container_name, check=False)
        probe.teardown()

    # FIXED (duplicate of Direct's own SIM_LAG_S MEASUREMENT BUG fix, see
    # docs/AUDIT_ACCEPTANCE_TRACKING.md "SIM_LAG_S MEASUREMENT BUG
    # (26/09/2026)" and corrected_sim_lag_s()'s own doc comment):
    # previously computed as `ns3_real_elapsed_s_at_log_read -
    # wifi_stats["sim_time_s"]` -- an out-of-band real-time read (from
    # ns3sim_resource_usage, itself never actually populated with an
    # "elapsed_s" key by sample_ns3sim_resource_usage(), so this was
    # ALWAYS None in practice) minus the EARLY, target-based snapshot's
    # sim-time -- two different observation points. `wifi_stats` (the
    # target-based snapshot) is UNCHANGED for every other use in this
    # function -- only the lag computation itself moves to the last-
    # available, same-instant-paired snapshot, exactly like Direct.
    sim_lag_s = corrected_sim_lag_s(ns3_log_text) if ns3_log_text else None
    simulator_invalid = sim_lag_s is not None and sim_lag_s > MAX_HEALTHY_SIM_LAG_S

    return {
        "schema_version": "fleetqox.wifi_gateway_probe.v1",
        "status": status,
        "error": error_text,
        "trace": str(trace_path.relative_to(ROOT)),
        "num_robots": num_robots,
        "endpoints": all_logical_endpoints,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(e) is not None for e in all_logical_endpoints
        ),
        "gateway_result": gateway_result,
        "ns3_log": ns3_log_text,
        "wifi_stats": wifi_stats,
        # Retained for backward-compatible/diagnostic visibility only --
        # no longer used to compute sim_lag_s (see that field's own
        # comment above).
        "ns3_real_elapsed_s_at_log_read": ns3_real_elapsed_s_at_log_read,
        # The LAST available FLEETQOX_WIFI_STATS snapshot (not the
        # target-based `wifi_stats` above) -- exposed so a caller can
        # independently verify sim_lag_s's own same-instant provenance,
        # same as Direct. See parse_last_wifi_stats().
        "last_wifi_stats": parse_last_wifi_stats(ns3_log_text) if ns3_log_text else None,
        "sim_lag_s": sim_lag_s,
        "simulator_invalid": simulator_invalid,
        "disable_gateway": disable_gateway,
    }
