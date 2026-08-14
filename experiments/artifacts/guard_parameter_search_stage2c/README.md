# GUARD elephant-threshold search

This directory preserves the five-seed AliStorage-50% development grid over
4, 6, 8, and 12 BDP elephant thresholds plus unrestricted concurrency. It is
not a held-out performance claim.

Thresholds 4, 8, and 12 pass every preregistered latency, queue, and safety
gate. The frozen tie-break order selects 12 BDP: above-1-MiB mean FCT falls
1.94% (95% CI: -3.03% to -0.85%), overall mean FCT falls 0.71% (CI: -1.00%
to -0.43%), and mean queue occupancy falls 8.57%. A fresh comparison against
HPCC and Homa is required before any performance claim. Raw-output locators
are listed in `run_registry.csv`.
