# Membership-coalescing V6 pilot and holdout

`membership_coalescing_ladder_v6.json` freezes an explicit-generation-ACK
qualification pilot followed by a 40-run receiver-share holdout.  The tools
provide no subset, parallel-worker, or partial-performance path.  They never
launch ns-3 unless the operator explicitly selects a run phase.

## Frozen design

The qualification pilot is seed 95 at N=15, with exactly one `control` and one
`membership_coalescing_v6` run over the same traffic hash.  The formal holdout
uses seeds 96--100, N in {2, 4, 8, 15}, and the same two arms, for 40 runs.  A
pilot run is a separate experiment identity and cannot be reused as a formal
cell.

The V6 delivery condition is a per-membership-generation grant ACK.  Candidate
admission reconciles each generation's grant send, grant receive, ACK send,
and ACK receive identities; requires every membership batch to be fully
acknowledged; and requires zero pending generations, normal-path retries, and
stale grant/ACK events.  Candidate grant sends plus ACK sends must be at most
3N.  Control must emit no generation or grant ACK.  Aggregate data progress is
not delivery evidence.

The PFC audit always reconciles the raw trace and stats, including pause,
resume, matched interval, cumulative duration, maximum duration, and unmatched
counts.  Unmatched events reject either arm.  The frozen zero-PFC gate applies
only to the coalesced arm; control PFC is retained as a safety/performance
observation and is not rejected based on direction.

## Fail-closed workflow

Create the campaign preflight first:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --phase preflight
```

Preflight creates one pilot and 20 formal traffic identities, freezes both run
orders, records the exact clean simulator Git SHA, and leaves
`formal_execution_armed=false`.  It launches no simulation.

Run only the two-cell pilot, then invoke the mechanism-only pilot analyzer:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --phase pilot

python3 experiments/analyze_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --scope pilot \
  --json-out /absolute/path/to/campaign/pilot_admission.json
```

The pilot analyzer accepts exactly seed 95/N=15/two arms.  It reads mechanism
artifacts only; it neither opens FCT/queue files nor emits performance fields.
On any failure it does not write an admission artifact.

Bind the successful artifact into preflight before formal execution:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --phase seal-pilot \
  --pilot-admission /absolute/path/to/campaign/pilot_admission.json
```

This phase freshly reruns the mechanism-only pilot analysis, requires its
result to equal the supplied artifact, verifies the exact spec, simulator SHA,
and pre-seal preflight hash, then records the artifact path and SHA-256 in
`preflight.json`.  A hand-written PASS cannot arm execution.  The formal
runner refuses to start if the artifact is absent, rejected, changed, or
belongs to another preflight.

Run and analyze the formal matrix:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --phase run

python3 experiments/analyze_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  --campaign-dir /absolute/path/to/campaign \
  --scope holdout \
  --json-out /absolute/path/to/campaign/holdout_analysis.json

python3 experiments/aggregate_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v6.json \
  /absolute/path/to/campaign/holdout_analysis.json \
  --output-dir /absolute/path/to/campaign/aggregate
```

The runner is serial, records the simulator SHA in every manifest, enforces
the frozen wall/RSS/flow/trace/storage caps, and stops at the first failed
process.  It never opens FCT or queue files.  The holdout analyzer first admits
all 40 mechanism cells (hash, config, lifecycle/stats completion, drops,
recovery, timeout, trace closure, generation ACKs, PFC consistency, and final
C/N accuracy).  Only after all 40 pass does it open FCT and receiver
switch-egress queue artifacts.  The aggregator then computes five-seed paired
Student-t 95% intervals and applies every frozen non-regression threshold.

`--resume` only skips already completed manifests in the selected execution
phase.  There is no argument for choosing a seed, N, arm, run count, or worker
count.

The experiment covers synchronized receiver-share membership traces through
N=15 only; passing F<=3N does not establish general asymptotic complexity.
`guard_proactive_release=0`, so the experiment does not cover proactive
release while a membership generation remains unacknowledged.
