# Membership-coalescing V11 safe-activation holdout

V11 is a preregistered, default-disabled test of a serialized two-stage join
protocol. V11 remains outside the admitted final GUARD configuration. This
document freezes identities, mechanism evidence, resource limits, and
performance gates, then records the completed holdout. V11 passes every
mechanism check but misses one preregistered performance gate. We therefore
reject V11.

## Frozen gate rejects V11

The staged chain admitted all four replay runs and all four pilot runs without
opening their performance files. The formal analyzer then admitted the full
40-run matrix: 20 control runs and 20 V11 runs. All 40/40 runs passed their
applicable mechanism checks. In the V11 runs, every phase, barrier, lifecycle,
frame-accounting, terminal, drop, recovery, timeout, and priority flow control
(PFC) check passed. This mechanism result unsealed the five-seed paired
performance analysis.

All effects subtract the paired uncoalesced control from V11. Negative latency
and queue effects favor V11. The performance analysis passes 22 of 23
preregistered gates. The only failure is the receiver-queue mean at 15 active
flows (N=15). Its point estimate is -15.007%, but its Student-t 95% confidence
interval (CI) is [-24.163%, -5.851%]. The preregistered gate requires a CI upper
bound no greater than -10%. The point estimate reaches the target, while its
five-seed CI crosses the frozen threshold.

At N=15, the receiver-queue p99 point estimate is -26.264% with a 95% interval
of [-32.388%, -20.141%], passing its -20% upper-bound gate. The mean flow
completion time (FCT) point estimate is +0.0231% with a 95% interval of
[0.0204%, 0.0258%], passing the +1% upper-bound gate. The completion-span point
estimate is +0.0114% with a 0.0213% upper bound, and the Jain-index difference
has a -0.000000188 lower bound. Each candidate seed sends 38 wire control frames
versus 225 in the control arm, a ratio of 0.168889 and below both the 64-frame
and 0.30 gates. Candidate PFC pause and resume events are zero in all five
seeds; the control arm records 571 to 622 pause events per seed.

The remaining N-specific results pass their frozen gates:

| N | Mean FCT point / CI upper | Span point / CI upper | Queue-mean point / CI upper | Queue-p99 point / CI upper | Jain CI lower |
|---:|---:|---:|---:|---:|---:|
| 2 | -0.937% / +0.224% | -0.211% / -0.084% | -0.264 bytes / +0.943 bytes | 0 bytes / 0 bytes | -0.000581 |
| 4 | -0.254% / +0.217% | -0.254% / -0.157% | -0.797 bytes / +2.481 bytes | 0 bytes / 0 bytes | -0.0000632 |
| 8 | -0.145% / +0.057% | -0.259% / -0.166% | -0.193 bytes / +0.396 bytes | 0 bytes / 0 bytes | -0.0000454 |

The FCT and span columns report percentage effects. The N in {2,4,8} queue
columns report absolute-byte effects, and the Jain column reports index
differences. **Decision.** The frozen conjunction requires all 23 gates. The
22/23 result rejects admission and keeps V11 outside final GUARD. Any follow-on
mechanism requires a new preregistration and fresh pilot and formal identities.

### Audit identities and artifacts

The holdout uses frozen simulator commit
`389b2974b6e2712cdc0044b7fb5e7d066db3bfd9`. The specification SHA-256 is
`22b5a2e7d2465e8e6855b214360cc40fd206ca554aca835297317e8ae576367e`.
The analyzer source SHA-256 is
`5644b20224fe089aab730aa5212f52874b1fda030b794e1c18b72c459d2d08b8`.
The formal analyzer output, `formal_admission_v11.json`, has SHA-256
`c4c3a9a010bec9ad3d2540fa6d31a5797c72e88fd83d46cbd7fd5505313358e2`.

The replay admission has SHA-256
`b0d48e831a6a3d5a05d839a151c05f111a2183e7a2fad010b834d69fcd0ead6a`;
the pilot admission has SHA-256
`f2687580540e6f9656764c0d3c966ba612262fd9ded5d2e9e7a89e03e8809a70`.
The final summary artifacts are:

| Artifact | SHA-256 |
|---|---|
| `summary/admission.json` | `501d127b841ffc8d57e732b78ef0ba41f46e4c50ecb0a9d7a35464a4e9311979` |
| `summary/paired_t95.csv` | `7127617926a1cb32e52b7ead5b6c73ab4a04092f22f0349d20311272b8059b9b` |
| `summary/per_seed.csv` | `687c91278ea46332e15adb83e31ee51f6aebc4a3b5e73e685ae8daeb40726320` |

The campaign root is
`/tmp/guard-membership-v11.BE4jaA/campaign`. Replay, pilot, formal, and summary
admissions reside directly under that root or its `summary/` directory. The
campaign occupies 1,119,327 apparent bytes and 1.6 MiB on disk, below the
frozen 1 GiB limit. Raw simulator outputs remain outside the repository. This
document records hashes of the admission and summary artifacts.

## Frozen mechanism

V11 uses `K=4`. For `N<=K`, a join never enters the V10 generation-zero
initial path. A transaction first sends required prepare grants to every
incumbent whose equal-share upper bound must decrease. Only after all prepare
ACKs close may it send required activation grants to the gated waiters. The
first transaction may have an empty prepare set because it has no incumbent.
Activation ACK closure is the only event that admits a waiter. Membership
changes arriving during either phase remain queued for the next serialized
transaction. Low-mode completion release is optional, immediate, and has no
ACK.

The fifth registration selects high-fan-in mode irreversibly for that epoch.
It starts the independent V10 sliding initial collection with quiet window
`W=16640 ns`, twice the 8,320-ns OS4 cross-leaf base RTT, and hard deadline
`5W=83200 ns`. Post-initial membership keeps its separate 12,480-ns window.
Transition prepare cannot start before both the serialized low-N prefix closes
and the high collection flushes. Required transition prepare covers the
incumbents; required transition activation covers the frozen waiters. The two
sets must be disjoint and their union must equal every lifecycle flow. Only
post-transition high-mode release retains the geometric completion rule.

The candidate arm fixes:

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

Control fixes the small-set limit, both collection windows, and reliability
rounds to zero, while retaining the paired topology, workload, and other GUARD
isolation settings. The minimum clean simulator revision is
`946cbbaa418d71580f747e10ca164fc7e041538f`.

## Frozen identities and stage chain

Replay is candidate-only and reuses all four V10 seed-120 formal inputs:

| N | Required flow SHA-256 |
|---:|---|
| 2 | `57a6835cbd93248b6331ce4f018751ba8dbca389f8cfa292e250901cced306e9` |
| 4 | `fbbcd5ec93970aa02f8935eca9572b315bb979ec5208ec589c706051c7579ea0` |
| 8 | `75bcbf0353fd46d53d283363b5a6677081aa1f06f60f6b61fd45f5826eb3c86e` |
| 15 | `77cd632be5757954ca2c24b161d7ac7e699b30abbbd801ebaae6dfa7ff5fb882` |

The fresh mechanism-only pilot is seed 125 at N in {4,15}, paired control and
V11. Formal evaluation is seeds 126--130 at N in {2,4,8,15}, paired control
and V11, for exactly 40 cells. The only allowed chain is:

```text
preflight -> replay -> seal-replay -> pilot -> seal-pilot -> run
```

The replay analyzer must jointly admit all four candidate cells without
opening FCT or queue artifacts. Its single admission artifact is hash-bound to
the spec, simulator SHA, preflight, and all four run records. The pilot seal
similarly binds all four pilot runs and the replay seal. Formal performance is
unavailable until every one of the 40 formal mechanism cells passes. No seed,
N, arm, or failed cell can be dropped.

## Raw mechanism admission

Candidate grant traces must contain the V11 fields
`transaction_id,grant_phase,membership_target_n,subject_role`. Receiver
`sent` and accepted `ack_received` rows are authoritative for transaction and
target. Sender `received` and `ack_sent` rows carry the same phase and role but
zero transaction and target; the analyzer joins them by generation and flow.

Every required prepare or activation grant must close in timestamp order as
`sent -> received -> ack_sent -> ack_received`. Prepare's final ACK must not
follow activation's first send, one transaction must close before the next
starts, and transaction IDs must be contiguous. Each transaction prepares the
current incumbents and activates disjoint waiters to reach its advertised
target. Activation sets cover each lifecycle flow exactly once. A high-N run
has exactly one transition, one high collection flush, and a transition
prepare/activation union equal to the lifecycle set. Low-N runs have neither.
For high N, `prefix_close_ns` and `collection_flush_ns` are no later than
`transition_prepare_start_ns`; the initial flush obeys the frozen quiet/hard
timer.

The analyzer reconciles raw phase batches, grants, and ACKs with accepted
counters and retry-inclusive wire counters. Global grant and ACK totals must
match, both unattributed counters must be zero, and `wire_reconciled=1`.
Barrier violations, early unlocks, post-transition fast grants, retries,
stale or rejected generations, pending work, trace truncation, drops,
recovery, timeout, and candidate PFC events must be zero. Terminal phase,
ACK, membership, waiter, collection, transition, and revision fields must all
be zero. Every flow must have a final target-N C/N grant within 1 Mb/s.

Low-mode release is implemented immediately for every completion; it is not
geometric. Therefore V11 deliberately corrects the low-N event-pattern cap
used in the earlier simulator design note. Safe serialized admission costs at
most `N(N+1)` frames and immediate optional release at most `N(N-1)/2` grant
frames, so

```text
F <= (3N^2 + N) / 2: N=2 -> 7, N=4 -> 26.
```

The high-transition event-pattern caps remain 43 at N=8 and 64 at N=15.
Thus the frozen per-run caps are 7, 26, 43, and 64. They include retries and
apply only to these synchronized, one-epoch, completion-only inputs. They are
not a universal `<3N` or asymptotic claim.

## Formal performance gates

Once all 40 mechanisms pass, the aggregator uses five paired seed-level
observations and a Student-t 95% interval. Mean FCT and completion-span
percentage CI upper bounds must be at most +1% for every N. Jain-index
difference CI lower bounds must be at least -0.005.

V10 exposed a near-zero-queue denominator problem at small N. V11 makes the
first preregistered measurement correction: at N in {2,4,8}, the CI upper
bounds of candidate-minus-control queue mean and p99 must each be at most 60
bytes. Queue percentages remain in per-seed/interval output, but are not
admission gates; a positive candidate queue over a zero control queue is
reported as non-estimable rather than hidden behind an epsilon. This change
does not retroactively rejudge V10.

At N=15, the queue-mean percentage CI upper bound must be at most -10%, and
queue-p99 at most -20%. Every N=15 candidate cell must have zero PFC, at most
64 retry-inclusive control frames, and a candidate/control control-frame
ratio at most 0.30. These preservation gates were frozen from the V10 high-N
advantage before V11 replay.

## Bounded execution

All 48 simulator identities (four replay, four pilot, and 40 formal) run
serially. There are 26 unique traffic inputs because paired arms reuse their
input. Limits are 1,000 generated flows, 4,096 grant-trace lines, 32 lifecycle
rows, 16 MiB per run, 1,800 seconds and 8 GiB RSS per process, and 1 GiB for
the campaign. The runner never reads FCT or queue files.

```bash
SPEC=experiments/membership_coalescing_ladder_v11.json
CAMPAIGN=/absolute/path/to/v11-campaign

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase preflight
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase replay
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope replay \
  --json-out "$CAMPAIGN/known_replay_admission_v11.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-replay \
  --replay-admission "$CAMPAIGN/known_replay_admission_v11.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase pilot
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope pilot \
  --json-out "$CAMPAIGN/pilot_admission_v11.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-pilot \
  --pilot-admission "$CAMPAIGN/pilot_admission_v11.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase run
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope holdout \
  --json-out "$CAMPAIGN/holdout_analysis_v11.json"
python3 experiments/aggregate_membership_coalescing_holdout.py "$SPEC" \
  "$CAMPAIGN/holdout_analysis_v11.json" \
  --output-dir "$CAMPAIGN/summary"
```

Failure does not authorize changing K, W, the hard deadline, phase semantics,
frame caps, queue correction, seeds, N, arms, or matrix subset. Such a change
requires a new preregistration and fresh pilot/formal identities. Passing V11
would admit only this isolated receiver-share mechanism through N=15; it would
not establish final-GUARD superiority. Remaining-aware refresh, receiver
concurrency, proactive release, size priority, and tail bypass remain outside
scope.
