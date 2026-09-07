# V34 cap-triggered serialized vector refresh

V34 isolates a timing mechanism found while diagnosing V24 and V32. A V24 run
with zero spillover allocation vectors still improved one difficult seed. The
improvement was byte-for-byte reproduced by retaining K=1 allocation and only
requesting a serialized receiver-vector refresh when fresh fabric-cap reports
change eligibility or materially change the reported cap. Thus V34 does not
reclaim or redistribute spillover capacity.

The frozen comparison uses AliStorage2019 at 40% load, fresh seeds 235--239,
and four arms: the V32 ACK-slack control, the same control plus cap-triggered
refresh, HPCC, and Homa. Both GUARD arms freeze the same 8320-ns diameter
floor, whole-flow first gate, and 16-packet ACK slack; the only arm difference
is `guard_cap_triggered_refresh=0/1`. Evidence thresholds are fixed to one
fabric-bound report to enter and two unbound reports to exit. Performance is
sealed until all 20 runs complete with identical within-seed traffic, zero
drop/recovery/PFC, closed V14 transactions, no spillover vectors, and positive
candidate cap-report and refresh counters.

The selection gates require candidate improvements over Homa for long-flow
mean FCT, overall mean/P95/P99, small-flow P95, and queue mean/P99. They also
require no material Jain regression beyond 0.01, performance improvements over
HPCC, and non-inferiority plus P95/P99 attribution against the V32 control.
Failure retains V32 and is recorded without changing thresholds or seeds;
success still requires a later independent holdout before a paper claim.

Commands after the V34 implementation, spec, analyzer, selector, tests, and
documentation are committed and the repository is clean:

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v34_cap_triggered_refresh_development.json \
  --campaign-dir /tmp/guard-v34-cap-refresh --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v34_cap_triggered_refresh_development.json \
  --campaign-dir /tmp/guard-v34-cap-refresh --phase admission
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v34-cap-refresh --phase admission
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v34_cap_triggered_refresh_development.json \
  --campaign-dir /tmp/guard-v34-cap-refresh --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v34-cap-refresh --phase formal
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v34-cap-refresh
python3 experiments/select_guard_v34_cap_triggered_refresh.py \
  /tmp/guard-v34-cap-refresh
```

## Frozen result

All 20 runs passed mechanism admission. Candidate refresh requests were
90/98/107/68/106 for seeds 235--239; the control emitted no cap reports or
refreshes, and neither arm emitted a spillover vector. Relative to the V32
control, candidate long-flow mean FCT improved by 0.51% with a paired 95%
interval of [0.25%, 0.77%], and overall mean improved by 0.21%
[0.16%, 0.26%].

Relative to Homa, GUARD improved overall mean FCT by 2.18%
[1.33%, 3.03%], P99 by 3.53% [1.58%, 5.48%], small-flow P95 by 0.60%
[0.54%, 0.66%], queue mean by 32.23% [28.42%, 36.04%], and queue P99 by
29.56% [21.93%, 37.19%]. However, the frozen long-flow mean interval was
-2.22% [-4.60%, +0.16%] and the overall P95 interval was -1.14%
[-2.44%, +0.15%]. Those two Homa gates therefore failed, as did the strict
candidate-versus-control P95, P99, and queue-P99 gates. V34 is rejected by its
pre-registered selector even though most headline metrics improved. No seed
was removed and no threshold was changed after performance was opened.
