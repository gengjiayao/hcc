# GUARD elephant-concurrency search

This directory preserves a fresh five-seed AliStorage-50% development test of
limiting above-8-BDP receiver service to one elephant at a time. It is not a
held-out performance claim.

The candidate lowered overall mean FCT by 1.10% (95% CI: -1.87% to -0.34%)
and mean queue occupancy by 15.73%. Above-1-MiB mean FCT fell by 1.84%, but
its CI (-3.78% to +0.095%) narrowly crossed zero. The preregistered long-flow
gate therefore rejects the candidate and retains unrestricted concurrency.
Raw-output locators are listed in `run_registry.csv`.
