# FleetRMW Status and Roadmap

## Status contract

The normative machine-readable status is
`ros2_ws/src/rmw_fleetqox_cpp/capabilities.json`. Human summaries must never
override it. In particular, `production_ready=false` remains authoritative.

Current checkpoint:

- 510 supported capabilities;
- 22 partially implemented capability groups;
- 14 explicitly unsupported items;
- 611 true and 45 false scoped claim boundaries;
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

### B0: intermittent subscriber memory corruption

Two long lossy 32-KiB runs ended with
`free(): invalid next size (fast)` in the subscriber. Short repeated selective
repair runs and 320 callback-owner teardown cases pass, so the defect is not
yet localized. It may be in typed large-string take/deserialization or a later
teardown interaction.

A fresh Jazzy ASan/UBSan Docker build passed 5,000/5,000 same-process typed
32-KiB publish/take iterations with clean finalization. This reduces the
likelihood of a simple serializer-only failure but does not cover the original
lossy inter-process fragment/repair path.

A distinct use-after-free (not this one) is root-caused and fixed:
`rmw_destroy_publisher()` could be called with a `node` pointer that
`rmw_destroy_node()` had already freed, by upstream `rcl`'s global rosout
logging fini path at process shutdown. Fixed via pointer-identity tracking in
`node_is_valid()`; see `docs/EXPERIMENTAL_RESULTS_V1.md`. This does not close
B0 — the `free(): invalid next size (fast)` report above remains open and
unreproduced.

Exit gate:

- deterministic reproducer or a statistically meaningful stress reproducer;
- ASan and UBSan clean;
- root cause fixed, not suppressed;
- repeated Docker gate checked into the test matrix;
- no crash over the long fleet workload.

### B1: fleet-scale large-sample convergence

The best retained 16-robot, 32-KiB, roaming-loss seed-7 result is `155/160`.
The fair repair queue reduces amplification and deferrals but does not improve
the delivery frontier. One seed and an incomplete row cannot support a fleet
reliability claim.

Exit gate:

- complete delivery and ACK convergence for 8/16/32 robots;
- at least three fixed seeds per profile;
- bounded queue/state/CPU/RSS and no hidden unbounded retry;
- exact payload size and same-hop provenance;
- repeatable result from a clean Docker image.

### B2: production QUIC and PKI

Current QUIC evidence proves real paths and scoped failover behaviors. It does
not prove production certificate lifecycle or distributed gateway operations.

A defect in the online client-CRL refresh path is root-caused and fixed
(a GnuTLS credentials object reloaded in place across handshakes retained
stale revocation state despite reporting a successful reload). This closes
one specific sub-item of this blocker's exit gate
(online client-CRL refresh); server-certificate rotation, CA rotation, and
active-session revocation are untouched and remain false per
`capabilities.json`.

Exit gate:

- public maintained APIs only;
- online server/client certificate and CA rotation;
- active-session revocation and fail-closed expiry;
- forward secrecy and asymmetric session establishment;
- leader election/consensus, split-brain fencing, rejoin/failback, regional
  recovery, and operational runbooks;
- long multi-attacker soak.

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
