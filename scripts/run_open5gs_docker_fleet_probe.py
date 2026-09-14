"""Bảng V/VI "5G" profile, Open5GS + UERANSIM edition -- REPLACES the
ns-3-based ghost-node "nr" profile (run_ns3_docker_container_fleet_probe.py's
run_nr_probe()/wire_network_nr_l2()) with a real Open5GS 5G SA core plus
simulated UERANSIM gNB/UEs, per explicit user request: comparing FleetRMW
against Fast DDS/CycloneDDS/Zenoh over the SAME kind of traffic (ROS 2 /
FleetQoX trace replay, Ricart-Agrawala coordination) but now actually
traversing NGAP/GTP-U/PFCP signaling and a real UPF, not a hand-rolled
ghost-node L2 splice.

IMPORTANT LABELING REQUIREMENT (per user instruction -- do not drop this
when reporting results anywhere: docs, tables, commit messages): this is
"5G SA emulation (Open5GS + UERANSIM)", i.e. UERANSIM's gNB/UE stack talks
NGAP/NAS/RRC to a REAL Open5GS core over plain sockets -- there is NO
radio-channel model at all (no SINR, no fading, no propagation loss, no
contention/backoff on an actual PHY). Every previous ns-3 "5G" profile
result in docs/AUDIT_ACCEPTANCE_TRACKING.md and docs/BANG_V_VI_KET_QUA.md
stays valid as its own distinct data point (ns-3 5G-LENA radio-channel
simulation); this module is NOT a continuation of that number series, it
is a different network profile with a different loss/latency source
(core-network + Linux-scheduler overhead under N-way concurrent GTP-U
tunnels, not radio contention). Never present this profile's numbers as
"the same 5G row, just re-measured" -- they answer a different question
(middleware behavior on TOP OF a real 5G core) than the ns-3 profile did
(middleware behavior under a simulated radio channel).

Architecture
------------
Vendored from https://github.com/herlesupreeth/docker_open5gs (BSD-2-Clause,
see external/open5gs/LICENSE) into external/open5gs/, adapted only for this
host's docker network layout (see external/open5gs/.env's TEST_NETWORK --
the vendor default 172.22.0.0/16 collided with other docker bridges already
running on this shared host; remapped to 10.90.0.0/24, see the commit that
vendored it for the full collision list).

Two lifecycles, deliberately split because the core-network stack is slow
to bring up (12 NFs, each doing real SBI/NRF registration handshakes) and
has nothing to do with any individual probe run -- rebuilding it per run
would make a 36-run Bảng V/VI batch (N=8/16/32 x 4 methods x n=3) absurdly
slow for no benefit, the same way a real telco doesn't reprovision its core
per experiment:

  1. "Core" lifecycle (call ONCE per session, OUTSIDE any individual probe):
     build_open5gs_images() -> start_open5gs_core() -> start_open5gs_gnb()
     -> provision_open5gs_subscribers(count=<max N you'll ever need, e.g.
     32>). Leaves mongo/nrf/scp/ausf/udr/udm/smf/upf/amf/pcf/bsf/nssf and
     the single shared UERANSIM gNB running as long-lived, fixed-name
     containers (sa-deploy.yaml/nr-gnb.yaml both hardcode container_name,
     so only ONE Open5GS core can run on this host at a time -- by design,
     matching the vendor's own single-core-per-host assumption). Call
     stop_open5gs_core() when you're completely done with this profile.

  2. Per-probe lifecycle (Open5gsTopologyProbe, one instance per run):
     brings up exactly N UE containers (each running UERANSIM's nr-ue
     against the shared gNB, using one of the pre-provisioned subscriber
     identities) plus N lightweight "app" containers that join each UE
     container's network namespace via `docker run --network=container:
     <ue>` -- Docker's native netns-sharing primitive, no manual nsenter/
     veth wiring needed here the way the ns-3 profiles require (Open5GS's
     UPF already IS the L3 fabric; this script doesn't need to build one).
     Each UE's `nr-ue` process creates its own uesimtun0 TUN device inside
     its OWN container's netns once its PDU session is up (confirmed via
     UERANSIM's source/docs: one nr-ue process = one private TUN, no
     cross-container netns sharing needed for THAT part) -- this script
     polls for that interface and reads its assigned IP (drawn from
     external/open5gs/.env's UE_IPV4_INTERNET pool, allocated by the UPF)
     to populate self.ips, exactly like finish_wire_network_nr() does for
     the ns-3 NR profile's overlay IPs. Subclasses
     run_ns3_docker_container_fleet_probe.ReferenceTopologyProbe purely to
     INHERIT its already-validated, network-topology-agnostic methods
     (launch_endpoints, launch_coordination_endpoints,
     wait_for_ready_then_start, wait_for_completion, collect_results,
     sample_resource_usage, the Zenoh-router/Fast-DDS-discovery-server
     helpers) -- every one of those methods only ever reads
     self.{rigger_name,endpoint_container_names,ips,endpoints}, never
     anything ns-3/wifi-specific, so they carry over unmodified. Only
     start_containers()/teardown() (and the new UE-IP-discovery step) are
     overridden here.

UE-to-UE routing note (why this doesn't need a NAT workaround the way the
ns-3 ghost-node profile did): external/open5gs/upf/tun_if.py's MASQUERADE
rule is `-s $UE_IPV4_INTERNET ! -o ogstun ! -d $PCSCF_IP -j MASQUERADE` --
note `! -o ogstun`, i.e. it only NATs traffic actually leaving via some
OTHER interface (real internet egress). Traffic between two UEs on the
same UPF/APN stays entirely within ogstun-mediated GTP-U tunnel switching
(standard "local breakout" 5G-core behavior) and is never matched by that
rule, so peer IPs/source addresses reach every other endpoint unmodified --
confirmed by reading the rule, not yet by a live packet capture (that
still needs the small-scale test this module's docstring elsewhere refers
to, once the user authorizes running it).

Subscriber identities: every UE gets a distinct IMSI (KI/OPC intentionally
shared test constants across all of them -- this is a closed lab core with
no roaming/security requirement, not a real deployment) generated by
_subscriber_for_index() and provisioned via Open5GS's own
misc/db/open5gs-dbctl (pure bash + mongosh, no pymongo dependency) run
inside the already-running `nrf` container (arbitrary choice among the
NFs -- all of them share the docker_open5gs image, which bundles both the
dbctl script and mongosh via the mongodb-org apt package).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fleetqox.trace import generate_trace_events, write_simulator_csv  # noqa: E402
from scripts.run_ns3_docker_container_fleet_probe import (  # noqa: E402
    DEFAULT_IMAGE,
    READY_DEADLINE_S,
    ReferenceTopologyProbe,
    build_static_subscriptions,
    compute_coordination_metrics,
    compute_graph_join_failures,
    compute_jitter_stale_repair_stats,
    compute_latency_stats_ms,
    docker,
    endpoint_list,
    rigger_run,
)

OPEN5GS_ROOT = ROOT / "external" / "open5gs"
OPEN5GS_CONTAINER_PREFIX = "fleetqox_o5gs"
CORE_COMPOSE_FILE = "sa-deploy.yaml"
GNB_COMPOSE_FILE = "nr-gnb.yaml"
CORE_IMAGE = "docker_open5gs"
UERANSIM_IMAGE = "docker_ueransim"
CORE_NETWORK_NAME = "docker_open5gs_default"
GNB_CONTAINER_NAME = "nr_gnb"
CORE_SERVICES = (
    "mongo", "nrf", "scp", "ausf", "udr", "udm", "smf", "upf", "amf", "pcf", "bsf", "nssf",
)
# webui/metrics/grafana deliberately excluded -- observability-only, no
# NF in the signaling/data path depends on them, and skipping them cuts a
# meaningful chunk off the "how long until the core is usable" wait.
DBCTL_CONTAINER = "nrf"
DBCTL_PATH = "/open5gs/misc/db/open5gs-dbctl"
UE_TUN_WAIT_S = 45.0
UE_KI = "8baf473f2f8fd09487cccbd7097c6862"
UE_OPC = "11111111111111111111111111111111"
UE_AMF = "8000"
# 001(MCC) + 01(MNC) + 10-digit MSIN, matching .env's UE1_IMSI format.
IMSI_MSIN_BASE = 1_230_000_000


def _compose(compose_file: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return docker(
        "compose", "-f", str(OPEN5GS_ROOT / compose_file), "--env-file", str(OPEN5GS_ROOT / ".env"),
        "-p", "fleetqox_open5gs", *args, check=check,
    )


def _subscriber_for_index(index: int) -> dict[str, str]:
    msin = IMSI_MSIN_BASE + index
    imsi = f"00101{msin:010d}"
    return {
        "imsi": imsi,
        "ki": UE_KI,
        "opc": UE_OPC,
        "amf": UE_AMF,
        # IMEI/IMEISV only matter if the UE has no SUPI configured, which
        # is never true here (ueransim-ue.yaml always sets `supi:`) -- kept
        # distinct per index anyway purely for readable per-UE logs.
        "imei": f"35693803{index:07d}",
        "imeisv": f"4370816125{index:06d}",
    }


def build_open5gs_images() -> None:
    """One-time image builds -- neither sa-deploy.yaml nor nr-gnb.yaml
    declares a `build:` key for these (see module docstring's "not yet
    reused the vendor's docker-compose build wiring" reasoning: this
    project drives everything through explicit `docker build`/`docker run`
    rather than compose-managed builds elsewhere too), so both images must
    be built explicitly before any `compose up`/`docker run` against
    them."""
    result = docker(
        "build", "-t", CORE_IMAGE, str(OPEN5GS_ROOT / "base"), check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker_open5gs image build failed:\n{result.stdout}\n{result.stderr}")
    result = docker(
        "build", "-t", UERANSIM_IMAGE, str(OPEN5GS_ROOT / "ueransim"), check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker_ueransim image build failed:\n{result.stdout}\n{result.stderr}")


def start_open5gs_core(*, ready_timeout_s: float = 90.0) -> None:
    """Brings up the 12 core NFs (see CORE_SERVICES) as long-lived,
    fixed-name containers -- call ONCE per session (see module docstring).
    Idempotent: `compose up -d` on an already-running stack is a no-op per
    service."""
    result = _compose(CORE_COMPOSE_FILE, "up", "-d", *CORE_SERVICES, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"open5gs core `compose up` failed:\n{result.stdout}\n{result.stderr}")
    # AMF is the right readiness proxy: every other core NF it depends on
    # (nrf/scp/ausf/udm/udr/pcf/bsf, see sa-deploy.yaml's depends_on chain)
    # must already be up and NRF-registered for amf's own open5gs-amfd to
    # start cleanly, and AMF is what the gNB/UEs actually need reachable
    # next. Checks the process is alive inside the container rather than
    # just "container running" (a crash-looping open5gs-amfd would still
    # show the container as Up between restarts).
    deadline = time.monotonic() + ready_timeout_s
    while time.monotonic() < deadline:
        check = docker("exec", "amf", "pgrep", "-f", "open5gs-amfd", check=False)
        if check.returncode == 0:
            return
        time.sleep(2.0)
    log = docker("logs", "--tail", "80", "amf", check=False)
    raise TimeoutError(
        f"open5gs-amfd did not come up within {ready_timeout_s}s; amf log tail:\n{log.stdout}\n{log.stderr}"
    )


def start_open5gs_gnb(*, ready_timeout_s: float = 30.0) -> None:
    """Brings up the single shared UERANSIM gNB against the running core
    -- call ONCE per session, AFTER start_open5gs_core()."""
    result = _compose(GNB_COMPOSE_FILE, "up", "-d", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gNB `compose up` failed:\n{result.stdout}\n{result.stderr}")
    deadline = time.monotonic() + ready_timeout_s
    while time.monotonic() < deadline:
        check = docker("exec", GNB_CONTAINER_NAME, "pgrep", "-f", "nr-gnb", check=False)
        if check.returncode == 0:
            return
        time.sleep(1.0)
    log = docker("logs", "--tail", "80", GNB_CONTAINER_NAME, check=False)
    raise TimeoutError(
        f"nr-gnb did not come up within {ready_timeout_s}s; gNB log tail:\n{log.stdout}\n{log.stderr}"
    )


def provision_open5gs_subscribers(count: int) -> list[dict[str, str]]:
    """(Re-)provisions `count` distinct subscriber identities (index
    0..count-1, see _subscriber_for_index()) via open5gs-dbctl, then
    patches each record's security context directly via mongosh. Two real
    bugs found (and fixed here) via this profile's own small-scale smoke
    test:

      1. open5gs-dbctl's `add {imsi key opc}` stores the given key
         directly as `security.opc` (opc = null). But
         external/open5gs/ueransim/ueransim-ue.yaml hardcodes
         `opType: 'OP'` -- i.e. UERANSIM treats its templated UE1_OP
         value as a raw, UNciphered Operator key and derives OPC from it
         internally via Milenage, while dbctl told Open5GS to use that
         SAME raw string AS ALREADY-CICHERED OPC directly. Two
         structurally different derivations of the "same" key material
         -- AKA's MAC check only happens to pass by the second retry in
         a way that surfaces as a permanent "SQN out of range" failure
         with no successful resync, confirmed live via a manual
         single-UE test against dbctl's own default (add succeeded, gNB
         NG-setup succeeded, but every authentication attempt failed
         identically). Moving the same key from `security.opc` to
         `security.op` (matching ueransim-ue.yaml's declared opType)
         fixed it in that same manual test -- first Authentication
         Request still triggers one "SQN out of range" (a normal 3GPP
         AUTS resynchronization round-trip for a subscriber whose SQN
         state was just reset, not a fault), and the immediate retry
         then succeeds and proceeds through PDU session establishment.

      2. open5gs-dbctl's `add` is a bare `insertOne` with no uniqueness
         check on imsi (confirmed by inspecting the live subscribers
         collection: calling this function twice with an overlapping
         range -- exactly what core-up followed by a second core-up
         does -- left TWO documents for the same imsi in mongo).
         remove-then-add makes this idempotent regardless of how many
         times/with what overlapping ranges it's called, and as a side
         effect keeps the per-subscriber SQN state starting from a
         known-clean point every time this is called -- cheap enough to
         call again before every probe run reusing pool identities
         across a batch (Open5gsTopologyProbe.start_containers() does
         this), not just once at core-up time."""
    mongo_ip = _read_env_var("MONGO_IP")
    subscribers = [_subscriber_for_index(i) for i in range(count)]
    for sub in subscribers:
        docker(
            "exec", DBCTL_CONTAINER, DBCTL_PATH, f"--db_uri=mongodb://{mongo_ip}/open5gs",
            "remove", sub["imsi"], check=False,
        )
        docker(
            "exec", DBCTL_CONTAINER, DBCTL_PATH, f"--db_uri=mongodb://{mongo_ip}/open5gs",
            "add", sub["imsi"], sub["ki"], sub["opc"], check=False,
        )
        # Move the key from security.opc (dbctl's default) to security.op
        # -- see reason #1 above. sub["opc"] is genuinely an OP value
        # despite the field name (kept as "opc" only so _subscriber_for_index()'s
        # dict shape matches dbctl's own `add {imsi key opc}` positional
        # argument order).
        docker(
            "exec", DBCTL_CONTAINER, "mongosh", "--quiet", f"mongodb://{mongo_ip}/open5gs",
            "--eval",
            f"db.subscribers.updateOne({{imsi:'{sub['imsi']}'}}, "
            f"{{$set: {{'security.op': '{sub['opc']}', 'security.opc': null}}}})",
            check=False,
        )
    return subscribers


def _read_env_var(name: str) -> str:
    text = (OPEN5GS_ROOT / ".env").read_text()
    match = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    if not match:
        raise RuntimeError(f"{name} not found in external/open5gs/.env")
    return match.group(1).strip()


def stop_open5gs_core() -> None:
    """Tears down gNB + core. Does NOT pass -v (leaves the mongo subscriber
    data volume intact) so a later start_open5gs_core() in the same host
    session doesn't require re-provisioning -- pass --wipe-subscribers on
    the CLI (see main()) if a clean-slate reset is actually wanted."""
    _compose(GNB_COMPOSE_FILE, "down", check=False)
    _compose(CORE_COMPOSE_FILE, "down", check=False)


def wipe_open5gs_subscribers() -> None:
    _compose(CORE_COMPOSE_FILE, "down", "-v", check=False)


class Open5gsTopologyProbe(ReferenceTopologyProbe):
    """Per-run container lifecycle against an already-running Open5GS core
    + gNB (see module docstring). Deliberately does NOT call
    ReferenceTopologyProbe.__init__ -- that method sets up ns3sim-specific
    naming this profile has no use for; this constructor sets up only what
    the INHERITED methods actually read (self.rigger_name,
    self.endpoint_container_names, self.ips, self.endpoints, self.image,
    self.output_dir)."""

    def __init__(
        self,
        *,
        run_id: str,
        image: str,
        num_robots: int,
        output_dir: Path,
        subscribers: list[dict[str, str]],
    ) -> None:
        self.run_id = run_id
        self.image = image
        self.num_robots = num_robots
        # endpoint_list() returns num_robots + 1 entries (control_station
        # plus num_robots robots, see that function's docstring) -- every
        # per-endpoint container list here must be sized off
        # len(self.endpoints), NOT num_robots directly, or the last
        # endpoint (control_station, index 0, or whichever position a
        # future refactor put it at) silently has no UE container to pair
        # with. Confirmed as a real bug via a live 2-robot smoke test
        # ("list index out of range" in _wait_for_ue_ip's enumerate loop)
        # before this comment was written.
        self.endpoints = endpoint_list(num_robots)
        if len(subscribers) < len(self.endpoints):
            raise ValueError(
                f"only {len(subscribers)} subscribers provisioned, need "
                f"{len(self.endpoints)} (num_robots + 1 for control_station) -- "
                "call provision_open5gs_subscribers(count=<N>) with a larger count first"
            )
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.subscribers = subscribers[: len(self.endpoints)]
        self.rigger_name = f"{OPEN5GS_CONTAINER_PREFIX}_{run_id}_rigger"
        self.ue_container_names = [
            f"{OPEN5GS_CONTAINER_PREFIX}_{run_id}_ue{i}" for i in range(len(self.endpoints))
        ]
        self.endpoint_container_names = [
            f"{OPEN5GS_CONTAINER_PREFIX}_{run_id}_{endpoint}" for endpoint in self.endpoints
        ]
        self.ips: dict[str, str] = {}

    def _mount_args(self) -> list[str]:
        return ["-v", f"{ROOT}:/work", "-w", "/work"]

    def start_containers(self) -> None:
        # Reset (remove+re-add) exactly this run's subscriber identities
        # right before launching fresh UE containers against them -- see
        # provision_open5gs_subscribers()'s docstring reason #2: every UE
        # container here is a brand new nr-ue process (SQN=0 on its side),
        # and Open5GS's network-side SQN for these same IMSIs only ever
        # advances, so without this reset every run after the first one
        # to touch a given subscriber slot fails AKA with "SQN out of
        # range" (confirmed live during this profile's own smoke test).
        provision_open5gs_subscribers(len(self.subscribers))
        docker(
            "rm", "-f", self.rigger_name, *self.ue_container_names, *self.endpoint_container_names,
            check=False,
        )
        # rigger here is just a /work-mounted file-ops target for
        # launch_endpoints()/wait_for_ready_then_start()/wait_for_completion()
        # (mkdir/test -f/touch) -- no nsenter/NET_ADMIN needed, unlike the
        # ns-3 profiles' rigger, since Docker's own --network=container:
        # below does all the netns wiring this profile needs.
        docker(
            "run", "-d", "--name", self.rigger_name, "--network=none",
            *self._mount_args(), self.image, "sleep infinity",
        )
        gnb_ip = _read_env_var("NR_GNB_IP")
        mcc = _read_env_var("MCC")
        mnc = _read_env_var("MNC")
        for i, ue_name in enumerate(self.ue_container_names):
            sub = self.subscribers[i]
            docker(
                "run", "-d", "-i", "-t", "--name", ue_name, f"--network={CORE_NETWORK_NAME}",
                "--cap-add=NET_ADMIN", "--privileged",
                # -i -t (matching the vendor's own nr-ue.yaml
                # stdin_open:true/tty:true): ueransim-ue_init.sh ends with
                # `./nr-ue -c ... & exec bash $@` -- that trailing bare
                # `exec bash` becomes PID 1 after backgrounding nr-ue, and
                # without an allocated tty its stdin is just a closed pipe,
                # so it hits EOF and exits immediately, taking the whole
                # container (and the nr-ue child it just started) down
                # with it. Confirmed by reading the script; a tty keeps
                # that bash sitting idle instead of exiting.
                "-e", "COMPONENT_NAME=ueransim-ue", "-e", f"MCC={mcc}", "-e", f"MNC={mnc}",
                "-e", f"NR_GNB_IP={gnb_ip}", "-e", f"UE1_IMSI={sub['imsi']}",
                "-e", f"UE1_KI={sub['ki']}", "-e", f"UE1_OP={sub['opc']}",
                "-e", f"UE1_AMF={sub['amf']}", "-e", f"UE1_IMEI={sub['imei']}",
                "-e", f"UE1_IMEISV={sub['imeisv']}",
                "-v", f"{OPEN5GS_ROOT / 'ueransim'}:/mnt/ueransim",
                UERANSIM_IMAGE,
            )
        for i, endpoint_name in enumerate(self.endpoint_container_names):
            # --network=container:<ue> joins that UE's netns wholesale
            # (including its uesimtun0 once the PDU session comes up) --
            # Docker's native equivalent of what the ns-3 profiles build
            # by hand via nsenter/veth (see module docstring).
            docker(
                "run", "-d", "--name", endpoint_name,
                f"--network=container:{self.ue_container_names[i]}", "--init",
                *self._mount_args(), self.image, "sleep infinity",
            )
        self.ips = {
            endpoint: self._wait_for_ue_ip(self.ue_container_names[i])
            for i, endpoint in enumerate(self.endpoints)
        }

    def _wait_for_ue_ip(self, ue_name: str, timeout_s: float = UE_TUN_WAIT_S) -> str:
        """Polls for uesimtun0 to appear inside `ue_name` (created by
        nr-ue once its PDU session establishes) and returns its assigned
        IPv4 address, read straight from `ip -4 addr show` the same way
        tap_byte_counter() reads netdev counters elsewhere in this
        project -- direct introspection instead of log-scraping, since
        unlike the ns-3 NR profile's FLEETQOX_NR_MAPPING there's no
        simulator-internal decision to recover, just a real kernel
        interface to look at.

        Also adds an explicit route for the whole UE_IPV4_INTERNET pool
        via uesimtun0 -- `ip addr add` only creates a host /32 route (no
        subnet route, same gap finish_wire_network_nr() had to work
        around for the ns-3 NR profile's overlay IPs), so without this
        every packet aimed at ANOTHER UE's uesimtun0 address falls
        through to the container's pre-existing docker-network default
        route (via eth0, the docker_open5gs_default bridge) instead of
        the UPF-terminated tunnel -- confirmed live: two fresh UEs could
        register/establish PDU sessions fine but ping between their
        uesimtun0 addresses was 100% loss until this route was added,
        100% delivered after. Only this one subnet is redirected, NOT
        the container's default route -- NGAP/GTP-U signaling traffic
        (this container's own eth0 address talking to gNB_IP/AMF_IP on
        docker_open5gs_default) must keep using eth0 unchanged."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            result = docker("exec", ue_name, "ip", "-4", "-o", "addr", "show", "uesimtun0", check=False)
            if result.returncode == 0 and result.stdout.strip():
                match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
                if match:
                    ue_internet_pool = _read_env_var("UE_IPV4_INTERNET")
                    docker("exec", ue_name, "ip", "route", "add", ue_internet_pool, "dev", "uesimtun0", check=False)
                    return match.group(1)
            time.sleep(1.0)
        log = docker("logs", "--tail", "80", ue_name, check=False)
        raise TimeoutError(
            f"uesimtun0 never came up on {ue_name} within {timeout_s}s "
            f"(PDU session establishment failed?); nr-ue log tail:\n{log.stdout}\n{log.stderr}"
        )

    def teardown(self) -> None:
        # Only this run's UE/app/rigger containers -- the shared core +
        # gNB (fixed container names, started by start_open5gs_core()/
        # start_open5gs_gnb()) deliberately outlive any single probe run,
        # see module docstring.
        docker(
            "rm", "-f", self.rigger_name, *self.ue_container_names, *self.endpoint_container_names,
            check=False,
        )


def run_open5gs_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    policy: str,
    seconds: int,
    seed: int,
    subscribers: list[dict[str, str]],
    start_offset_ms: float = 2000.0,
    drain_s: float = 10.0,
    discovery_timeout_s: float = 15.0,
    static_mode: bool = True,
    extra_rmw_env: dict[str, str] | None = None,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
) -> dict[str, Any]:
    """Bảng V "5G" row, Open5GS + UERANSIM edition -- see module docstring
    for the labeling requirement and architecture. Caller MUST have already
    run build_open5gs_images() + start_open5gs_core() + start_open5gs_gnb()
    + provision_open5gs_subscribers(count>=num_robots) at least once in
    this session (not repeated per call -- see those functions' own
    docstrings); `subscribers` here is normally that last call's return
    value, reused across every run in a batch."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    trace_path = output_dir / f"trace_ref_{num_robots}robot_seed{seed}.csv"
    events = generate_trace_events(
        scenario=f"open5gs_docker_fleet_probe_{num_robots}robot",
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

    probe = Open5gsTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir,
        subscribers=subscribers,
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    resource_usage: dict[str, dict[str, float]] = {}
    try:
        probe.start_containers()
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
        time.sleep(start_offset_ms / 1000.0 + max(seconds, 1) / 2.0)
        resource_usage = probe.sample_resource_usage()
        probe.wait_for_completion(
            timeout_s=seconds + drain_s + start_offset_ms / 1000.0 + 60.0,
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
        "schema_version": "fleetqox.open5gs_docker_fleet_probe.v1",
        "network_profile": "5G_SA_emulation_open5gs_ueransim",
        "network_profile_label": "5G SA emulation (Open5GS + UERANSIM)",
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


def run_open5gs_coordination_probe(
    *,
    image: str = DEFAULT_IMAGE,
    output_dir: Path,
    num_robots: int,
    seed: int,
    subscribers: list[dict[str, str]],
    num_crossings: int = 5,
    crossing_duration_ms: float = 300.0,
    reply_timeout_s: float = 5.0,
    defer_release_timeout_s: float = 8.0,
    priority_mode: str = "lamport",
    scenario_timeout_s: float = 120.0,
    start_offset_ms: float = 2000.0,
    discovery_timeout_s: float = 15.0,
    rmw_implementation: str = "rmw_fleetqox_cpp",
    discovery_mode: str = "default",
    extra_rmw_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bảng VI coordination-metrics row, Open5GS + UERANSIM edition -- see
    module docstring and run_open5gs_probe()'s docstring for the shared
    core/subscriber-pool prerequisites."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name.lstrip(".")
    endpoints = endpoint_list(num_robots)
    results_dir_container = f"{output_dir.relative_to(ROOT)}/container_results"
    (ROOT / results_dir_container).mkdir(parents=True, exist_ok=True)

    probe = Open5gsTopologyProbe(
        run_id=run_id, image=image, num_robots=num_robots, output_dir=output_dir,
        subscribers=subscribers,
    )
    ready_deadline_s = max(READY_DEADLINE_S, int(discovery_timeout_s) + 15)
    start_wait_timeout_s = ready_deadline_s + 30
    status = "ok"
    error_text = ""
    endpoint_results: dict[str, Any] = {}
    try:
        probe.start_containers()
        if rmw_implementation == "rmw_zenoh_cpp":
            probe.start_zenoh_router()
        if rmw_implementation == "rmw_fastrtps_cpp" and discovery_mode == "discovery_server":
            probe.start_fastdds_discovery_server()
        probe.launch_coordination_endpoints(
            num_crossings=num_crossings,
            crossing_duration_ms=crossing_duration_ms,
            reply_timeout_s=reply_timeout_s,
            defer_release_timeout_s=defer_release_timeout_s,
            priority_mode=priority_mode,
            seed=seed,
            start_offset_ms=start_offset_ms,
            discovery_timeout_s=discovery_timeout_s,
            start_wait_timeout_s=start_wait_timeout_s,
            scenario_timeout_s=scenario_timeout_s,
            results_dir_container=results_dir_container,
            rmw_implementation=rmw_implementation,
            discovery_mode=discovery_mode,
            extra_rmw_env=extra_rmw_env,
        )
        probe.wait_for_ready_then_start(ready_deadline_s=ready_deadline_s)
        probe.wait_for_completion(
            timeout_s=start_offset_ms / 1000.0 + scenario_timeout_s + 60.0,
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

    return {
        "schema_version": "fleetqox.open5gs_docker_coordination_probe.v1",
        "network_profile": "5G_SA_emulation_open5gs_ueransim",
        "network_profile_label": "5G SA emulation (Open5GS + UERANSIM)",
        "status": status,
        "error": error_text,
        "num_robots": num_robots,
        "endpoints": endpoints,
        "endpoint_results": endpoint_results,
        "endpoint_results_complete": all(
            endpoint_results.get(endpoint) is not None for endpoint in endpoints
        ),
        "coordination_metrics": compute_coordination_metrics(endpoint_results),
        "discovery_convergence_max_s": (
            max(discovery_convergence_samples_s) if discovery_convergence_samples_s else None
        ),
        "graph_join_failures": compute_graph_join_failures(endpoint_results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build-images", help="docker build docker_open5gs + docker_ueransim (once)")
    core_up = sub.add_parser("core-up", help="bring up core + gNB + provision subscribers (once)")
    core_up.add_argument("--max-robots", type=int, default=32)

    sub.add_parser("core-down", help="tear down core + gNB (keeps subscriber DB)")
    sub.add_parser("core-wipe", help="tear down core + gNB AND wipe subscriber DB")

    probe_p = sub.add_parser("probe", help="run one Bảng V trace-replay probe")
    probe_p.add_argument("--image", default=DEFAULT_IMAGE)
    probe_p.add_argument("--output-dir", type=Path, required=True)
    probe_p.add_argument("--num-robots", type=int, default=8)
    probe_p.add_argument("--policy", default="fifo")
    probe_p.add_argument("--seconds", type=int, default=3)
    probe_p.add_argument("--seed", type=int, default=13)
    probe_p.add_argument("--rmw-implementation", default="rmw_fleetqox_cpp")
    probe_p.add_argument("--discovery-mode", default="default")
    probe_p.add_argument("--summary-json", type=Path, default=None)

    coord_p = sub.add_parser("coordination-probe", help="run one Bảng VI coordination probe")
    coord_p.add_argument("--image", default=DEFAULT_IMAGE)
    coord_p.add_argument("--output-dir", type=Path, required=True)
    coord_p.add_argument("--num-robots", type=int, default=8)
    coord_p.add_argument("--seed", type=int, default=13)
    coord_p.add_argument("--rmw-implementation", default="rmw_fleetqox_cpp")
    coord_p.add_argument("--discovery-mode", default="default")
    coord_p.add_argument(
        "--priority-mode", choices=("lamport", "fleetqox"), default="lamport",
        help="'lamport' = Ours-NoQoX (current behavior). 'fleetqox' = Ours-FleetQoX "
        "(task_criticality-aware priority) -- see fleetqox_coordination_endpoint.py.",
    )
    coord_p.add_argument("--summary-json", type=Path, default=None)

    args = parser.parse_args()

    if args.command == "build-images":
        build_open5gs_images()
        print(json.dumps({"status": "ok"}))
        return 0

    if args.command == "core-up":
        start_open5gs_core()
        start_open5gs_gnb()
        # +1: endpoint_list(max_robots) is max_robots robots PLUS
        # control_station (see Open5gsTopologyProbe.__init__'s comment) --
        # provision one extra subscriber so a --num-robots=max_robots probe
        # has exactly enough UE identities for every endpoint.
        subscribers = provision_open5gs_subscribers(args.max_robots + 1)
        print(json.dumps({"status": "ok", "subscribers_provisioned": len(subscribers)}))
        return 0

    if args.command == "core-down":
        stop_open5gs_core()
        print(json.dumps({"status": "ok"}))
        return 0

    if args.command == "core-wipe":
        wipe_open5gs_subscribers()
        print(json.dumps({"status": "ok"}))
        return 0

    if args.command == "probe":
        # +1 for control_station -- see Open5gsTopologyProbe.__init__'s
        # comment on why endpoint_list(num_robots) has num_robots+1 entries.
        subscribers = [_subscriber_for_index(i) for i in range(args.num_robots + 1)]
        summary = run_open5gs_probe(
            image=args.image, output_dir=args.output_dir, num_robots=args.num_robots,
            policy=args.policy, seconds=args.seconds, seed=args.seed, subscribers=subscribers,
            rmw_implementation=args.rmw_implementation, discovery_mode=args.discovery_mode,
        )
        if args.summary_json:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(json.dumps(summary, indent=2))
        print(json.dumps({"status": summary["status"], "error": summary["error"]}))
        return 0 if summary["status"] == "ok" else 1

    if args.command == "coordination-probe":
        # +1 for control_station -- see Open5gsTopologyProbe.__init__'s
        # comment on why endpoint_list(num_robots) has num_robots+1 entries.
        subscribers = [_subscriber_for_index(i) for i in range(args.num_robots + 1)]
        summary = run_open5gs_coordination_probe(
            image=args.image, output_dir=args.output_dir, num_robots=args.num_robots,
            seed=args.seed, subscribers=subscribers,
            rmw_implementation=args.rmw_implementation, discovery_mode=args.discovery_mode,
            priority_mode=args.priority_mode,
        )
        if args.summary_json:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(json.dumps(summary, indent=2))
        print(json.dumps({"status": summary["status"], "error": summary["error"]}))
        return 0 if summary["status"] == "ok" else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
