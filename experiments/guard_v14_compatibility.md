# GUARD V14 mechanism-compatibility campaign

This campaign is a fail-closed, candidate-only check of the complete GUARD V14
mechanism bundle. It is deliberately not a performance experiment. The runner,
analyzer, and report tool do not open or hash FCT, queue, queue-length, or VOQ
artifacts, and the report makes no comparison with HPCC or Homa.

The exact simulator ancestry floor is
`3d266420608df72f92dddb8901a88e1c1c889ec2`. Preflight also requires a clean
simulator worktree, checks the V14 capability tokens in the simulator source,
freezes every generated flow file and command, and seals their hashes before a
run starts. Any missing run, changed seed, changed command, changed flow,
truncated trace, mechanism failure, or resource violation rejects the campaign.
It is not permissible to replace a failed seed or tune a scenario after seeing
its result.

## Frozen order and configuration

The runner is strictly serial. It first runs the four byte-identical V13 replay
inputs at seed 126 for N = 2, 4, 8, and 15. A fresh analyzer invocation must
admit all four, and `seal-replay` recomputes that admission before arming the
fresh stage. The fresh stage then runs eight scenarios at seeds 143, 144, and
145, for 24 candidate-only runs and 28 total runs.

The replay admission has one permitted path:
`CAMPAIGN/replay-admission.json`. Before any content read or hash, the runner,
fresh analyzer, and report reject every other path and reject a symlink at that
path. They then validate the replay schema, admitted status, four-run count,
mechanism-only scope, spec/preflight hashes, and simulator SHA. Every manifest
also seals the stage-gate digest, which the analyzer requires to match the
current gate.

Every fresh run explicitly enables the final bundle: selective registration,
proactive release, size priority, sender SRPT, one-RTT bypass, the 8-BDP tail
bypass, fixed windows, remaining-aware refresh at one BDP, mixed-PG vectors,
serialized progress refresh, serialized draining, the prefix barrier, wire
watchdog, and fail-closed transition handling. It freezes membership coalescing
at 12,480 ns, initial quiet time at 16,640 ns, at most five windows, no initial
full deadline, small-set limit four, and two reliability RTTs. Receiver
concurrency, work-conserving reclaim, and cap-aware reclaim remain disabled.

`proactive_drain_interleave` is the only fresh override. It changes gamma from
1 to 16 to expose the draining state machine and is explicitly diagnostic, not
a performance configuration. No fresh scenario disables any final-bundle
feature. The four V13 replay runs alone use the frozen historical isolation
profile.

## Fresh scenarios

All fresh flows are cross-ToR, so the frozen 100 Gb/s topology gives an exact
104,000-byte base-RTT BDP for the size-priority and trace-budget derivations.
With size priority enabled, input PG is not treated as the effective PG: the
flow size maps registered flows to PG4 for [1,2) BDP, PG5 for [2,4), PG6 for
[4,8), and PG7 for at least 8 BDP.

| Scenario | Flows (registered) | No-fault trace bound | Mechanism evidence required |
| --- | ---: | ---: | --- |
| `same_pg_n4` | 4 (4) | 548 | Exact small-set path: no high-fan transition, prefix, watchdog, or audit record. |
| `same_pg_n15` | 15 (15) | 4,215 | Same-PG high-fan transition and exactly one normally closed audit record. |
| `mixed_pg_n15` | 15 (15) | 5,175 | Sizes 156/208/416/832 KB derive PG4/5/6/7; a mixed vector freezes and its audit PG-set hash is recomputed. |
| `same_sender_multiflow_srpt` | 8 (8) | 2,376 | Sender SRPT participates and records at least one non-round-robin choice. |
| `one_rtt_short_background` | 24 (8) | 1,864 | Sixteen 64 KiB cross-ToR flows remain below one BDP and bypass registration while eight long flows register. |
| `tail_bypass_overlap` | 8 (8) | 3,144 | Four 9-BDP flows precede a burst by 300 us; tail bypass and transition both occur in the run. |
| `proactive_drain_interleave` | 9 (9) | 5,697 | Pending draining commits at a boundary, `requests = direct + pending = completion releases`, maximum D records and reserved bps are nonzero, and a receiver-authoritative frozen snapshot proves `D + A = C`. |
| `remaining_refresh_dual_receiver_epoch` | 26 (26) | 5,914 | Progress requests/transactions/commits and busy deferral occur; both receivers have two distinct normally closed audit epochs. |

The trace envelope is derived from the actual frozen flow sizes, not copied from
the JSON as an asserted number. It charges every BDP progress crossing, every
registered-flow release, and five setup transactions per receiver epoch. Each
logical transaction is budgeted for two complete required generations, with
all four wire events (`sent`, `received`, `ack_sent`, and `ack_received`) for
every active recipient. This gives eight rows per active recipient per logical
transaction. A completion-retired ACK substitutes `ack_retired` for
`ack_received` and adds one local `ack_retire_close` row; permanent exact-tuple
tombstones bound that addition to one row per registered flow. The derived
envelope must be at most 75% of the 8,192-line cap;
loss, retry, stale generation, or truncation is outside the no-fault envelope
and independently rejects admission.

## Resource and safety gates

Each run is limited to 64 flows, 16 MiB of output plus launcher log, 1,800
seconds of wall time, and 8 GiB RSS. The complete campaign is capped at 512 MiB.
The simulator is invoked with `--smoke`, bulk monitoring, PFC enabled, and IRN
disabled. The runner polls these bounds once per second, terminates the process
group if one is exceeded, and refuses multiple newly created output
directories.

Completion is read only from the final `finished so far: X/ total: X` marker in
`config.log`, with registered flows also closed by the bounded lifecycle trace.
The analyzer requires zero drops, recovery, timeouts, PFC events, stale or
rejected generations, fallback, barrier violations, pending ACKs, and terminal
state. Required prepare/decrease grants must complete their four-event ACK
sequence before activation/increase.  A flow removed after `ack_sent` instead
uses one receiver-authoritative `ack_retire_close` safety close followed by an
`ack_retired` wire receipt.  The analyzer requires one-to-one safety/wire
closure, matching aggregate counters, zero retained/overflow records, and no
stale ACK.  The wire receipt cannot decrement pending state or close a phase
again.  Across the complete chain it also requires exact phase, transaction,
endpoint-IP, subject-role/action, and immutable target provenance; the local
safety close and retired receipt both carry the frozen target for this check.
Receiver-authoritative `sent`, `ack_received`, and `ack_retire_close`
rows must set `snapshot_valid=1` and retain one immutable frozen
tuple throughout an allocation revision.  The tuple contains frozen `C`,
ACTIVE/DRAINING counts, `D`, `A`, and the encoded-target sum.  It must satisfy
`D + A = C`; encoded targets and every grant must fit `A`.  Live
ACTIVE/DRAINING counts and live `D` are separate evidence.  Live
`D > frozen_D` rejects admission.  Live `D < frozen_D` is the permitted
conservative mid-barrier case only when `capacity_recompute_pending=1`, and the
next allocation revision must refreeze its live `D`.  Sender-side `received`
and `ack_sent` rows must set `snapshot_valid=0` and expose no receiver snapshot
or live-state values.  Allocation revisions are run-monotonic per receiver
hardware and cannot be reused after a coordinator epoch reset.
When multiple events share one nanosecond, their original CSV row ordinals must
still prove strict `sent < received < ack_sent < ack_received` order, or
the two partial orders `sent < received < ack_sent < ack_retired` and
`sent < ack_retire_close < ack_retired` for a retired recipient.  The local
safety close may precede grant arrival when a receiver flow completes with the
grant already in flight.  The normal ACK or retirement safety-close row must
precede every activation row for that transaction.

Every started high-fan transition must produce one unique terminal audit row
with outcome `ready`, closure `activation_ack_closed`, observed prefix at least
the target, and active occupancy below receiver capacity. The analyzer requires
the aggregate high-transition count, prefix-start count, and audit-row count to
be identical. It also checks that the reported combined upper bound equals the
sum of its active and draining components. The audit field
`active_plus_draining_upper_bound_bps` is a conservative wire-prefix quantity:
each prefix waiter can contribute a full-capacity bound, so it may legitimately
exceed `C` and is not used as a capacity invariant. Multi-transition or
multi-receiver fresh runs may likewise make the older aggregate watchdog
`non_reconstructable` or `inconsistent`; the per-transition audit rows are the
authoritative evidence.

## Execution

Choose a new campaign directory. Preflight refuses an existing directory.

```bash
python3 experiments/run_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --repo /path/to/guard \
  --campaign-dir /path/to/v14-compat \
  --phase preflight

python3 experiments/run_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --repo /path/to/guard \
  --campaign-dir /path/to/v14-compat \
  --phase replay

python3 experiments/analyze_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --campaign-dir /path/to/v14-compat \
  --scope replay \
  --json-out /path/to/v14-compat/replay-admission.json

python3 experiments/run_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --repo /path/to/guard \
  --campaign-dir /path/to/v14-compat \
  --phase seal-replay \
  --replay-admission /path/to/v14-compat/replay-admission.json

python3 experiments/run_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --repo /path/to/guard \
  --campaign-dir /path/to/v14-compat \
  --phase compatibility

python3 experiments/analyze_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  --campaign-dir /path/to/v14-compat \
  --scope compatibility \
  --json-out /path/to/v14-compat/compatibility-admission.json

python3 experiments/report_guard_v14_compatibility.py \
  experiments/guard_v14_compatibility.json \
  /path/to/v14-compat/compatibility-admission.json \
  --campaign-dir /path/to/v14-compat \
  --output-dir /path/to/v14-compat/report
```

The compatibility analyzer output used by the report must be exactly
`CAMPAIGN/compatibility-admission.json` and must not be a symlink. Reporting
reruns the complete fail-closed fresh analyzer and requires exactly equal JSON
content before writing the CSVs; a copied, edited, or top-level-only `passed`
file cannot produce an admitted report.

`--resume` may skip only a run whose sealed manifest already says `completed`;
it cannot alter an identity or rerun a failed one in place.

## Claim boundary

The mechanism report establishes compatibility and fail-closed safety evidence
only. The grant trace is sparse and has no PG or target-vector hash.  It proves
that the simulator's encoded-target sum fits the frozen allocation and that
every emitted grant fits it, but cannot independently reconstruct each member
of `sum(P)` from the trace. The mixed-PG set
can be derived from frozen flow sizes and its audit hash can be checked, but the
full target-vector hash cannot be independently reconstructed from current
outputs. Sender SRPT is supported only by aggregate selection/non-RR counters.
The tail trace has no timestamped bypass event, so the staggered scenario proves
tail-bypass/transition co-occurrence, not their exact temporal overlap. These
limits must remain explicit in any later use of the admission result.
