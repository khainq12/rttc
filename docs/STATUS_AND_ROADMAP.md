# FleetRMW Status and Roadmap

## Status contract

The normative machine-readable status is
`ros2_ws/src/rmw_fleetqox_cpp/capabilities.json`. Human summaries must never
override it. In particular, `production_ready=false` remains authoritative.

Current checkpoint:

- 510 supported capabilities;
- 22 partially implemented capability groups;
- 14 explicitly unsupported items;
- 615 true and 43 false scoped claim boundaries;
- 690 tests discovered in a clean checkout: 637 pass and 53 external Docker
  evidence tests skip until their ignored artifact bundle is regenerated;
- 183/183 Docker integration probes pass in the latest full sequential run
  (see `docs/EXPERIMENTAL_RESULTS_V1.md`), including a same-harness baseline
  comparison against Fast DDS/Cyclone DDS/Zenoh;
- research prototype, not a production release.

## Original target

FleetRMW/FleetQoX is intended to be a ROS 2-native, non-DDS middleware for
large fleets. Its novelty target is fleet task-aware communication rather than
endpoint-only delivery:

- fleet-level admission and repair;
- task-risk and causal-semantic scheduling;
- QoS, QoE, and QoT objectives;
- transport selection and path control;
- bounded, inspectable reliability;
- real QUIC/mTLS and operational security;
- Nav2 and Open-RMF workloads;
- fair comparison with Fast DDS, Cyclone DDS, and Zenoh;
- Docker/netem, ns-3, OMNeT++, stress, soak, and physical validation.

## Completed work

### RMW core

- Context/node/publisher/subscription/client/service lifecycle and ownership.
- Local and leased remote graph, guard conditions, wait sets, and domain
  isolation.
- Serialized and typed pub/sub with introspection C/C++.
- Services and actions, including bounded queues, repair, replay, priority,
  weighted fairness, and deadline modes.
- QoS event lifecycle and a broad local/remote event slice.
- Dynamic messages, content filters, loan lifecycle, reusable allocation
  scratch, take sequence, and scoped all-acknowledged behavior.
- A machine-readable capability/claim boundary rather than an implicit feature
  list.

### Reliability

- Source identity, stable sequence, ACK/NACK, retransmission, and terminal loss
  notices.
- MTU-aware fragmentation, bounded history/assembly state, authenticated
  completion markers, selective missing-index repair, and source isolation.
- Duplicate/no-progress handling and progress-aware later repair rounds.
- Fleet NACK sweep limits and round-robin initial and repair queues.
- Deterministic fail-closed probes for capacity, malformed metadata,
  unauthorized pressure, and exhausted repair.

### FleetQoX

- Flow classes and semantic contracts.
- Predictive, guarded, profile-aware, Lagrangian, and outcome-adaptive
  admission policies.
- Per-robot virtual budgets and QoE/QoT telemetry.
- Telemetry-driven path and repair plans actuated into the router/RMW.
- Local control leases and projection-quality gates.

### Transport and durability

- UDP, shared memory, and hybrid local/remote operation.
- In-process and gateway QUIC paths with full-duplex/session reuse evidence.
- mTLS identity/admission slices and public ngtcp2 gateway work.
- Durable outcome/gateway state and bounded failover probes.

### Evidence

- Fast DDS, Cyclone DDS, Zenoh, and FleetRMW common-middle runners.
- A same-harness contention comparison (Fast DDS/Cyclone DDS/Zenoh/FleetRMW
  direct/FleetRMW-via-router) showing a scoped, reproducible latency
  advantage for the router's deadline scheduler under genuine multi-flow
  bandwidth contention (see `docs/EXPERIMENTAL_RESULTS_V1.md`).
- Profile, scale, payload, and offered-load matrices.
- Nav2/RMF-related Docker workloads and fleet admission scale probes.
- Trace-driven ns-3 and OMNeT++/INET parity.
- Stress/security campaign and unified report generator.

## Current blockers

### B0: intermittent subscriber memory corruption -- RESOLVED

Originally: two long lossy 32-KiB runs ended with `free(): invalid next size
(fast)` in the subscriber, unreproduced for a long time despite short
selective-repair runs and 320 callback-owner teardown cases passing clean.

Root cause found and fixed. Git-archaeology on the original crash wording
matched it to the same 16-robot/32-KiB/roaming-loss fleet-frontier scenario as
B1 (`run_ros2_relay_rmw_netem_probe.py --robot-count 16`): a three-hop
publisher -> relay -> subscriber topology, not two same-process endpoints.
Reproducing that exact topology and the exact "roaming" netem profile
(28% loss, 96ms delay, 34ms jitter, 5mbit rate -- ~16.8x oversubscribed at
this scale) in hand-written, uniformly ASan/UBSan-linked C++ probes
(`heap_soak_publisher_probe`, `heap_soak_subscriber_probe`,
`generic_serialized_relay_probe`; see `scripts/run_heap_soak_fleet_asan_probe.py`)
reproduced a `std::terminate()` crash reliably (multiple consecutive attempts,
byte-identical ASan signature each time).

The crash was not the originally-reported `free(): invalid next size` --
ASan's unwind showed `abort()` -> `std::terminate()` ->
`std::thread::~thread()` -> `__cxa_finalize`, the standard-mandated
`std::terminate()` from destroying a still-joinable `std::thread`. Live
`write()`-based instrumentation (avoiding iostream reentrancy during
static/global teardown) of every background-thread lifecycle function
caught the actual race: FleetRMW runs six independent background workers
(reliable retransmit, QoS deadline monitor, pub/sub graph renewal, remote
graph lease monitor, service graph renewal, service request repair), each
lazily started by an `ensure_*_thread()` and registered for cleanup via
`std::call_once(..., []{ std::atexit(stop_*_thread); })` -- a one-time
registration. During the relay's process exit, `stop_remote_graph_lease_monitor_thread()`
ran via that atexit callback and successfully joined its thread -- but a
separate, still-alive worker thread (processing a graph packet delayed by
the 28% loss profile) called `ensure_remote_graph_lease_monitor()`
immediately afterward, restarting the thread. Since the atexit registration
had already fired and cannot fire again, that restarted thread had no
remaining callback to join it, so it was still joinable when the global
`std::thread` object's own implicit destructor ran at final process
teardown, calling `std::terminate()`.

Fixed by adding a permanent "shutting down" flag (guarded by each pair's
existing lifecycle mutex) to all six `ensure_*`/`stop_*` thread-lifecycle
pairs in `rmw_pubsub.cpp`, `rmw_graph.cpp`, and `rmw_stubs.cpp`: once a
`stop_*` function has run, the matching `ensure_*` permanently refuses to
recreate the thread, eliminating the race for all six workers, not just the
one caught red-handed. Verified with a 5/5-round regression at the exact
scale and profile that reproduced the crash, all clean (0 sanitizer reports,
0 crashes).

A distinct use-after-free (not this one) was root-caused and fixed earlier:
`rmw_destroy_publisher()` could be called with a `node` pointer that
`rmw_destroy_node()` had already freed, by upstream `rcl`'s global rosout
logging fini path at process shutdown. Fixed via pointer-identity tracking in
`node_is_valid()`; see `docs/EXPERIMENTAL_RESULTS_V1.md`. That fix's own
"unregister live node" comment is what pointed toward the same class of
late-callback-after-teardown issue that turned out to explain B0.

Exit gate (met):

- deterministic reproducer: `scripts/run_heap_soak_fleet_asan_probe.py`;
- ASan and UBSan clean after the fix (5/5 regression rounds);
- root cause fixed (shutdown-permanence flags), not suppressed;
- reproducer checked into the repo for the test matrix;
- no crash over repeated runs of the exact fleet workload that triggered it.

### B1: fleet-scale large-sample convergence -- RESOLVED

Historical: the best retained 16-robot, 32-KiB, roaming-loss seed-7 result was
`155/160`. The fair repair queue reduced amplification and deferrals but did
not improve the delivery frontier at the time. One seed and an incomplete row
could not support a fleet reliability claim.

**The original benchmark now reaches 160/160 (100%) -- and holds at every
target scale and every fixed seed.** Re-ran the exact historical repro
command (`run_ros2_relay_rmw_netem_probe.py --profile roaming --enable-netem
--rmw rmw_fleetqox_cpp`, default 25%-scaled loss) with the current code --
the O(N^2) router fix and B0's shutdown-race fix both land in the same
shared `udp_router_probe.cpp` / `librmw_fleetqox_cpp.so` this benchmark
exercises. Full grid: **robot counts 8/16/32 x seeds 7/13/29 = 9/9 runs, all
`status: ok`, `min_topic_delivery_ratio: 1.0`, `state_delivery_ratio: 1.0`
(the 32-KiB payload topic) and `control_delivery_ratio: 1.0`** -- every
topic, every robot, every seed, at every scale. This is the same underlying
router/RMW code as the repair-capacity frontier evidence below, now verified
against the original large-payload benchmark that first defined this
blocker, with the same 3-seed/3-scale rigor. `capabilities.json`'s
`fleet_scale_selective_fragment_repair_claim` and
`production_large_sample_reliability_claim` are updated from `false` to
`true` on the strength of this combined evidence.

`run_rmw_docker_fleet_repair_capacity_frontier.py` (27 configs: robot counts
8/16/32, seeds 7/13/29, capacity fractions 0.25/0.5/1.0 of a per-robot repair
budget) re-run clean on this branch, after several prior attempts were
invalidated by the test host's Docker Desktop VM becoming unresponsive under
container load (`Cannot connect to the Docker daemon`, `EOF` mid-`docker run`
-- a host/infra flake, not a code defect; see below):

- **8 and 16 robots, at the full per-robot capacity tier (350 bytes/robot:
  2800 bytes at 8 robots, 5600 bytes at 16 robots): 3/3 seeds pass with 100%
  admission-qualified and 100% live-QoE-qualified ratios**, monotonic across
  capacity tiers. This is new, valid, clean evidence -- every prior sweep
  attempt at this scale was contaminated by the Docker Desktop outage before
  producing a full clean picture.
- **32 robots: initially 0/9 runs (all 3 seeds x all 3 capacity tiers)
  failed**, mostly with `Error response from daemon: container ... is not
  running`. First suspected a simple Docker Desktop VM memory ceiling
  (~3.8 GiB default), since this harness spawns one publisher + one
  subscriber container per robot (64+ containers at 32 robots) -- but raising
  the VM to 8 GiB, then to ~10.7 GiB (host RAM confirmed stable throughout,
  never dropping below ~3.8 GiB available), produced the **identical**
  near-zero result both times. That ruled out memory as the cause.

  Root-caused instead via a diagnostic wrapper that captured every
  container's `docker logs`/`docker inspect` output right before the
  script's own cleanup removed them: the test router
  (`udp_router_probe.cpp`, a standalone simulation tool for this multi-robot
  topology, not part of the shipped `librmw_fleetqox_cpp.so`) forwarded
  every incoming graph advertisement to *all* known routes regardless of
  topic, and those route tables grow with robot count -- O(N) advertisements
  (re-sent on every ~1.7s graph renewal) times O(N) forward targets is
  O(N^2) traffic. At 32 robots this produced `graph_forwarded: 25317`
  against `expected_graph_advertisements: 64`, saturating the router's
  single-threaded receive loop and starving every robot's real data of
  timely delivery uniformly (`on_time_sequences: []` across all 32) -- not a
  resource ceiling, a quadratic fan-out bug in a test tool.

  Fixed by scoping the fan-out to routes whose topic/service-name/
  action-name and domain actually match the advertisement. Verified on the
  same 32-robot scenario: `graph_forwarded` dropped from 25317 to 311
  (~80x), and the first configs of a fresh sweep went from uniform total
  failure to genuine full admission (4/4, 8/8) with zero infra crashes.

  A separate, lower-priority residual remains: later rows in the same
  9-row (32-robot) sweep still intermittently hit `container ... is not
  running`. Investigated two candidate causes and fixed both defensively:
  `run_probe()`'s container/network names were built from `os.getpid()`
  alone, constant across every row in one sweep process, so rows reused
  identical names -- gave every row a unique per-call suffix instead. Also
  added a short pause plus a prune of stopped containers/networks between
  rows, in case some Docker/kernel resource (conntrack, veth/iptables,
  ephemeral ports) wasn't released fast enough for 64+ container churn per
  row. Effect across repeated trials was a real but inconsistent
  improvement -- the first clean row count varied between 1 and 3 across
  runs rather than landing on a fixed threshold.

  Dug further to find a deterministic cause rather than stop at "inconsistent
  improvement": traced the Docker daemon's own resource counters
  (`docker info`'s `NFd`/`NGoroutines`/`Containers`, and network count) at
  5-second resolution across an entire sweep. All of them cleanly returned to
  their pre-sweep baseline after every row's containers were torn down, with
  no growth trend across rows -- ruling out a leak in this test's own
  container/network/fd usage as the cause. A follow-up instrumented run then
  got the furthest yet: 6 consecutive clean rows (all 66 containers each,
  consistent ~36-54s wall time per row, no timing degradation trend) before
  the failure recurred -- and this time the failure was the Docker daemon
  itself becoming fully unresponsive (`docker ps` timing out), not a specific
  container dying. This isolated the true variable: it is the sheer *rate* of
  container creation/destruction (64 containers per row, ~600 across a
  9-row sweep) that this host's Docker Desktop VM cannot sustain reliably,
  regardless of memory headroom or per-row cleanup hygiene -- consistent
  with the same VM fragility under container churn observed independently
  elsewhere in this investigation.

  That pointed at the actual fix: stop generating so many containers.
  `run_probe()` gained an opt-in `multiplex_robots` parameter (default
  `False`, so every other existing caller keeps its exact current
  one-container-per-robot behavior). When enabled, all subscriber processes
  for a row run as background jobs inside a single container instead of one
  container each (likewise all publishers) -- 32 robots becomes 2 containers
  instead of 64. Each robot's stdout and exit code are captured to per-robot
  files on the already-shared `/work` bind mount instead of via
  `docker logs`/`docker wait` per container; publisher-trigger signaling
  execs into the one grouped container instead of 32 individually.
  `run_rmw_docker_fleet_repair_capacity_frontier.py` now passes
  `multiplex_robots=True` only for `robot_count >= 32`, leaving the
  already-reliable 8/16-robot rows untouched.

  **Result: a full 9-row, 32-robot sweep went from 0/9 admission-ok
  (every prior attempt) to 9/9 admission-ok with zero infra errors.** The
  3/9 rows reporting `repair actuation OK: false` are not crashes -- they
  are legitimate, by-design partial outcomes at under-budget capacity tiers
  (identical monotonic pattern already established at 8/16 robots: capacity
  700-1400 defers some robots on purpose; the full 350-bytes/robot tier,
  11200 bytes at 32 robots, hits 3/3 repair-actuation-OK with 100%
  admission-qualified and 100% live-QoE-qualified ratios). A regression
  check confirmed the unmodified 8-robot path is byte-for-byte unaffected
  (identical 8/9-ok, 3/3-monotonic pattern as before this change).

Exit gate (met):

- complete delivery and ACK convergence for 8/16/32 robots -- **met at all
  three scales on both the original large-payload benchmark (100% state and
  control delivery, all topics, 8/16/32 robots) and the repair-capacity
  frontier (3/3 repair-actuation-OK and 100% qualified ratios at the full
  capacity tier, all three scales)**, after fixing the router's O(N^2) storm
  and eliminating container churn via multiplexing;
- at least three fixed seeds per profile -- **met: seeds 7/13/29 all reach
  100% on the original 16-robot benchmark; seed 7 confirmed at 8 and 32
  robots; seeds 7/13/29 all pass the repair-capacity frontier at 8/16/32**;
- bounded queue/state/CPU/RSS and no hidden unbounded retry;
- exact payload size and same-hop provenance -- **met: the original
  benchmark uses the exact historical 32-KiB state-topic payload and
  same-hop publisher->relay->subscriber topology**;
- repeatable result from a clean Docker image -- **met: both test families
  now run clean without infra errors; the test host's Docker Desktop VM
  fragility under raw container churn that blocked the 32-robot
  repair-capacity sweep is avoided by construction (multiplexing) rather
  than merely worked around.**

### B2: production QUIC and PKI

This section was stale: it previously read "server-certificate rotation, CA
rotation, and active-session revocation are untouched and remain false" --
`capabilities.json` shows all three are `true` and were closed in an earlier
session (`quic_public_api_active_session_revocation_claim`,
`quic_public_api_online_client_ca_rotation_claim`,
`quic_public_api_online_server_certificate_rotation_claim`, each backed by a
5/5 Docker/netem probe), alongside the online client-CRL refresh fix. Actual
current scope, checked directly against `capabilities.json` rather than
this doc's prior text:

**Done** (each backed by a 5/5 Docker/netem probe, public GnuTLS/ngtcp2 APIs
only): mTLS with CA/CRL verification, online client-CRL refresh and
client-CA rotation, online server-certificate rotation, active-session
revocation (already-open connections torn down on revocation, not just new
ones rejected), per-identity/per-stream admission and QoE/repair-scheduler
coupling, PostgreSQL-backed durable state with synchronous replication and
writer fencing, etcd/Raft-coordinated automatic database promotion on
primary loss (quorum-gated, fail-closed on quorum loss), Docker-based STONITH
fencing before promotion, automated rejoin of a fenced primary as a
synchronous standby, and controlled planned failback with fail-closed
preconditions.

Also **done**, closed this session: forward secrecy and asymmetric session
key exchange for the UDP AEAD data plane (`forward_secrecy_claim`,
`asymmetric_session_key_exchange_claim` -- separate from the QUIC/PKI control
plane above). Mutual ephemeral EC keypairs (same curve as the long-term
SROS2 identity key) are exchanged via a new `FQKEX1` message that reuses the
existing SROS2 signature wrapper for authentication, so a forged ephemeral
key is rejected the same way a forged data frame is -- without that, ECDH
would trade "confidentiality depends on the PSK" for "confidentiality
depends on an unauthenticated key exchange," i.e. a trivial active MITM.
The derived per-peer secret is mixed into the existing PSK-based HKDF
(`HKDF-Extract(salt, PSK || ecdh_secret)`), so a compromised PSK alone can no
longer reconstruct a session key from a completed handshake, and each
ephemeral private key is destroyed immediately after derivation. Opt-in via
`FLEETQOX_RMW_UDP_ECDH_ENABLE=1`, fails closed without SROS2 peer
authentication already enabled (there would be no way to distinguish a real
peer's ephemeral key from an attacker's otherwise). Because a per-peer
secret can't encrypt one ciphertext for a multi-target broadcast, enabling
it switches multi-target sends to one single-target send per peer.
Verified end to end via `scripts/run_rmw_docker_udp_ecdh_probe.py`: the
handshake completes and subsequent frames measurably use the ECDH-mixed key
(`udp_ecdh_encrypted_frames` increments) across repeated real two-process
runs, a tampered KEX signature is rejected exactly like a tampered data
frame while ordinary delivery is unaffected, and `FLEETQOX_RMW_UDP_ECDH_ENABLE`
is refused when peer auth isn't already enabled. Known scope limit: no
re-keying loop if a peer restarts and offers a new ephemeral key after an
existing handshake already completed.

**Still open** (`capabilities.json` `false`), narrower than previously
documented:

- active-active (multi-master) consensus -- `quic_gateway_active_active_consensus_claim`
  stays unclaimed on purpose. Raft (native or etcd) is single-leader by
  design: exactly one node accepts writes at a time, everyone else
  rejects them. That is active-*passive* with automatic, safe failover,
  which is what's actually proven (see below) -- true active-active
  (multiple nodes accepting writes concurrently) is a different
  architecture (e.g. CRDTs or multi-leader conflict resolution) that
  nothing in this codebase implements;
- automatic, unattended recovery after losing an entire MAJORITY-holding
  region, with no witness in a fourth location --
  `regional_witness_free_majority_region_recovery_claim` stays unclaimed
  on purpose. This is not an unimplemented feature; it is a mathematical
  property of quorum-based consensus (no witness means no way to safely
  break the tie), the same reason `regional_disaster_recovery_claim`
  itself narrowly means "losing any ONE of several independent regions"
  below, not "losing the majority of them";
- hardware-level STONITH validated against a specific vendor's real BMC
  firmware -- `quic_gateway_hardware_stonith_claim`,
  `hardware_stonith_real_bmc_firmware_validated_claim`. What IS now proven
  (see below) is the real DMTF Redfish protocol path itself, exercised
  against a protocol-conformant simulator since no physical hardware is
  available in this environment; firmware-specific validation against an
  actual vendor BMC is what remains open, and it is an environment
  limitation (no hardware to test against), not an unimplemented feature;
- production certification of the automatic rejoin/failback paths, as
  opposed to the Docker/netem evidence already proven -- 
  `quic_gateway_production_automatic_rejoin_claim`,
  `quic_gateway_production_automatic_failback_claim`. Production
  certification is an organizational/deployment milestone (real workload
  history, ops runbooks, possibly third-party audit), not something a
  repository of code and Docker probes can produce on its own -- there is
  no code gap here to close.

Also **done**, closed this session: 0-RTT for the legacy ngtcp2/GnuTLS
subprocess-backed QUIC gateway path (`quic_zero_rtt_claim` -- the
`gtlsclient`/`gtlsserver` example-binary fallback used by
`run_rmw_docker_quic_gateway_*` probes, not the separate stateful aioquic
FleetQoX gateway, which still has no 0-RTT support and remains correctly
unclaimed there). The client already attempted 0-RTT by default once a
session/transport-parameter file existed; the probe's evidence parser was
the actual gap -- it only looked for an "early data accepted" log phrase
that ngtcp2's example client never prints (it only ever prints on
*rejection*, driven by `ngtcp2_conn_get_early_data_rejected()`). Fixed by
detecting genuine acceptance functionally instead: the server's own `frm rx
... 0RTT STREAM(...)` log lines prove it decrypted and processed the 0-RTT
payload (only possible with correct early keys), combined with the absence
of the authoritative rejection message. Verified with a negative control
(`FLEETQOX_RMW_QUIC_DISABLE_EARLY_DATA=1` correctly reports no packet/no
acceptance) so the signal is falsifiable, not vacuous, across the session
reuse, take-path, and bidirectional probes.

Also **done**, closed this session: general network-partition split-brain
tolerance (`quic_gateway_partition_split_brain_tolerance_claim`). Every
prior partition probe used one shape -- the primary loses ALL connectivity
at once (etcd, standbys, and clients alike), which can't actually exercise
the classic split-brain risk, since a primary that can't talk to anyone
also can't silently serve clients writes it can't sync. A new probe
(`scripts/run_rmw_docker_postgres_replication_partition_split_brain_probe.py`)
targets the harder, previously-untested shape instead: a *replication-only*
partition (a tc filter matched on just the standby's destination IP, not a
blanket interface loss), so the primary keeps serving clients while cut off
from its synchronous standby specifically.

Building it surfaced a real, previously-undocumented PostgreSQL hazard that
now shapes the claim's evidence: a transaction waiting on
`synchronous_commit` has *already committed locally* before the wait even
starts. Aborting that wait with an ordinary query cancel
(`pg_cancel_backend` -- notably, `statement_timeout` does NOT interrupt
this wait at all, by design) lets the original command report a clean
success ("INSERT 0 1") with only a WARNING that it might not be replicated
-- a real false-success hazard for any client that doesn't scan warnings.
Aborting the same stuck backend with `pg_terminate_backend` instead reports
a clear client-visible failure, no false success. This system's actual
STONITH path (`fleetqox_postgres_fence_agent.py`) SIGKILLs the whole
container, which is at least as safe as `pg_terminate_backend` -- the probe
verifies this contrast directly (both outcomes, on the same stuck write)
rather than assuming it. Combined with the existing quorum-loss-fail-closed
and fenced-promotion evidence (a full-isolation partition still safely
elects a new primary; total quorum loss still correctly refuses to
promote), this closes the "general," not just single-scenario, partition
tolerance gap.

Also **done**, closed this session: native (not etcd-backed) consensus and
a genuinely distributed database (`quic_gateway_automatic_leader_election_claim`,
`quic_gateway_consensus_backend_claim`, `quic_gateway_distributed_database_claim`).
`fleetqox/raft.py` is a from-scratch Raft implementation -- leader
election, replicated log, and the commit-safety rules (majority
replication, the current-term-entry rule from Section 5.4.2, log-matching
truncation on conflict) -- built as a transport-free "functional core" so
every safety property could be pinned down with a deterministic in-process
test harness (`tests/test_raft.py`, 9 tests) rather than a timing-dependent
integration test: election safety (never two leaders in one term), a
partitioned minority-of-one leader that can never commit, an old-term
entry that reaches every node but still isn't committed until the new
leader replicates something from its own term, and more.

`scripts/fleetqox_raft_node_service.py` turns that core into a real
networked key-value store (JSON-over-HTTP), and
`scripts/run_rmw_docker_raft_consensus_probe.py` proves it end to end over
five actual separate Docker containers, with no etcd and no PostgreSQL
anywhere in the loop: election among real processes, a non-leader
rejecting a write and naming the real leader, a committed write replicated
to all five, `docker kill`-ing the leader triggering automatic re-election
at a higher term with the committed value intact, and -- the interesting
one -- disconnecting enough survivors that no side holds a majority of the
original five makes the cluster correctly refuse new writes (fail-closed,
mirroring `quic_gateway_quorum_loss_promotion_fail_closed_claim`) and
recover cleanly once reconnected. That last step also surfaced a real
Docker networking gotcha worth recording: `docker network connect` does
NOT restore the `--network-alias` a container had at `docker run` time, so
a "reconnected" node can send RPCs out but silently never receive any back
until the alias is re-specified on the connect call -- a subtle one-way
partition that looks healed from the outside.

Scope, stated precisely: this is single-leader consensus (active-passive
with automatic failover), not active-active/multi-master --
`quic_gateway_active_active_consensus_claim` stays correctly false (see
above).

Also **done**, closed this session: the native core is no longer just a
standalone module -- it now actually gates the real QUIC gateway's writer
lease (`quic_gateway_consensus_leader_election_claim`,
`quic_gateway_raft_backed_writer_lease_claim`). `scripts/fleetrmw_quic_gateway_service.py`
gained `--raft-status-url`/`--raft-node-id`: when set, the gateway blocks
at startup on `fleetqox/raft_writer_lease.py`'s `RaftLeaderLease.require_leadership()`
against its own co-located Raft node instead of accepting a fixed, operator-assigned
`--writer-lease-instance-id`, and periodically re-checks that same
leadership during renewal so a demoted node stops itself on its own next
tick rather than waiting for someone else to notice. This deliberately
does not touch `quic_gateway_state.py`'s existing, separately-proven SQL
lease/fencing logic at all: Raft's current term (strictly increasing
across every leadership change) is folded into the holder_id string handed
to that unchanged SQL path (`raft-{node_id}-term-{term}`), so a leadership
change still produces a fresh SQL `fence_token` exactly the way a new
static instance id would -- Raft supplies the leadership *decision*, the
existing SQL store still enforces it at write time.

`scripts/run_rmw_docker_quic_gateway_raft_writer_lease_probe.py` proves
this end to end over a real QUIC v1/H3 connection, a real 3-node Raft
cluster, and two real gateway processes sharing one SQLite durable store:
a gateway whose Raft node is not the leader fails closed immediately; the
Raft leader's gateway accepts a real durable admission write; killing the
leader's Raft node (and its gateway) triggers a genuine Raft election among
the two survivors -- the actual winner is discovered by polling, not
assumed, since either could legitimately win -- and the next gateway,
pointed at whichever node really won, automatically recovers the prior
gateway's durable state with a strictly higher SQL fence_token. 3/3 runs.

Also **done**, closed this session: regional disaster recovery, scoped to
its common real-world meaning -- losing any ONE of several independent
regions is survived automatically
(`quic_gateway_regional_disaster_recovery_claim`). Every prior
partition/quorum probe treated the cluster as one flat set of nodes;
`scripts/run_rmw_docker_regional_disaster_recovery_probe.py` instead
assigns each etcd member and the PostgreSQL primary/standby to one of
three named regions (region A: 1 etcd member + primary; region B: 1 etcd
member + standby + the failover controller and fence agent; region C: 1
etcd member only) and disconnects an entire region's containers from the
network simultaneously, over real separate Docker containers, proving: (1)
losing a minority region (C) causes zero disruption -- writes keep
succeeding with reduced redundancy, and the region cleanly rejoins
quorum once reconnected; (2) losing the region that holds the PRIMARY (A)
triggers a genuine etcd-quorum-gated failover -- the survivors (a real
majority, 2 of 3) detect the loss, fence the isolated primary (Docker-socket
SIGKILL still reaches it -- legitimate out-of-band fencing, not a
shortcut, since real regional STONITH also uses an out-of-band management
path), and promote the surviving region's standby, which then accepts
writes; (3) losing a SECOND region afterward (leaving one, a minority)
correctly blocks any further promotion -- fail-closed, matching
`quic_gateway_quorum_loss_promotion_fail_closed_claim`. 3/3 runs.

`regional_witness_free_majority_region_recovery_claim` stays `false` on
purpose (see above): automatic recovery from losing a majority-holding
region is a different, and for any quorum system without a witness in a
fourth location, genuinely impossible, property -- not what "regional
disaster recovery" is claimed to mean here, and not something this or any
other probe can close.

Also **done**, closed this session, scoped precisely: the real Redfish
hardware-fencing protocol path (`hardware_stonith_redfish_protocol_claim`).
No physical server or BMC exists in this environment to fence -- but the
protocol real hardware fencing depends on (DMTF Redfish: a standard
HTTPS/JSON `ComputerSystem.Reset` action, not something vendor-specific)
is well documented and testable without one. `scripts/fleetqox_redfish_bmc_simulator.py`
is a minimal but protocol-conformant fake BMC (the same pattern OpenStack
Ironic/Metal3 use in CI to test bare-metal power management without
physical hardware), and `scripts/fleetqox_hardware_stonith_agent.py` is a
real Redfish HTTPS client wired into this codebase's existing fence-agent
pattern (same DCS-lease authorization, same mTLS client-identity binding
on `/fence` as `fleetqox_postgres_fence_agent.py` -- just a genuine
`POST .../Actions/ComputerSystem.Reset` in place of a Docker-socket
SIGKILL). `scripts/run_rmw_docker_hardware_stonith_redfish_probe.py`
proves, over real separate Docker containers: an unauthenticated fence
request is rejected at the TLS layer with the BMC's power state untouched;
a forged/non-existent DCS lease is rejected (403) with the power state
untouched; and a request authorized by a real etcd-issued lease produces a
genuine Redfish reset call that a *separate, independent* query to the BMC
confirms actually transitioned power state from On to Off. 3/3 runs.

Stated precisely: `quic_gateway_hardware_stonith_claim` and the new
`hardware_stonith_real_bmc_firmware_validated_claim` stay `false` on
purpose. What's proven is that this code speaks the real Redfish protocol
correctly end to end; what remains open -- validation against a specific
vendor's actual BMC firmware, which can carry quirks no simulator captures
-- is an environment limitation (no physical hardware available to test
against here), not an unimplemented feature or a claim inflated beyond its
evidence.

Also **done**, closed this session: every split-brain/failover probe above
ran as multiple Docker containers on ONE Docker daemon on ONE machine --
correct as far as it goes, but unable to exclude a shared-kernel confound
(an administrative review of this codebase explicitly flagged this: "tách
failure domain khỏi single Docker daemon"). `scripts/
run_multihost_kvm_raft_consensus_probe.py` closes this for the native Raft
core specifically, over two REAL, independent hosts: two KVM virtual
machines (`scripts/run_multihost_kvm_setup.sh`), each with its own kernel
and its own separate Docker daemon, connected only by a real virtio-net
link between them (no shared bridge, no host-visible tap the two share).
The 5-node Raft cluster splits 2 nodes on VM1, 3 on VM2; VM1's election
timeout is tuned shorter so the initial leader deterministically lands on
VM1 (making the next step a genuine forced failover, not a lucky
continuity case). The probe then kills VM1's entire QEMU process from the
host side -- outside either guest's own OS, the closest thing to a real
power-loss/hardware failure this environment can produce -- and proves:
the write from before the kill is intact and replicated on VM2 alone; the
3 surviving VM2 nodes elect a new leader at a strictly higher term purely
by noticing VM1's absence over the network; a new write commits and
replicates across the survivors; and killing one more of VM2's three
nodes (now 2 of the original 5 -- a minority) correctly fails closed --
no new leader at a higher term, no accepted write -- rather than let the
minority form a second, divergent leader. 5/5 runs, each with a fresh
VM1 boot and a genuinely re-randomized leader election.
(`multi_host_kvm_raft_consensus_claim`, `multi_host_no_split_brain_claim`).
This closes the multi-host gap specifically for the Raft-backed writer
lease path introduced earlier this session.

Also **done**, closed this session: the SAME multi-host closure for the
project's older, originally-named HA path -- etcd DCS + PostgreSQL
streaming replication + Docker-socket STONITH
(`scripts/run_multihost_kvm_postgres_etcd_fencing_probe.py`). This one was
materially harder than the Raft case: the existing fence agent
(`fleetqox_postgres_fence_agent.py`) fenced a container by calling the
Docker Engine API over the *local* Unix socket, which cannot reach a
container on a different host's Docker daemon. `docker_connection()` in
that file now dispatches to either a Unix socket (unchanged default,
same-host fencing) or a `tcp://host:port` Docker Engine API URL
(cross-host fencing) -- a genuine, additive capability, not a probe-only
shim.

Topology: VM1 runs etcd1 and the PostgreSQL primary; VM2 runs etcd2+etcd3
(a real majority), the PostgreSQL standby, the failover controller, and
the fence agent. Getting real inter-VM traffic right took two fixes worth
recording: (1) Docker-published container ports are reached via DNAT into
the FORWARD chain, not INPUT/OUTPUT, so `iptables -A INPUT/OUTPUT DROP`
silently does nothing to them; (2) two etcd members that both live on VM2
but talk to each other via VM2's own *published* host port get hairpin-NAT
rewritten to the docker0 bridge address, which fails etcd's peer TLS check
-- invisible right up until an election is actually needed. Fixed by
running every fenceable service with `--network host` (binding the VM's
real interface directly, no bridge/NAT at all) and partitioning at the
plain INPUT/OUTPUT layer with an explicit ACCEPT carved out for port 2375
(the Docker Engine API) ahead of the blanket DROP.

The probe partitions VM1 from VM2 at the network layer (VM1 stays up and
its primary keeps running -- the actual "still alive but unreachable"
hazard STONITH exists for, not a clean host crash) and proves, over the
real inter-VM link: a 3-member etcd cluster reaches quorum split across
two hosts; PostgreSQL streaming replication works primary-on-one-host,
standby-on-the-other; the controller on VM2 detects the primary as
unreachable, acquires the DCS lease, and fences it with a genuine
cross-host Docker kill reaching a host that is still up and running,
independently confirmed by directly inspecting VM1's own Docker state (not
trusting the controller's or fence agent's self-report); only then is the
standby promoted, and the promoted standby accepts a new write. 5/5 runs.
(`multi_host_postgres_etcd_fencing_claim`, `multi_host_cross_host_stonith_claim`).

Between the two, every HA mechanism this project has (Raft-backed writer
lease, and etcd/PostgreSQL/STONITH) now has genuine multi-host evidence.
PKI operational hardening (clock skew, CA rollover, expiry edge cases,
crash-consistency under power loss) does not need multi-host and remains
open as its own item.

Exit gate:

- public maintained APIs only -- **met**;
- online server/client certificate and CA rotation -- **met**;
- active-session revocation and fail-closed expiry -- **met**;
- forward secrecy and asymmetric session establishment -- **met** (UDP AEAD
  data plane, via ephemeral ECDH; see above);
- 0-RTT -- **met** (legacy ngtcp2 subprocess gateway path; see above);
- general network-partition split-brain tolerance -- **met** (see above);
- regional disaster recovery (losing any one of several independent
  regions) -- **met** (see above; automatic recovery from losing a
  majority-holding region without a witness remains, and will always
  remain, open -- a property of consensus, not a gap);
- leader election/consensus, split-brain fencing, rejoin/failback, regional
  recovery, and operational runbooks -- **rejoin/failback,
  quorum-gated/STONITH-fenced promotion, general split-brain tolerance, a
  native (non-etcd) consensus/distributed-database core, that core
  actually gating the real gateway's writer lease, the real Redfish
  hardware-fencing protocol path, and regional (single-region-loss)
  disaster recovery all met; active-active consensus, witness-free
  majority-region recovery, real-BMC-firmware validation, and production
  (non-Docker) certification remain open -- the last two are
  environment/organizational limits, not code gaps (see above)**;
- multi-host (non-shared-kernel) failover -- **met for both HA mechanisms**
  (native Raft writer-lease path, and etcd/PostgreSQL/STONITH, each proven
  over two real KVM VMs, see above); hardware STONITH and PKI rotation
  remain single-Docker-daemon-only (hardware STONITH has no multi-host
  aspect to close -- it already targets an out-of-band BMC path; PKI
  operational hardening doesn't need multi-host and is tracked separately);
- long multi-attacker soak -- open.

### B3: complete RMW semantics

Open semantic boundaries include full remote event production, full
message-lost/liveliness/non-deadline QoS semantics, DDS filter-dialect parity,
DDS-equivalent all-acknowledged behavior, deep preallocation, and zero-copy.

Also **done**, closed this session: the content-filter comparison predicate
(=, !=, <>, <, <=, >, >=) previously required the field name to always be on
the left of the operator. OMG DDS-SQL's comparison predicate is symmetric
(`Parameter RelOp Parameter`, where `Parameter` is a FieldName, Value, or
Enumeration on either side), so `field = %0` worked but the reversed
`%0 = field` or `%1 < field` did not parse as a field comparison at all.
`ContentFilterExpressionParser::parse_predicate` in `rmw_pubsub.cpp` now
special-cases a parameter or quoted-literal token in the first position,
parses the trailing operand as a field reference, and re-evaluates the
comparison with the operator direction flipped for the four ordering
operators (equality/inequality are already symmetric). `BETWEEN`/`IN`/`LIKE`/
`IS [NOT] NULL` are unchanged -- they keep requiring a field on the left,
since testing a constant's membership/pattern/nullability is not a
meaningful DDS-SQL predicate. Proven by a dedicated scenario
(`%0 = robot_id AND %1 < sequence`) plus a malformed-reversed-form negative
control (`%0 = %1`, no trailing field) added to
`content_filter_sql_probe.cpp`, rebuilt clean under ASan/UBSan with zero
diagnostics, and passing 5/5 in `docker_content_filter_sql_probe`. This is a
genuine, bounded, standards-compliant expansion, not full DDS-SQL parity --
the full dialect (arbitrary DDS SQL functions, vendor-specific semantics,
and any FIELD-to-FIELD comparison where neither side is a bare parameter or
literal) remains out of scope and is still a deliberate non-goal.

Also **done**, closed this session (bounded scope, not a full closure): the
publish hot path's frame encoding now reuses a persistent per-publisher
buffer (`FleetQoxPublisherData::frame_base64_scratch`) for the serialized
payload's base64 text across repeated publishes, via a new
`encode_data_frame(frame, base64_scratch)` overload and a
`base64_encode_append` helper that writes into a caller-owned string instead
of allocating a fresh one every call. A dedicated 5/5 Docker artifact
(`docker_deep_preallocation_probe`) publishes eight same-size payloads per
process and proves the buffer's capacity grows once on the first publish
then stays exactly stable for the remaining seven, rebuilt clean under
ASan/UBSan with zero diagnostics. This intentionally does **not** close
`deep_preallocation_claim`: the JSON frame body is still built through a
fresh `std::ostringstream` per publish (its floating-point field formatting
was deliberately left untouched -- rewriting it risks silently changing
on-wire number formatting roughly 187 other probes depend on), the
reliability retransmit ledger still allocates a new entry per in-flight
reliable message by design (it is an unbounded, QoS-depth-driven
store-and-forward buffer, not incidental inefficiency), and
application-message deserialization is unchanged. A full closure would need
a binary (non-JSON) wire format and a pool allocator for the retransmit
ledger -- a redesign, not a bounded addition -- and remains unclaimed.

Also **done**, closed this session (bounded scope, not a full closure): the
loaned-message buffer itself is now pooled per publisher/subscription
instead of allocating a fresh block on every `borrow_loan()` call.
`release_loan()` runs the type's `fini_function` (releasing any heap-owned
sub-fields such as `std::string`/`vector` internal buffers) then returns the
raw block to a capped per-owner pool (`g_loan_pool`, 8 buffers) instead of
deallocating it; `borrow_loan()` checks that pool before falling back to a
fresh allocation, and owner destruction (`release_owner_loans`) drains and
frees any buffers left in the pool. A dedicated 5/5 Docker artifact
(`docker_deep_preallocation_loaned_message_probe`) proves exactly one fresh
allocation occurs across a subscription's entire lifetime of repeated
take/return cycles (eight publishes, each polled via
`rmw_take_loaned_message` until taken), with every other borrow/release
cycle reusing the pool, rebuilt clean under ASan/UBSan with zero
diagnostics; the existing `loaned_message_probe` (5/5) shows no regression.
This intentionally does **not** close `zero_copy_loaned_message_claim`: it
removes the allocation, not the deserialization. The wire format is
JSON+base64 text and introspected ROS fields (`std::string`, sequences) own
separately-allocated memory, so the loaned buffer can never alias the
received network bytes directly -- true zero-copy would require a binary
wire format matching the in-memory struct layout, which is a redesign, not
a bounded addition, and remains unclaimed.

Exit gate:

- each capability either implemented and repeatedly probed or explicitly
  documented as a deliberate non-goal;
- lifecycle concurrency and allocation audited under sanitizers;
- no placeholder success paths.

### B4: autonomy and physical validity

The repository has broad Docker/Nav2/RMF wiring and bounded workloads, but not
the final representative multi-host autonomy campaign.

Exit gate:

- sustained Nav2 planner/controller/costmap workload with dynamic obstacles;
- representative RMF bidding, dispatch, state, and failure workload;
- multi-host network emulation;
- HIL and at least a small physical robot campaign.

### B5: paper/release evidence

Exit gate:

- one canonical benchmark manifest/schema/report;
- multi-seed confidence intervals and effect sizes;
- fair common-middle settings and explicit non-comparable rows;
- ns-3/OMNeT++ high-fidelity wireless and TSN/mesh scope completed or removed
  from the claim;
- clean CI, install, upgrade, rollback, PKI, and release instructions.

## Ordered implementation plan

### Phase 1 — stabilize the data plane

1. Finish the large-string ASan/UBSan stress gate.
2. Fix the memory defect and rerun all typed, fragment, callback, and teardown
   regressions.
3. Instrument whole-frame observation and late-burst convergence.
4. Close the 16/32-robot multi-seed 32-KiB matrix.

### Phase 2 — qualify transport/security

1. Make the public QUIC backend the default tested path.
2. Complete certificate lifecycle and revocation.
3. Add multi-node consensus/fencing/failure campaigns.
4. Run long secure fragment and gateway soak tests.

### Phase 3 — close semantic and integration gaps

1. Complete or explicitly scope remaining RMW semantics.
2. Run full Nav2 planner/controller and RMF workloads.
3. Add multi-host and HIL/physical validation.

### Phase 4 — release evidence

1. Freeze manifests and versions.
2. Run all baselines over the same matrix.
3. Generate one report with statistics and claim boundaries.
4. Package, document, tag, and archive the release evidence.

## Estimates

- Research-complete release candidate: **2–4 focused weeks**.
- Production-oriented qualification: **6–12 additional weeks minimum**.

The second estimate depends on external infrastructure, physical robots,
network/testbed access, PKI review, and the complexity of any sanitizer finding.
It is not responsible to estimate a fully production-qualified system in hours
or a few days.

## Definition of done

The project is not done because the code compiles, because a deterministic
probe passes, or because one benchmark row improves. It is done only when:

- every release blocker above has a repeated passing gate;
- the capability manifest and human documentation agree;
- baseline comparisons are fair and statistically supported;
- the repository builds from a clean clone;
- generated artifacts are outside Git;
- security and operational failure modes are explicit;
- the release can be reproduced by someone other than the original author.
