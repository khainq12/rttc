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
| FleetRMW-via-router vs. best DDS baseline, 8-robot contention | 1.9-2.5x lower control-plane p95 at wifi/wan; 1.5-1.6x higher (slower) at roaming | Profile-dependent, not a universal-superiority claim; see "Baseline comparison" |

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

### Fleet frontier: a partial-by-design result

`run_rmw_docker_fleet_repair_capacity_frontier.py` sweeps robot count,
deadline, and repair-payload size specifically to find where actuated repair
under a shared bandwidth budget stops keeping up. The sweep (27
configurations, 9 robot/deadline groups): 18/27 admitted under the shared
budget, 11/27 met the full live-QoE repair contract.

The correct current claims remain:

```text
fleet_scale_selective_fragment_repair_claim=false
production_large_sample_reliability_claim=false
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
30,000-byte state per robot sharing one constrained link), the picture is
profile-dependent rather than a uniform FleetRMW win. At `wifi` and `wan`,
FleetRMW routed through its own UDP router clearly beats both 100%-delivery
DDS baselines even with no scheduling at all (control-plane p95: 86 ms vs.
164 ms at wifi, 107 ms vs. 270 ms at wan — roughly 1.9x and 2.5x faster).
At `roaming`, the same router-based FleetRMW path is *slower* than both DDS
baselines: 722-743 ms vs. 476-479 ms, about 55% higher p95, reproduced across
3 independent runs at each end of the comparison. The deadline-aware
holdback scheduler makes no measurable difference at any profile in this
run (within 1% of unscheduled at wifi/wan, and if anything slightly slower,
not faster, at roaming across all 3 reps) — a genuine reversal from a
previously published `roaming` result that no longer reproduces.

That previous number was measured against a build where FleetRMW's UDP
transport had two root-caused defects (an async-ICMP receive-thread death
and an `EMSGSIZE` crash on large publishes over the router, both fixed in
this run) that silently prevented the 30,000-byte state channel from ever
actually being delivered under contention. With state delivery at 0%, the
prior "roaming" measurement never actually put real 30 KB-per-robot traffic
on the link — it was, in effect, measuring control-only traffic on an
otherwise-idle path. Now that state delivery genuinely works, `roaming`'s
much lower link rate (5 Mbit/s vs. wifi's 20 Mbit/s) becomes the dominant
bottleneck, and routing all 8 robots' state traffic through one central
relay pays a real bandwidth cost the multicast/peer-to-peer DDS baselines
do not. The wifi/wan win is real and reproducible; the roaming win was not.

This supports a narrower scoped claim than before: FleetRMW's router-mediated
transport measurably reduces control-plane tail latency under multi-flow
contention when the link is not itself the bottleneck (wifi/wan here). It
does not support:

- universal cross-RMW superiority (FleetRMW's own unscheduled transport is
  slower than DDS on an uncontended link, by the single-flow comparison
  above, and slower than DDS under contention on a bandwidth-constrained
  link, by the roaming comparison above);
- any deadline-scheduler latency benefit — none was measurable in this run
  at any profile, contradicting the previously published `roaming` claim;
- latency superiority as a blanket claim independent of contention level or
  link bandwidth;
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
