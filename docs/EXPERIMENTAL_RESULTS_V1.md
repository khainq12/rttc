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

The publish hot path's frame encoding now reuses a persistent per-publisher
buffer for the payload's base64 text across repeated publishes instead of
allocating a fresh string every call; `docker_deep_preallocation_probe`
proves the buffer's capacity grows once on the first of eight same-size
publishes and stays exactly stable for the rest, 5/5, rebuilt clean under
ASan/UBSan. This is a genuine but bounded reduction, not a closure of "deep
preallocation": the JSON frame body is still built via a fresh
`std::ostringstream` per publish (its floating-point formatting was left
untouched to avoid risking the on-wire number format ~187 other probes
depend on), and the reliability retransmit ledger still allocates per
in-flight reliable message by design.

The loaned-message buffer is now pooled per publisher/subscription instead
of allocating a fresh block on every borrow; `fini_function` still runs on
release (freeing any `std::string`/vector-owned sub-allocations) but the raw
block returns to a capped 8-buffer pool instead of being deallocated.
`docker_deep_preallocation_loaned_message_probe` proves exactly one fresh
allocation across a subscription's whole lifetime of repeated
take-loaned-message/return cycles, 5/5, rebuilt clean under ASan/UBSan, with
no regression in the existing `loaned_message_probe`. This does not close
"zero-copy": the JSON+base64 wire format and variable-length introspection
fields mean the loaned buffer can never alias the received network bytes,
so deserialization still runs on every take.

Remote liveliness-incompatible QoS event production is now proven over a
real two-process/UDP wire. The detection code
(`incompatible_qos_policy_kind()`) was already shared verbatim between local
match-checking and remote-endpoint discovery, but no existing test exercised
it for LIVELINESS specifically across a real wire boundary: the local
probe (`qos_liveliness_incompatible_event_probe.cpp`) creates both endpoints
in one process, and the existing remote-graph probe
(`remote_event_probe.cpp`) covers reliability/durability/deadline but not
liveliness. A new dedicated two-container probe
(`remote_liveliness_incompatible_event_probe.cpp` +
`run_rmw_docker_remote_liveliness_incompatible_event_probe.py`) proves both
liveliness-incompatibility causes -- AUTOMATIC-vs-MANUAL_BY_TOPIC kind
mismatch and slow-vs-fast lease-duration mismatch -- in both directions (a
local publisher against a remote-learned subscription, and a local
subscription against a remote-learned publisher), 5/5 across real Docker
containers with `netem delay 5ms 1ms`, rebuilt clean under ASan/UBSan. Full
DDS message-lost/resource-limit semantics and the full QoS/type
compatibility matrix remain architecturally bounded, not pending effort:
upstream `rmw_qos_profile_t` has no resource_limits fields and upstream
`rmw_qos_policy_kind_t` has no enum values for OWNERSHIP/PRESENTATION/
PARTITION/DESTINATION_ORDER, so neither can close without forking `rmw`
itself.

Remote type-incompatible-event detection now uses each side's RIHS
structural type hash (`rosidl_type_hash_t`, via `get_type_hash_func`) as
the authoritative check when both a local and a remote-learned endpoint
carry a valid one, instead of only the `type_name` string. The hash was
already computed locally for `rmw_get_publishers/subscriptions_info_by_
topic` but never sent to peers; `GraphAdvertisement` now carries an
optional hex-encoded hash, with a fallback to the prior `type_name`
comparison whenever either side lacks a valid one (every hand-built probe
type support elsewhere in this suite has no `get_type_hash_func`, so this
fallback is what keeps that existing coverage passing unmodified). A
dedicated two-container probe
(`remote_type_hash_incompatible_event_probe.cpp` +
`run_rmw_docker_remote_type_hash_incompatible_event_probe.py`) proves a
same-type-name/different-hash pair is detected as incompatible in both
directions (the case plain string equality would miss), a same-hash pair
stays compatible, and a no-valid-hash pair falls back to compatible
type_name matching, 5/5 across real Docker containers with
`netem delay 5ms 1ms`, rebuilt clean under ASan/UBSan. This closes the
remote structural type-hash gap for event production only; actual
message routing/matching remains `type_name`-based, a separate, larger
change not attempted here.

MANUAL_BY_NODE liveliness (upstream value 2, deprecated in favor of
MANUAL_BY_TOPIC) is now supported instead of fail-closed. It was previously
rejected at publisher/subscription creation alongside the genuinely invalid
UNKNOWN policy, but it is a deprecated-but-well-defined DDS kind (DDS's
MANUAL_BY_PARTICIPANT) still accepted by rclcpp/rmw, so fail-closed was
stricter than required. Its distinguishing behavior -- one assertion shared
by every MANUAL_BY_NODE publisher on the same node -- is now implemented
locally (fan-out to every sibling publisher on the same `owner_node`) and
remotely (grouped by `domain_id` + `node_name` + `node_namespace`, already
carried in every graph advertisement). A local 5/5 artifact proves a silent
sibling publisher is kept alive by another publisher's asserts on the same
node, that the shared lease still expires once the whole node goes idle,
and that sharing does not leak across a node boundary. A two-container
UDP/netem 5/5 artifact proves the same sharing and expiry over the real
wire.

A leftover bug from that same MANUAL_BY_NODE work is now fixed:
`liveliness_qos_incompatible()` implements DDS's liveliness-kind
compatibility as a strictness ordering (AUTOMATIC(1) < MANUAL_BY_NODE(2) <
MANUAL_BY_TOPIC(3), incompatible when offered rank < requested rank), but
had only ever special-cased the single AUTOMATIC-vs-MANUAL_BY_TOPIC pair --
written before MANUAL_BY_NODE was a creatable kind. Once MANUAL_BY_NODE
became creatable this session, two genuinely incompatible pairs
(AUTOMATIC-vs-MANUAL_BY_NODE, MANUAL_BY_NODE-vs-MANUAL_BY_TOPIC) were
silently treated as compatible. Since this function is shared by local
matching and both remote directions, the fix is proven by 4 new local
scenarios and 2 new remote (two-container UDP/netem) scenarios, all 5/5,
rebuilt clean under ASan/UBSan.

Also fixed: a silent message-lost blind spot, unrelated to the upstream
`resource_limits` gap that keeps `full_message_lost_event_production_claim`
`false`. `frame_exceeds_lifespan()` was checked before `observe_frame()`
recorded a frame's sequence number, so a LIFESPAN-expired frame's sequence
never advanced tracking state -- if no later frame ever arrived on the same
stream to reveal the gap, the loss was 100% invisible, with
`message_lost_total_count` never incrementing and no callback ever firing.
Fixed by observing the sequence first and explicitly recording the loss;
the analogous take-path drop (expired while queued, not at arrival) got the
same fix. A new two-container UDP/netem artifact publishes exactly one
frame and nothing else, so it can only pass if this exact tail-loss case is
now visible -- 5/5 real runs, rebuilt clean under ASan/UBSan, and verified
to genuinely fail against the pre-fix code before the fix was restored.

BEST_AVAILABLE liveliness resolution is now also proven against a real
remote endpoint (previously only tested locally, same-process). The
resolution mechanism (`rmw_dds_common::qos_profile_get_best_available_for_
topic_publisher/subscription`, sourced from `rmw_get_publishers/
subscriptions_info_by_topic`, which already merges remote endpoints) turned
out to be correct: a new two-container UDP/netem artifact proves a
BEST_AVAILABLE publisher resolves to a remote MANUAL_BY_TOPIC
subscription's kind/lease and a BEST_AVAILABLE subscription resolves to a
remote AUTOMATIC publisher's kind/lease, 5/5, ASan/UBSan clean. What this
work did surface is a real timing consideration: resolution happens once,
synchronously, at creation time, so the remote endpoint must already be
discovered first or resolution silently defaults as if it didn't exist. A
1000ms discovery margin measured flaky; 3000ms did not (confirmed directly
against the same probe binary).

OWNERSHIP, PARTITION, DESTINATION_ORDER, and PRESENTATION -- the four DDS
QoS policies named above as architecturally unreachable through
`rmw_qos_profile_t`/`rmw_qos_policy_kind_t` -- are now closed as FleetQoX-
specific extensions, not through the standard path (which stays permanently
bounded, so `full_non_deadline_qos_event_production_claim` correctly stays
`false`). `rmw` already provides `rmw_specific_publisher_payload`/
`rmw_specific_subscription_payload` (both upstream `void *`) for exactly
this situation; rclcpp exposes it via `rmw_implementation_payload`
(`qos_extensions.hpp`, `rclcpp_qos_extensions.hpp`). OWNERSHIP and PARTITION
arbitrate/match at TOPIC granularity (no DDS-keyed-instance concept exists
in `rmw`); a first version of OWNERSHIP's strength-based arbitration had no
way to detect a departed exclusive owner and would suppress every other
publisher forever, fixed with a last-seen timeout; a first version of
PARTITION correctly wired matching into every compatibility/event predicate
but missed the actual `DataFrame`-level delivery gate, caught by its own
probe's mismatch case. DESTINATION_ORDER sorts the local frame queue by
`source_timestamp_ns`, proven with genuine asymmetric `netem` delay across
three containers (a "slow" publisher's head start is overtaken by a "fast"
one, constructing a real out-of-arrival-order scenario). PRESENTATION at
GROUP scope required a genuinely new control surface --
`rmw_fleetqox_cpp_begin/end_coherent_changes` (`presentation_group.hpp`),
callable only via direct linkage against `rmw_fleetqox_cpp` since neither
rclcpp nor standard `rmw` has a coherent-changes concept -- because GROUP
scope spans multiple topics under one Publisher entity that `rmw` has no
equivalent for; publishes between begin/end buffer instead of sending, and
end flushes the whole cross-topic batch as one atomic burst, delivered
through a single `g_bus_mutex` critical section so no `rmw_take` can ever
observe one topic's new value without every other member already queued.
OWNERSHIP, PARTITION, and DESTINATION_ORDER each pass 5/5 real Docker/netem
runs through the real rclcpp `rmw_implementation_payload` path; PRESENTATION
passes ASan/UBSan-clean over this RMW's own same-process loopback transport
(the property under test is RMW-instance-local, not distributed, so a
second container proves nothing extra), all four of its claims holding:
immediate delivery outside any span, zero visibility through a real 500ms
hold, atomic joint delivery of both topics right after flush, and
ordered_access preserving publish order across an interleaved cross-topic
publish.

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

### Fleet frontier addendum: delivery ratio alone was not proof of ACK convergence

The 9/9 100%-delivery result above was re-audited against the RMW's own
`publisher.ack_wait_complete` / `relay.downstream_ack_wait_complete` flags,
not just `min_topic_delivery_ratio`, and several of those same runs failed
that stricter check despite 100% delivery. Root cause: `rmw_publisher_
wait_for_all_acked` could prune a still-alive subscriber from a publisher's
pending-ack set on a single graph snapshot where it wasn't reported as
currently matched, with no grace period -- one missed graph-advertisement
renewal was enough to falsely converge the wait and let the harness's
publisher process exit while the relay was still mid-repair (one run's
publisher reported zero matched subscriptions across all 32 topics within
under a millisecond of publishing). Fixed with a grace period requiring
continuous absence longer than one graph-advertisement lease window before
pruning (`FLEETQOX_RMW_WAIT_FOR_ALL_ACKED_INACTIVE_SUBSCRIBER_GRACE_MS`),
plus two smaller scale-dependent fixes surfaced once that dominant cause was
gone: fragment-NACK retry backoff now grows past its old 8x-interval cap so
a long-struggling assembly's retries stop competing with themselves for
bandwidth (`FLEETQOX_RMW_FRAGMENT_NACK_BACKOFF_MAX_SHIFT`), and the generic
serialized relay's executor switched from single- to multi-threaded so it
no longer falls behind message-callback delivery across many concurrent
routes at fleet scale (64 routes at 32 robots).

Re-run against the complete criterion (`publisher.ack_wait_complete AND
relay.downstream_ack_wait_complete AND min_topic_delivery_ratio == 1.0`):
**9/9 (100%)** across 8/16/32 robots x seeds 7/13/29, every run with a
genuine multi-second (not sub-millisecond) publisher ACK convergence and
zero stuck fragment assemblies at teardown.

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
- action frame/QoS paths and large status/service payload fragmentation;
- content-filter comparison predicates in DDS-SQL's standard reversed operand
  order (a parameter or quoted literal on the left of `=`/`!=`/`<>`/`<`/
  `<=`/`>`/`>=`, e.g. `%0 = robot_id` or `%1 < sequence`, not just
  field-first), rebuilt clean under ASan/UBSan and passing 5/5 in
  `docker_content_filter_sql_probe` alongside a malformed-reversed-form
  (`%0 = %1`) negative control;
- content-filter `LIKE ... ESCAPE` (e.g. `value LIKE %0 ESCAPE %1`), letting
  a pattern's own `%`/`_` be matched literally via a caller-chosen
  single-character escape; a malformed multi-character escape value fails
  closed at `set_content_filter` time. Rebuilt clean and passing 3/3 in
  `run_rmw_docker_content_filter_sql_probe.py`
  (`escape_evaluated=3`/`escape_matched=1`/`escape_dropped=2`,
  `multi_char_escape_rejected=true`). This is the final content-filter
  dialect scope decision: arbitrary DDS SQL functions and vendor-specific
  extensions are a permanent non-goal, not a pending gap.

Open boundaries include full remote graph/event production, full non-deadline
QoS event semantics, full DDS content-filter dialect (arbitrary DDS SQL
functions, vendor-specific semantics, and field-to-field comparisons where
neither side is a parameter or literal), full DDS writer-history
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

`quic_gateway_regional_disaster_recovery_claim` is also now `true`, scoped
to its common real-world meaning: losing any ONE of several independent
regions is survived automatically. `scripts/run_rmw_docker_regional_disaster_recovery_probe.py`
assigns each etcd member and the PostgreSQL primary/standby to one of
three named regions and disconnects an entire region's containers
simultaneously (not one node at a time), over real separate Docker
containers, proving: losing a minority region causes zero disruption and
rejoins quorum cleanly once reconnected; losing the region holding the
PRIMARY triggers a genuine etcd-quorum-gated failover to the surviving
region's standby (the isolated primary is still fenced via the Docker
socket -- legitimate out-of-band fencing, since real regional STONITH also
uses an out-of-band management path); and losing a second region
afterward correctly blocks any further promotion (fail-closed). 3/3 runs.
`regional_witness_free_majority_region_recovery_claim` stays `false`:
automatic recovery from losing a majority-holding region without a
witness in a fourth location is mathematically impossible for any quorum
system, not what this claim means, and not closeable by any probe.

`hardware_stonith_redfish_protocol_claim` is also now `true`, scoped
precisely: no physical server or BMC exists in this environment, but the
protocol real hardware fencing depends on (DMTF Redfish) is standard and
testable without one, the same way OpenStack Ironic/Metal3 test bare-metal
power management in CI. `scripts/fleetqox_redfish_bmc_simulator.py` is a
minimal but protocol-conformant fake BMC; `scripts/fleetqox_hardware_stonith_agent.py`
is a real Redfish HTTPS client wired into this codebase's existing
fence-agent pattern (same DCS-lease authorization and mTLS client-identity
binding as `fleetqox_postgres_fence_agent.py`, a genuine
`POST .../Actions/ComputerSystem.Reset` in place of a Docker-socket
SIGKILL). `scripts/run_rmw_docker_hardware_stonith_redfish_probe.py`
proves an unauthenticated fence request is rejected at the TLS layer, a
forged DCS lease is rejected (403), and a request authorized by a real
etcd lease produces a genuine Redfish reset that a *separate, independent*
BMC query confirms actually flipped power state from On to Off. 3/3 runs.
`quic_gateway_hardware_stonith_claim` and the new
`hardware_stonith_real_bmc_firmware_validated_claim` stay `false`:
validation against a specific vendor's real BMC firmware is an environment
limitation (no hardware available here), not an unimplemented feature.

Every split-brain/failover result above ran as containers on one Docker
daemon on one machine, which cannot exclude a shared-kernel confound.
`scripts/run_multihost_kvm_raft_consensus_probe.py` closes this for the
native Raft path over two real KVM VMs (separate kernels, separate Docker
daemons, connected only by a real virtio-net link): a 5-node cluster split
2 nodes on VM1 / 3 on VM2, VM1 tuned to win the initial election
deterministically, then VM1's whole QEMU process is killed from the host
side (outside either guest's OS). Across 5/5 runs with a fresh VM1 boot
and a genuinely re-randomized election each time: the pre-kill write
survives intact on VM2 alone; VM2's 3 survivors elect a new leader at a
strictly higher term purely by noticing VM1's absence over the network; a
post-failover write commits and replicates; and killing one more of VM2's
three nodes (2 of the original 5, a minority) correctly fails closed --
no new leader, no accepted write
(`multi_host_kvm_raft_consensus_claim`, `multi_host_no_split_brain_claim`).

The same multi-host closure now also covers the project's other HA path:
etcd DCS + PostgreSQL streaming replication + Docker-socket STONITH.
`docker_connection()` in `fleetqox_postgres_fence_agent.py` now dispatches
to either a Unix socket (unchanged default) or a `tcp://host:port` Docker
Engine API URL, so the fence agent can reach into a DIFFERENT host's
Docker daemon. Two real networking gotchas surfaced getting this right:
Docker-published ports are reached via DNAT into FORWARD, invisible to
plain INPUT/OUTPUT rules; and two etcd members on the same VM talking via
each other's *published* host port get hairpin-NAT'd to the bridge
address, failing etcd's peer TLS check (invisible until an election is
actually needed). Fixed with `--network host` for every fenceable service
plus an explicit port-2375 ACCEPT ahead of the partition's blanket DROP.

Topology: VM1 runs etcd1 + PostgreSQL primary; VM2 runs etcd2+etcd3 (a
real majority) + standby + controller + fence agent. The probe partitions
VM1 from VM2 at the network layer (VM1 stays up, its primary keeps
running -- the actual hazard STONITH exists for) and proves: 3-member etcd
quorum split across two hosts; cross-host streaming replication; the
controller on VM2 detecting the primary as unreachable, acquiring the DCS
lease, and fencing it with a genuine cross-host Docker kill reaching a
still-running host, independently confirmed by directly inspecting VM1's
own Docker state; then promoting the standby, which accepts a new write.
5/5 runs (`multi_host_postgres_etcd_fencing_claim`,
`multi_host_cross_host_stonith_claim`).

Both mechanisms also now prove **failback**, not just failover. Raft:
after the minority test, VM1 is relaunched (a real QEMU boot) and its two
nodes rejoin with completely empty state; all 5 nodes converge on the
same committed values via ordinary log replication and the cluster again
recognizes one leader
(`multi_host_failback_full_redundancy_restored_claim`). etcd/PostgreSQL:
etcd1 self-heals once the partition lifts (it was only partitioned, never
killed); the dead primary's redundancy is restored by bootstrapping a
genuinely fresh standby on VM1 replicating from the current primary
(VM2's promoted former standby), inheriting its replicator role/pg_hba
entry from that primary's own basebackup lineage and reaching
synchronous streaming with only a new slot and `synchronous_standby_names`
entry of its own
(`multi_host_failback_redundancy_restored_claim`). Both 3/3, on top of
the failover-only 5/5 above. Scoped precisely: this restores 2-host
redundancy with the failover's roles left in place, not a policy-driven
switchover back to the original primary specifically.

Between the two, every HA mechanism this project has now carries genuine
multi-host failover *and* failback evidence. Hardware STONITH already
targets an out-of-band BMC path (no multi-host aspect to close).

PKI operational hardening: CA rollover (both directions, including a
genuine restore-and-reconfirm, not just rotate-once), CRL revocation and
fail-closed refresh, and server/client-CA rotation were already proven;
what no probe had generated was a certificate identical in CA, subject,
and required URI SAN to the passing case, differing only in its temporal
validity window. `docker_ngtcp2_public_certificate_temporal_validity_probe.py`
closes that with `cryptography`'s `CertificateBuilder` setting an explicit
expired and an explicit not-yet-valid window -- the same
`now >= notBefore && now <= notAfter` check inside
`gnutls_certificate_verify_peers3` any real clock-skew scenario ultimately
exercises, proven this way specifically to avoid skewing the shared
machine's actual wall clock. 5/5 runs
(`certificate_expired_rejected_claim`,
`certificate_not_yet_valid_rejected_claim`). Crash-consistency under a
hard kill is the same evidence the Raft and etcd/PostgreSQL multi-host
probes above already provide, not a separate gap.

PKI cert/CA rotation, re-verified multi-host:
`run_multihost_kvm_udp_peer_auth_crl_reload_probe.py` re-runs both the
CRL-revocation claim and CA rotation itself across two real KVM VMs
instead of same-host Docker containers. A long-lived verifier on one VM:
accepts a CA-A-signed sender; has that sender's certificate revoked via a
live CRL rewrite on its own host and rejects it without restarting; then
has its trusted CA itself swapped from CA-A to an independently-generated
CA-B (fresh CA file plus a fresh CRL issued under CA-B, same on-disk
paths); accepts a CA-B-signed sender with the identical identity name;
and rejects the original CA-A-signed sender outright -- a genuine
rotation, not an additive trust grant, with the verifier process never
restarted. 4/4 rounds pass.

This surfaced and fixed two real defects, not just re-confirmed behavior:
the reload path's use of OpenSSL's `X509_STORE_load_locations` produced
`X509_V_ERR_CERT_SIGNATURE_FAILURE` against a validly CA-B-signed
certificate after rotation, because `ros2 security create_keystore`
always names its CA `sros2CA` -- two independently-generated CAs sharing
that exact Subject Name is the normal case here, and something in that
API's lookup path did not handle it; switched to direct PEM parsing plus
`X509_STORE_add_cert`. A new `FLEETQOX_RMW_DEBUG_PEER_AUTH_VERIFY`
diagnostic (peer certificate serial, live trust-store certificate count,
OpenSSL verify-error string) then made the actual remaining blocker
visible: reusing one UDP source port across rounds made this RMW's own
sequence-level duplicate suppression silently drop a later round's
genuinely-valid, newly-CA-authenticated samples as a continuation of an
earlier round's already-complete stream, with every peer-auth counter
reporting clean -- unrelated to CRL/CA logic once traced. A fresh source
port per round was the actual fix.

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
