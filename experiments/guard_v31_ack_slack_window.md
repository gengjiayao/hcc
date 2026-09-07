# V31 path-adaptive ACK-slack window

V31 is a preregistered development experiment, not a paper claim. It changes
one GUARD transport-window rule relative to frozen K=1: the steady window and
eligible whole-flow first gate are
`min(topology-diameter BDP, path BDP + 16 MTU packets)`. The 16-packet slack is
two existing eight-packet ACK intervals; it was frozen from the first
same-leaf diagnostic threshold that retained V30's bounded-incast result.

The formal matrix uses fresh seeds 225--229 and four paired arms: K=1 GUARD,
V31 GUARD, HPCC, and Homa. The admission seed is evaluated for completion,
mechanism activity, exact configuration, drops, recovery, timeout, PFC, and
bounded artifacts before any performance data is read. All 20 cells must pass
the mechanism analyzer before the performance summarizer and selector run.

The selector retains the V30 gates. In particular, V31 must improve long-flow
mean FCT over K=1 by at least 1% at the upper 95% confidence bound, beat both
HPCC and Homa on every frozen latency comparison, avoid queue regressions over
K=1 beyond 5%, and preserve Jain's index within 0.005. Passing development
still requires a fresh independent holdout before any claim.

## Frozen outcome

V31 is rejected on mechanism admission. The sealed simulator revision was
`800c0c2`; the spec and preflight SHA-256 values were
`696e3aad04af9efe0d0c6682badcdc9693957cfbd93d5d1abfdf064fe9a25169`
and
`7afebb38a88bf3a4a1601761c78be6abd2a77479e53d09c8703393e69a672112`.
Seed 225 passed all four arms. Across the full matrix, 19 of 20 runs completed,
but the V31 seed-226 arm (`633074730`) stopped after 6140 of 9714 flows at the
fail-closed `ACTIVE-empty state crossed a live transaction` assertion. The
formal mechanism report therefore admitted 19/20 and kept performance sealed;
no FCT or queue artifact was read for selection.

The failure exposed a terminal coordinator edge rather than an ACK-slack
formula error: the last ACTIVE completion could close its pending ACK but the
cleanup path only finalized the live vector when a draining record remained.
A separate default-preserving fix now closes empty PREPARE/ACTIVATE
generations with zero pending ACKs before terminal reset. A one-run diagnostic
on the exact failed flow snapshot then completed 9714/9714 with zero
drop/recovery/PFC and terminal draining/retired-ACK state at zero. That
diagnostic does not rehabilitate V31; any performance retest requires a new
version and fresh seeds.
