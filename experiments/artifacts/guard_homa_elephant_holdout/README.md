# Optimized GUARD high-load holdout

This directory contains the independent seeds 66--70 comparison of the
development-selected 12-BDP elephant-concurrency policy against HPCC and Homa
on AliStorage at 50% offered load. All 15 runs complete, remain below the
artifact cap, share exact flow hashes within each seed, exercise their required
controller paths, and record zero switch drops or recovery.

Relative to Homa, GUARD reduces overall mean FCT by 0.60% (95% CI: -1.05% to
-0.15%), P99 slowdown by 2.19%, mean queue occupancy by 49.47%, and P99 queue
occupancy by 39.98%. Above-1-MiB mean FCT differs by +0.90%, with a confidence
interval crossing zero. Relative to HPCC, GUARD reduces overall mean FCT by
13.44% and P99 FCT by 15.44%; its queue intervals cross zero.

`metrics.csv` contains arm means and paired intervals. `run_registry.csv`
contains per-seed mechanism counters and repository-relative raw locators.
