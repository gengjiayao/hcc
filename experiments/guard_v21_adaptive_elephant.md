# V21 sublinear elephant concurrency

V20 established that fixed K=2 is not a robust replacement for K=1.  It
removed one severe large-cohort outlier but was slower in the other four seeds
and increased mean queue occupancy.  V21 therefore changes the policy, not a
timer or workload: with E current elephants, the service width is
`min(Kmax, floor(sqrt(E)))`, with `Kmax=2`.  The rule keeps K=1 for E at most
three and promotes to K=2 only for a cohort of at least four.  The 12-path-BDP
elephant definition, 100-Mb/s deferred floor, deterministic remaining-size
ordering, canonical vector, safety transactions, and every other V19 setting
remain unchanged.

The development campaign freezes AliStorage2019 at 50% load and fresh seeds
175--179.  Seed 175 is a mechanism-only gate.  Both arms must complete every
flow with zero drop/recovery/timeout/PFC, close all V14/V18 state, and exercise
bounded service.  The adaptive arm must additionally record a promotion and
maximum effective K=2, while the baseline must record neither.  FCT and queue
artifacts remain sealed until all ten cells pass.

Selection uses the same conservative gates as V20: the greater-than-1-MiB
mean/P95 and overall mean paired t95 upper bounds must improve, short-flow P95
may rise by at most 0.5%, queue mean/P99 by at most 5%, and the Jain difference
lower bound must stay above -0.005.  Passing authorizes a fresh three-arm
holdout; it is not a performance claim by itself.

## Frozen result

All ten runs completed and passed the mechanism gate.  Both arms had zero
drop/recovery/timeout/PFC activity and closed every capacity deferral.  The
adaptive arm recorded hundreds of K=2 promotions in every seed and maximum
effective K=2; the K=1 arm recorded none.  Seed 176 exercised one prefix
watchdog timeout in both arms with identical evidence: 28,000 bytes remained
at the deadline, one ACK-clock fallback batch opened and closed, and all
terminal fields were zero.  The mechanism analyzer preserves that deadline
remaining-byte evidence rather than incorrectly requiring it to be zero.

The frozen selector retained K=1.  Adaptive concurrency improved the
greater-than-1-MiB mean point estimate by 4.58%, P95 by 0.31%, and overall mean
by 4.23%, while queue mean was -0.06%, queue P99 was identical, and short-flow
P95 was -0.002%.  However, four seeds were bit-for-bit equivalent in every
reported performance metric; seed 178 alone improved elephant mean by 22.92%
and overall mean by 21.16%.  Consequently, the elephant-mean t95 interval was
[-17.31%, +8.14%] and did not meet the preregistered upper bound.  The rule is
Pareto-improving in this sample but too rarely active in a performance-relevant
state to support a five-seed general-workload claim.

The campaign root is `/tmp/guard-v21-adaptive-elephant-development-v1`
(1,437,416 bytes).  Output IDs, in K=1/adaptive order, are
`863091746/56124498`, `106445833/338019996`,
`127030668/856809039`, `531861057/203053950`, and
`907418717/365437078`.  Artifact SHA-256 values are:

- `summary/general_metrics.csv`:
  `9528c30b6ed8750eab039aa54088f30f4c241627a351e3d68ab2833ad52e585a`.
- `summary/general_report.json`:
  `f6bc9302a8ff0ef2a95fcd1c7d551c2bf51096e17e6ec5234fd1ab9520276182`.
- `summary/mechanism-formal.json`:
  `78053af4267ffb3370120503ad1ee277d6b742190743832cb9b1630d786f8b0c`.
- `summary/selection.json`:
  `23f9fe7c0464c09429aea421b481c535984e46a3d8847fff0b2a92dfedf54a4c`.
