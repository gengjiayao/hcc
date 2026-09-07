# V19 frozen bounded-elephant service

V18 established a safe canonical receiver vector but did not carry forward
the bounded-elephant policy selected before the V14 state-machine rewrite.
Consequently, every registered elephant participated in inverse-remaining
weighting.  The V18 development run exposed receiver-local late elephant
clusters with multi-millisecond outliers even though short-flow latency and
queue containment remained strong.

V19 changes only canonical target construction.  An elephant is a registered
flow whose advertised total size is strictly greater than 12 path BDPs.  Every
non-elephant remains in the residual service set, while only the shortest
remaining elephant (K=1) joins that set.  Other elephants retain the exact
sender-enforced 100-Mb/s floor.  Residual capacity is distributed by the
existing inverse-remaining weight.  Selection ties use the complete flow
identity, so an unordered container cannot alter the vector.

The policy runs only inside the complete V18 frozen-vector, serialized
refresh/draining, and capacity-admission bundle.  Requested targets retain a
floor for every active record and sum to at most `C-D`; versioned prepare and
activation barriers are unchanged.  The existing bounded-concurrency counters
now count canonical-vector allocations as well as the legacy allocator.

The frozen development campaign is
`experiments/campaigns/guard_v19_ali50_development.json`: AliStorage2019 at 50%
load, fresh seeds 165--169, and matched GUARD/HPCC/Homa arms.  Seed 165 is a
mechanism-only admission run.  Performance remains sealed until all 15 cells
complete with zero drop/recovery/PFC, every V18 capacity deferral closes, and
the K=1/T=12 policy records at least one limited canonical allocation.

## Frozen result

The campaign completed and admitted all 15 runs.  Every flow completed, all
drop/recovery/timeout/PFC counters were zero, and the V18 capacity-admission
deferral/resume counts closed at 7/7, 8/8, 8/8, 9/9, and 8/8.  Canonical
bounded service triggered in every GUARD run: limited allocations were
299, 281, 232, 221, and 213, while the maximum deferred-elephant count was
two in each seed.

Against HPCC, GUARD reduced overall mean, P95, and P99 FCT by 11.71%, 22.33%,
and 14.16%, respectively; the greater-than-1-MiB mean fell by 10.91%.  Against
Homa, GUARD retained lower queue occupancy (mean -49.00%, P99 -44.26%) and
lower at-most-8-KiB mean/P95/P99 FCT (-0.51%/-0.85%/-1.24%), but overall mean
FCT rose 1.62% [1.04%, 2.20%] and greater-than-1-MiB mean FCT rose 5.17%
[3.74%, 6.59%].  Thus V19 removes V18's catastrophic long-flow outliers and
beats HPCC broadly, but it does not beat Homa on elephant latency.  All five
per-seed elephant-mean differences have the same unfavorable sign, motivating
a fresh K=1 versus K=2 development experiment rather than a claim.

The bounded campaign root is `/tmp/guard-v19-ali50-development-v1` (1,904,685
bytes).  Its portable artifacts are:

- `summary/general_metrics.csv`: SHA-256
  `4f760436c11258e0af39596bbbba07a2d0efb3485875a75098e76e4e0451d562`.
- `summary/general_report.json`: SHA-256
  `66f45b706d147c6219f4e1776abd1c0a2ece4f5253a91209564490ece41e15ab`.
- `summary/mechanism-formal.json`: SHA-256
  `f01a3308d318a04199e6c7fabab96a43f53a58f20353339dea1abecc194c5ab1`.

The output IDs, in seed order and GUARD/HPCC/Homa order, are
`615404688/238648048/771845348`, `785728264/1228282/349861074`,
`248059485/405730490/311512229`, `990090194/79511334/227044926`, and
`957443304/509555681/455702297`.
