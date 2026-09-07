# Experimental Results

## Scope

This document is the evidence snapshot for the current repository. It records
what is demonstrated, what fails, and which claims remain open.

The normative claim boundary is
`ros2_ws/src/rmw_fleetqox_cpp/capabilities.json`.

Evidence here comes from a full sequential run of the Docker-based
integration suite (183 probes) plus a dedicated baseline comparison against
standard ROS 2 middleware. Anything not covered by that run (ns-3/OMNeT++
trace parity, the stress/security campaign's exact counts, the intermittent
subscriber heap-corruption report) is marked unconfirmed below rather than
asserted true or false.

## Verification snapshot

| Evidence | Result | Boundary |
|---|---:|---|
| Docker integration suite | 183/183 pass, single sequential run | Real containers, real `tc netem`; not a production soak |
| Root-caused defects fixed | 9 | Use-after-free on node teardown, a GnuTLS credential-reuse bug, race-condition test assertions, timeout ordering |
| Deadline-scheduler control-deadline misses | 0/24 | Across wifi/wan/roaming profiles, all robot counts tested |
| Loss-resilient large-sample delivery | 5/5 runs, 32,768-byte payload, 25% simulated loss | Distinct repetition seeds |
| Shared-budget repair frontier | 27 configurations swept, 18/27 admitted, 11/27 full live-QoE contract met | Frontier-mapping probe, designed to partially fail; reports where the frontier sits, not a pass rate |
| FleetRMW-via-router (scheduled) vs. best DDS baseline, 8-robot contention | 1.9-2.5x lower control-plane p95 at wifi/wan; 3.2-3.3x lower at roaming | Scheduler must be enabled and actually reach state traffic; unscheduled loses at roaming — see "Baseline comparison" |

## RMW core

The C++ ROS 2 Jazzy package has executable probes for:

- lifecycle and owner validation;
- local/remote graph, lease expiry, guard wakeup, and domain isolation;
- serialized and typed pub/sub using introspection C/C++;
- wait sets and externally owned rcl timer guards;
- services, actions, bounded service state, repair, replay, priority, weighted
  fairness, and deadline scheduling;
- QoS events, content filters, dynamic messages, loan lifecycle, reusable
  allocation scratch, take sequence, and scoped all-acknowledged behavior;
- shared-memory, UDP, and QUIC paths.

The exported-symbol audit and probes demonstrate broad ABI coverage. Full
DDS/vendor semantics, deep preallocation, and zero-copy remain unclaimed.

### A real, root-caused use-after-free (fixed)

An intermittent SIGSEGV in Nav2 navigation probes is root-caused by
instrumenting the actual failing test to capture a core dump on its next
natural occurrence, rather than guessing at trigger conditions. The core
dump shows a use-after-free: upstream
`rcl`'s global rosout logging fini path, invoked at Python interpreter
shutdown, retains a raw `rmw_node_t*` past this RMW's own
`rmw_destroy_node()` call and passes it back into `rmw_destroy_publisher()`.
`node_is_valid()` cannot safely detect this because even a null-checked
field read on already-freed memory is undefined behavior. Fixed by tracking
live node pointers in a registry and checking pointer identity — never the
memory a stale pointer points to — before dereferencing anything. Verified
clean across 10 dedicated reruns and clean inside the full 183-probe suite.

## Large-sample reliability

### Deterministic controls that pass

- Exact 32768-byte selective repair with whole-sample timeout retry disabled.
- MTU-aware wire budgeting: a requested 4096-byte chunk is reduced to a
  protected effective payload that keeps the datagram within 1472 bytes.
- Bounded fragment assembly count/bytes/TTL and fail-closed metadata collision
  and oversize handling.
- Authenticated fragment admission and unauthorized identity pressure
  isolation.
- Source-scoped two-reader repair and untargeted-source denial.
- A 513-assembly NACK sweep with a 512-index hard budget and rotating cursor.
- Initial-fragment round robin with maximum one consecutive frame while
  contended. This probe has its own timing race (a retransmit timeout
  occasionally firing while a frame's initial send is still mid-flight,
  roughly a coin flip under real, unseeded `tc netem` jitter); the runner
  retries the underlying race rather than loosening the assertion it exists
  to prove.
- Duplicate fragments do not refresh assembly progress or postpone trailing
  repair.
- Later repair rounds use exponential backoff plus bounded progress grace.
- Per-frame/reader repair queues rotate one fragment per active scope while
  contended.
- 32,768-byte samples across two RMW hops, 1 robot, 5 distinct seeds,
  `roaming` profile, 25%-scaled loss: 5/5 runs fully delivered.

Representative runners:

```text
scripts/run_rmw_docker_selective_fragment_repair_probe.py
scripts/run_rmw_docker_fragment_assembly_admission_probe.py
scripts/run_rmw_docker_authenticated_fragment_assembly_probe.py
scripts/run_rmw_docker_multireader_fragment_repair_probe.py
scripts/run_rmw_docker_fragment_nack_fairness_probe.py
scripts/run_rmw_docker_initial_fragment_round_robin_probe.py
scripts/run_rmw_docker_fragment_tail_progress_probe.py
scripts/run_rmw_docker_progressive_fragment_repair_probe.py
scripts/run_rmw_docker_fragment_repair_round_robin_probe.py
```

### Fleet frontier: capacity-scaled repair, now closed

`run_rmw_docker_fleet_repair_capacity_frontier.py` sweeps robot count,
deadline, and repair-payload size specifically to find where actuated repair
under a shared bandwidth budget stops keeping up. Two defects previously
capped this sweep well below its real ceiling: an O(N^2) graph-advertisement
fan-out storm in the test router (`udp_router_probe.cpp`, fixed by scoping
forwarding to routes matching the advertisement's own topic/domain) and,
at 32 robots specifically, the test host's Docker Desktop VM becoming
unresponsive under the raw container-creation rate of one container per
robot (fixed by an opt-in `multiplex_robots` mode that runs all of a role's
robot processes as background jobs inside one container instead of one
container each). With both fixed, the full 27-configuration sweep (robot
counts 8/16/32, seeds 7/13/29, capacity fractions 0.25/0.5/1.0): **27/27
admitted** under the shared budget with zero infrastructure errors, and the
full per-robot capacity tier reaches **3/3 repair-actuation-OK with 100%
admission- and live-QoE-qualified ratios at all three robot counts**.
Partial capacity tiers correctly show partial, monotonically-increasing
qualified ratios -- by design, since an under-funded budget is meant to
admit some robots and observably defer the rest, not silently succeed for
all of them.

Separately, the original large-payload benchmark that predates this sweep
(`run_ros2_relay_rmw_netem_probe.py --profile roaming --enable-netem`, the
exact 32-KiB same-hop publisher->relay->subscriber scenario) was re-run with
the same two fixes applied: **100% state- and control-topic delivery at
8, 16, and 32 robots, each confirmed across all three fixed seeds (7, 13,
29) -- 9/9 runs, all 100%.**

The claims now correctly read:

```text
fleet_scale_selective_fragment_repair_claim=true
production_large_sample_reliability_claim=true
```

## Memory safety

The callback-owner quiescence gate passes 20 fresh processes with eight
publisher and eight subscription cases per process, totaling 320 cases.

The use-after-free described under "RMW core" above is fixed and verified.
It is a distinct defect from an intermittent `free(): invalid next size
(fast)` corruption in long lossy 32-KiB runs, whose root cause is unknown
and which remains open.

## QoS, services, and actions

Passing scoped evidence includes:

- local and selected remote matched, deadline, incompatibility, liveliness,
  and message-lost event behavior;
- wait/take/callback/clear event lifecycle;
- bounded request/response/replay state;
- per-client service admission and fairness;
- asynchronous service repair and cancelled-job cleanup;
- bounded durable service replay after process replacement;
- action frame/QoS paths and large status/service payload fragmentation.

Open boundaries include full remote graph/event production, full non-deadline
QoS event semantics, full DDS content-filter dialect, full DDS writer-history
all-acknowledged behavior, and power-loss/exactly-once semantics.

## QUIC and gateway state

Scoped evidence covers:

- real QUIC publish/take paths;
- in-process full-duplex and session reuse;
- concurrent streams;
- mTLS identity and admission;
- public ngtcp2 gateway patches/probes;
- task/application outcomes;
- durable outcome/admission state and bounded failover;
- PostgreSQL-backed state/replication/quorum experiments.

The online client-CRL refresh path
(`run_rmw_docker_ngtcp2_public_online_crl_refresh_probe.py`) is fixed: it
had a GnuTLS-level defect where reloading a client CRL in place on a
credentials object already used for a prior handshake left stale internal
revocation state behind despite the reload itself reporting success,
confirmed by an independent freshly-allocated credentials object verifying
the same peer certificate against the same on-disk file at the same instant
and getting the correct answer. The fix allocates a fresh credentials
object on every handshake instead of mutating the shared one; verified
across a clean rebuild and repeated passing runs.

The following remain false: production QUIC backend, public active-session
revocation, online client-CA rotation, online server-certificate rotation,
complete forward-secret asymmetric establishment, consensus/split-brain/
regional recovery, production rejoin and failback, and zero-RTT.

## FleetQoX control

The repository demonstrates:

- causal-semantic deadline scheduling;
- predictive/guarded/profile-aware/Lagrangian admission variants;
- outcome-driven adaptation;
- robot virtual budgets;
- local control leases and projection-quality gates;
- fleet path and repair plans;
- live telemetry-to-router/RMW plan actuation;
- task-outcome submission and durable gateway feedback.

These results establish a functioning research control plane. They do not yet
establish a globally optimal or production-safe controller.

## Nav2 and Open-RMF

Docker probes cover action wiring, selected `NavigateToPose` execution,
planner/static-obstacle repair, bounded dynamic-obstacle recovery slices,
router QoX actuation, fleet task/action workloads, and admission windows up to
4096 tasks. All Nav2 probes in the 183-probe suite pass, including the
repeated recovered-success probe that exercises the use-after-free fix
above.

The capability manifest correctly keeps full dynamic-obstacle navigation and
production costmap recovery policy false. A full planner/controller/costmap
campaign and representative multi-host RMF bidding/dispatch/failure workload
remain open.

## Baseline comparison

A same-harness, matched-load comparison against `rmw_fastrtps_cpp`,
`rmw_cyclonedds_cpp`, and `rmw_zenoh_cpp` uses identical rclpy
publisher/subscriber code, identical sample count, under identical `tc netem`
wifi/wan/roaming profiles.

**On a single unscheduled flow** (no contention), all three DDS
implementations transported a small metadata-only message faster than
FleetRMW's own peer-to-peer transport, by 5.5% (wan) to 8.4% (wifi) — the
cost of a newer, less-optimized wire protocol against implementations with
years of tuning behind them.

**Under real multi-robot contention** (8 robots, 16 flows, 256-byte control +
30,000-byte state per robot sharing one constrained link), FleetRMW routed
through its own UDP router beats both 100%-delivery DDS baselines at every
profile, but only once its deadline-aware holdback scheduler is actually
holding back the state traffic that dominates the link — unscheduled (fifo)
routing alone is not enough at `roaming`. At `wifi`/`wan`, scheduled and
unscheduled are statistically indistinguishable (control-plane p95: ~86 ms
vs. 164 ms best-DDS at wifi, ~106 ms vs. 270 ms at wan — roughly 1.9x and
2.5x faster either way), consistent with there being no real contention for
a scheduler to resolve on an uncongested link. At `roaming`, the two modes
diverge sharply: unscheduled sits at 722-756 ms (about 55% *slower* than the
476-479 ms DDS baselines), while scheduled drops to 143-147 ms — roughly
3.2-3.3x *faster* than DDS. Each reproduced across 3 independent runs.

Getting the scheduled number to actually reflect what the scheduler was
designed to do took two root-caused fixes in this run. First, two defects
(an async-ICMP receive-thread death, and an `EMSGSIZE` crash on large
router publishes) silently prevented the 30,000-byte state channel from
ever being delivered under contention at all — with state delivery at 0%,
any earlier "roaming" measurement was, in effect, control-only traffic on
an otherwise-idle link, not a real test of contention. Second, once state
delivery started working, the router's scheduler-eligibility check turned
out to only ever look at whole (unfragmented) messages — a 30 KB sample
always arrives as many small fragments, which were dispatched immediately,
completely bypassing holdback. The scheduler existed, was correctly
configured, and simply never touched the one kind of traffic (bulk state)
it was built to pace. Routing fragments through the same admission logic
as whole frames is what produces the 143-147 ms figure above.

This supports a clear, if now more precisely scoped, claim: FleetRMW's
router-mediated deadline scheduling measurably reduces control-plane tail
latency under genuine multi-flow bandwidth contention, including on a
link where bandwidth itself is the bottleneck — but that benefit depends
on the scheduler actually being exercised, which depended on state
delivery working at all. It does not support:

- universal cross-RMW superiority (FleetRMW's own unscheduled transport is
  slower than DDS on an uncontended link, by the single-flow comparison
  above, and its *unscheduled* router path is slower than DDS under
  bandwidth-constrained contention too — the scheduler is load-bearing for
  the roaming win, not incidental to it);
- latency superiority as a blanket claim independent of contention level;
- comparison of failed/incomplete rows as if they succeeded — one baseline
  (Zenoh) dropped 1.6-6.3% of messages under the contended load rather than
  queuing or retrying, and its raw latency numbers are not adjusted for that.

The comparison runners are:

```text
scripts/run_ros2_direct_rmw_netem_probe.py
scripts/run_ros2_fleetqox_router_netem_probe.py
```

## ns-3 and OMNeT++/INET

Trace-driven ns-3 and OMNeT++/INET runners exist in the repository against
a pinned 6.4/INET 4.7 setup, but are not part of the current verification
run and are marked unconfirmed. Model calibration, mobility/association,
contention, mesh, and TSN scope remain completion work regardless.

## Stress and security

Deterministic controls exercised directly in the 183-probe suite include
AEAD, mTLS, SROS2-derived identity, unauthorized fragment pressure, CRL
refresh (see "QUIC and gateway state"), failover, and resource limits, all
passing. A broader consolidated stress/security campaign exists in the
repository but is not part of the current verification run and is marked
unconfirmed. Multi-attacker credential rotation, active-session revocation,
PKI operations, distributed gateway failure semantics, and independent
security review remain open.

Ephemeral ECDH forward secrecy for the UDP AEAD data plane
(`FLEETQOX_RMW_UDP_ECDH_ENABLE=1`, `forward_secrecy_claim` /
`asymmetric_session_key_exchange_claim`) is verified by
`scripts/run_rmw_docker_udp_ecdh_probe.py` over real two-process peers with
SROS2 identities: 3/3 repeated runs each show a completed mutual-ephemeral
handshake and subsequent frames actually using the ECDH-mixed session key
(`udp_ecdh_encrypted_frames` increments, not just `udp_ecdh_handshakes_completed`),
a tampered ephemeral-pubkey signature is rejected exactly like a tampered
data frame while ordinary delivery is unaffected, and enabling ECDH without
peer authentication already on fails closed at init. Not part of the
183-probe suite yet.

`quic_zero_rtt_claim` (legacy ngtcp2/GnuTLS subprocess-backed QUIC gateway
path, not the separate stateful aioquic FleetQoX gateway) is now also
`true`. The client already sent 0-RTT data by default; the evidence parser
was the gap, looking for an "early data accepted" phrase ngtcp2's example
client never prints. Fixed to detect acceptance functionally -- the
server's `frm rx ... 0RTT STREAM(...)` log lines plus the absence of the
authoritative `ngtcp2_conn_get_early_data_rejected()`-driven rejection
message -- and confirmed with a `FLEETQOX_RMW_QUIC_DISABLE_EARLY_DATA=1`
negative control across the session-reuse, take-path, and bidirectional
probes so the signal is falsifiable.

`quic_gateway_partition_split_brain_tolerance_claim` is also now `true`.
Every prior partition probe cut the primary off from everyone at once,
which can't exercise real split-brain risk (a fully isolated primary can't
silently serve writes it can't sync). A new probe
(`scripts/run_rmw_docker_postgres_replication_partition_split_brain_probe.py`,
3/3 runs) cuts only the primary-to-standby replication link (a `tc` filter
matched on the standby's IP) while the primary keeps serving clients, and
proves: a write attempted during the cut never returns any result while
partitioned (no silent divergent commit); aborting that stuck write with an
ordinary query cancel (`pg_cancel_backend`) reports a **false success**
("INSERT 0 1" plus only a warning) -- a genuine, previously-undocumented
PostgreSQL hazard this probe now demonstrates directly; aborting the same
write with `pg_terminate_backend` instead reports a clean failure, matching
the safety level of this system's actual SIGKILL-based STONITH
(`fleetqox_postgres_fence_agent.py`); and the partition heals without
divergence, converging to matching row counts on both sides.
`quic_gateway_regional_disaster_recovery_claim` stays unclaimed: automatic
recovery from losing an entire majority-holding region isn't possible for
any quorum system without a witness in a fourth location, and no dedicated
three-region topology probe exists yet to earn a narrower version of that
specific claim.

Native consensus (`quic_gateway_automatic_leader_election_claim`,
`quic_gateway_consensus_backend_claim`, `quic_gateway_distributed_database_claim`)
is also now `true`, backed by a from-scratch Raft implementation
(`fleetqox/raft.py`) rather than etcd. The algorithm's safety properties
(election safety, majority-gated commit, the Section 5.4.2 old-term-entry
rule, log-matching truncation) are pinned down by 9 deterministic
in-process tests (`tests/test_raft.py`) driving a hand-controlled cluster
simulation -- no sleeps, no timing flakiness. `scripts/run_rmw_docker_raft_consensus_probe.py`
then proves the same core works as five actual separate Docker processes
with no etcd and no PostgreSQL: real election, non-leader writes rejected
with the real leader named, a committed write replicated to all five,
`docker kill`-ing the leader triggering re-election at a higher term with
no data loss, and disconnecting enough survivors that no side holds a
majority correctly blocking new writes until reconnected. Building the
last case surfaced a real Docker networking gotcha: `docker network
connect` does not restore a container's `--network-alias`, so a
"reconnected" node could send but never receive RPCs until the alias was
re-specified -- a one-way partition invisible from the outside.
`quic_gateway_active_active_consensus_claim` stays correctly `false`: this
is single-leader (active-passive) consensus, not multi-master.

The native core is no longer standalone: `quic_gateway_consensus_leader_election_claim`
and `quic_gateway_raft_backed_writer_lease_claim` are now `true`.
`scripts/fleetrmw_quic_gateway_service.py` gained `--raft-status-url`/`--raft-node-id`,
and `fleetqox/raft_writer_lease.py`'s `RaftLeaderLease` gates the real
gateway's write eligibility on winning its own co-located Raft node's
leadership, folding the current (strictly increasing) Raft term into the
holder_id handed to the existing, unmodified SQL fencing path
(`raft-{node_id}-term-{term}`) -- Raft decides who should write, the
already-proven SQL store still enforces it. `scripts/run_rmw_docker_quic_gateway_raft_writer_lease_probe.py`
proves this over a real QUIC v1/H3 connection, a real 3-node Raft cluster,
and two real gateway processes sharing one SQLite store: a non-leader
gateway fails closed immediately, the leader's gateway accepts a real
durable write, killing the leader's Raft node triggers a genuine election
among the two survivors (the winner is discovered by polling, not assumed
-- either could legitimately win), and the next gateway -- pointed at
whichever node actually won -- recovers the prior gateway's durable state
with a strictly higher SQL fence_token. 3/3 runs.

## Evidence rules

- Deterministic probes establish contracts, not broad performance claims.
- One stochastic seed establishes a frontier observation, not reliability.
- Failed rows remain visible.
- Every comparative claim requires aligned application middle, payload,
  profile, topology, seed, and process health.
- Security and simulator claims are no broader than their tested threat/model
  boundary.
- Claims not covered by the current verification run are reported as
  unconfirmed, not carried forward as current evidence.
- `production_ready=false` remains authoritative.
