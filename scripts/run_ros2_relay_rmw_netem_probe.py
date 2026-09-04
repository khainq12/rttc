"""Run a publisher-relay-subscriber ROS 2 RMW baseline under Docker netem."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_rmw_docker_multi_robot_live_telemetry_plan_probe import (  # noqa: E402
    NETEM_SCHEMA_VERSION,
    NETEM_SEED_SEMANTICS,
    netem_config_for_path,
    netem_shell_prefix,
    profile_by_name,
)
from scripts.run_ros2_direct_rmw_netem_probe import (  # noqa: E402
    PUBLISHER_SCRIPT,
    SUBSCRIBER_SCRIPT,
    _float,
    container_diagnostics,
    excerpt,
    netem_status_ok,
    parse_last_json,
    probe_rmw_available,
    read_json,
    ros_command,
    run,
    start_container,
    topic_specs_for_robot_count,
    wait_for_container_path,
    wait_for_container_tcp,
    write_zenoh_session_config,
)


SCHEMA_VERSION = "fleetrmw.ros2_relay_rmw_netem_probe.v2"
DEFAULT_IMAGE = "localhost/fleetrmw/rmw-netem:jazzy"
SERIALIZED_RELAY_BUILD = ".tmp_fleetrmw_matched_v2_build"
SERIALIZED_RELAY_INSTALL = ".tmp_fleetrmw_matched_v2_install"
SERIALIZED_RELAY_LOG = ".tmp_fleetrmw_matched_v2_log"
SERIALIZED_RELAY_EXECUTABLE = (
    "/work/.tmp_fleetrmw_matched_v2_install/rmw_fleetqox_cpp/lib/"
    "rmw_fleetqox_cpp/fleetrmw_generic_serialized_relay_probe"
)
FLEETQOX_RMW = "rmw_fleetqox_cpp"
DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES = 1024
DEFAULT_FLEETQOX_RELIABLE_MAX_RETRANSMISSIONS = 6
DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES = 1472
DEFAULT_FLEETQOX_FRAGMENT_NACK_INTERVAL_MS = 50
DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_REQUESTS = 6
DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST = 8
DEFAULT_FLEETQOX_FRAGMENT_TAIL_GUARD_MS = 1000
DEFAULT_FLEETQOX_FRAGMENT_HISTORY_LIMIT = 1024
DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_LIMIT = 1024
DEFAULT_FLEETQOX_FRAGMENT_MAX_ASSEMBLY_BYTES = 16 * 1024 * 1024
DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS = 60000
DEFAULT_FLEETQOX_FRAGMENT_SEND_QUEUE_LIMIT = 32768
DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_THRESHOLD = 0
DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS = 0
DEFAULT_FLEETQOX_FRAGMENT_REPAIR_QUEUE_LIMIT = 64
DEFAULT_FLEETQOX_FRAGMENT_REPAIR_COOLDOWN_MS = 100
DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS = 250
DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_GRACE_MS = 1000


def ingress_specs(specs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {**spec, "topic": f"{spec['topic']}/_fleetqox_ingress"}
        for spec in specs
    ]


def fleetqox_static_addresses(network: str) -> dict[str, str]:
    inspected = json.loads(run(["docker", "network", "inspect", network]).stdout)
    if not inspected or not isinstance(inspected[0], dict):
        raise RuntimeError("Docker network inspection returned no network")
    ipam = inspected[0].get("IPAM", {})
    configurations = ipam.get("Config", []) if isinstance(ipam, dict) else []
    for configuration in configurations:
        if not isinstance(configuration, dict) or not configuration.get("Subnet"):
            continue
        subnet = ipaddress.ip_network(str(configuration["Subnet"]), strict=False)
        if subnet.version != 4:
            continue
        addresses = [
            ipaddress.ip_address(int(subnet.network_address) + offset)
            for offset in (10, 11, 12)
        ]
        if any(address not in subnet or address == subnet.broadcast_address for address in addresses):
            continue
        return {
            "publisher": str(addresses[0]),
            "relay": str(addresses[1]),
            "subscriber": str(addresses[2]),
        }
    raise RuntimeError("Docker network has no usable IPv4 subnet for FleetRMW peers")


def write_relay_probe_scripts(
    *,
    subscriber_script: Path,
    publisher_script: Path,
    relay_script: Path,
    destination_specs: list[dict[str, str]],
    source_specs: list[dict[str, str]],
    samples: int,
    payload_bytes: int,
    publish_interval_ms: int,
    timeout_s: float,
    publisher_linger_s: float,
) -> None:
    destination_json = json.dumps(destination_specs, sort_keys=True)
    source_json = json.dumps(source_specs, sort_keys=True)
    subscriber_script.write_text(
        SUBSCRIBER_SCRIPT.replace("__SAMPLES__", str(samples))
        .replace("__TIMEOUT_S__", repr(timeout_s))
        .replace("__TOPIC_SPECS_JSON__", destination_json),
        encoding="utf-8",
    )
    publisher_script.write_text(
        PUBLISHER_SCRIPT.replace("__SAMPLES__", str(samples))
        .replace("__PUBLISH_INTERVAL_S__", repr(publish_interval_ms / 1000.0))
        .replace("__PAYLOAD_BYTES__", str(payload_bytes))
        .replace("__PUBLISHER_LINGER_S__", repr(publisher_linger_s))
        .replace("__TOPIC_SPECS_JSON__", source_json),
        encoding="utf-8",
    )
    mappings = [
        {
            "source": source["topic"],
            "destination": destination["topic"],
            "kind": destination["kind"],
            "flow": destination["flow"],
        }
        for source, destination in zip(source_specs, destination_specs, strict=True)
    ]
    relay_script.write_text(
        RELAY_SCRIPT.replace("__MAPPINGS_JSON__", json.dumps(mappings, sort_keys=True))
        .replace("__SAMPLES__", str(samples))
        .replace("__TIMEOUT_S__", repr(timeout_s))
        .replace("__RELAY_LINGER_S__", repr(publisher_linger_s + 0.5)),
        encoding="utf-8",
    )


def ensure_generic_serialized_relay(*, root: Path, image: str) -> None:
    package_root = root / "ros2_ws" / "src" / "rmw_fleetqox_cpp"
    executable = (
        root
        / SERIALIZED_RELAY_INSTALL
        / "rmw_fleetqox_cpp"
        / "lib"
        / "rmw_fleetqox_cpp"
        / "fleetrmw_generic_serialized_relay_probe"
    )
    build_inputs = tuple(
        path
        for path in package_root.rglob("*")
        if path.is_file()
        and (
            path.suffix in {".c", ".cc", ".cpp", ".h", ".hh", ".hpp", ".cmake"}
            or path.name in {"CMakeLists.txt", "package.xml"}
        )
    )
    if executable.exists() and all(
        path.exists() and path.stat().st_mtime <= executable.stat().st_mtime
        for path in build_inputs
    ):
        return
    run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "bash",
            "-v",
            f"{root}:/work",
            "-w",
            "/work",
            image,
            "-lc",
            "source /opt/ros/jazzy/setup.bash && "
            f"rm -rf /work/{SERIALIZED_RELAY_BUILD} "
            f"/work/{SERIALIZED_RELAY_INSTALL} "
            f"/work/{SERIALIZED_RELAY_LOG} && "
            f"colcon --log-base /work/{SERIALIZED_RELAY_LOG} build "
            "--base-paths ros2_ws/src "
            "--packages-select fleetrmw_interfaces rmw_fleetqox_cpp "
            f"--build-base /work/{SERIALIZED_RELAY_BUILD} "
            f"--install-base /work/{SERIALIZED_RELAY_INSTALL} "
            "--cmake-args -DCMAKE_BUILD_TYPE=Release",
        ]
    )


def generic_serialized_relay_command(
    *,
    rmw: str,
    domain_id: int,
    zenoh_session_config_uri: str | None,
    source_specs: list[dict[str, str]],
    destination_specs: list[dict[str, str]],
    samples: int,
    timeout_s: float,
    linger_s: float,
    environment: dict[str, str] | None = None,
) -> str:
    exported_environment = {
        "RMW_IMPLEMENTATION": rmw,
        "ROS_DOMAIN_ID": str(domain_id),
        **(environment or {}),
    }
    command = (
        "source /opt/ros/jazzy/setup.bash && "
        f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
        "export "
        + " ".join(
            f"{key}={shlex.quote(value)}"
            for key, value in exported_environment.items()
        )
        + " && "
    )
    if zenoh_session_config_uri:
        command += (
            "export ZENOH_SESSION_CONFIG_URI="
            f"{shlex.quote(zenoh_session_config_uri)} && "
        )
    arguments = [
        SERIALIZED_RELAY_EXECUTABLE,
        "--samples",
        str(samples),
        "--timeout-ms",
        str(max(1, round(timeout_s * 1000.0))),
        "--linger-ms",
        str(max(0, round(linger_s * 1000.0))),
    ]
    for source, destination in zip(
        source_specs,
        destination_specs,
        strict=True,
    ):
        arguments.extend(
            [
                "--mapping",
                f"{source['topic']}={destination['topic']}",
            ]
        )
    return command + " ".join(shlex.quote(value) for value in arguments)


def run_probe(
    *,
    root: Path,
    image: str,
    rmw: str,
    profile: str,
    enable_netem: bool,
    require_netem: bool,
    netem_loss_scale: float,
    repetition_seed: int | None,
    samples: int,
    robot_count: int,
    payload_bytes: int = 0,
    publish_interval_ms: int,
    timeout_s: float,
    publisher_linger_s: float = 6.0,
    relay_mode: str = "generic_serialized",
    fleetqox_loss_resilient_fragment_chunk_bytes: int = (
        DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES
    ),
    fleetqox_reliable_max_retransmissions: int = (
        DEFAULT_FLEETQOX_RELIABLE_MAX_RETRANSMISSIONS
    ),
    fleetqox_reliable_ack_timeout_ms: int | None = None,
    fleetqox_udp_send_pacing_us: int = 0,
    fleetqox_udp_datagram_budget_bytes: int = (
        DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES
    ),
    fleetqox_fragment_nack_interval_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_NACK_INTERVAL_MS
    ),
    fleetqox_fragment_nack_max_requests: int = (
        DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_REQUESTS
    ),
    fleetqox_fragment_nack_max_indexes_per_request: int = (
        DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST
    ),
    fleetqox_fragment_tail_guard_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_TAIL_GUARD_MS
    ),
    fleetqox_fragment_history_limit: int = (
        DEFAULT_FLEETQOX_FRAGMENT_HISTORY_LIMIT
    ),
    fleetqox_fragment_assembly_limit: int = (
        DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_LIMIT
    ),
    fleetqox_fragment_max_assembly_bytes: int = (
        DEFAULT_FLEETQOX_FRAGMENT_MAX_ASSEMBLY_BYTES
    ),
    fleetqox_fragment_assembly_ttl_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS
    ),
    fleetqox_fragment_async_send: bool = False,
    fleetqox_fragment_send_queue_limit: int = (
        DEFAULT_FLEETQOX_FRAGMENT_SEND_QUEUE_LIMIT
    ),
    fleetqox_fragment_queue_admission_threshold: int = (
        DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_THRESHOLD
    ),
    fleetqox_fragment_queue_admission_timeout_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS
    ),
    fleetqox_fragment_repair_queue_limit: int = (
        DEFAULT_FLEETQOX_FRAGMENT_REPAIR_QUEUE_LIMIT
    ),
    fleetqox_fragment_repair_cooldown_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_REPAIR_COOLDOWN_MS
    ),
    fleetqox_fragment_whole_fallback_interval_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS
    ),
    fleetqox_fragment_whole_fallback_grace_ms: int = (
        DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_GRACE_MS
    ),
    fleetqox_publisher_test_drop_fragment_indexes: str = "",
) -> dict[str, Any]:
    if samples <= 0 or robot_count <= 0:
        raise ValueError("samples and robot_count must be positive")
    if payload_bytes < 0:
        raise ValueError("payload_bytes must be non-negative")
    if publish_interval_ms < 0 or timeout_s <= 0 or publisher_linger_s < 0:
        raise ValueError("timing values are outside their valid range")
    if netem_loss_scale < 0:
        raise ValueError("netem_loss_scale must be non-negative")
    if not 0 <= fleetqox_loss_resilient_fragment_chunk_bytes <= 60000:
        raise ValueError(
            "fleetqox_loss_resilient_fragment_chunk_bytes is outside 0..60000"
        )
    if not 0 <= fleetqox_reliable_max_retransmissions <= 100:
        raise ValueError(
            "fleetqox_reliable_max_retransmissions is outside 0..100"
        )
    if (
        fleetqox_reliable_ack_timeout_ms is not None
        and not 1 <= fleetqox_reliable_ack_timeout_ms <= 60000
    ):
        raise ValueError("fleetqox_reliable_ack_timeout_ms is outside 1..60000")
    if not 0 <= fleetqox_udp_send_pacing_us <= 100000:
        raise ValueError("fleetqox_udp_send_pacing_us is outside 0..100000")
    if (
        fleetqox_udp_datagram_budget_bytes != 0
        and not 512 <= fleetqox_udp_datagram_budget_bytes <= 65507
    ):
        raise ValueError(
            "fleetqox_udp_datagram_budget_bytes is outside 512..65507 or zero"
        )
    if not 10 <= fleetqox_fragment_nack_interval_ms <= 1000:
        raise ValueError(
            "fleetqox_fragment_nack_interval_ms is outside 10..1000"
        )
    if not 0 <= fleetqox_fragment_nack_max_requests <= 100:
        raise ValueError(
            "fleetqox_fragment_nack_max_requests is outside 0..100"
        )
    if not 1 <= fleetqox_fragment_nack_max_indexes_per_request <= 64:
        raise ValueError(
            "fleetqox_fragment_nack_max_indexes_per_request is outside 1..64"
        )
    if not 100 <= fleetqox_fragment_tail_guard_ms <= 60000:
        raise ValueError(
            "fleetqox_fragment_tail_guard_ms is outside 100..60000"
        )
    if not 0 <= fleetqox_fragment_history_limit <= 4096:
        raise ValueError("fleetqox_fragment_history_limit is outside 0..4096")
    if not 0 <= fleetqox_fragment_assembly_limit <= 16384:
        raise ValueError(
            "fleetqox_fragment_assembly_limit is outside 0..16384"
        )
    if not 0 <= fleetqox_fragment_max_assembly_bytes <= 256 * 1024 * 1024:
        raise ValueError(
            "fleetqox_fragment_max_assembly_bytes is outside 0..268435456"
        )
    if not 1000 <= fleetqox_fragment_assembly_ttl_ms <= 600000:
        raise ValueError(
            "fleetqox_fragment_assembly_ttl_ms is outside 1000..600000"
        )
    if not 0 <= fleetqox_fragment_send_queue_limit <= 262144:
        raise ValueError(
            "fleetqox_fragment_send_queue_limit is outside 0..262144"
        )
    if not 0 <= fleetqox_fragment_queue_admission_threshold <= 262144:
        raise ValueError(
            "fleetqox_fragment_queue_admission_threshold is outside 0..262144"
        )
    if not 0 <= fleetqox_fragment_queue_admission_timeout_ms <= 60000:
        raise ValueError(
            "fleetqox_fragment_queue_admission_timeout_ms is outside 0..60000"
        )
    if not 0 <= fleetqox_fragment_repair_queue_limit <= 262144:
        raise ValueError(
            "fleetqox_fragment_repair_queue_limit is outside 0..262144"
        )
    if not 0 <= fleetqox_fragment_repair_cooldown_ms <= 60000:
        raise ValueError(
            "fleetqox_fragment_repair_cooldown_ms is outside 0..60000"
        )
    if not 0 <= fleetqox_fragment_whole_fallback_interval_ms <= 60000:
        raise ValueError(
            "fleetqox_fragment_whole_fallback_interval_ms is outside 0..60000"
        )
    if not 0 <= fleetqox_fragment_whole_fallback_grace_ms <= 60000:
        raise ValueError(
            "fleetqox_fragment_whole_fallback_grace_ms is outside 0..60000"
        )
    if fleetqox_publisher_test_drop_fragment_indexes:
        try:
            test_drop_indexes = [
                int(value.strip())
                for value in
                fleetqox_publisher_test_drop_fragment_indexes.split(",")
                if value.strip()
            ]
        except ValueError as error:
            raise ValueError(
                "fleetqox_publisher_test_drop_fragment_indexes is invalid"
            ) from error
        if (
            not test_drop_indexes
            or any(value < 0 for value in test_drop_indexes)
        ):
            raise ValueError(
                "fleetqox_publisher_test_drop_fragment_indexes is invalid"
            )
    if relay_mode not in {"generic_serialized", "rclpy_typed"}:
        raise ValueError("unsupported relay_mode")
    if rmw == FLEETQOX_RMW and relay_mode != "generic_serialized":
        raise ValueError("FleetRMW matched-middle mode requires the generic serialized relay")
    destinations = topic_specs_for_robot_count(robot_count)
    sources = ingress_specs(destinations)
    expected_control = samples * robot_count
    expected_state = samples * robot_count
    if rmw == FLEETQOX_RMW:
        try:
            ensure_generic_serialized_relay(root=root, image=image)
        except subprocess.CalledProcessError as exc:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "reason": "fleetqox_workspace_build_failed",
                "image": image,
                "rmw": rmw,
                "profile": profile,
                "robot_count": robot_count,
                "returncode": exc.returncode,
                "stdout_excerpt": excerpt(exc.stdout),
                "stderr_excerpt": excerpt(exc.stderr),
            }
        availability = {
            "available": True,
            "source": "workspace_colcon_install",
            "install": f"/work/{SERIALIZED_RELAY_INSTALL}",
        }
    else:
        availability = probe_rmw_available(image, rmw)
    if not availability["available"]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "skipped",
            "reason": "rmw_unavailable",
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "robot_count": robot_count,
            "rmw_probe": availability,
        }

    nonce = time.time_ns()
    suffix = f"{os.getpid()}-{nonce}"
    domain_id = 120 + (nonce % 80)
    network = f"fleetrmw-ros2-relay-net-{suffix}"
    subscriber_name = f"fleetrmw-ros2-relay-sub-{suffix}"
    relay_name = f"fleetrmw-ros2-relay-mid-{suffix}"
    publisher_name = f"fleetrmw-ros2-relay-pub-{suffix}"
    zenoh_router_name = f"fleetrmw-ros2-relay-zenoh-router-{suffix}"
    work_dir = root / f".tmp_fleetrmw_ros2_relay_{suffix}"
    subscriber_script = work_dir / "subscriber.py"
    relay_script = work_dir / "relay.py"
    publisher_script = work_dir / "publisher.py"
    netem_status_path = work_dir / "publisher_netem_status.json"
    netem_status_container = f"/work/{netem_status_path.relative_to(root)}"
    publisher_ready_container = "/tmp/fleetrmw_probe_ready"
    publisher_start_container = "/tmp/fleetrmw_probe_start"
    zenoh_config = work_dir / "zenoh-session-router.json5"
    zenoh_config_container = f"/work/{zenoh_config.relative_to(root)}"
    use_zenoh_router = rmw == "rmw_zenoh_cpp"
    use_fleetqox_direct_peers = rmw == FLEETQOX_RMW
    fleetqox_environments: dict[str, dict[str, str]] = {}
    fleetqox_addresses: dict[str, str] = {}
    netem = netem_config_for_path(
        profile_by_name(profile),
        path_id="primary_wifi",
        loss_scale=netem_loss_scale,
        repetition_seed=repetition_seed,
    )
    fleetqox_one_way_budget_ms = (
        float(netem["delay_ms"]) + 2.0 * float(netem["jitter_ms"])
    )
    if fleetqox_reliable_ack_timeout_ms is None:
        fleetqox_reliable_ack_timeout_ms = max(
            100,
            int(math.ceil(2.0 * fleetqox_one_way_budget_ms + 50.0)),
        )
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        if relay_mode == "generic_serialized":
            ensure_generic_serialized_relay(root=root, image=image)
        write_relay_probe_scripts(
            subscriber_script=subscriber_script,
            relay_script=relay_script,
            publisher_script=publisher_script,
            destination_specs=destinations,
            source_specs=sources,
            samples=samples,
            payload_bytes=payload_bytes,
            publish_interval_ms=publish_interval_ms,
            timeout_s=timeout_s,
            publisher_linger_s=publisher_linger_s,
        )
        run(["docker", "network", "create", network])
        if use_fleetqox_direct_peers:
            fleetqox_addresses = fleetqox_static_addresses(network)
            fleetqox_environments = {
                "publisher": {
                    "FLEETQOX_RMW_BIND": "0.0.0.0:49811",
                    "FLEETQOX_RMW_PEERS":
                        f"{fleetqox_addresses['relay']}:49812",
                },
                "relay": {
                    "FLEETQOX_RMW_BIND": "0.0.0.0:49812",
                    "FLEETQOX_RMW_PEERS": (
                        f"{fleetqox_addresses['publisher']}:49811,"
                        f"{fleetqox_addresses['subscriber']}:49813"
                    ),
                },
                "subscriber": {
                    "FLEETQOX_RMW_BIND": "0.0.0.0:49813",
                    "FLEETQOX_RMW_PEERS":
                        f"{fleetqox_addresses['relay']}:49812",
                },
            }
            for environment in fleetqox_environments.values():
                environment.update(
                    {
                        "FLEETQOX_RMW_RELIABLE_ACK_TIMEOUT_MS":
                            str(fleetqox_reliable_ack_timeout_ms),
                        "FLEETQOX_RMW_RELIABLE_MAX_RETRANSMISSIONS":
                            str(fleetqox_reliable_max_retransmissions),
                        "FLEETQOX_RMW_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES":
                            str(
                                fleetqox_loss_resilient_fragment_chunk_bytes
                            ),
                        "FLEETQOX_RMW_UDP_SEND_PACING_US":
                            str(fleetqox_udp_send_pacing_us),
                        "FLEETQOX_RMW_UDP_DATAGRAM_BUDGET_BYTES":
                            str(fleetqox_udp_datagram_budget_bytes),
                        "FLEETQOX_RMW_FRAGMENT_NACK_INTERVAL_MS":
                            str(fleetqox_fragment_nack_interval_ms),
                        "FLEETQOX_RMW_FRAGMENT_NACK_MAX_REQUESTS":
                            str(fleetqox_fragment_nack_max_requests),
                        "FLEETQOX_RMW_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST":
                            str(
                                fleetqox_fragment_nack_max_indexes_per_request
                            ),
                        "FLEETQOX_RMW_FRAGMENT_TAIL_GUARD_MS":
                            str(fleetqox_fragment_tail_guard_ms),
                        "FLEETQOX_RMW_FRAGMENT_HISTORY_LIMIT":
                            str(fleetqox_fragment_history_limit),
                        "FLEETQOX_RMW_FRAGMENT_ASSEMBLY_LIMIT":
                            str(fleetqox_fragment_assembly_limit),
                        "FLEETQOX_RMW_FRAGMENT_MAX_ASSEMBLY_BYTES":
                            str(fleetqox_fragment_max_assembly_bytes),
                        "FLEETQOX_RMW_FRAGMENT_ASSEMBLY_TTL_MS":
                            str(fleetqox_fragment_assembly_ttl_ms),
                        "FLEETQOX_RMW_FRAGMENT_ASYNC_SEND":
                            ("1" if fleetqox_fragment_async_send else "0"),
                        "FLEETQOX_RMW_FRAGMENT_SEND_QUEUE_LIMIT":
                            str(fleetqox_fragment_send_queue_limit),
                        "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_THRESHOLD":
                            str(fleetqox_fragment_queue_admission_threshold),
                        "FLEETQOX_RMW_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS":
                            str(fleetqox_fragment_queue_admission_timeout_ms),
                        "FLEETQOX_RMW_FRAGMENT_REPAIR_QUEUE_LIMIT":
                            str(fleetqox_fragment_repair_queue_limit),
                        "FLEETQOX_RMW_FRAGMENT_REPAIR_COOLDOWN_MS":
                            str(fleetqox_fragment_repair_cooldown_ms),
                        "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS":
                            str(fleetqox_fragment_whole_fallback_interval_ms),
                        "FLEETQOX_RMW_FRAGMENT_WHOLE_FALLBACK_GRACE_MS":
                            str(fleetqox_fragment_whole_fallback_grace_ms),
                    }
                )
            if fleetqox_publisher_test_drop_fragment_indexes:
                fleetqox_environments["publisher"][
                    "FLEETQOX_RMW_TEST_DROP_FRAGMENT_INDEXES"
                ] = fleetqox_publisher_test_drop_fragment_indexes
        if use_zenoh_router:
            write_zenoh_session_config(zenoh_config, router_host=zenoh_router_name)
            start_container(
                root=root,
                image=image,
                name=zenoh_router_name,
                network=network,
                command=(
                    "source /opt/ros/jazzy/setup.bash && "
                    "exec ros2 run rmw_zenoh_cpp rmw_zenohd"
                ),
            )
            wait_for_container_tcp(zenoh_router_name, port=7447, timeout_s=15.0)

        def command_for(script: Path, *, role: str) -> str:
            if use_fleetqox_direct_peers:
                environment = {
                    "RMW_IMPLEMENTATION": rmw,
                    "ROS_DOMAIN_ID": str(domain_id),
                    **fleetqox_environments[role],
                }
                exports = " ".join(
                    f"{key}={shlex.quote(value)}"
                    for key, value in environment.items()
                )
                return (
                    "source /opt/ros/jazzy/setup.bash && "
                    f"source /work/{SERIALIZED_RELAY_INSTALL}/setup.bash && "
                    f"export {exports} && "
                    f"python3 /work/{script.relative_to(root)}"
                )
            return ros_command(
                rmw=rmw,
                domain_id=domain_id,
                python_path=f"/work/{script.relative_to(root)}",
                zenoh_session_config_uri=(
                    zenoh_config_container if use_zenoh_router else None
                ),
            )

        start_container(
            root=root,
            image=image,
            name=subscriber_name,
            network=network,
            command=command_for(subscriber_script, role="subscriber"),
            extra_args=(
                ("--ip", fleetqox_addresses["subscriber"])
                if use_fleetqox_direct_peers else ()
            ),
        )
        time.sleep(0.5)
        start_container(
            root=root,
            image=image,
            name=relay_name,
            network=network,
            command=(
                generic_serialized_relay_command(
                    rmw=rmw,
                    domain_id=domain_id,
                    zenoh_session_config_uri=(
                        zenoh_config_container if use_zenoh_router else None
                    ),
                    source_specs=sources,
                    destination_specs=destinations,
                    samples=samples,
                    timeout_s=timeout_s,
                    linger_s=publisher_linger_s + 0.5,
                    environment=(
                        fleetqox_environments["relay"]
                        if use_fleetqox_direct_peers else None
                    ),
                )
                if relay_mode == "generic_serialized"
                else command_for(relay_script, role="relay")
            ),
            extra_args=(
                ("--ip", fleetqox_addresses["relay"])
                if use_fleetqox_direct_peers else ()
            ),
        )
        time.sleep(1.0)
        start_container(
            root=root,
            image=image,
            name=publisher_name,
            network=network,
            command=(
                f"export FLEETQOX_PROBE_READY_FILE={publisher_ready_container} "
                f"FLEETQOX_PROBE_START_FILE={publisher_start_container} && "
                + command_for(publisher_script, role="publisher")
            ),
            extra_args=(
                (
                    "--ip",
                    fleetqox_addresses["publisher"],
                    *(("--cap-add", "NET_ADMIN") if enable_netem else ()),
                )
                if use_fleetqox_direct_peers
                else (("--cap-add", "NET_ADMIN") if enable_netem else ())
            ),
        )
        wait_for_container_path(
            publisher_name, publisher_ready_container, timeout_s=12.0
        )
        if enable_netem:
            run(
                [
                    "docker",
                    "exec",
                    publisher_name,
                    "bash",
                    "-lc",
                    netem_shell_prefix(
                        netem,
                        status_file=netem_status_container,
                        require=require_netem,
                    ),
                ]
            )
        run(["docker", "exec", publisher_name, "touch", publisher_start_container])
        publisher_rc = int(run(["docker", "wait", publisher_name]).stdout.strip())
        relay_rc = int(run(["docker", "wait", relay_name]).stdout.strip())
        subscriber_rc = int(run(["docker", "wait", subscriber_name]).stdout.strip())
        publisher_logs = run(["docker", "logs", publisher_name])
        relay_logs = run(["docker", "logs", relay_name])
        subscriber_logs = run(["docker", "logs", subscriber_name])
        publisher = parse_last_json(publisher_logs.stdout)
        relay = parse_last_json(relay_logs.stdout)
        subscriber = parse_last_json(subscriber_logs.stdout)
        netem_status = {"direct_pub": read_json(netem_status_path)}
        netem_ok = netem_status_ok(
            netem_status, enabled=enable_netem, required=require_netem
        )
        control_count = int(subscriber.get("control_payload_count", 0))
        state_count = int(subscriber.get("state_payload_count", 0))
        relay_count = int(relay.get("relayed_count", 0))
        expected_total = len(destinations) * samples
        ok = (
            publisher_rc == 0
            and relay_rc == 0
            and subscriber_rc == 0
            and publisher.get("status") == "ok"
            and relay.get("status") == "ok"
            and subscriber.get("status") == "ok"
            and publisher.get("payload_size_contract_ok") is True
            and relay_count >= expected_total
            and control_count >= expected_control
            and state_count >= expected_state
            and netem_ok
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "failed",
            "system": rmw,
            "topology": "publisher-relay-subscriber",
            "relay_scope": (
                "rclcpp_generic_serialized_passthrough"
                if relay_mode == "generic_serialized"
                else "rclpy_std_msgs_string_deserialize_republish"
            ),
            "relay_mode": relay_mode,
            "relay_executor_drain_mode": (
                relay.get("executor_drain_mode")
                if relay_mode == "generic_serialized" else None
            ),
            "middle_payload_remains_serialized":
                relay_mode == "generic_serialized",
            "middle_application_deserialization":
                relay_mode != "generic_serialized",
            "middle_rmw_termination_republish":
                relay_mode == "generic_serialized",
            "fleetqox_direct_peer_transport": use_fleetqox_direct_peers,
            "fleetqox_static_peer_addresses":
                fleetqox_addresses if use_fleetqox_direct_peers else {},
            "fleetqox_reliable_ack_timeout_ms": (
                fleetqox_reliable_ack_timeout_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_reliable_max_retransmissions": (
                fleetqox_reliable_max_retransmissions
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_loss_resilient_fragment_chunk_bytes": (
                fleetqox_loss_resilient_fragment_chunk_bytes
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_udp_send_pacing_us": (
                fleetqox_udp_send_pacing_us
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_udp_datagram_budget_bytes": (
                fleetqox_udp_datagram_budget_bytes
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_nack_interval_ms": (
                fleetqox_fragment_nack_interval_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_nack_max_requests": (
                fleetqox_fragment_nack_max_requests
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_nack_max_indexes_per_request": (
                fleetqox_fragment_nack_max_indexes_per_request
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_tail_guard_ms": (
                fleetqox_fragment_tail_guard_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_history_limit": (
                fleetqox_fragment_history_limit
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_assembly_limit": (
                fleetqox_fragment_assembly_limit
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_max_assembly_bytes": (
                fleetqox_fragment_max_assembly_bytes
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_assembly_ttl_ms": (
                fleetqox_fragment_assembly_ttl_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_async_send": (
                fleetqox_fragment_async_send
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_send_queue_limit": (
                fleetqox_fragment_send_queue_limit
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_queue_admission_threshold": (
                fleetqox_fragment_queue_admission_threshold
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_queue_admission_timeout_ms": (
                fleetqox_fragment_queue_admission_timeout_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_repair_queue_limit": (
                fleetqox_fragment_repair_queue_limit
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_repair_cooldown_ms": (
                fleetqox_fragment_repair_cooldown_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_whole_fallback_interval_ms": (
                fleetqox_fragment_whole_fallback_interval_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_fragment_whole_fallback_grace_ms": (
                fleetqox_fragment_whole_fallback_grace_ms
                if use_fleetqox_direct_peers else None
            ),
            "fleetqox_publisher_test_drop_fragment_indexes": (
                fleetqox_publisher_test_drop_fragment_indexes
                if use_fleetqox_direct_peers else None
            ),
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "profile_config": profile_by_name(profile).as_dict(),
            "robot_count": robot_count,
            "topic_count": len(destinations),
            "samples": samples,
            "payload_bytes": payload_bytes,
            "payload_size_contract_ok": (
                publisher.get("payload_size_contract_ok") is True
            ),
            "payload_size_min_bytes": int(
                publisher.get("payload_size_min_bytes", 0)
            ),
            "payload_size_max_bytes": int(
                publisher.get("payload_size_max_bytes", 0)
            ),
            "publish_interval_ms": publish_interval_ms,
            "timeout_s": timeout_s,
            "repetition_seed": repetition_seed,
            "publisher_linger_s": publisher_linger_s,
            "zenoh_router_enabled": use_zenoh_router,
            "netem_enabled": enable_netem,
            "netem_required": require_netem,
            "netem_loss_scale": netem_loss_scale,
            "netem": netem,
            "netem_status": netem_status,
            "netem_schema_version": NETEM_SCHEMA_VERSION,
            "netem_seed_semantics": NETEM_SEED_SEMANTICS if enable_netem else "",
            "rmw_probe": availability,
            "publisher_returncode": publisher_rc,
            "relay_returncode": relay_rc,
            "subscriber_returncode": subscriber_rc,
            "publisher_stderr_excerpt": excerpt(publisher_logs.stderr),
            "relay_stderr_excerpt": excerpt(relay_logs.stderr),
            "subscriber_stderr_excerpt": excerpt(subscriber_logs.stderr),
            "publisher": publisher,
            "relay": relay,
            "subscriber": subscriber,
            "relay_expected_count": expected_total,
            "relay_payload_count": relay_count,
            "control_payload_count": control_count,
            "state_payload_count": state_count,
            "control_expected_count": expected_control,
            "state_expected_count": expected_state,
            "control_delivery_ratio": control_count / expected_control,
            "state_delivery_ratio": state_count / expected_state,
            "control_latency_ms_mean": _float(
                subscriber.get("control_latency_ms_mean")
            ),
            "state_latency_ms_mean": _float(
                subscriber.get("state_latency_ms_mean")
            ),
            "control_latency_ms_p95": _float(
                subscriber.get("control_latency_ms_p95")
            ),
            "state_latency_ms_p95": _float(subscriber.get("state_latency_ms_p95")),
            "min_topic_delivery_ratio": _float(
                subscriber.get("min_topic_delivery_ratio")
            ),
            "per_topic_payload_count": subscriber.get("per_topic_payload_count", {}),
            "per_topic_delivery_ratio": subscriber.get("per_topic_delivery_ratio", {}),
        }
    except subprocess.CalledProcessError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "reason": "harness_exception",
            "image": image,
            "rmw": rmw,
            "profile": profile,
            "robot_count": robot_count,
            "repetition_seed": repetition_seed,
            "returncode": exc.returncode,
            "stdout_excerpt": excerpt(exc.stdout),
            "stderr_excerpt": excerpt(exc.stderr),
            "publisher_diagnostics": container_diagnostics(publisher_name),
            "relay_diagnostics": container_diagnostics(relay_name),
            "subscriber_diagnostics": container_diagnostics(subscriber_name),
            "zenoh_router_diagnostics": (
                container_diagnostics(zenoh_router_name) if use_zenoh_router else {}
            ),
        }
    finally:
        for name in (
            publisher_name,
            relay_name,
            subscriber_name,
            zenoh_router_name,
        ):
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                capture_output=True,
                text=True,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--rmw", default="rmw_fastrtps_cpp")
    parser.add_argument("--profile", default="roaming")
    parser.add_argument("--enable-netem", action="store_true")
    parser.add_argument("--require-netem", action="store_true")
    parser.add_argument("--netem-loss-scale", type=float, default=0.25)
    parser.add_argument("--repetition-seed", type=int, default=7)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--robot-count", type=int, default=8)
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="exact UTF-8 message data size; zero preserves the metadata-only payload",
    )
    parser.add_argument("--publish-interval-ms", type=int, default=50)
    parser.add_argument("--timeout-s", type=float, default=25.0)
    parser.add_argument("--publisher-linger-s", type=float, default=6.0)
    parser.add_argument(
        "--fleetqox-loss-resilient-fragment-chunk-bytes",
        type=int,
        default=DEFAULT_FLEETQOX_LOSS_RESILIENT_FRAGMENT_CHUNK_BYTES,
    )
    parser.add_argument(
        "--fleetqox-reliable-max-retransmissions",
        type=int,
        default=DEFAULT_FLEETQOX_RELIABLE_MAX_RETRANSMISSIONS,
    )
    parser.add_argument(
        "--fleetqox-reliable-ack-timeout-ms",
        type=int,
        default=0,
        help="zero derives the timeout from the selected netem profile",
    )
    parser.add_argument(
        "--fleetqox-udp-send-pacing-us",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--fleetqox-udp-datagram-budget-bytes",
        type=int,
        default=DEFAULT_FLEETQOX_UDP_DATAGRAM_BUDGET_BYTES,
        help="zero disables the application-level UDP wire-size budget",
    )
    parser.add_argument(
        "--fleetqox-fragment-nack-interval-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_NACK_INTERVAL_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-nack-max-requests",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_REQUESTS,
    )
    parser.add_argument(
        "--fleetqox-fragment-nack-max-indexes-per-request",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_NACK_MAX_INDEXES_PER_REQUEST,
    )
    parser.add_argument(
        "--fleetqox-fragment-tail-guard-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_TAIL_GUARD_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-history-limit",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_HISTORY_LIMIT,
    )
    parser.add_argument(
        "--fleetqox-fragment-assembly-limit",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_LIMIT,
    )
    parser.add_argument(
        "--fleetqox-fragment-max-assembly-bytes",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_MAX_ASSEMBLY_BYTES,
    )
    parser.add_argument(
        "--fleetqox-fragment-assembly-ttl-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_ASSEMBLY_TTL_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-async-send",
        action="store_true",
    )
    parser.add_argument(
        "--fleetqox-fragment-send-queue-limit",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_SEND_QUEUE_LIMIT,
    )
    parser.add_argument(
        "--fleetqox-fragment-queue-admission-threshold",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_THRESHOLD,
    )
    parser.add_argument(
        "--fleetqox-fragment-queue-admission-timeout-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_QUEUE_ADMISSION_TIMEOUT_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-repair-cooldown-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_REPAIR_COOLDOWN_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-repair-queue-limit",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_REPAIR_QUEUE_LIMIT,
    )
    parser.add_argument(
        "--fleetqox-fragment-whole-fallback-interval-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_INTERVAL_MS,
    )
    parser.add_argument(
        "--fleetqox-fragment-whole-fallback-grace-ms",
        type=int,
        default=DEFAULT_FLEETQOX_FRAGMENT_WHOLE_FALLBACK_GRACE_MS,
    )
    parser.add_argument(
        "--fleetqox-publisher-test-drop-fragment-indexes",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--relay-mode",
        choices=("generic_serialized", "rclpy_typed"),
        default="generic_serialized",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        default=Path("results_rmw_socket/ros2_relay_rmw_netem_probe_summary.json"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_probe(
        root=ROOT,
        image=args.image,
        rmw=args.rmw,
        profile=args.profile,
        enable_netem=args.enable_netem,
        require_netem=args.require_netem,
        netem_loss_scale=max(args.netem_loss_scale, 0.0),
        repetition_seed=args.repetition_seed,
        samples=max(args.samples, 1),
        robot_count=max(args.robot_count, 1),
        payload_bytes=max(args.payload_bytes, 0),
        publish_interval_ms=max(args.publish_interval_ms, 0),
        timeout_s=max(args.timeout_s, 1.0),
        publisher_linger_s=max(args.publisher_linger_s, 0.0),
        relay_mode=args.relay_mode,
        fleetqox_loss_resilient_fragment_chunk_bytes=max(
            min(args.fleetqox_loss_resilient_fragment_chunk_bytes, 60000),
            0,
        ),
        fleetqox_reliable_max_retransmissions=max(
            min(args.fleetqox_reliable_max_retransmissions, 100),
            0,
        ),
        fleetqox_reliable_ack_timeout_ms=(
            max(min(args.fleetqox_reliable_ack_timeout_ms, 60000), 1)
            if args.fleetqox_reliable_ack_timeout_ms > 0
            else None
        ),
        fleetqox_udp_send_pacing_us=max(
            min(args.fleetqox_udp_send_pacing_us, 100000),
            0,
        ),
        fleetqox_udp_datagram_budget_bytes=(
            max(min(args.fleetqox_udp_datagram_budget_bytes, 65507), 512)
            if args.fleetqox_udp_datagram_budget_bytes > 0
            else 0
        ),
        fleetqox_fragment_nack_interval_ms=max(
            min(args.fleetqox_fragment_nack_interval_ms, 1000),
            10,
        ),
        fleetqox_fragment_nack_max_requests=max(
            min(args.fleetqox_fragment_nack_max_requests, 100),
            0,
        ),
        fleetqox_fragment_nack_max_indexes_per_request=max(
            min(args.fleetqox_fragment_nack_max_indexes_per_request, 64),
            1,
        ),
        fleetqox_fragment_tail_guard_ms=max(
            min(args.fleetqox_fragment_tail_guard_ms, 60000),
            100,
        ),
        fleetqox_fragment_history_limit=max(
            min(args.fleetqox_fragment_history_limit, 4096),
            0,
        ),
        fleetqox_fragment_assembly_limit=max(
            min(args.fleetqox_fragment_assembly_limit, 16384),
            0,
        ),
        fleetqox_fragment_max_assembly_bytes=max(
            min(args.fleetqox_fragment_max_assembly_bytes, 256 * 1024 * 1024),
            0,
        ),
        fleetqox_fragment_assembly_ttl_ms=max(
            min(args.fleetqox_fragment_assembly_ttl_ms, 600000),
            1000,
        ),
        fleetqox_fragment_async_send=args.fleetqox_fragment_async_send,
        fleetqox_fragment_send_queue_limit=max(
            min(args.fleetqox_fragment_send_queue_limit, 262144),
            0,
        ),
        fleetqox_fragment_queue_admission_threshold=max(
            min(args.fleetqox_fragment_queue_admission_threshold, 262144),
            0,
        ),
        fleetqox_fragment_queue_admission_timeout_ms=max(
            min(args.fleetqox_fragment_queue_admission_timeout_ms, 60000),
            0,
        ),
        fleetqox_fragment_repair_cooldown_ms=max(
            min(args.fleetqox_fragment_repair_cooldown_ms, 60000),
            0,
        ),
        fleetqox_fragment_repair_queue_limit=max(
            min(args.fleetqox_fragment_repair_queue_limit, 262144),
            0,
        ),
        fleetqox_fragment_whole_fallback_interval_ms=max(
            min(args.fleetqox_fragment_whole_fallback_interval_ms, 60000),
            0,
        ),
        fleetqox_fragment_whole_fallback_grace_ms=max(
            min(args.fleetqox_fragment_whole_fallback_grace_ms, 60000),
            0,
        ),
        fleetqox_publisher_test_drop_fragment_indexes=(
            args.fleetqox_publisher_test_drop_fragment_indexes
        ),
    )
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(
            f"status={summary['status']} rmw={summary.get('rmw')} "
            f"relay={summary.get('relay_payload_count')}"
        )
    return 0 if summary["status"] in {"ok", "skipped"} else 1


RELAY_SCRIPT = r'''
import json
import time

import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from std_msgs.msg import String

MAPPINGS = __MAPPINGS_JSON__
SAMPLES = __SAMPLES__
TIMEOUT_S = __TIMEOUT_S__
RELAY_LINGER_S = __RELAY_LINGER_S__

rclpy.init()
node = rclpy.create_node("fleetrmw_same_hop_baseline_relay")
qos = QoSProfile(
    history=HistoryPolicy.KEEP_LAST,
    depth=max(10, SAMPLES * 2),
    reliability=ReliabilityPolicy.RELIABLE,
)
publishers = {
    mapping["source"]: node.create_publisher(String, mapping["destination"], qos)
    for mapping in MAPPINGS
}
counts = {mapping["source"]: 0 for mapping in MAPPINGS}


def make_callback(source):
    def callback(message):
        publishers[source].publish(message)
        counts[source] += 1
    return callback


subscriptions = [
    node.create_subscription(String, mapping["source"], make_callback(mapping["source"]), qos)
    for mapping in MAPPINGS
]
discovery_deadline = time.time() + 8.0
while time.time() < discovery_deadline:
    rclpy.spin_once(node, timeout_sec=0.05)
    if all(publisher.get_subscription_count() > 0 for publisher in publishers.values()):
        break

deadline = time.time() + TIMEOUT_S
while time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.1)
    if all(count >= SAMPLES for count in counts.values()):
        break

time.sleep(RELAY_LINGER_S)
status = "ok" if all(count >= SAMPLES for count in counts.values()) else "failed"
result = {
    "status": status,
    "relayed_count": sum(counts.values()),
    "expected_count": SAMPLES * len(MAPPINGS),
    "per_source_count": counts,
    "min_source_count": min(counts.values()) if counts else 0,
    "mapping_count": len(MAPPINGS),
}
print(json.dumps(result, sort_keys=True))
node.destroy_node()
rclpy.shutdown()
raise SystemExit(0 if status == "ok" else 1)
'''


if __name__ == "__main__":
    raise SystemExit(main())
