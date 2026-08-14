# GUARD tail-threshold development search

This directory preserves the frozen five-seed AliStorage50 search over tail
bypass thresholds of 1, 2, 4, and 8 BDPs while retaining the selected
one-elephant/12-BDP receiver policy. It is not a held-out performance claim.

All 20 runs passed the mechanism and safety checks. Relative to 8 BDPs, the
smaller thresholds reduced mean queue occupancy by 1.09--2.93%, with paired
95% confidence intervals below zero. However, neither overall nor greater-than-
1-MiB mean FCT satisfied the preregistered non-degradation confidence bound.
Therefore no candidate was selected and the 8-BDP baseline remains frozen.
`selection.json` records all gates and intervals; `run_registry.csv` locates the
raw outputs relative to the repository.
