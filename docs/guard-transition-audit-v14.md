# GUARD V14 transition audit and fail-closed deadline

V14 adds two default-off controls on top of the V12 exact-prefix barrier and
the V13 full-wire watchdog.  These controls are mechanism instrumentation and
a safety policy; by themselves they make no performance claim.

## Per-transition audit

Enable the bounded CSV with:

```text
--guard_transition_audit 1
--guard_transition_audit_max_records 10000
```

`--guard_transition_audit_output` selects an explicit path.  The default and
hard maximum are both 10,000 records.  A shared sink streams records from all
receiver hardware and retains only one open transition per `RdmaHw`, so the
memory bound does not grow with completed transitions.  The footer and
`guard_stats` row report `records`, `attempted`, `written`, and `truncated`.
The audit path must be dedicated and cannot alias another simulation input,
output, trace, config, or log artifact.
When the audit is disabled, its config keys, CSV, footer, and stats row are not
emitted.

Each terminal record carries the receiver node and NIC, audit epoch,
transaction ID, priority-group-set hash, target-vector hash and entry count,
receiver capacity, incumbent active rate upper bound, draining offered-rate
upper bound, their checked sum, draining full-wire byte upper bound, frozen
prefix target and receiver-observed bytes, absolute start/deadline, deadline
policy and outcome, and terminal closure.  PG and target-vector hashes use a
deterministic sorted FNV-1a encoding; they identify source vectors but are not
collision-free proofs.  The rate and byte bounds remain separate because they
have different units.

The record is opened only after prepare ACK closure and after the absolute
deadline is frozen.  It closes on activation ACK closure, fallback activation
ACK closure, epoch reset, simulation stop, or fail-closed abort.  Flow removal
retires its observed prefix before the live transition maps are erased.  This
preserves a frozen per-transition prefix total without retaining completed
flow objects.  All aggregate and counter additions are checked before they
can exceed `uint64_t` or the signed simulator time domain.

Unlike the V13 summary scalars, every row is self-contained.  Two transitions
on one receiver and one transition on each of two receivers therefore remain
separate reconstructable records instead of becoming a mixed sum/max budget.

## Fail-closed deadline

Enable the abort policy with:

```text
--guard_transition_prefix_barrier 1
--guard_transition_prefix_wire_watchdog 1
--guard_transition_prefix_fail_closed 1
```

The fail-closed flag requires the V13 watchdog.  If all frozen prefixes become
receiver-ready, the normal activation and ACK-closure path is unchanged.  If
the absolute deadline expires, the simulator aborts before setting
fallback-active state or sending a fallback grant.  When the per-transition
audit is also enabled, its row is resolved as `timeout_fail_closed` and flushed
with terminal closure `fail_closed_abort` before the abort.  `run.py` propagates
the nonzero simulator status as an experiment failure.

With the flag off, V13 retains its isolated nominal-liveness behavior: a
timeout may enter the existing ordered ACK-clocked fallback path.  V12, V13,
and all unrelated default runs therefore keep their existing semantics and
output shape.

## Verification boundary

The source tests cover default-off configuration, normal readiness,
fail-closed ordering, epoch/reset/terminal closure, trace caps, overflow
guards, and the output schema.  The point-to-point C++ suite exercises two
transitions across two `RdmaHw` instances sharing a capped sink.  No performance
matrix is part of this change; a later preregistered V14 campaign must admit
the mechanism before reading performance artifacts.
