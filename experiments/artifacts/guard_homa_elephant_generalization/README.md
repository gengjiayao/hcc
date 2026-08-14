# Elephant-concurrency cross-workload validation

This directory preserves an independent seeds 71--75 validation of the
development-selected 12-BDP, one-elephant receiver policy on AliStorage,
WebSearch, and FbHdp. All 45 GUARD/HPCC/Homa runs complete, use exact matched
flow hashes within each seed, pass controller-specific admission, and record
zero switch drops or recovery.

Relative to Homa, GUARD lowers overall mean FCT by 2.62% on AliStorage and
4.18% on FbHdp; WebSearch's -4.36% interval crosses zero. P99 slowdown falls
by 15.77%, 14.01%, and 23.00%, and mean queue occupancy falls by 39.60%,
61.05%, and 48.83%, respectively. Relative to HPCC, overall mean FCT falls by
14.10%, 13.12%, and 16.35%; GUARD's AliStorage and FbHdp queue occupancy is
higher, so the policy does not remove that latency--queue tradeoff.

`metrics.csv` contains all arm means and paired intervals. `run_registry.csv`
contains per-seed mechanism counters and repository-relative raw locators.
