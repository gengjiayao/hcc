# GUARD V12 exact-prefix transition activation

V12 is an opt-in follow-up to the V11 safe two-stage membership protocol.  It
does not change the quiet windows, hard collection horizon, small-set limit,
low-fan-in transactions, release generations, or grant header.  Its only
normal-path change is the activation of the frozen waiter snapshot when V11
crosses into high-fan-in mode.

## Configuration and compatibility

`GUARD_TRANSITION_PREFIX_BARRIER 1` (or
`--guard_transition_prefix_barrier 1`) requires both the V11
`GUARD_SMALL_SET_FASTPATH_LIMIT 4` configuration and
`GUARD_FIXED_WINDOW 1`.  The option defaults to zero.  With it disabled, V11
continues to use the original unordered-set activation path and the V12 trace
columns and stats row are absent.

The sender places its exact `qp->m_win` in the existing `FlowStatTag`; the
receiver caches that value independently of lifecycle tracing.  This preserves
the selected window under both `GLOBAL_T=0` (path-pair window) and `GLOBAL_T=1`
(global maximum window), including non-MTU-aligned values.  Packet tags are
simulator metadata, so the data-frame wire size is unchanged.  The compact
GUARD grant remains 60 bytes on the simulated wire.

## Normal transition

The fifth registration still irreversibly selects V11 high-fan-in collection.
V12 starts only after the frozen transition's incumbent prepare generation has
received every required ACK.  An empty prepare recipient set takes the same
barrier path; it cannot jump directly to activation.

For every live waiter `f` in the frozen snapshot, the receiver records

```text
target[f] = min(advertised_flow_size[f], sender_exact_qp_window[f]).
```

The prefix is ready only when every live waiter satisfies

```text
ReceiverNextExpectedSeq[f] >= target[f].
```

The snapshot is sorted by `(register_ns, flow_id)` and held with safe ns-3
references until the transaction closes.  Before any prepare grant, all frozen
incumbents and waiters must resolve to one receiver NIC, one receiver capacity,
and one priority group; the barrier repeats the validation after prepare
closure.  A cross-queue transition aborts instead of making a drain claim. Once
all predicates are true, V12 sends one required transition-activation
generation to the whole live snapshot.  V11's ACK barrier closes that single
generation before any waiter becomes an incumbent.

If every frozen incumbent and waiter leaves while prepare is awaiting ACKs,
the empty snapshot closes without starting a timer.  A late join remains in a
new membership revision and is never used to keep the old transaction alive.

This predicate proves receipt of each waiter's contiguous pre-grant prefix.  It
does not claim that the complete last-hop queue is empty: a packet may still be
serializing, and the evaluation must use measured queue occupancy for such a
statement.

## Nominal liveness trigger and degraded fallback

At barrier start V12 computes, with unsigned 128-bit intermediate arithmetic,

```text
nominal_delay = max_snapshot_base_RTT
              + ceil(sum(max(0, target-received)) * 8 / receiver_capacity).
deadline = now + nominal_delay.
```

Both the delay and the absolute simulator timestamp are checked against the
signed 64-bit time domain.  DATA progress and removals recheck readiness; the
timer is only a liveness trigger.  The formula is not a hard drain bound:
incumbent traffic, frame headers, IFG, PFC, recovery, and other service can
consume capacity.

If the timer fires, it first rechecks the exact predicate at that timestamp.  A
still-unready transition is explicitly `timeout`/`degraded` and activates the
same frozen snapshot in deterministic microbatches of at most
`GUARD_SMALL_SET_FASTPATH_LIMIT` (four) waiters.  Each batch is one required
generation.  Duplicate or stale ACKs cannot advance the cursor, and the next
batch starts only after the current generation has fully closed.  Waiters are
committed to the incumbent set together only after the final batch closes.

The fallback is a safety and liveness path, not an admitted fast path.  A
performance campaign must require zero timeout, degraded transition, fallback
batch, observed PFC event, and recovery event before interpreting the normal
path.

## Membership and removal safety

- Registrations after the frozen revision never enter its ordered snapshot;
  the existing V11 revision delta keeps them dirty for a later collection.
- A removal is erased from ready, ordered, held-reference, target, and current
  batch state.  It immediately and idempotently rechecks the barrier without
  resetting the deadline.
- Empty-cohort removal cancels the deadline timer and clears all V12 state via
  the V11 epoch reset.
- Reliability refreshes retain the current phase, generation, outcome, and
  batch attribution; wire/global grant and ACK reconciliation is unchanged.

## Raw evidence

With V12 enabled, receiver-authoritative transition-activation trace rows add:

```text
prefix_target_bytes,prefix_observed_bytes,drain_outcome,
activation_batch_index,activation_batch_size,activation_register_ns
```

This is `O(control frames)` and remains under the existing trace line cap.  The
batch index is the emitted activation-generation ordinal, while batch size is
the number of recipients in that generation.  V12 intentionally does not
predict a final batch count in an activation row because future waiters may
complete while a current generation awaits ACKs.  The final emitted and closed
generation totals are reported by the fallback batch counters and can be
cross-checked against distinct required generations in the trace.

The `guard_transition_prefix` stats row reports starts, ready/timeout/degraded
transitions, required/ready waiters, target/received/remaining bytes, wait and
timestamps, fallback batch counts and closure, order/barrier violations, plus
terminal timer, cursor, cohort, future queue, current batch, target-map, and
held-reference state.  All terminal fields must be zero after a completed run.

These counters establish mechanism behavior only.  They do not by themselves
establish a latency, queueing, or control-frame improvement.
