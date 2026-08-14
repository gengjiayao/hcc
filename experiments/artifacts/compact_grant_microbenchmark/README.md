# Compact GUARD grant microbenchmark

This artifact compares the parent simulator (`bc44373`, run `249137408`) with
the compact-grant simulator (`993b676`, run `544496865`).  Both runs consume
the frozen two-flow input with SHA-256
`f6d3e6ead4525cf5a2e81be0b5564ec4d37e57e28089c9574feb8bf6c78086a9`.

The paired audit accepts only a byte-encoding change.  It requires identical
grant event semantics, FCT rows, and non-byte GUARD counters, plus full
completion and zero drop, recovery, PFC, or trace truncation.  The result
reduces four serialized grants from 376 to 240 bytes (94 to 60 bytes per
grant), a 36.17% reduction.  FCT rows remain byte-identical.

Re-run the audit from the repository root:

```bash
python3 experiments/analyze_compact_grant.py \
  --spec experiments/compact_grant_microbenchmark.json \
  --legacy-run experiments/artifacts/compact_grant_microbenchmark/legacy \
  --compact-run experiments/artifacts/compact_grant_microbenchmark/compact \
  --legacy-grants experiments/artifacts/compact_grant_microbenchmark/legacy/grants.csv \
  --compact-grants experiments/artifacts/compact_grant_microbenchmark/compact/grants.csv \
  --output /tmp/compact-grant-result.json
```

`SHA256SUMS` covers every frozen input and result consumed by the audit.
