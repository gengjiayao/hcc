# GUARD expanded adaptive-scope search

This directory preserves the frozen five-seed AliStorage50 search over 16-,
24-, and 32-BDP adaptive-target scopes. It is not a held-out claim.

All 20 runs passed mechanism and safety checks. The 24- and 32-BDP scopes were
identical on these traces: mean queue occupancy fell 5.03% with a paired 95%
confidence interval below zero, and overall mean FCT changed by +0.076%.
However, greater-than-1-MiB mean FCT had a +0.684% confidence upper bound,
above the frozen +0.5% limit. The 16-BDP scope met the latency constraints but
its queue interval crossed zero. No candidate was selected. `selection.json`
records every gate and `run_registry.csv` locates the raw outputs.
