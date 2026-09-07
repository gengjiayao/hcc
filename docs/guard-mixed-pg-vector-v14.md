# GUARD V14 mixed-PG target vectors

V14 adds the default-off `GUARD_MIXED_PG_VECTOR_FASTPATH` mechanism.  It does
not change V13 or the default GUARD path.  Enabling it requires CC mode 11 and
the complete fail-closed prefix chain: the V12 exact-prefix barrier, the V13
wire watchdog, and V14 fail-closed deadline handling.

## Receiver resource and identity

Priority groups are queues, not independent receiver links.  Every frozen
cohort must resolve to the same receiver NIC and the same nonzero link
capacity.  All PGs in that cohort share this single capacity `C`; a two-PG
cohort cannot allocate `C` once per PG.  The controller aborts before sending a
grant if a cohort crosses receiver NICs or capacities.

The legacy 64-bit QP indexes overlap PG and port bits.  V14 therefore retains
the complete `(sip,dip,sport,dport,pg)` identity beside each live entry and
each tombstone.  An alias with a different full tuple aborts on insertion,
lookup, or deletion rather than resolving to an unrelated live or completed
flow.

## Frozen vector

At transaction start the receiver freezes one canonical vector.  Records are
sorted by the full tuple and include the incumbent/waiter role, remaining-byte
snapshot, and actual encoded Mbps target.  The versioned hash covers V14, the
receiver NIC, `C`, membership revision, record count, and every pointer-free
record field.  Container iteration order and later tombstone state do not
affect it.  A late join increments membership state for a later transaction;
it cannot enter or mutate the current vector.

Encoding floors a requested rate to whole Mbps, then raises it to
`ceil(MinRate/1Mbps)` because that is the sender's effective minimum wire rate.
The encoded sum must be no greater than `C`.  This check prevents a nominal
target below `MinRate` from understating the issued upper bound.

## Barriers and actions

For each flow, the conservative potential upper bound is
`max(acknowledged_upper, issued_target)`.  Prepare sends a required generation
only to incumbents whose potential upper exceeds their frozen target.  After
all prepare ACKs and the exact-prefix barrier, one required activation
generation covers every waiter plus every continuing incumbent that must
increase.  The generation ledger binds each recipient to its generation,
action, and target; retry is identical, stale generations are rejected, and a
removed recipient becomes a tombstone.

For example, changing `70/30` Gbit/s incumbents to an `80/10/10` Gbit/s vector
first requires the second incumbent to acknowledge 10 Gbit/s.  Only then may
the first incumbent's 80 Gbit/s increase and the waiter's 10 Gbit/s activation
be issued.  The potential aggregate never exceeds the single 100 Gbit/s `C`.

A release recomputes the survivor vector.  If any survivor decreases, the
release uses the same required prepare/activation sequence.  An optional
release is permitted only when every survivor is nondecreasing.

## Evidence and scope

The `guard_mixed_pg_vector` stats row reports bounded counters: freezes,
mixed-PG freezes, maximum entries, prepare decreases, waiter activations,
continuing increases, required/optional release decisions, the folded latest
hash, and terminal record/hold/ledger sizes.  Terminal sizes must all be zero.
Only the current frozen vector and generation ledger are retained; completed
vectors are not accumulated.

This change provides mechanism and arithmetic evidence only.  It does not
claim a performance improvement and does not add or run a performance matrix.
