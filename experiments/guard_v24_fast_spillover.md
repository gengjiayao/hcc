# V24 fast-enter, slow-exit cap spillover

V23 preserved the K=1 remaining-size order and the canonical `C-D` vector
budget, but its three-report entry rule formed an actual spillover vector in
only two of five seeds. V24 changes only the report-state timing: the first
fresh, shared-fabric-bound report at least 5% below the receiver grant enters
spillover, while two consecutive fresh unbound reports leave it. The donor
still retains 1.1 times the largest applicable reported cap, and only the
unused share moves to the second-shortest greater-than-12-path-BDP elephant.

The optimization is disabled by default. It remains mutually exclusive with
adaptive elephant concurrency, time-based aging, legacy work-conserving
reclaim, and legacy cap-aware reclaim. Every material eligibility or cap
change enters the serialized V14 capacity-vector coordinator. V24 never
replaces the shortest elephant and never increases the receiver's frozen
allocation budget.

The paired development matrix, seeds 190--194, entry/exit thresholds, resource
caps, mechanism gates, and performance gates were frozen in
`campaigns/guard_v24_fast_spillover_development.json` before execution. All ten
mechanism cells must pass before the analysis may read FCT or queue artifacts.
The candidate advances only if the upper endpoint of its paired 95% interval
improves greater-than-1MB mean FCT by at least 5.5%, while all predeclared tail,
short-flow, queue, and Jain guardrails also pass. Advancement authorizes a
fresh three-arm GUARD/HPCC/Homa holdout; this development matrix is not itself
a paper result.

## Frozen outcome

V24 is rejected at the mechanism-replication gate. The exact clean simulator
revision was `e2cf0a2be64eb347075841267184715fe5a4b521`. All ten runs
completed with zero drop, recovery, timeout, and PFC activity, and every base
V18/V14 transaction closed. The spillover arm sent 2,697--3,338 cap reports
per seed and requested 8--22 serialized capacity refreshes. Actual spillover
vectors nevertheless formed only in seeds 190, 193, and 194: their counts were
11, 4, and 5, while seeds 191 and 192 formed zero vectors. The frozen 10/10
mechanism gate therefore admitted only 8/10 cells. Performance remained
sealed; no FCT or queue artifact was read and no selection was performed.

The rejected campaign is
`/tmp/guard-v24-fast-spillover-development-v1`. Its preflight SHA-256 is
`386932e61b86e0b8b13c18d3f50fa416cedb3f161ab67129f74d7c212de0299d`;
the formal mechanism report SHA-256 is
`e55aa16620b5beff49bf56b31ec4d5325867f5557e91f21f397a89c8671c669e`.
Fast entry fixed the V23 confirmation-latency failure, but it did not ensure a
second elephant existed while the qualified donor held reclaimable receiver
share. A mechanism-only diagnostic replay at clean revision `1eafde4` showed
that 2,157/2,300 and 2,438/2,612 allocator calls in failed seeds 191 and 192
had fewer than two elephants. Every remaining two-elephant call saw an
inactive donor (143/143 and 174/174); none saw a qualified non-donor, a stale
donor, or a donor cap at or above its target. Thus the zero vectors are not a
confirmation-count bug: cap qualification and receiver-local elephant overlap
simply do not coincide in those seeds. Changing entry/exit counts again is not
justified. The diagnostic campaign
`/tmp/guard-v24-spillover-diagnostics-v1` remained mechanism-only and did not
read performance artifacts.

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v24_fast_spillover_development.json \
  --campaign-dir /tmp/guard-v24-fast-spillover --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v24_fast_spillover_development.json \
  --campaign-dir /tmp/guard-v24-fast-spillover --phase admission
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v24-fast-spillover --phase admission
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v24_fast_spillover_development.json \
  --campaign-dir /tmp/guard-v24-fast-spillover --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v24-fast-spillover --phase formal
# The next two commands are permitted only after mechanism-formal passes 10/10.
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v24-fast-spillover
python3 experiments/select_guard_v24_fast_spillover.py \
  /tmp/guard-v24-fast-spillover
```
