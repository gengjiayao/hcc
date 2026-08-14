# GUARD adaptive-target development search

This directory preserves the frozen five-seed AliStorage50 search over adaptive
fabric-target queue budgets of 0.25, 0.5, and 1.0 BDP, plus the disabled
baseline. It is not a held-out performance claim.

All 20 runs passed mechanism and safety admission. The 0.5-BDP budget reduced
mean queue occupancy by 9.34% with a paired 95% confidence interval below
zero, while changing overall mean FCT by only +0.095%. However, its
greater-than-1-MiB mean-FCT confidence bound reached +1.47%, above the frozen
+0.5% limit; the other budgets also failed at least one preregistered gate.
No candidate was selected. `selection.json` records the complete decision and
`run_registry.csv` locates raw outputs relative to the repository.
