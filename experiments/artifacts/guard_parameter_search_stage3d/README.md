# GUARD 12-BDP adaptive-scope refinement

This directory preserves a frozen five-seed AliStorage50 comparison between
the disabled adaptive target and its mechanism-qualified 12-BDP scope. It is
not a held-out performance claim.

All ten runs passed mechanism and safety checks. The scoped policy changed
overall mean FCT by +0.021%, greater-than-1-MiB mean FCT by +0.068%, and P99
slowdown by -0.025%. Mean queue occupancy decreased by 0.53%, but its paired
95% confidence interval crossed zero. The preregistered queue-improvement gate
therefore failed and the disabled baseline remained selected. Full intervals
are in `selection.json`; raw locators are in `run_registry.csv`.
