# V28 topology-diameter transport-window floor

V28 tests one mechanism change against the selected K=1 GUARD
configuration.  A four-flow, no-contention diagnostic separated same-leaf
from cross-leaf paths at 2 MiB and 8 MiB.  Selected GUARD was slower than Homa
on both same-leaf flows, whereas temporarily using the existing global-window
mode made those flows faster than Homa and left both cross-leaf FCTs
byte-identical.  Doubling the grant-refresh interval and tracing both GUARD
caps did not change FCT, ruling out receiver refresh frequency and rate
binding in this diagnostic.

The existing global-window mode is not the candidate because it also changes
the base RTT used by flow classification.  V28 instead adds a GUARD-only
transport-window floor.  The sender's congestion window and exact
first-grant gate use
`max(path BDP, receiver-link-rate times topology-diameter RTT)`.  The frozen
16-host topology has a maximum base RTT of 8320 ns and a 100-Gb/s receiver
link, so the floor is exactly 104000 bytes.  The original path RTT and BDP
remain unchanged for selective registration, one-RTT bypass, tail bypass,
size priority, remaining-aware allocation, and receiver concurrency.

The enlarged first-grant prefix is carried explicitly to the receiver by the
existing exact gate tag.  The transition-prefix barrier therefore observes
the same byte frontier enforced at the sender; the floor cannot silently
release a sender before the receiver has accounted for that prefix.  The
feature is default-off and requires fixed-window GUARD with the exact prefix
barrier.  Checked 128-bit arithmetic derives the floor from configured time
and line rate.  Runtime counters expose the number of raised flows, aggregate
extra bytes, and maximum transport window.

The frozen development matrix uses AliStorage2019 at 40% offered load on the
16-host OS4 topology, fresh seeds 210--214, and four arms: selected K=1 GUARD,
V28, HPCC, and Homa.  The two GUARD arms differ only in the 0-ns versus
8320-ns floor.  Seed 210 is the mechanism-only admission run.  Performance
remains sealed until all 20 runs complete with matched flow hashes, zero
drop/recovery/timeout/PFC, closed V14 controller ledgers, and exact V28
counters.  V28 must raise at least one flow and report a 104000-byte maximum;
the control must report zero activity.

Only after 20/20 mechanism admission may the selector read performance.  The
candidate must improve greater-than-1-MiB mean FCT over K=1 by at least 1% at
the upper confidence bound, avoid overall and short-flow regressions, retain
the existing HPCC advantages, and beat Homa on every frozen latency and queue
metric.  Every seed and every failed gate is retained.  Passing selects V28
only for a later independent holdout; failure retains K=1.

Commands:

```sh
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v28_transport_window_floor_development.json \
  --campaign-dir /tmp/guard-v28-transport-window --phase preflight

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v28_transport_window_floor_development.json \
  --campaign-dir /tmp/guard-v28-transport-window --phase admission

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v28-transport-window --phase admission

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v28_transport_window_floor_development.json \
  --campaign-dir /tmp/guard-v28-transport-window --phase formal

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v28-transport-window --phase formal

python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v28-transport-window

python3 experiments/select_guard_v28_transport_window_floor.py \
  /tmp/guard-v28-transport-window
```

## Frozen outcome

The exact simulator revision was `742952f`.  The frozen spec and preflight
SHA-256 values were
`42a9485516d7a91d047aaacf059a3e483f9a250f08ec2edcd8c74a7afb37aeaf`
and
`af6eb3ffcd706c568ee512f552ddbd667e9bdec58ca3c1bd2310220f1040fd96`.
All 20 runs passed the mechanism gate with identical per-seed flow hashes,
complete flow sets, zero drop/recovery/timeout/PFC, and closed controller
state.  Across the five candidate runs, the floor raised 4565--4706 flows and
the maximum transport window was exactly 104000 bytes; every control counter
was zero.

V28 was rejected by the complete frozen gate set.  It improved
greater-than-1-MiB mean FCT over K=1 GUARD by 3.03% (95% CI
[2.15%, 3.91%]) and overall mean FCT by 1.22% ([1.00%, 1.43%]).  Relative to
Homa, it improved greater-than-1-MiB mean FCT by 3.70%
([2.29%, 5.10%]), overall mean by 2.83% ([2.05%, 3.61%]), P99 by 4.00%
([2.66%, 5.33%]), mean queue by 27.70% ([24.28%, 31.12%]), and P99 queue by
33.26% ([18.26%, 48.26%]).  It also beat HPCC on every frozen latency metric.

Four gates still failed.  Relative to K=1, mean and P99 queue increased by
18.38% and 10.61%, and the goodput Jain index fell by 0.0104.  Relative to
Homa, every seed had a lower overall P95 FCT, but the five-seed paired estimate
was -2.40% with a 95% CI of [-5.25%, +0.45%], so it did not meet the frozen
zero-upper-bound rule.  The selector therefore retained K=1.  The full
campaign is `/tmp/guard-v28-transport-window`; no seed, arm, threshold, or
metric was changed after performance was opened.
