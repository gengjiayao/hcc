# GUARD high-load lambda search

This directory preserves the frozen AliStorage-50% development search over
lambda 1.8, 2.0, 2.2, and 2.4. It is not a held-out performance claim.
`selection.json` applies the preregistered five-seed paired-CI gates.

Neither 2.0 nor 2.2 produced a statistically supported long-flow or overall
FCT improvement over 1.8. Lambda 2.4 failed mechanism admission in seeds 49
and 50 because the shared-fabric controller path became inactive. The search
therefore retains lambda 1.8. Failed seeds are preserved rather than replaced;
raw-output locators are listed in `run_registry.csv`.
