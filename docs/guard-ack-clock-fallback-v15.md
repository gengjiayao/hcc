# GUARD V15 ACK-clocked prefix fallback

V14 remains rejected on the frozen `remaining_refresh_dual_receiver_epoch`
seed 143 cell.  Its receiver-local watchdog budget used `C-O` as if it were
an end-to-end guaranteed service rate.  Same-sender traffic to another
receiver and shared-fabric scheduling invalidate that interpretation.  V15
does not enlarge the timeout or reclassify the V14 run.

V15 changes only the action taken when the existing prefix watchdog expires.
The deadline is a diagnostic trigger, not a proof that the prefix should have
arrived.  A ready transition follows the unchanged direct activation path.  A
non-ready transition activates the immutable waiter snapshot in deterministic
`(register_ns, flow_id)` order, at most four recipients per generation.  Every
generation is required and the next batch is sent only after all current
grant ACK obligations close.  The final batch closes before the transaction
commits.  The canonical target vector and draining reservation continue to
bound the sum of potential sender caps by the receiver capacity.

The new mode is default-off and explicit:

```text
GUARD_TRANSITION_PREFIX_BARRIER 1
GUARD_TRANSITION_PREFIX_WIRE_WATCHDOG 1
GUARD_TRANSITION_PREFIX_FAIL_CLOSED 0
GUARD_TRANSITION_PREFIX_ACK_CLOCK_FALLBACK 1
GUARD_MIXED_PG_VECTOR_FASTPATH 1
```

The runner and simulator reject the mode unless congestion control is full
GUARD, the watchdog is enabled, and fail-closed V14 mode is disabled.  A
mixed-PG vector must select exactly an admitted timeout policy: V14
fail-closed or V15 ACK-clocked fallback.  Legacy V12 experiments remain
unchanged when the mixed-PG vector is disabled.

Mechanism admission for a fresh V15 campaign must check both outcomes.  Ready
transitions require exact prefix evidence.  Fallback transitions require
nonzero timeout/degraded counters, ordered batches no larger than four,
required grant/ACK closure for every batch, zero order/barrier violations,
and zero terminal timer, fallback, cursor, cohort, current-batch, target, and
hold state.  Both outcomes still require zero drops, PFC, recovery, retry,
stale ACK, overflow, and trace truncation in the frozen lossless scope.

V15 makes no universal queue-empty or timeout-bound claim.  It guarantees
that timeout cannot block progress or release multiple frozen waiters without
the existing ACK barriers.  Performance, including any cost from serial
fallback batches, must be measured only on new pilot and formal seeds after
the full mechanism matrix passes.
