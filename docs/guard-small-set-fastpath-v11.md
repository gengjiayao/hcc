# GUARD V11 opt-in small-set transactions

V11 is a default-disabled safety mechanism for testing whether serialized
small receiver cohorts can avoid the initial membership wait. It is not an
experiment authorization and carries no latency, queue, or control-overhead
claim. Enablement is intentionally narrow: `--guard_small_set_fastpath_limit`
accepts only `0` or `4`, and zero preserves V10 behavior.

## Transaction protocol

For a nonempty epoch with at most four active registered flows, each join is a
nonzero-generation transaction. The receiver first sends a required `prepare`
generation to every incumbent whose acknowledged upper bound exceeds the new
equal-share target. It waits for all ACKs before sending a required `activate`
generation to the transaction's gated newcomers. Only accepted activation ACKs
move those newcomers into the incumbent set. Membership changes that arrive in
either phase remain queued for a later transaction. A release-only target that
does not decrease any incumbent is sent immediately as an optional generation.

The fifth join irreversibly selects high-fan-in mode until the receiver becomes
empty. The first high-fan-in collection uses the V10 sliding initial window;
the V11 configuration fixes its quiet window at 16,640 ns and its hard deadline
at five windows. Post-initial collections retain the 12,480-ns membership
window and geometric release rule. The transition `prepare` starts only after
both the first high collection has flushed and the serialized small-set prefix
has closed. It then activates the frozen waiter snapshot after its prepare ACK
barrier. A registration after that snapshot remains dirty and cannot be erased
when the older snapshot starts.

Initial-collection flush and transition commit are separate states. This keeps
a late registration from opening a second "initial" timer while the transition
is still waiting for ACKs. Empty reset cancels the phase and pending timers and
clears the epoch-local revisions and cohorts, but does not reset the globally
monotonic grant generation.

V11 requires the following existing isolation settings:

```text
--guard_small_set_fastpath_limit 4
--guard_membership_coalesce_ns 12480
--guard_initial_collection_quiet_ns 16640
--guard_membership_coalesce_max_windows 5
--guard_initial_collection_full_deadline 0
--guard_remaining_aware 0
--guard_receiver_concurrency 0
--guard_proactive_release 0
```

`run.py` and direct simulator configuration both reject any other V11
combination. The existing coalescing checks additionally require a finite-loss
reliability timer, fixed sender window, and disabled refresh, adaptive reclaim,
and cap-aware reclaim.

## Fail-closed evidence

The receiver maintains monotonic membership, consumed, and frozen-ready
revisions. A flush records the current ready revision; starting that snapshot
consumes only that revision. Thus a later registration leaves positive pending
work. Scheduling a high-fan-in timer with a clean revision, or finding a gated
waiter without a dirty revision, aborts.

`guard_small_set_fastpath` stats distinguish accepted phase work from wire
frames. Accepted `*_batches`, `*_grants`, and `*_acks` exclude reliability
retries and duplicate ACKs. `*_wire_grants` counts every transmitted grant,
including retries; `*_wire_acks` counts every sender ACK frame, including an
ACK repeated for a duplicate grant. Their totals must equal the existing global
grant/ACK counters, with both unattributed counters at zero. The terminal phase,
pending ACKs, pending membership, waiter snapshots, collection state, and
revision pair are reported explicitly.

The compact grant remains 15 bytes. Bit zero of its existing one-byte control
field remains `ack_required`; bits one through three carry a V11-only phase tag.
Legacy modes leave those bits zero. The bounded grant trace appends these fields
only when V11 is enabled:

```text
transaction_id,grant_phase,membership_target_n,subject_role
```

`grant_phase` is one of `fast_prepare`, `fast_activate`,
`transition_prepare`, `transition_activate`, or `release` on attributed V11
frames. Receiver `sent` and accepted `ack_received` rows are authoritative for
the transaction and target. Sender rows carry the wire phase tag; their
transaction and target remain zero and must be joined to receiver rows by flow
and generation.

The first accepted transition in a run latches `prefix_close_ns`,
`collection_flush_ns`, and `transition_prepare_start_ns`; later collections,
transactions, and empty-epoch resets cannot overwrite them. Epoch-local copies
are reset separately so a later epoch cannot use stale times for its barrier.
`last_transaction_close_ns` is the separate moving timestamp. The reported
high-transition registered count and waiter count are taken from the frozen
transition transaction, not from the fifth-join trigger instant.
Any barrier violation or first-grant unlock outside an activate phase is
counted and must reject a run.

For the frozen receiver-share sequence only, the preregistered control-frame
ceilings are `N^2 + 2N - 1` for `N <= 4` (7 at N=2 and 23 at N=4) and `3N + 19`
for the N=8/N=15 high transition (43 and 64). These are admission ceilings for
that fixed event pattern, not a proof for arbitrary N, arrival timing, churn,
or loss. Retries are included and can therefore make a run fail the ceiling.

The source and header unit tests cover default-off isolation, nonzero
generations, ACK serialization, the busy-flush/late-waiter revision case,
second-collection mode selection, timestamp latching, phase-tag size
preservation, and retry-inclusive frame accounting. They do not launch ns-3.
