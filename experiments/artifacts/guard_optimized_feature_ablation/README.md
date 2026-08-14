# Optimized GUARD feature ablation

This directory preserves the fresh-seed, one-factor ablation on seeds 11--15.
Each workload/seed uses one flow hash across all nine arms.  Performance did
not influence admission or the frozen configuration.

The analyzer admitted 129 of 135 runs.  Every rejection belongs to the arm
that disables GUARD's fixed one-BDP sender safety window.  Three such runs
left one or two flows incomplete and three additional runs invoked loss
recovery.  The analyzer therefore rejects all three workload cohorts for that
arm and emits no biased latency comparison from completed survivors.

The remaining cohorts show that static size priorities provide the largest
latency contribution, bounded sender remaining-work scheduling contributes a
smaller consistent gain, and remaining-aware grants and tail bypass improve
selected mean or tail metrics.  ACK coalescing mainly reduces control events.
One-RTT bypass has little latency effect in these workloads.  The experimental
unused-share reclaimer records no rebalance event and produces bit-identical
results when disabled, so final GUARD disables it by default.

`ci.csv` contains five-seed arm means and paired intervals.  `per_seed.csv`
contains admitted per-seed metrics.  `run_registry.csv` retains every admitted
and rejected run with portable raw-output locators and explicit failure text.
