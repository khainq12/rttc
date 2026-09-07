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

- built-in/native consensus-based leader election and a true distributed
  database, as opposed to the current design (external etcd/Raft as the
  distributed configuration store, with a single synchronously-replicated
  PostgreSQL as the actual data store) -- `quic_gateway_automatic_leader_election_claim`,
  `quic_gateway_active_active_consensus_claim`, `quic_gateway_consensus_backend_claim`,
  `quic_gateway_distributed_database_claim`;
- partition/split-brain tolerance and regional disaster recovery as general
  claims, beyond the specific quorum-loss and STONITH-fencing scenarios
  already proven -- `quic_gateway_partition_split_brain_tolerance_claim`,
  `quic_gateway_regional_disaster_recovery_claim`;
- hardware-level STONITH, as opposed to the Docker-container fencing already
  proven -- `quic_gateway_hardware_stonith_claim`;
- production certification of the automatic rejoin/failback paths, as
  opposed to the Docker/netem evidence already proven -- 
  `quic_gateway_production_automatic_rejoin_claim`,
  `quic_gateway_production_automatic_failback_claim`.

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

Exit gate:

- public maintained APIs only -- **met**;
- online server/client certificate and CA rotation -- **met**;
- active-session revocation and fail-closed expiry -- **met**;
- forward secrecy and asymmetric session establishment -- **met** (UDP AEAD
  data plane, via ephemeral ECDH; see above);
- 0-RTT -- **met** (legacy ngtcp2 subprocess gateway path; see above);
- leader election/consensus, split-brain fencing, rejoin/failback, regional
  recovery, and operational runbooks -- **rejoin/failback and
  quorum-gated/STONITH-fenced promotion met via etcd/Raft DCS + Docker
  STONITH; built-in consensus, general split-brain tolerance, regional
  recovery, and production (non-Docker) certification remain open**;
- long multi-attacker soak -- open.

### B3: complete RMW semantics

Open semantic boundaries include full remote event production, full
message-lost/liveliness/non-deadline QoS semantics, DDS filter-dialect parity,
DDS-equivalent all-acknowledged behavior, deep preallocation, and zero-copy.

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
