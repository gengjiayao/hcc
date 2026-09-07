# V27 initial-window priority development test

V27 tests one mechanism change against the selected K=1 GUARD configuration.
Per-flow matching on the untouched optimized holdout localized GUARD's robust
Homa loss for flows above 1 MiB to same-leaf paths.  GUARD placed those flows
in their static low service class from the first packet, whereas Homa served
its initial unscheduled window at PG3.

The candidate carries a bounded scheduling class in IPv4 DSCP.  Only packets
whose sequence number is in the sender's initial fixed window may use PG3;
the QP then closes that privilege permanently and returns to its static class.
The UDP PG remains immutable and continues to identify the QP and every MMU,
ECN, and PFC accounting operation.  Thus the experiment changes strict
priority service, not the lossless priority identity.  The transition is only
high to low, and retransmission cannot reopen it.

The frozen matrix uses AliStorage2019 at 40% offered load on the 16-host OS4
topology, fresh seeds 205--209, and four arms: selected GUARD, V27, HPCC, and
Homa.  Seed 205 is a mechanism-only admission run.  The remaining seeds may
run only after all four arms complete with identical flow hashes, zero drops,
recovery, timeout, and PFC, and all V14 controller ledgers close.  V27 must
record positive initial-window traffic and exactly one high-to-low transition
for every eligible flow; the control must record no activity.

Performance remains sealed until all 20 runs pass the formal mechanism audit.
The selection script then applies the gates stored in the campaign JSON.  In
particular, V27 must improve the greater-than-1-MiB mean FCT over selected
GUARD, close that metric relative to Homa, retain the existing HPCC latency
advantages, and avoid regressions in short-flow tail, queue occupancy, and
Jain index.  Every seed and failed gate is retained.  Failure selects the old
K=1 configuration and cannot be repaired by changing a seed, threshold, arm,
or metric after performance is opened.

Commands:

```sh
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v27_initial_window_priority_development.json \
  --campaign-dir /tmp/guard-v27-initial-window --phase preflight

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v27_initial_window_priority_development.json \
  --campaign-dir /tmp/guard-v27-initial-window --phase admission

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v27-initial-window --phase admission

python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v27_initial_window_priority_development.json \
  --campaign-dir /tmp/guard-v27-initial-window --phase formal

python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v27-initial-window --phase formal

python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v27-initial-window

python3 experiments/select_guard_v27_initial_window_priority.py \
  /tmp/guard-v27-initial-window
```

## Frozen outcome

The exact simulator revision was `4f18af6`; the preflight and spec SHA-256
values were `fcb4b766cdae60a671fed3cf185dbcd6c64ac41796b0352378cffa547d029581`
and `931227b5f5f973c4255525fed80dd709c11a493000a416568436198830b0bc11`.
All 20 runs passed the
mechanism gate.  V27 recorded one high-to-low transition for every eligible
flow in all five seeds, with no drop, recovery, timeout, or PFC event.

V27 was rejected by the frozen performance gates.  Relative to selected K=1
GUARD, greater-than-1-MiB mean FCT changed by +0.363% (95% CI
[-0.440%, +1.166%]) and overall mean FCT by +0.297% ([+0.070%, +0.525%]).
Relative to Homa, greater-than-1-MiB mean FCT was +0.496%
([-1.891%, +2.882%]) and overall P95 FCT was +1.725%
([+0.189%, +3.261%]).  V27 retained GUARD's substantial latency advantage
over HPCC and its queue advantage over Homa, but the isolated service-class
change did not close the Homa long-flow gap.  The complete retained campaign
is `/tmp/guard-v27-initial-window`; no seed or threshold was changed.
