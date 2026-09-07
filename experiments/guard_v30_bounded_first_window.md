# V30 bounded whole-flow first window

V30 combines the independently useful parts of V28 and V29 without giving a
long flow extra pre-grant burst.  Every GUARD flow uses the 104000-byte
topology-diameter steady window after its first grant.  Before that grant, a
flow above 104000 bytes remains limited to its exact path BDP.  Only a flow
whose entire payload fits in 104000 bytes may use the topology-diameter first
window.  The extra pre-feedback injection is therefore bounded by the whole
flow and cannot continue as an uncontrolled long-flow train.

The mechanism follows two retained diagnostics.  For isolated 2/8-MiB flows,
the V29 exact first gate preserved every V28 speedup and beat Homa on both
same- and cross-leaf paths.  For a seven-way same-leaf incast of 65536-byte
flows, the bounded whole-flow gate reduced the maximum FCT from V29's
48.082 us to 44.042 us, below Homa's 44.502 us.  It also narrowed the seven
GUARD completions instead of changing any receiver grant or fabric-cap rule.

The feature is default-off and requires the already checked positive
post-grant transport floor.  Runtime evidence reports the scope bit and the
number of same-leaf flows whose entire payload used the raised first gate.
The exact first-gate value remains in the packet tag consumed by the V14
prefix barrier.  All registration, tail, priority, and concurrency thresholds
still use the original path RTT/BDP.

The frozen matrix uses AliStorage2019 at 40% offered load on the 16-host OS4
topology, fresh seeds 220--224, and K=1 GUARD, V30, HPCC, and Homa arms.  Seed
220 is mechanism-only admission.  Performance remains sealed until all 20
runs pass matched-flow, completion, zero drop/recovery/timeout/PFC, controller
closure, and exact V30-scope gates.  The V28/V29 performance thresholds are
unchanged: V30 must improve long-flow mean over K=1, avoid internal
latency/queue/Jain regressions, beat Homa on every frozen metric, and retain
all HPCC latency advantages.  Passing nominates V30 for an independent
holdout; failure retains K=1 without removing a seed or changing a gate.

The reproducible command sequence is the same as V29 with the spec
`experiments/campaigns/guard_v30_bounded_first_window_development.json`, the
campaign directory `/tmp/guard-v30-bounded-first-window`, and selector
`experiments/select_guard_v30_bounded_first_window.py`.

## Frozen outcome

The exact simulator revision was `7ece0fa`.  The frozen spec and preflight
SHA-256 values were
`451eecf17c47d785f8aeda2f6baffcad0da8ecedf1be029a2017640c6ceae895`
and
`d497816af06d53bb840b0d3e86c0354efe4305ad0931851744b8a7f6df42bf5b`.
All 20 runs passed mechanism admission, with matched traces, complete flows,
zero drop/recovery/timeout/PFC, and closed controller state.

V30 was rejected by three internal gates and one Homa gate.  Relative to K=1,
it improved long-flow mean FCT by 2.02% (95% CI [1.86%, 2.17%]) and overall
mean by 0.98% ([0.83%, 1.12%]), but mean queue rose 10.65% and Jain fell by
0.0094.  P99 queue, unlike V28/V29, passed its frozen +5% gate.

V30 produced the strongest Homa comparison so far: overall mean, P95, and P99
FCT improved by 3.52%, 3.21%, and 5.99%, while mean and P99 queue improved by
34.00% and 26.58%; every confidence interval excluded zero.  The long-flow
mean point estimate improved by 2.70%, but its 95% CI was
[-5.50%, +0.10%], narrowly missing the zero-upper-bound gate.  All HPCC gates
passed.  The campaign remains at `/tmp/guard-v30-bounded-first-window`, and
the selector retained K=1 without any post-hoc change.
