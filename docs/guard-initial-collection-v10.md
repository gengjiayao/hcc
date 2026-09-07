# GUARD V10 optional initial sliding quiet

V10 makes the first membership-vector collection policy selectable while
preserving V9 by default. It changes only an active epoch in which the receiver
has not emitted a membership vector. It is not an experiment authorization and
does not change the frozen V9 outcome.

## Modes

`--guard_initial_collection_full_deadline 1` is the default. The first
membership event starts an epoch at `t0`; its vector remains scheduled at the
V9 hard deadline `D = t0 + max_windows * W_i`, where `W_i` is the resolved
initial quiet window. Later registrations
or releases in that initial epoch accumulate without moving the timer.

With `--guard_initial_collection_full_deadline 0`, the first event schedules
the vector at `t0 + W_i`, where `W_i` is the resolved initial quiet window.
Each later initial membership event at time `t` reschedules it to:

```text
min(t + W_i, D)
```

The implementation computes the deadline branch before adding `W_i`, retaining
V9's overflow checks. If the quiet target and hard deadline coincide, the
`remaining <= W_i` branch selects the hard deadline. A flush at `D` is therefore
counted as hard; an earlier flush is counted as quiet.

After the first vector, neither initial-only setting is consulted. Registrations
retain the existing one-window quiet timer bounded by the normal batch
deadline, and completion releases retain geometric deferral and their existing
quiet-window behavior. An empty receiver cancels the pending timer and clears
the epoch start, deadline, target, and emitted-vector flag without resetting
the globally monotonic grant generation. The next nonempty epoch applies the
selected initial mode again.

## Configuration and evidence

`--guard_initial_collection_quiet_ns` defaults to zero, which resolves `W_i`
to `--guard_membership_coalesce_ns` and preserves V9 commands unchanged. A V10
isolation run can instead use a 16,640-ns initial quiet window while retaining
the 12,480-ns post-initial window:

```bash
--guard_membership_coalesce_ns 12480 \
--guard_initial_collection_quiet_ns 16640 \
--guard_initial_collection_full_deadline 0
```

Both window arguments are restricted to [0, 65536]. `run.py` accepts only zero
or one for the mode and writes
`GUARD_INITIAL_COLLECTION_FULL_DEADLINE` to the simulator config. The matching
`GuardInitialCollectionFullDeadline` Attribute defaults to true. It also writes
`GUARD_INITIAL_COLLECTION_QUIET_NS`, consumed by the zero-inheriting
`GuardInitialCollectionQuietWindow` Attribute. Direct config files enforce the
same ranges.

The bounded membership stats report:

- `initial_collection_full_deadline`, the configured mode;
- `initial_collection_quiet_ns`, the resolved initial-only quiet window;
- `initial_collection_reschedules`, which is zero for a V9-compatible fixed
  epoch and counts actual sliding timer replacements;
- `initial_collection_quiet_flushes` and
  `initial_collection_hard_flushes`, whose sum equals
  `initial_collection_flushes`; and
- the existing start, deferred-change, cancellation, total-wait, and maximum-
  wait counters.

The source regression tests verify the default, CLI/config validation,
overflow guards, empty reset, hard-deadline tie rule, mode-specific flush
assertion, and that the new quiet target is confined to the initial block.
They do not launch ns-3.

This mechanism only bounds an initial wait between one window and the fixed
deadline. It does not guarantee collection of an arbitrary number of flows,
does not establish an N-independent batch, and has no performance claim until
a separately preregistered experiment admits it.
