# V26 selected-elephant receiver authority

V25 showed that a class-scoped shared-fabric target of 2.09 is safe in the
fresh sample but changes greater-than-1-MiB mean FCT by only -1.21%, with a
paired 95% interval crossing zero. V26 therefore does not increase the target
again. It tests whether the receiver's canonical selected-elephant decision
should be authoritative for that selected flow.

The candidate changes one rate-composition rule. A flow qualifies only after
all of the following are true: its advertised size is strictly greater than
12 exact path BDPs; the sender has applied a real nonzero-generation receiver
grant; that grant is strictly above the 100-Mb/s deferred floor; and the flow
is not already using the independent tail-bypass path. A qualified flow follows
its receiver grant instead of the smaller of the receiver grant and HPCC rate.
Deferred elephants, flows at or below 12 BDPs, tail-bypass flows, receiver
K=1 ordering, canonical `C-D` target vectors, and all other controls retain the
V25 baseline semantics. The feature is disabled by default and mutually
exclusive with the prior adaptive-target, class-target, concurrency, aging,
and spillover candidates.

This rule intentionally weakens shared-fabric rate composition for one
receiver-selected elephant. It is therefore a hypothesis, not a safety claim:
the frozen experiment requires zero drop, recovery, timeout, and PFC in every
cell, while mean/P99 queue may increase by at most 5% at the upper endpoint of
the paired 95% interval. These gates protect against obtaining lower FCT by
silently moving congestion into the fabric.

Fresh seeds 200--204, all controls, and all selection gates are frozen in
`campaigns/guard_v26_elephant_receiver_authority_development.json`. All ten
mechanism cells must pass before any FCT or queue artifact is read. The
candidate must record authority bindings, actual rate changes, and positive
released bandwidth in every seed; the baseline must record zero for each.
The performance gates are unchanged from V25: the greater-than-1-MiB mean-FCT
paired interval must end at or below -5.5%, its P95 and overall mean must not
regress, short-flow P95 may regress by at most 0.5%, queue mean/P99 by at most
5%, and the Jain-index difference may not fall below -0.005. Passing only
authorizes a fresh three-arm GUARD/HPCC/Homa holdout.

```bash
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v26_elephant_receiver_authority_development.json \
  --campaign-dir /tmp/guard-v26-elephant-authority --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v26_elephant_receiver_authority_development.json \
  --campaign-dir /tmp/guard-v26-elephant-authority --phase admission
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v26-elephant-authority --phase admission
python3 experiments/run_general_workloads.py \
  experiments/campaigns/guard_v26_elephant_receiver_authority_development.json \
  --campaign-dir /tmp/guard-v26-elephant-authority --phase formal
python3 experiments/analyze_guard_v17_general_mechanisms.py \
  /tmp/guard-v26-elephant-authority --phase formal
# Run these only after the formal mechanism gate admits 10/10 cells.
python3 experiments/summarize_general_workloads.py \
  /tmp/guard-v26-elephant-authority
python3 experiments/select_guard_v26_elephant_receiver_authority.py \
  /tmp/guard-v26-elephant-authority
```

## Frozen outcome

V26 is rejected by the frozen paired-performance gates. The exact clean
simulator revision was `7a9e16a8f460cf99926c93e5c6e5446477f10692`.
All ten formal cells completed, passed the V14/V18 safety and protocol gates,
and recorded zero drop, recovery, timeout, and PFC activity. The candidate
recorded 1,370--2,161 authority bindings and 176--329 authority-driven rate
changes per seed; its maximum released fabric cap was 33.1--99.3 Gb/s. The
baseline recorded zero for all three fields. Thus the negative result is not
caused by an inactive code path.

Receiver authority did not improve elephant latency. Relative to K=1, its
greater-than-1-MiB mean FCT was +0.094% with paired t95 interval
[-0.370%, +0.558%], P95 was +0.253% [-1.700%, +2.206%], and overall mean FCT
was +0.063% [-0.016%, +0.141%]. Short-flow P95 and Jain passed their guards.
Mean queue increased 4.526% [-0.717%, +9.768%], and P99 queue increased
5.505% [-1.835%, +12.846%]; both confidence bounds failed the frozen +5%
ceiling. The selected arm therefore remains K=1.

The result rules out persistent shared-fabric capping as the material cause of
the remaining Homa elephant-mean gap in this workload: bypassing that cap for
the receiver-selected elephant changes latency negligibly while increasing
queue occupancy. A successor must test a different mechanism rather than a
larger target or broader cap bypass.

The accepted campaign is
`/tmp/guard-v26-elephant-authority-development-v2`. Artifact SHA-256 values
are: preflight
`f3753bf711f9f82c2192f9d8b636bc33cf4e1a2e003b85411bbbcaef61d677e0`,
mechanism report
`a0bdaf1f18a323e650465099442b7e66fdd27a239fd663ed62ee461c6072af64`,
performance report
`57c9ad57f244da37c5fb637ccde4c5b87aa9a0aadf9a04118fd4c9da53d1cc0d`,
and selection
`360d736ab1b618ed76579966e075d1867d8e8b971550135d655d96905512f211`.
The earlier `v1` directory was superseded before formal execution because its
generic base gate incorrectly required HPCC to remain the final binding path
in the authority arm; no performance artifact from `v1` was read.
