# Cap-aware reclamation smoke gate

This bounded mechanism test uses `experiments/inputs/cap_reclaim_smoke.flow`
(SHA-256 `c2de9f11e86a5be8546a75b65e73338090aea13d0e43ad05c0b6f749f58d2b39`)
and simulator commit `fb95697ebc6bcdf17c4c255636fcf2292c58d126`.
It contains 23 four-MiB flows on the 16-host OS4 topology: one cross-leaf and
one local flow share receiver 15, while 21 other cross-leaf flows create a
shared-fabric bottleneck.  Runs are serial, bulk-profile, 20 ms, PG4, PFC on,
IRN off, and each completes 23/23 flows with zero switch drops or recovery.

The frozen pilot gate requires both target-flow FCTs to remain within 5% of
the baseline, all-flow mean FCT within 1%, and nonzero cap reports,
rebalancing, and reclaimed rate.  The 0.25 safety floor harms the fabric donor
and 0.75 harms both target flows.  The 0.50 floor is the only setting that
passes this single-seed mechanism gate: the fabric target improves 16.75%,
the local target changes +1.88%, and overall mean changes +0.70%.  This is not
a publishable performance result and does not enable the feature by default;
it only authorizes an independently jittered multi-seed validation later in
the optimization sequence.

Raw run directories remain under `mix/output/<output_id>` and are ignored by
Git.  `results.csv` is the bounded, auditable result registry.
