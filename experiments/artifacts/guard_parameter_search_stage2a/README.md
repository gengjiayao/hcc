# GUARD receiver-concurrency search

This directory preserves a fresh five-seed AliStorage-50% development test of
serving either all registered flows or only the shortest registered flow above
the minimum rate. It is not a held-out performance claim.

One-flow service lowered above-1-MiB mean FCT by 3.40% (95% CI: -4.91% to
-1.89%) and mean queue occupancy by 17.44%, but raised overall mean FCT by
8.75% (CI: -7.59% to 25.10%). The preregistered overall-latency gate therefore
rejects the candidate and retains unrestricted receiver concurrency. Raw-output
locators are listed in `run_registry.csv`.
