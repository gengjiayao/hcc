# GUARD V22 fixed-K elephant aging

V22 is a development-only attempt to remove the long-flow starvation visible
in the V19/V21 AliStorage50 diagnostics without reopening the fixed K=2 queue
cost measured by V20.  It does not authorize a paper claim by itself.

## Frozen mechanism

- Keep the final GUARD receiver concurrency at K=1.
- Classify elephants with the unchanged strict `size > 12 path BDP` rule.
- An elephant deferred for two of its own base RTTs replaces the shortest
  elephant for one canonical frozen-vector quantum.  The displaced elephant
  begins its own wait at that boundary.
- The selected set still contains exactly one elephant.  Every other elephant
  keeps the encoded 100-Mb/s floor, so the existing `D + A <= C` target-vector
  and prepare/activation barriers remain unchanged.
- `guard_elephant_aging_rtts=0` is the default.  Aging is mutually exclusive
  with adaptive elephant concurrency and is accepted only with the complete
  V18 canonical-vector, serialized-refresh/draining, and capacity-admission
  bundle.

The two-RTT value, seeds 180--184, arms, resource bounds, and statistical gates
are frozen in `campaigns/guard_v22_elephant_aging_development.json` before any
V22 simulator run.  V19/V21 performance did not choose this value; the value is
a mechanism-derived starvation bound.

## Execution and evidence order

1. Commit a clean simulator revision and preflight all five traffic inputs.
2. Run seed 180 for `guard_k1` and `guard_aging` only.
3. Apply the mechanism-only admission gate.  It reads completion, config,
   safety, protocol, and bounded-control statistics, but no FCT or queue file.
4. If seed 180 passes, run seeds 181--184 serially.  Keep all ten cells.
5. Re-run the mechanism gate across 10/10 cells.  Performance remains sealed
   unless every cell completes with zero drop/recovery/PFC, closed V14 state,
   and exact arm behavior.  The aging arm must rotate at least once and finish
   with zero active deferred state; the baseline must report zero aging.
6. Only then summarize paired five-seed performance and apply
   `select_guard_v22_elephant_aging.py` once.

The selection gate requires significant improvement in greater-than-1-MiB
mean FCT, no regression in its P95 or overall mean, at most 0.5% short-flow P95
cost, at most 5% mean/P99 queue cost, and no material Jain regression.  Failure
retains K=1.  Passing selects V22 only for a fresh GUARD/HPCC/Homa holdout.

## Reproduction

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v22_elephant_aging_development.json \
  --campaign-dir /tmp/guard-v22-elephant-aging-development-v1 --phase preflight

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v22_elephant_aging_development.json \
  --campaign-dir /tmp/guard-v22-elephant-aging-development-v1 --phase admission
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v22-elephant-aging-development-v1 --phase admission

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v22_elephant_aging_development.json \
  --campaign-dir /tmp/guard-v22-elephant-aging-development-v1 --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v22-elephant-aging-development-v1 --phase formal
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v22-elephant-aging-development-v1
python3 experiments/select_guard_v22_elephant_aging.py \
  /tmp/guard-v22-elephant-aging-development-v1
```

## Frozen outcome

The campaign at `/tmp/guard-v22-elephant-aging-development-v1` completed all
10 runs and passed the mechanism gate.  Each run completed all flows with zero
drop, recovery, timeout, and PFC activity.  The aging arm recorded 178--373
rotations per seed and zero terminal deferred state, so this is a policy result
rather than a failure to exercise the mechanism.

The selector retained K=1.  Aging improved the standard all-flow goodput Jain
index by 0.00852 with paired t95 CI [0.00277, 0.01427], and its point estimates
reduced mean queue by 9.44% and P99 queue by 13.72%.  Those queue confidence
intervals crossed the frozen +5% ceiling.  More importantly, aging made the
greater-than-1-MiB mean FCT worse in four of five seeds.  Its paired mean
percentage was +1123.51% with t95 CI [-735.83%, +2982.84%]; overall mean FCT
was +370.77% [-253.85%, +995.40%].  Seed 180 improved, while seeds 181--184
regressed, demonstrating high sensitivity rather than a stable benefit.

The result rejects periodic time-based replacement as the next GUARD policy.
V22 must not be used as the final GUARD configuration and does not authorize a
three-arm holdout.  Its artifacts are bound by these SHA-256 values:

- preflight: `758bb11da253c5635bd008b40cc7012cf827b6fe2b225c742780435740e30c6c`
- formal mechanism gate: `dc00e8e15fec554323c668c54693f5fc5740c4155ed5b96243c0eaf51e130d56`
- performance report: `4e625b6749cb2abed98cc64083356db74380c114067c42f3a80ccc23170ab686`
