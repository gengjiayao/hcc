# Bounded reviewer experiment campaign

These tools run the corrected GUARD reviewer matrix serially and summarize it
without treating individual flows as independent experimental repetitions.
They use the Python standard library and do not change the simulator model.

## Safety-first workflow

First inspect the expanded matrix and generate each candidate traffic file in a
temporary location.  `--dry-run` never launches ns-3 or installs the generated
traffic input:

```bash
python3 experiments/run_campaign.py \
  experiments/campaigns/reviewer_minimal.json --dry-run
```

The checked-in campaign expands to 165 unique runs.  It covers full GUARD,
HPCC-only, receiver-rate-only, last-hop INT, lambda/beta/gamma sensitivity,
matched PFC/IRN settings, AliStorage, WebSearch, two offered loads, and five
seeds.  Identical traffic parameters reuse a byte-identical input.

By default the runner enforces these hard bounds:

- 25,000 generated flows per run;
- 30 minutes wall time per run;
- 100 MiB output per run; and
- 5 GiB across the campaign directory, its simulator outputs, and traffic inputs.

It starts a single run at a time and terminates the whole simulator process
group if a bound is exceeded.  Override a bound only after inspecting a dry
run.  The runner never deletes an output automatically.

After reviewing the dry-run counts, launch a campaign into an explicit results
directory:

```bash
python3 experiments/run_campaign.py \
  experiments/campaigns/reviewer_minimal.json \
  --campaign-dir experiments/results/reviewer-minimal
```

Use `--resume` with the same directory to skip manifests already marked
`completed`.  Use `--max-runs 1` for an initial end-to-end smoke test.

Each attempt writes `runs/<run-key>/manifest.json`.  A manifest records the
expanded parameters and CLI, Git SHA and dirty state, topology/CDF/flow SHA-256,
generated and completed flow counts, timestamps, exit status, stop reason,
output location and byte count, and all active safety limits.  `run.py` retains
the exact ns-3 `config.txt` in the simulator output directory.

## Validation and aggregation

After all runs finish:

```bash
python3 experiments/summarize_campaign.py \
  experiments/results/reviewer-minimal
```

This creates:

- `summary/runs.csv`: one validation row per simulator run;
- `summary/metrics.csv`: per-seed metrics, cross-seed aggregates, and paired
  differences; and
- `summary/rejections.txt`: failed manifests, invariant violations, incomplete
  pairs, and traffic-hash mismatches.

The summarizer reads raw FCT, `config.txt`, the extended GUARD/recovery/drop
statistics, seven-column per-priority PFC events, and the bounded
`out_queue_stats` summary.  It falls back to the legacy queue trace only for old
artifacts.  It rejects incomplete runs and checks that:

- full GUARD has both grants and HPCC feedback updates;
- HPCC-only has no grants and does have feedback updates;
- receiver-rate-only has grants and no HPCC updates; and
- config values agree with the manifest.

FCT metrics are separated into `<=8 KB`, `8 KB--1 BDP`, `1 BDP--1 MiB`, and
`>1 MiB`.  P99.9 is emitted only with at least 1,000 observations.  The bounded
queue summary includes zero-valued samples and reports sample count, mean, P95,
P99, and maximum queue bytes.  For legacy traces only, queue metric names say
`nonzero`, because those traces omit zero-valued samples.  PFC results include
per-priority counts, matched intervals, cumulative and maximum pause duration,
and unmatched transitions.  Recovery results include switch drops, recovery
and IRN NACKs, retransmitted packets and bytes, and timeouts.

Every run first yields one value per metric and seed.  Aggregate rows compute a
two-sided 95% Student-t confidence interval over those seed-level values.
Paired algorithm differences require matching seed and an identical traffic
SHA-256; a mismatch is a hard rejection.

## Scope

This campaign deliberately excludes GoogleRPC because its small mean message
size exceeds the 25,000-flow safety cap at the selected scale.  It also does not
claim to supply missing RDMA hardware measurements, NDP/ExpressPass/pHost
implementations, a validated standard Homa baseline, or absent collective
traffic generators.  Those require separate artifacts before they can support
paper claims.

Run the unit tests with:

```bash
python3 -m unittest discover -s experiments/tests -v
```
