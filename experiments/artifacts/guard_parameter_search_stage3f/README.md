# GUARD 20-BDP adaptive-scope bisection

This directory preserves the final, preregistered bisection of the adaptive
fabric-target development line. It is not a held-out claim.

All ten AliStorage50 runs on seeds 101--105 passed mechanism and safety checks.
The 20-BDP scope reduced mean queue occupancy by 7.41% with a paired 95%
confidence interval below zero, while its point estimate improved overall mean
FCT by 0.05%. However, the confidence upper bounds for greater-than-1-MiB mean
FCT and P99 slowdown were +0.88% and +1.09%, above the unchanged +0.5% gates.
The candidate was rejected and this optimization line stopped. Complete
intervals are in `selection.json`; raw locators are in `run_registry.csv`.
