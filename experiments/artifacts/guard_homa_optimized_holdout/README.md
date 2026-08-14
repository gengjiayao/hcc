# Optimized GUARD held-out validation

This directory records the result of the configuration frozen in
`experiments/campaigns/guard_homa_optimized_holdout.json`.  Algorithm choices
were developed with seeds 1--5.  The five consecutive seeds here, 6--10, were
generated only after the configuration was frozen.  Seed 6 first passed the
mechanism gate; the final statistics include all five seeds.  Every arm within
a seed uses the same flow snapshot.

All 45 runs completed every flow, remained below 100 MiB, and recorded zero
switch drops, NACK recovery, retransmission, or timeout recovery.  GUARD's
admission runs exercised grants, valid shared-fabric feedback, actual pacing
changes, tail bypass, remaining-progress refresh, and non-round-robin sender
scheduling.  Homa's message/grant/completion and priority checks also passed.

The table reports paired percentage change, GUARD minus Homa, with two-sided
Student-t 95% intervals across the five matched seeds.  Negative latency and
queue values favor GUARD.

| Workload | Mean FCT | Mean slowdown | P95 slowdown | P99 slowdown | Mean queue |
|---|---:|---:|---:|---:|---:|
| AliStorage2019 | -1.64% [-2.76, -0.51] | -1.01% [-1.48, -0.54] | -1.57% [-2.50, -0.64] | -12.90% [-17.53, -8.27] | -33.66% [-37.70, -29.62] |
| FbHdp2015 | -3.58% [-7.38, +0.22] | -2.26% [-3.53, -0.99] | -9.35% [-14.84, -3.87] | -20.07% [-28.54, -11.59] | -46.59% [-53.91, -39.27] |
| WebSearch | -2.71% [-5.61, +0.18] | -6.78% [-12.85, -0.71] | -11.46% [-20.89, -2.03] | -22.87% [-36.82, -8.92] | -54.33% [-61.98, -46.68] |

GUARD also lowers mean FCT relative to HPCC by 13.82%, 14.71%, and 16.53%
for AliStorage2019, FbHdp2015, and WebSearch, respectively.  This is not a
claim of metric-wise dominance.  GUARD's queue is higher than HPCC's in these
runs, AliStorage2019's greater-than-1-MiB mean FCT is 1.44% higher than Homa
[+0.28%, +2.59%], and absolute P99 FCT for AliStorage2019 and FbHdp2015 is
statistically indistinguishable from Homa.  These outcomes remain in
`metrics.csv`; no seed or metric was removed.

Files:

- `spec.json` and `preflight.json` freeze the design, flow counts, and hashes.
- `admission.json` contains the seed-6 mechanism checks and bounded controller
  summaries.
- `run_registry.csv` records all 45 output IDs and per-run metrics.
- `metrics.csv` contains arm means and paired absolute/percentage intervals for
  every reported metric and flow-size bucket.
- `manifest.json` hashes the bounded files.  Raw outputs are intentionally not
  duplicated in Git; their repository-relative locators are in the registry.

To reproduce the summary from retained raw outputs:

```bash
python3 experiments/summarize_general_workloads.py \
  experiments/results/guard-homa-optimized-holdout \
  --export-dir experiments/artifacts/guard_homa_optimized_holdout
```
