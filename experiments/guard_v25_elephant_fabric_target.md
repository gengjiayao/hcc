# V25 class-scoped elephant fabric target

V24 established that receiver-share spillover cannot be a general fix: in its
two failed seeds, almost every canonical allocation had fewer than two
receiver-local elephants, and every remaining pair lacked a qualified donor.
V25 therefore leaves receiver allocation unchanged and targets the persistent
shared-fabric constraint.

The baseline GUARD target is `0.95 * 1.8 = 1.71`. V25 scales that target by
`11/9` only when the sender's advertised flow size is strictly greater than
12 exact path BDPs, yielding `2.09`, the same effective target as the earlier
lambda-2.2 candidate. Flows at or below the boundary retain 1.71. Receiver
K=1 ordering, the 100-Mb/s deferred floor, canonical `C-D` target vectors,
short-flow bypass, and every other final-bundle mechanism remain unchanged.
The feature is disabled by default and is mutually exclusive with adaptive
fabric targeting, adaptive elephant concurrency, aging, and cap spillover.

The scale is not selected from this experiment. The earlier frozen global
lambda sweep supplied the signal: lambda 2.2 improved the greater-than-1-MiB
mean point estimate by 7.14%, but globally changing every flow produced a wide
interval. V25 scopes that already tested target to the flow class with the
measured Homa gap. Fresh seeds 195--199, exact scope, resource limits,
mechanism requirements, and paired gates are frozen in
`campaigns/guard_v25_elephant_fabric_target_development.json`.

All ten mechanism cells must pass before any FCT or queue file is read. The
candidate must execute the class-scoped target in every seed. It advances only
if the upper endpoint of the paired 95% interval improves greater-than-1-MiB
mean FCT by at least 5.5%, while greater-than-1-MiB P95, overall mean, short
P95, mean/P99 queue, and Jain guardrails all pass. Advancement authorizes a
fresh GUARD/HPCC/Homa holdout; this development result is not a paper claim.

## Frozen outcome

V25 is rejected by the frozen paired-performance gates. The exact simulator
revision was `6ad2d20429f8f4287d6fce4323b40cfee7f10c43`. All ten runs
passed the mechanism gate with every flow complete, zero drop/recovery/PFC,
and closed protocol state. The candidate exercised 299--418 scoped updates in
every seed and recorded the exact effective target 2.09, so this is a policy
result rather than an inactive feature.

The greater-than-1-MiB mean FCT point estimate improved by 1.21%, but its
paired t95 interval was [-4.04%, +1.61%], far short of the preregistered -5.5%
upper endpoint. Greater-than-1-MiB P95 was -0.18% [-0.50%, +0.14%] and overall
mean was -0.29% [-0.94%, +0.36%]. Short-flow P95 and Jain passed their guards.
Mean queue rose 1.52% [-0.47%, +3.52%], while P99 queue rose 1.57%
[-2.09%, +5.22%] and narrowly missed its +5% ceiling. Thus class-scoping the
previous lambda-2.2 signal is safe in this sample but too weak to close the
measured Homa elephant gap.

The campaign root is `/tmp/guard-v25-elephant-target-development-v1`.
Artifact SHA-256 values are: preflight
`612a86fdda8807c4111538306b2d7335c1678ecaacd8e6aa86b0f22f705b5d66`,
mechanism report
`58a285be761cd9441f9f373fe4f2f1602c586cc0ff0b0ae23f4f97899c9da70e`,
performance report
`0cd1a4a7b94a77177899b90adc4f595421d102601213b05b494e5896ce30d2ac`,
and selection
`5ef95878de35916eca40931b04bdfa5a5c89f7544bac42afae467ba7b8c86493`.

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v25_elephant_fabric_target_development.json \
  --campaign-dir /tmp/guard-v25-elephant-target --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v25_elephant_fabric_target_development.json \
  --campaign-dir /tmp/guard-v25-elephant-target --phase admission
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v25-elephant-target --phase admission
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v25_elephant_fabric_target_development.json \
  --campaign-dir /tmp/guard-v25-elephant-target --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v25-elephant-target --phase formal
# Run these only after the formal mechanism gate admits 10/10 cells.
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v25-elephant-target
python3 experiments/select_guard_v25_elephant_fabric_target.py \
  /tmp/guard-v25-elephant-target
```
