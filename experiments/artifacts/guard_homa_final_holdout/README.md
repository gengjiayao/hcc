# Final GUARD/Homa/HPCC holdout

This directory preserves the bounded summary of the final, frozen GUARD
configuration on seeds 16--20.  The configuration disables the experimental
unused-share reclaimer after a separate one-factor ablation observed zero
rebalancing events and bit-identical outcomes with that feature disabled.

All 45 runs completed every generated flow and passed the zero-drop,
zero-recovery, mechanism-activity, configuration, and matched-flow-hash gates.
The simulator revision and portable raw-output locators appear in
`manifest.json` and `run_registry.csv`.

The paired percent rows in `metrics.csv` report left minus right.  Relative to
Homa, GUARD reduces mean FCT by 2.15% on AliStorage and 2.86% on FbHdp; the
WebSearch point estimate is -1.41% with a confidence interval that crosses
zero.  GUARD reduces P99 slowdown by 12.36%--26.07% and mean queue occupancy
by 36.03%--56.83% across the three workloads.  Relative to HPCC, GUARD reduces
mean FCT by 13.44%--14.82%, while its mean queue occupancy is higher.  These
results support a latency/queue tradeoff, not universal metric-wise dominance.
