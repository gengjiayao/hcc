# Membership-coalescing V12 exact-prefix holdout

V12 is an armed, preregistered follow-up to the rejected V11 holdout. It
changes only high-fan-in transition activation: after the V11 transition
prepare generation is fully acknowledged, the receiver waits until every
frozen waiter has delivered its exact first-window prefix. V12 remains
default-disabled and outside final GUARD until the complete frozen campaign
passes. The frozen replay rejects V12 at the mechanism gate described below.

## Frozen mechanism

V12 retains V11's `K=4` serialized prepare/activate transactions, 12,480-ns
post-initial membership window, 16,640-ns sliding initial quiet window,
83,200-ns hard collection horizon, immediate low-mode release, geometric
post-transition release, ACK policy, and retry-inclusive frame accounting.
The candidate adds:

```text
--guard_transition_prefix_barrier 1
```

The control arm sets this option and the small-set limit to zero. All other
behavior-affecting settings remain paired and explicit. In particular, both
arms use PFC, disable IRN, remaining awareness, receiver concurrency,
proactive release, size priority, refresh, work-conserving reclaim, and
cap-aware reclaim, and use fixed sender windows. The candidate fixes:

```text
--guard_small_set_fastpath_limit 4
--guard_membership_coalesce_ns 12480
--guard_initial_collection_quiet_ns 16640
--guard_membership_coalesce_max_windows 5
--guard_initial_collection_full_deadline 0
--guard_grant_reliability_rtts 2.0
--guard_fixed_window 1
--guard_selective_registration 1
--guard_remaining_aware 0
--guard_receiver_concurrency 0
--guard_proactive_release 0
--guard_size_priority 0
--guard_grant_refresh_bdps 0
```

Every transition member must come from the same receiver NIC, receiver
capacity, and priority group. The frozen receiver-share workload sends every
flow to receiver 15 in priority group 4. For each frozen live waiter `f`, the
receiver records

```text
target[f] = min(flow_size[f], sender_exact_qp_window[f]).
```

The normal path becomes ready only when the receiver's contiguous next
expected sequence for every waiter is at least its target. It then sends one
required `transition_activate` generation to the entire live waiter cohort in
ascending `(register_ns, flow_id)` order. A nominal deadline can enter the
bounded fallback implemented by the simulator, but fallback is a liveness
mechanism, not an admitted performance path: any timeout, degraded
transition, fallback batch or closure, order violation, or prefix barrier
violation rejects V12.

## Frozen identities and stage chain

The candidate-only replay reuses the four V11 formal seed-126 inputs:

| N | Required flow SHA-256 |
|---:|---|
| 2 | `55cb59f42c008a37d0e24555fbdc33f1bec478e2816e2ba3f79eccdde9e971a2` |
| 4 | `87a41f2df3f9a2e8d70498c0db29764ddc7fb53a96f38ae13022d6c627f2949b` |
| 8 | `4d98144057c964d54299c5005883de6e13d01bb3491602b6a0b13d87e4b496f4` |
| 15 | `430550e35b0d5c53c07ac1da5a408a6b31c24b71e257b9cdffcfbc252c67437f` |

Replay performance remains sealed. Its N=15 candidate must use the normal
`ready` path; a timeout or fallback rejects the replay. The fresh pilot is
seed 131 at N in {4,15}, paired control and V12, for four mechanism-only runs.
Formal evaluation is fresh seeds 132--136 at N in {2,4,8,15}, paired control
and V12, for exactly 40 runs.

The only permitted stage order is:

```text
preflight -> replay -> seal replay -> pilot -> seal pilot
          -> formal 40/40 mechanism validation -> performance unseal
          -> aggregate all five paired seeds
```

The runner never opens FCT or queue artifacts. Replay and pilot analyzers open
only bounded mechanism artifacts. During formal analysis, performance remains
sealed until every one of the 40 identities passes its mechanism checks. No
failed identity, seed, N, or arm may be dropped.

## Mechanism admission

All candidate runs must complete exactly N flows with zero switch drop,
recovery, timeout recovery, PFC event, trace truncation, retry, stale grant,
duplicate or stale ACK, rejected generation, pending generation, and terminal
membership state. The V11 raw phase audit remains in force: required grants
close as `sent -> received -> ack_sent -> ack_received`; prepare ACK closure
precedes activation; transactions are contiguous and serialized; activation
covers every lifecycle flow once; the transition prepare and activation sets
are disjoint and cover the lifecycle union; optional release and geometric
high-mode release rules hold; raw phase and global wire counters reconcile.

At N in {2,4}, no prefix barrier may start or become ready, and every prefix,
fallback, and terminal counter is zero. The retry-inclusive frame caps remain
7 and 26. At N in {8,15}, exactly one prefix barrier starts and exactly one
becomes ready. Timeout, degraded, fallback, order, barrier, recovery, PFC,
drop, retry, stale, and remaining-byte counters are zero; ready waiters equal
required waiters; all prefix terminal fields are zero. Each authoritative
`transition_activate sent` row must have:

```text
prefix_observed_bytes >= prefix_target_bytes
drain_outcome = ready
activation_batch_index = 1
activation_batch_size = actual live waiter cohort size
```

All such rows use one nonzero required generation and one transaction target.
Their order matches lifecycle registration provenance. The retry-inclusive
frame caps remain 43 and 64 at N=8 and N=15.

## Performance gates

Only after 40/40 formal mechanisms pass does the aggregator read performance.
It uses five paired observations and a Student-t 95% confidence interval (CI).
For every N, the mean-FCT and completion-span percentage CI upper bounds must
be at most +1%, and the Jain-index difference CI lower bound must be at least
-0.005.

V12 retains V11's queue gates without relaxation. At N in {2,4,8}, the CI
upper bounds of candidate-minus-control queue mean and p99 must each be at
most 60 bytes. At N=15, the queue-mean percentage CI upper bound must be at
most -10% and queue-p99 at most -20%. Every N=15 candidate also has zero PFC,
at most 64 control frames, and candidate/control control-frame ratio at most
0.30.

V12 adds a stability conjunction. For each fresh N=15 seed 132--136
individually, candidate-minus-control queue mean must be at most -10% and
queue p99 at most -20%. Passing the aggregate CI cannot hide a failing seed.
Any failed mechanism or performance gate rejects V12; it does not authorize a
seed, parameter, threshold, or workload change.

## Bounded execution

All 48 simulator identities run serially. There are 26 unique traffic inputs
because paired arms share each pilot or formal input. Limits are 1,000 flows,
4,096 grant-trace rows, 32 lifecycle rows, 16 MiB per run, 1,800 seconds and
8 GiB RSS per process, and 1 GiB for the campaign. The minimum simulator
revision is `e5a57fa399c8ee4b5d3190d172771343baace632`.

```bash
SPEC=experiments/membership_coalescing_ladder_v12.json
CAMPAIGN=/absolute/path/to/v12-campaign

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase preflight
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase replay
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope replay \
  --json-out "$CAMPAIGN/known_replay_admission_v12.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-replay \
  --replay-admission "$CAMPAIGN/known_replay_admission_v12.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase pilot
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope pilot \
  --json-out "$CAMPAIGN/pilot_admission_v12.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-pilot \
  --pilot-admission "$CAMPAIGN/pilot_admission_v12.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase run
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope holdout \
  --json-out "$CAMPAIGN/holdout_analysis_v12.json"
python3 experiments/aggregate_membership_coalescing_holdout.py "$SPEC" \
  "$CAMPAIGN/holdout_analysis_v12.json" \
  --output-dir "$CAMPAIGN/summary"
```

The formal analyzer output is the unseal artifact: it exists only after all
40 mechanisms pass and includes all 40 performance rows. A rejected replay,
pilot, formal mechanism, aggregate CI, or per-seed stability gate ends this
preregistered V12 campaign.

## Replay rejects V12 before performance evaluation

Preflight froze 4 replay, 4 pilot, and 40 formal identities at simulator
revision `55db7a7de8a5173367272d6297d519992fdc0a9d`. All four candidate-only
replay simulations completed. The N=2 and N=4 runs satisfy the low-fan-in
mechanism conditions: neither run starts the prefix barrier, both finish every
flow, and their prefix and fallback counters remain zero.

N=8 is the first blocking identity. Output `231211353` starts one prefix
barrier but records zero ready events, one timeout, and one degraded
transition. After waiting 45,280 ns, at the absolute deadline of
2,002,076,478 ns, the receiver had observed 723,000 of 728,000 target bytes,
leaving 5,000 bytes. The liveness path then activates the seven waiters in
fallback batches of four and three. All eight flows complete and the terminal
fields return to zero, but the frozen admission rule rejects every timeout,
degraded transition, and fallback batch.

N=15 independently reaches the same failure mode. Output `574141313` starts
one barrier and records zero ready events, one timeout, and one degraded
transition. After waiting 61,440 ns, at the absolute deadline of
2,002,112,081 ns, the receiver had observed 1,129,000 of 1,144,000 target
bytes, leaving 15,000 bytes. The liveness path uses four fallback batches,
closes all four, and limits each batch to at most four waiters. All 15 flows
complete and the terminal fields return to zero. This run corroborates the
N=8 blocker; the campaign excludes N=15 from passing evidence.

These counters establish the `rejected_mechanism` campaign disposition. The
campaign stops before replay sealing, pilot execution, and formal execution.
We opened no FCT or queue artifact during this mechanism audit, so V12
produces no performance result. The frozen mechanism, identities, thresholds,
and gates remain unchanged.

### Provenance

| Artifact | SHA-256 |
|---|---|
| Frozen specification | `826df66a45113fc91f3582b6dc0b6469881c4afa676585c69fa68ffcab1369e0` |
| Preflight manifest | `4be894a4da7deac011d221fb4e46520ef81c27b101f25ddf6212b6432575ee26` |
| Campaign manifest | `e72bb0a028cbc16209cb490243b6a59d10f88d7e0187364692871ff5d481c553` |

The replay mechanism artifacts map each reported counter to the simulator
output below. The table omits FCT and queue files because the mechanism
failure keeps performance sealed.

| N | Output | Guard stats SHA-256 | Grant trace SHA-256 | Lifecycle SHA-256 |
|---:|---:|---|---|---|
| 2 | `439699309` | `d3f39d0c28f28eb9a736c8873ead4017e136fe139650a454e93efa74086adb6a` | `c3ae646265edbbae8f2f6d1503b6e20a8e773a51253033619dd7a9b838ea85d0` | `357cca0bdef2edca89f17d15c621540bc6fbf8e07473e36c5efbea1f2c01d979` |
| 4 | `65006538` | `4ea2173149b07802c3a3e33c6b14a8915dc177b11ed3fe72edaf3e3d4f2041b6` | `68ab380ad9ff037b114cb67ed56233f7dfcef071832caaec25e7971819149b55` | `eb4245fb98ce267ca6df4def156ad0f620d495183bf7efa7bb680d041415e465` |
| 8 | `231211353` | `f530b3c8b7039114473623ad4ba62e5ae2266571010453d694fd9f8330e4aad2` | `2f3d28c301dca5b2cf2dc50ef61cdc2a3fd95d546e2384b8481f4bdd46d3c948` | `60fc98b6f92062e98296fda3ee0ff9bef9a8230300c4a4da7f28518efdffebaf` |
| 15 | `574141313` | `f2ca52d4f71c45bf7421ad0e32ea0b791e01634d8c7b4284c89ce1d702adee98` | `4caa4529a6f8091d2a81c827fa337ebf356a013944b6c5f5f5bbece683ee98a1` | `0b6296b0054f2841f0f67513872193d73b1a01211e2ae5ef6f56d2935b574609` |

The campaign directory occupies 160,753 bytes. The four simulator output
directories occupy 122,758 bytes in total, for 283,511 bytes across both
locations. The largest replay output occupies 37,168 bytes, below the 16-MiB
per-run cap.
