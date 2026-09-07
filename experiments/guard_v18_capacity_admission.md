# V18 draining-capacity admission

V17 exposed a production-scale safety boundary: a receiver may retain nearly
all capacity as conservative reservations for proactively released flows while
a new frozen waiter cohort arrives.  Since the sender clamps every grant to a
100-Mb/s minimum, admitting a cohort whose aggregate floor exceeds `C-D` would
break the receiver-cap invariant.  V17 aborted at this boundary.

V18 changes only that boundary.  Before a membership transaction consumes its
revision or moves its frozen waiter snapshot, the receiver computes the exact
number of live incumbents and waiters, the receiver capacity `C`, and current
draining reservation `D`.  If `N * R_min > C-D`, it leaves the transaction and
membership revision untouched.  Each contiguous drain completion removes its
reservation and retries the same snapshot.  The first feasible retry resumes
normal V17 vector construction.  No timer, seed-dependent threshold, or rate
below the sender's enforced minimum is introduced.

The mechanism is default-off and requires the complete serialized-draining,
mixed-vector, small-set GUARD chain.  Its bounded stats report deferral and
resume counts, maximum blocked waiters, and terminal blocked state.  The V18
development campaign uses fresh seeds 160--164 and requires every deferral to
resume and terminal blocked state to be zero before any FCT or queue artifact
is opened.

## Frozen development result

The authoritative campaign is `/tmp/guard-v18-ali50-development-v3` at
simulator commit `71481b42ffbd7436d48a9b6344877296f1b043c7`.  An earlier `v1`
attempt never launched ns-3 because the build directory still had the known
ns-3.19 `--enable-tests` GCC incompatibility enabled.  A `v2` mechanism run
exposed an analyzer-only mismatch: `run.py` omits the legacy fail-closed line
when its value is zero.  Commit `71481b4` taught the validator that this one
absent opt-in line means disabled while continuing to reject an explicit one.
The clean `v3` campaign then repeated the same frozen traces.

All 15 runs completed every flow with zero switch drop, recovery, timeout, or
PFC event.  GUARD capacity deferrals/resumes for seeds 160--164 were
`4/4`, `2/2`, `7/7`, `9/9`, and `7/7`; maximum blocked waiters was one and
terminal blocked state was zero in every run.  Only after that 15/15 mechanism
gate passed was performance opened.

The result is a mixed outcome and is not a final superiority claim.  Relative
to Homa, GUARD reduced mean and P99 sampled queue by 49.77% [44.66%, 54.88%]
and 44.53% [35.66%, 53.40%], respectively.  It also reduced <=8-KiB mean,
P95, and P99 FCT by 0.51%, 0.80%, and 0.98%.  However, overall P99 FCT was
8.75% [1.26%, 16.25%] higher, and >1-MiB mean FCT was 25.42% higher with a
wide interval crossing zero.  Relative to HPCC, overall P95/P99 FCT improved
23.91%/10.26%, but the >1-MiB bucket did not show a stable advantage.  The
largest regressions occur in late clusters of elephants sharing one receiver,
consistent with the final frozen-vector path not yet applying the previously
selected one-elephant/12-BDP service bound.

Portable summaries are
`summary/general_metrics.csv` (SHA-256
`19305e41e8b750668f0e36bb9dda0c9d35de3e97df96c959a5f99e787a30ef6c`),
`summary/general_report.json` (`f3aae1bcd65ed0d9efb0602e033869c024a6929479e769d95ec6483db4730bb0`),
and `summary/mechanism-formal.json`
(`5af4f2a097d6d0aa8240aeeda23e572da56dc5fd8a28f5b3376415ca1be4b631`).
