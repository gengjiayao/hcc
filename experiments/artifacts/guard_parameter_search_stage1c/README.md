# GUARD tail-gate development search

This directory preserves the frozen five-seed AliStorage50 development search
over the ungated tail bypass and congestion-gated safe ratios 0.8, 0.9, and
1.0.  It is not a held-out performance claim.  All 20 runs completed, shared
the per-seed flow hash across arms, exercised grants and shared-fabric rate
updates, and had zero switch drops and recovery events.

The gate was mechanistically meaningful: at seed 41 the three candidates
deferred 282--286 flows and later admitted 307--311 qualified bypasses, versus
384 unconditional bypasses in the baseline.  Across five seeds, gating lowers
mean queue occupancy by 4.88--6.11%, but all three point estimates worsen P99
slowdown by about 0.45% and their confidence intervals cross zero.  No
candidate passes the preregistered tail-latency gate, so the ungated behavior
remains selected.  `selection.json` contains every paired confidence interval;
raw outputs remain under the repository-relative locators in
`run_registry.csv`.
