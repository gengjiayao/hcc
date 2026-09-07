# V20 canonical elephant-concurrency development search

V19 consistently reduced queue occupancy and short-flow latency relative to
Homa, but K=1 serialized receiver-local elephant clusters enough to leave
greater-than-1-MiB mean FCT 5.17% higher.  V20 changes exactly one value: the
number of shortest-remaining elephants admitted to residual service.  It
compares the V19 baseline K=1 with K=2 at the same 12-path-BDP threshold.

The spec freezes AliStorage2019 at 50% load, seeds 170--174, and matched flow
files.  Seed 170 is admitted without opening FCT or queue artifacts.  Both arms
must complete all flows with zero drop, recovery, timeout, and PFC activity;
all V14/V18 transactions and capacity deferrals must close; and each arm must
record a positive number of bounded allocations and deferred elephants.  The
remaining four seeds may run only after that gate passes.  Performance remains
sealed until all ten cells pass the same mechanism gate.

K=2 is selected only if the paired t95 upper bound is at most -1% for
greater-than-1-MiB mean FCT, at most zero for its P95 and overall mean FCT, at
most +0.5% for at-most-8-KiB P95 FCT, and at most +5% for queue mean and P99.
The lower bound of the absolute Jain-index difference must also be at least
-0.005.  A selected result is development evidence only and authorizes a new
three-arm GUARD/HPCC/Homa holdout; it is not itself a baseline claim.

## Frozen result

All ten cells passed the mechanism gate.  Every flow completed with zero
drop/recovery/timeout/PFC activity.  Capacity deferrals equaled resumes in
every cell, and both K values triggered bounded allocation.  K=1 recorded
144/491/262/449/288 limited allocations with maximum deferred counts
2/3/2/6/2; K=2 recorded 23/89/18/163/36 with maxima 1/2/1/3/1.

K=2 was rejected by the frozen selection rule.  Its point estimates improved
greater-than-1-MiB mean FCT by 11.59%, P95 by 26.45%, and overall mean FCT by
9.50%, but their paired t95 upper bounds were +29.43%, +24.45%, and +24.82%.
The apparent mean improvement came entirely from seed 173, where K=1 exposed
a severe seven-elephant cohort; K=2 was slower for elephant mean FCT in the
other four seeds.  K=2 also increased queue mean by 13.74% [6.99%, 20.50%]
and queue P99 by 7.69% [-2.86%, 18.25%].  It passed only the short-flow P95 and
Jain constraints.  The selected arm therefore remains K=1.

This result motivates a workload-independent adaptive rule rather than a
fixed K=2: retain one elephant for ordinary cohorts and expose a second only
when the elephant backlog is large.  Any such rule requires fresh seeds and a
new preregistration; V20 is not reinterpreted.

The campaign root is `/tmp/guard-v20-elephant-k-development-v1` (1,432,085
bytes).  Seeds 170--174 used flow hashes beginning `2e1eabaa`, `aa46cd89`,
`ae398847`, `8a73ccf2`, and `2884c696`.  Output IDs, in K=1/K=2 order, are
`457394167/815951132`, `200669461/323920446`,
`574538517/708065042`, `256359322/568800316`, and
`98066204/507472991`.  Artifact SHA-256 values are:

- `summary/general_metrics.csv`:
  `9224115e85430690b1e4c8f5b0c2e2f40b23dff300c86c94deec6a3a296b6256`.
- `summary/general_report.json`:
  `0ced6bf9fd139e43123ed6468a63cd9e481a78124bfa585d945fd788ce0b38ed`.
- `summary/mechanism-formal.json`:
  `df591a52cce47e049cc755cd38be1672d1f99a86229b03257d9964987b5aaef1`.
- `summary/selection.json`:
  `3094b32fd84fa0558472990d7b4b44d6e2623fe9d469354dc04c8d96673bc54c`.
