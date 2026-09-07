# V23 cap-qualified elephant spillover

V22 showed that time-based replacement destroys the remaining-size order. V23
therefore keeps the shortest greater-than-12-path-BDP elephant selected. A
second elephant receives residual capacity only when three consecutive sender
reports identify the selected flow as shared-fabric bound and at least 5% below
its receiver grant. The receiver preserves 1.1 times the largest reported cap
in that streak and transfers only the difference; the frozen target vector
still sums to at most `C-D`.

The feature is disabled by default and is mutually exclusive with adaptive
elephant concurrency, V22 aging, legacy work-conserving reclaim, and legacy
cap-aware reclaim. Every cap change enters the serialized V14 vector
coordinator. An unbound report restores the K=1 allocation; the selected flow
is never replaced.

The development matrix, seeds 185--189, mechanism requirements, resource caps,
and paired gates were frozen in
`campaigns/guard_v23_elephant_spillover_development.json` before execution.
Mechanism analysis must pass all ten cells before any FCT or queue file is read.
The candidate advances only if its paired 95% interval improves greater-than-
1MB mean FCT by at least 5.5% while satisfying every short-flow, queue, tail,
and Jain guardrail. Advancement authorizes a fresh three-arm GUARD/HPCC/Homa
holdout; the development result itself is not a paper claim.

## Frozen outcome

V23 is rejected at the mechanism-replication gate. The exact clean simulator
revision was `c499ab3a370f0797a872a66f97758f764cbc121e`. All ten runs completed with
zero drop, recovery, timeout, and PFC activity. Both arms passed the base V18
vector/ACK closure checks. The spillover arm sent and received cap reports in
all five seeds and issued 4--13 serialized capacity-refresh requests, but an
actual spillover vector formed only in seeds 185 and 188 (1 and 6 vectors).
Seeds 186, 187, and 189 formed zero vectors, so the frozen requirement that the
candidate mechanism execute in every seed failed. Performance remained sealed:
no FCT or queue artifact was read and no selection result was produced.

The admitted/rejected raw campaign is
`/tmp/guard-v23-elephant-spillover-development-v5`. Its preflight SHA-256 is
`a5214e9b639cad3f60469916d6c90ccce8891248c47e278e8bfcc1695f689c49`; the
mechanism report SHA-256 is
`9f9580bf8e468e0adc5b18dfaa7ddba0d42bbb8f48628e46bb326a69e8ae72b5`.
The failure indicates that three-report confirmation frequently outlives the
flow's tenure as the current K=1 donor. A successor must use fresh seeds and
may reduce confirmation latency, but must retain the same conservative vector
budget and performance seal.

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v23_elephant_spillover_development.json \
  --campaign-dir /tmp/guard-v23-elephant-spillover --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v23_elephant_spillover_development.json \
  --campaign-dir /tmp/guard-v23-elephant-spillover --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v23-elephant-spillover --phase formal
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v23-elephant-spillover
python3 experiments/select_guard_v23_elephant_spillover.py \
  /tmp/guard-v23-elephant-spillover
```
