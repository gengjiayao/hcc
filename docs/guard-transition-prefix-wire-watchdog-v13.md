# GUARD V13 transition-prefix wire watchdog

V13 is a default-disabled repair to V12's nominal liveness deadline.  It does
not change V12's receiver-observed exact-prefix ready predicate, frozen
cohort, transition prepare or activation generations, membership revisions,
fallback batches, same-timestamp ordering, or terminal cleanup.  It changes
only the amount of time available before the unchanged degraded fallback can
run.

## Configuration and isolation

Enable V13 with:

```text
--guard_transition_prefix_barrier 1
--guard_transition_prefix_wire_watchdog 1
```

The direct simulator key is
`GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG 1`.  Both front ends reject V13 unless
the V12 prefix barrier is enabled.  The option defaults to zero.  With V13
disabled, `run.py` omits its configuration line, the V13 stats row is absent,
and the V12 deadline code and output shape are preserved.

## Deadline budget

The receiver computes the budget only after every required transition-prepare
ACK has closed.  For each live frozen waiter `f`, let

```text
T[f]       = min(flow_size[f], exact_first_grant_gate[f])
packets[f] = ceil(T[f] / MTU)
payload[f] = min(flow_size[f], packets[f] * MTU)
wire[f]    = payload[f] + packets[f] * data_header_bytes
W          = sum(wire[f]).
```

The packet ceiling uses quotient and remainder rather than `T + MTU - 1`, so
the ceiling itself cannot overflow.  `data_header_bytes` comes from
`CustomHeader::GetStaticWholeHeaderSize()`; it is 90 bytes in the current CC11
configuration.  The model's point-to-point DATA path has zero configured
inter-frame gap, so the formula adds no IFG term.  A final partial flow packet
contributes only the flow's remaining payload, while still contributing one
complete header.

For each still-live frozen incumbent `i`, V13 reads
`m_guard_grant_upper_bound_bps` after prepare ACK closure.  Thus

```text
O = sum(acknowledged_incumbent_upper_bound_bps[i])
C = receiver NIC bit rate
R = C - O.
```

Every incumbent bound must be positive, and a nonempty incumbent set must
satisfy `0 < O < C`.  An empty incumbent set is valid with `O=0` and `R=C`.
Zero or negative residual service fails closed.  The final budget is

```text
serialization_ns = ceil(W * 8e9 / R)
delay_ns         = max(exact_base_RTT_ns over waiters and incumbents)
                 + serialization_ns
deadline         = barrier_start + delay_ns.
```

All packet, payload, header, wire, occupancy, serialization, signed simulator
delay, and absolute `now + delay` operations are checked before conversion.
The helper returns failure and the receiver aborts on an invalid or
unrepresentable budget.

For the frozen N=8 shape, seven waiters each have a 104,000-byte gate and one
incumbent has an acknowledged 12.5-Gb/s upper bound on a 100-Gb/s receiver.
With a 1,000-byte MTU and 90-byte header, V13 derives:

```text
rounded_payload_budget_bytes = 728000
packet_count                  = 728
wire_bytes                    = 793520
residual_bps                  = 87500000000
serialization_ns              = 72551
max_rtt_ns                    = 8320
delay_ns                      = 80871.
```

Observed prefix progress is deliberately absent from this computation.  DATA
delivery still invokes the unchanged ready check, which immediately cancels
the longer timer and activates the complete live waiter cohort.  Therefore the
larger nominal bound cannot delay a transition that is already ready.

## Evidence and admission boundary

Only when V13 is enabled, the guard stats file adds one bounded row:

```text
guard_transition_prefix_watchdog enabled ...
records ... non_reconstructable ... inconsistent ...
rounded_payload_budget_bytes ... packet_count ... header_per_packet ...
wire_bytes ... incumbent_count ... occupancy_bps ... capacity_bps ...
residual_bps ... serialization_ns ... max_rtt_ns ... delay_ns ...
start_ns ... deadline_ns ... terminal_budget ...
```

`terminal_budget` is the number of live watchdog budgets at simulation end;
a completed run must report zero.  The existing V12 `guard_transition_prefix`
row remains byte-for-byte unchanged.

`rounded_payload_budget_bytes` is the sum of `payload[f]` above, not the raw
sum of exact prefix targets.  The distinction matters when a target is not
MTU-aligned but its flow continues: the watchdog conservatively budgets the
rest of that packet.

Each receiver HW persistently counts every started V13 budget in `records`.
The scalar budget fields retain the last record on that HW.  A second record
therefore sets its non-reconstructable flag rather than silently presenting
the last values as the only transition.  The global output sums byte/rate
counts and takes maxima for scalar time/capacity fields, as before, but now
marks the row `non_reconstructable=1` and `inconsistent=1` whenever there is
more than one budget record or more than one contributing receiver HW.  It
also marks an internally inconsistent record/field state.  Consequently the
fields may be recomputed as one same-source budget only when:

```text
records = 1
non_reconstructable = 0
inconsistent = 0.
```

The frozen single-receiver evaluation must enforce `records=1` and
`inconsistent=0`; a multi-epoch or multi-receiver result is evidence of a
different execution shape, not another valid observation of the frozen
budget.

The deadline is a nominal fallback bound, not a proof that the network will
deliver every prefix by that time.  A formal V13 evaluation must still require
zero PFC, recovery, timeout, degraded transition, and fallback event before
using performance results.  This implementation and its unit tests establish
mechanism behavior only; they do not establish a latency or queueing benefit.
