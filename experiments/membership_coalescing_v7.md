# Membership-coalescing V7 selective-ACK holdout

`membership_coalescing_ladder_v7.json` freezes a selective generation-ACK
pilot and formal holdout.  It retains the V6 topology, workload, GUARD
configuration, resource limits, PFC policy, and paired non-regression bounds.
The only intended mechanism change is which membership generations require a
grant ACK.

## Current execution status

The tools are based on `2d2ba413fe38bede7060d3d712fb7256fd7b6d99`.
The tools require `6c9c88441b602ab2824785d8c5de73d90f2c6a52` or a
descendant.  That commit exposes the V7 `ack_required` trace column and the
`ack_required_*`, `ack_optional_*`, generation-zero, and generation-mismatch
stats.  The runner probes the tracked simulator source for all frozen
capability tokens before creating a campaign directory or generating inputs,
so an older or schema-incompatible simulator fails closed.

After a later simulator commit implements the exact observable contract, the
same runner records that later clean Git SHA; it does not assume the base SHA.

## Frozen matrix

The qualification pilot contains exactly two seed-101, N=15 cells:
`control` and `membership_coalescing_v7`, sharing one traffic hash.  Formal
seeds are 102--106 at N in {2, 4, 8, 15}, again with both arms and paired
traffic, for exactly 40 serial cells.  The pilot is a separate identity and
cannot be reused as a formal cell.  There is no seed, N, arm, run-count,
parallel-worker, or partial-performance option.

## Selective generation-ACK contract

For each coalesced generation the analyzer reconstructs the granted flow set
and encoded rate vector from raw trace rows.  It tracks the conservative
acknowledged upper bound: a required generation resets that bound on closure,
while an optional non-decreasing generation advances it.

- A generation that first grants any flow or places any encoded target below
  that flow's conservative upper bound must set `ack_required=1`.  Every
  `(generation, flow)` grant send, grant
  receive, ACK send, and ACK receive must close in time order, and the final
  ACK must leave zero pending state.
- A generation may set `ack_required=0` only when it removes at least one flow,
  adds none, and every continuing flow's grant is non-decreasing.  Such a
  generation must emit zero ACK frames and have zero pending and retry state.
- The per-run stats must partition all batches into ACK-required and
  ACK-optional batches.  `fully_acked_batches` must equal the ACK-required count.
- Normal-path retries, stale grants, stale ACKs, and final pending state must
  all be zero.  Candidate grant sends plus ACK sends must be at most 3N.
- Control must use generation zero, `ack_required=0`, and emit no generation
  ACKs.

The seed-101 pilot additionally requires at least one ACK-required generation
and one ACK-optional pure-release generation, so admission cannot pass without
exercising both policy branches.

Both arms must reconcile every raw PFC field with stats and have zero unmatched
pause/resume events.  Only the coalesced arm has the frozen zero-PFC gate;
control PFC remains a reported observation rather than a directional filter.

## Fail-closed workflow

Once the simulator capability probe is satisfied, run:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --phase preflight

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --phase pilot

python3 experiments/analyze_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --scope pilot \
  --json-out /absolute/path/to/v7-campaign/pilot_admission_v7.json

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --phase seal-pilot \
  --pilot-admission /absolute/path/to/v7-campaign/pilot_admission_v7.json

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --phase run
```

Pilot sealing freshly reruns the mechanism-only analyzer and requires exact
agreement with the supplied artifact before binding its SHA-256 into
`preflight.json`.  A hand-written PASS cannot arm formal execution.

After all 40 formal processes finish, analyze and aggregate:

```bash
python3 experiments/analyze_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  --campaign-dir /absolute/path/to/v7-campaign \
  --scope holdout \
  --json-out /absolute/path/to/v7-campaign/holdout_analysis.json

python3 experiments/aggregate_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v7.json \
  /absolute/path/to/v7-campaign/holdout_analysis.json \
  --output-dir /absolute/path/to/v7-campaign/aggregate
```

The runner never opens FCT or queue files.  The analyzer first admits all 40
mechanism cells, including paired hash, exact config, lifecycle/stats
completion, zero drops/recovery/timeout, bounded raw traces, selective-ACK
closure, PFC consistency, and final C/N accuracy.  Only after all 40 pass does
it open FCT and receiver switch-egress queue artifacts.  The aggregator then
applies the unchanged V6 five-seed paired Student-t 95% thresholds.

The evidence is scoped to synchronized receiver-share traces through N=15;
F<=3N here is not a general asymptotic claim.  Proactive release remains off,
so that path is outside this experiment.
