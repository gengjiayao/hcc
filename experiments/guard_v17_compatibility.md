# GUARD V17 mixed-vector compatibility

V16 remains rejected because it required one mixed-size transaction to contain
exactly PG5, PG6, and PG7. That property varied with registration grouping and
is not required by the canonical-vector safety invariant.

V17 uses fresh seeds 152--154 and freezes the mechanism property before any
run: the mixed-size scenario must cover runtime PG4--PG7 (`all mask 0xf0`),
record at least one mixed frozen vector, and reach per-vector cardinality at
least two. It does not constrain which multiple PGs share one transaction.
The masks come directly from `GuardQpIdentity.pg`; they are not inferred from
configured sizes. The V15 ACK-clock policy, eight scenarios, timeout constants,
resource bounds, and all other safety gates remain unchanged.

The campaign is candidate-only and uses `--smoke`; FCT and queue artifacts are
sealed. Run the same preflight, replay, seal, compatibility, and analysis
sequence documented for V16, substituting the V17 spec and scripts and using
`/tmp/guard-v17-compat-formal-v1` as a new campaign directory.
