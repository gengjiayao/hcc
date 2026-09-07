# V29 post-grant topology-diameter window

V29 removes the unsupported part of V28 while preserving its measured steady
transport benefit.  V28 raised both the sender's first-grant gate and its
steady congestion window from the exact path BDP to the topology-diameter
BDP.  It beat Homa and HPCC on the frozen long-flow, overall-mean, P99, and
queue metrics, but it increased queue occupancy and reduced Jain fairness
relative to K=1 GUARD.

A no-contention component diagnostic kept the first-grant gate at the exact
path BDP and raised only the post-grant steady window.  Its four FCT values
were byte-identical to V28: same-leaf 2/8-MiB flows completed in
186.563/733.916 us and cross-leaf flows in 190.751/738.104 us.  Homa required
188.663/742.307 us and 192.857/746.501 us, respectively.  The extra pre-grant
burst therefore supplied no measured benefit in that causal test.

V29 stores separate first-grant and steady transport windows in every GUARD
QP.  The first-grant gate and its receiver-side packet tag retain the exact
path BDP.  Once a valid grant arrives, the ordinary fixed window uses
`max(path BDP, topology-diameter BDP)`.  Registration, one-RTT and tail
bypass, size priority, remaining-aware allocation, and receiver concurrency
continue to use the original path RTT/BDP.  The mode is default-off, requires
a positive checked transport floor, and reuses V28's bounded activity
counters plus an explicit after-first-grant scope record.

The frozen development matrix uses AliStorage2019 at 40% offered load on the
16-host OS4 topology, fresh seeds 215--219, and four arms: selected K=1 GUARD,
V29, HPCC, and Homa.  The two GUARD arms differ only in the 8320-ns floor and
the post-grant scope bit.  Seed 215 is a mechanism-only admission run.
Performance remains sealed until all 20 runs pass completion, identical-flow,
zero drop/recovery/timeout/PFC, V14 controller closure, and exact V29 scope
checks.

The performance gates are intentionally not relaxed from V28.  V29 must
improve greater-than-1-MiB mean FCT over K=1 by at least 1% at the upper
confidence bound; avoid overall, short-flow, queue, and Jain regressions; beat
Homa on every frozen latency and queue metric; and retain every HPCC latency
advantage.  Passing only nominates V29 for an independent holdout.  Any failed
gate retains K=1, with every seed and result preserved.

Commands:

```sh
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v29_post_grant_window_floor_development.json \
  --campaign-dir /tmp/guard-v29-post-grant-window --phase preflight

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v29_post_grant_window_floor_development.json \
  --campaign-dir /tmp/guard-v29-post-grant-window --phase admission

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v29-post-grant-window --phase admission

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v29_post_grant_window_floor_development.json \
  --campaign-dir /tmp/guard-v29-post-grant-window --phase formal

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v29-post-grant-window --phase formal

python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v29-post-grant-window

python3 experiments/select_guard_v29_post_grant_window_floor.py \
  /tmp/guard-v29-post-grant-window
```

## Frozen outcome

The exact simulator revision was `a658d94`.  The frozen spec and preflight
SHA-256 values were
`40802972447eb6a392efa1a93e9b687e1b5a918d38b294dcb85d70d59f60b75c`
and
`4427c2266416824d99d04585460d56e5b5e645587364a2f753096c4325bf7a98`.
All 20 runs passed mechanism admission with matched flow hashes, complete flow
sets, zero drop/recovery/timeout/PFC, and closed controller state.  Candidate
runs raised 4564--4630 steady windows to exactly 104000 bytes and recorded
after-first-grant scope; controls recorded no floor activity.

V29 was rejected.  Relative to K=1, it improved greater-than-1-MiB mean FCT
by 2.23% (95% CI [1.82%, 2.65%]) and overall mean by 0.79%
([0.55%, 1.03%]).  However, mean queue increased 13.25%, the P99-queue upper
confidence bound was +20.15%, and the goodput Jain index fell by 0.00835.
These costs were smaller than V28's, confirming that removing the long-flow
pre-grant burst helped, but they still exceeded the frozen internal gates.

Relative to Homa, V29 improved greater-than-1-MiB mean FCT by 1.97%
([0.03%, 3.91%]), overall mean by 2.10% ([1.50%, 2.70%]), P99 by 3.95%
([0.08%, 7.82%]), mean queue by 30.67% ([26.31%, 35.03%]), and P99 queue by
34.58% ([17.40%, 51.76%]).  Overall P95 changed by -0.62% with a 95% CI of
[-1.64%, +0.40%], so the zero-upper-bound Homa gate still failed.  V29 also
beat HPCC on every frozen latency metric.  The full retained campaign is
`/tmp/guard-v29-post-grant-window`; the selector retained K=1 without any
post-hoc seed, threshold, arm, or metric change.
