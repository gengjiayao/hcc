# GUARD V14 serialized refresh and draining

This optional bundle extends the existing mixed-PG canonical-vector
coordinator.  It does not introduce a second scheduler.  Both
`GuardSerializedProgressRefresh` and `GuardSerializedDraining` default to
false and must be enabled together.  The driver rejects configurations that
do not also enable full GUARD, the fail-closed prefix chain, the mixed-PG
vector, remaining-aware refresh, positive refresh spacing, and proactive
release.  Receiver concurrency remains unsupported by this path.
The serialized bundle is the sole exception to the older coalescing bans on
progress refresh and proactive release; work-conserving and cap-aware reclaim
remain rejected.

## Serialized progress

A threshold crossing records `pending_progress_seq` and dirties one progress
revision.  It does not advance `last_schedule_seq`.  If a transaction is
busy, further progress only updates the pending value and a later revision.
At the next coordinator boundary, GUARD freezes one vector containing the
membership, allocation, and progress revisions, each flow's contiguous
progress, remaining bytes, and encoded target.  Remaining-aware weights read
only these frozen remaining bytes.  A decrease is ACK-barriered before any
waiter or incumbent increase.  ACK validation uses the frozen full identity,
generation, action, and target; retry also reads the ledger rather than the
mutable current rate.  Only transaction close commits a frozen progress value
to `last_schedule_seq`.

### Wire ACK phase identity

The compact grant acknowledgement carries an explicit one-byte phase tag.
The sender copies the phase from the grant into the ACK; the receiver validates
that wire value against the frozen generation action.  It never infers an ACK
phase from the receiver's current coordinator state or from the grant header's
packed `ackRequired` byte.  The latter field is not part of an ACK and was
previously unset by the ACK parser, so it cannot establish action identity.

The ACK header is therefore 11 bytes (`sport`, `dport`, `pg`, `generation`,
and `phaseTag`) instead of 10.  Minimum-frame padding is computed from the
serialized header size, so the simulated Ethernet frame remains 60 bytes.
The default tag is zero, preserving the legacy non-phased interpretation when
the V14 bundle is disabled.

### Required ACK retirement

A receiver flow can complete after its required ACK has left the sender but
before that ACK returns to the receiver.  Removal first authenticates the live
generation ledger against the exact frozen target, including the full
five-field QP identity, run-monotonic generation, exact wire phase, action, and
target.  Before decrementing the live pending count, it copies those immutable
fields plus the flow ID, transaction ID, and retirement time into a
pointer-free retired-ACK record.  The local `ack_retire_close` trace row is the
single safety-barrier close; neither the later packet nor a duplicate can
decrement pending state or finish the transaction again.

ACK receive constructs the exact reversed wire identity before consulting a
live RxQP.  An exact identity/generation/phase/action match is accepted as
`ack_retired`, increments both the normal and retired receive counters, and
immediately erases the record.  A mismatch is stale and leaves the record
intact.  This works whether the RxQP still exists or has already been deleted,
and prevents legacy-key collisions from authenticating the packet.  Normal
live ACKs also require equality with the ledger's exact phase tag rather than
only a prepare/activate phase class.

The pointer-free ledger has a hard 4,096-record cap.  Exhaustion increments an
overflow counter and aborts instead of evicting unclosed wire evidence.
Transaction finish, epoch reset, and RxQP deletion do not clear it.  Terminal
admission requires `closures = received`, no retained records, no overflow,
and no stale ACKs.

The potential offered-rate bound of a flow is

```
P_f = max(last_acked_upper_bound_f, last_issued_target_f).
```

Before sending an increase, the coordinator checks the single mixed-PG
receiver resource rather than independent priority-group budgets.
It first raises the recorded potential to the possibly applied target, checks
`sum(P_f) + D <= C`, and only then emits each increase packet.  The frozen
vector hash also commits its membership/progress/capacity/drain reason mask.

## Draining reservations

Only an ACTIVE flow may enter proactive drain.  A live transaction first
records it as `DRAIN_PENDING`; transaction close converts it to `DRAINING`.
Because that live transaction can still issue a later target, the close
boundary refreshes the pending record from the final explicit ACKed and
issued ledgers before making its reservation immutable.  The reservation is

```
D_f = max(last_acked_upper_bound_f,
          last_issued_target_f,
          ceil(MinRate / 1 Mbps) * 1 Mbps).
```

The record retains a strong flow reference, full identity, receiver NIC and
priority group, both upper bounds, generation, remaining bytes, and release
time.  A draining flow receives no new grants.  Its reservation is released
only when `ReceiverNextExpectedSeq >= flow_size`; observing an out-of-order
final packet is insufficient.
If contiguous completion arrives while the record is still `DRAIN_PENDING`,
the coordinator tombstones that recipient and retires its ACTIVE potential so
the barrier cannot wait for an ACK from a sender QP that completion deletes.
It retains the completion-proven pending record until transaction close, then
performs the boundary `DRAINING` transition and immediately releases D using
the already-proved contiguous watermark.  If that was the last ACTIVE flow,
the live vector closes before the remaining D-only state is preserved.

For each frozen allocation, the coordinator computes

```
D = sum(D_f for DRAINING flows)
A = C - D.
```

Targets are encoded within `A`, and every send-side safety check enforces
`sum(P_f for active flows) + D <= C`.  If `D > C`, if `A` cannot cover the
effective minimum-rate floor, or specifically if it cannot admit the frozen
waiter floor, the path aborts closed.  The transition watchdog likewise uses
draining reservations as occupancy, so its serialization rate is the
remaining `C - active - D`.

An empty ACTIVE set does not reset the coordinator while a draining record
exists.  Terminal reset additionally requires empty waiter, vector, ledger,
prefix, dirty-progress, and pending-ACK state, with membership and progress
revisions fully consumed.
Completion-driven and ACK-driven closes share one ACTIVE-empty settlement
path: it closes any last live vector, consumes late membership/progress
revisions, preserves nonempty D, and performs at most one terminal reset.
When multiple D records complete at the same simulation timestamp, event order
therefore changes the terminal count one record at a time: the first `k ->
k-1` release retains the epoch while `k-1 > 0`, and only the event that makes
`k = 0` may reset it.  Every released record increments the draining release
counter exactly once.  If ACTIVE remains nonempty, releasing D instead advances
the progress revision and marks capacity dirty; an in-flight vector coalesces
that change for the next serialized revector rather than starting a competing
transaction.
Progress or capacity dirtied during a high-fan-in transaction is replayed
after its barrier closes, once no membership work has priority.

## Evidence and scope

The bounded grant trace marks receiver-authoritative frozen snapshots with
`snapshot_valid=1`.  Such a row carries the allocation/progress/membership
revisions, frozen receiver capacity, frozen ACTIVE and DRAINING record counts,
frozen draining reservation `D`, frozen allocatable capacity `A`, and the sum
of encoded targets.  It separately carries live ACTIVE/DRAINING counts, live
`D`, and `capacity_recompute_pending`.  Sender-side grant receives and ACK
sends are not authoritative and therefore emit `snapshot_valid=0` with all
snapshot and live-state fields zero.  The receiver preserves one immutable
frozen tuple for every row of an allocation revision; it never reconstructs
frozen `D` as `C-A` from a live record map.
`allocation_revision` is monotonic for the lifetime of one `RdmaHw` in a
simulation run and is not reset at a coordinator epoch boundary.  Epoch reset
still clears the current frozen revision and tuple.  Thus `(receiver node,
allocation_revision)` identifies exactly one frozen tuple even in a
multi-epoch trace.

The frozen ACTIVE count is the exact set union of live incumbents and the
transaction's frozen waiters; it is not required to equal the global live
ACTIVE count.  This distinction preserves the V11 busy-flush rule: a
registration after the flushed membership snapshot remains a deferred waiter
for the next revision and is not inserted into the current target vector.
Every global ACTIVE record outside the frozen cohort is classified
fail-closed.  It must be in the fast-path waiter set but in neither the
incumbent set nor the transaction-waiter set; its per-flow registration
revision and the live membership revision must be later than the frozen
snapshot with a positive, exact pending-revision delta; all acknowledged,
issued, current-grant, upper-bound, and generation state must remain zero; its
registration, flow-size, first-grant-gate, and base-RTT metadata must be valid;
and its receiver NIC and link capacity must match the frozen resource.  Any
other out-of-cohort ACTIVE record aborts.  The trace continues to report
`frozen_active_records` from the cohort and `live_active_records` from the
global set, so a legitimate `2 frozen + 1 deferred` state is explicit without
changing the vector hash or encoded targets.

The trace path fail-closes unless `frozen_D + frozen_A = frozen_C`, the encoded
target sum and each emitted grant fit `frozen_A`, and live `D` never exceeds
frozen `D`.  A contiguous completion may make live `D` smaller while an ACK
barrier still owns the old vector, but only after capacity recomputation has
been marked pending.  The barrier completes conservatively with its unchanged
frozen tuple, and the next allocation revision refreezes the then-live `D`.
For example, a revision frozen with `D=33.776 Gb/s` and `A=66.224 Gb/s` remains
that exact tuple if live `D` becomes zero before activation ACK closure; its
three encoded targets total `66.222 Gb/s`, so frozen occupancy remains below
100 Gb/s.  If no pending drain commits at the boundary, the next revision
freezes `D=0` and `A=100 Gb/s`; otherwise it freezes the exact then-live `D`
after those boundary commits.

Aggregate counters report refresh transactions and commits,
busy deferrals, pending/direct drains, boundary commits, contiguous releases,
maximum draining state, and terminal residue.  Retired-ACK counters separately
report safety closures, wire receipts, peak bounded records, terminal records,
and overflow.  They share the existing trace line cap; the only retained state
beyond live flows is the bounded, pointer-free late-ACK ledger.

Mechanism regressions cover the 50/50 to approximately 75/25 refresh, mutable
target/ACK mismatch, removal with a 50G or pending 100G potential, a sole 100G
drain followed by a 100G waiter, `D=99.95G` with a 100M effective minimum,
out-of-order final delivery, late progress, ACTIVE-empty draining state, and a
watchdog that subtracts draining occupancy.  The event-level regression also
schedules two ACTIVE-empty D completions at one timestamp, exercises a
completion-proven `DRAIN_PENDING` boundary, and verifies that two ACTIVE-nonempty
D releases remain one serialized capacity-dirty revector.  Grant-provenance
regressions cover the `33.776G -> 0` mid-ACK event, reject a substituted live
value in the frozen tuple, reject an unmarked capacity change, and require the
next revision to refreeze live capacity.  This document makes no performance
claim; a performance campaign must be separately preregistered and admitted.
The provenance suite additionally admits two frozen cohort members plus one
legal deferred waiter and rejects an out-of-cohort non-waiter, nonzero grant
potential, a missing dirty membership revision, a cross-NIC record, and
missing first-grant metadata.  The original busy-flush late-waiter revision
test remains part of the source suite.
