# Membership-coalescing V9 frozen holdout

V9 changes only the candidate's initial membership collection: the receiver
waits exactly five 12.480-us quiet windows (62.400 us) before its first flush.
The later completion-only vectors retain V8's geometric release rule. The
window is a frozen hard deadline, not a value selected after reading
performance. Control, topology, traffic sizes, PFC policy, resource limits,
and V8 paired-CI thresholds are unchanged.  As in V8, remaining-aware grants,
receiver concurrency, and tail bypass are disabled; the result therefore
isolates equal-share membership updates rather than the final GUARD scheduler.

## Execution outcome

The known-failure replay and the independent seed-113 pilot both passed their
mechanism-only gates.  In each candidate run, the first vector covered all 15
flows after exactly 62,400 ns, and 22 grants plus 15 ACKs gave 37 control
frames (`37 < 3N`) with no PFC, drop, recovery, retry, stale generation, or
terminal pending state.  The replay preserved the frozen V8 flow SHA-256.

All 40 formal mechanism cells (seeds 114--118 and N in {2,4,8,15}) then passed,
so performance was unsealed.  The frozen non-regression gate rejected V9.
For N=2, the paired 95% CI upper bounds were +4.85% for mean FCT and +2.33%
for completion span, above their +1% limits.  Mean-FCT also failed at N=8
(+2.33% upper bound), while mean-queue CIs exceeded the +5% limit at N=2,
N=4, and N=8.  N=15 passed the latency gates and reduced mean queue by 46.85%
on average, but that isolated benefit does not override failures at smaller N.
V9 is therefore not admitted as the default optimization, and its fixed
62.4-us initial wait must not be used in final-GUARD performance claims.

The bounded result artifacts have SHA-256 values
`2523c42b057d95e3425f024e046e54181fa5eaf8796551984b60f793cc6dd783`
(replay),
`345b6d1093a81eb04772074448996a42d0633e0ad128a61b5afff93da5c8ff36`
(pilot),
`744554b11f4d8025caa6bc4caf9e89eb39def7a001af09b130058840cbe75c14`
(40-run analysis),
`0e3f3e65dafe089389962de0461a99ddd6b7e9de4db654e9c8063ef91a5b1957`
(admission),
`bb1f5fc93584b7a1b2fb2c4766ecd7c08bb845ebf9391ced699fbfa152494eca`
(paired intervals), and
`8661e395ddec46c8e9ae0debc9f4ac10d0caa4623dac0753d2e37ccefa612de8`
(per-seed rows).

The required stage chain is `replay -> seal-replay -> pilot -> seal-pilot ->
run`. Replay is the known V8 failure identity, seed 108 at N=15, candidate
only. Its generated flow SHA-256 must be
`1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9`.
Pilot is seed 113 at N=15 with both arms. Formal evaluation is seeds 114--118
at N in {2, 4, 8, 15}, with paired control and candidate runs (40 cells).

## Fail-closed mechanism gate

Every candidate cell must show one initial collection start and flush, no
cancellation, an exact 62,400-ns wait and maximum wait, and a first-flush
timestamp exactly 62,400 ns after the first registration. The last initial
registration must precede that flush; generation one must contain all N flows
at one send timestamp. Later generations, if present, must be ACK-optional
pure releases satisfying the frozen geometric rule. Retry, stale generation,
pending state, truncation, drop, recovery, timeout, and PFC counters must be
zero, and grants plus ACKs must be strictly below 3N. The control arm must not
exercise initial collection.

Replay and pilot analyses contain mechanism evidence only. Formal performance
files stay sealed until all 40 formal cells pass the same mechanism gate and
paired traffic hashes match. Only then does the aggregator apply the unchanged
V8 five-seed paired Student-t 95% non-regression rules. A failed replay, pilot,
or formal mechanism cell rejects the frozen experiment; it does not authorize
changing the window or selecting another seed.

## Bounded execution

The frozen spec caps generated flows at 1,000, grant trace lines at 4,096,
lifecycle lines at 32, each run artifact at 16 MiB, resident memory at 8 GiB,
the campaign at 1 GiB, and wall time at 1,800 seconds. Runs are serial. Inspect
the campaign size before resuming a partially completed stage.

Against a clean simulator at the spec's minimum commit or a descendant, run:

```bash
SPEC=experiments/membership_coalescing_ladder_v9.json
CAMPAIGN=/absolute/path/to/v9-campaign

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase preflight
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase replay
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope replay \
  --json-out "$CAMPAIGN/replay-admission-v9.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-replay \
  --replay-admission "$CAMPAIGN/replay-admission-v9.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase pilot
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope pilot \
  --json-out "$CAMPAIGN/pilot-admission-v9.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-pilot \
  --pilot-admission "$CAMPAIGN/pilot-admission-v9.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase run
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope holdout \
  --json-out "$CAMPAIGN/holdout-analysis-v9.json"
python3 experiments/aggregate_membership_coalescing_holdout.py "$SPEC" \
  "$CAMPAIGN/holdout-analysis-v9.json" \
  --output-dir "$CAMPAIGN/summary"
```

V9 tests use synthetic fixtures and do not run ns-3:

```bash
python3 -m unittest experiments.tests.test_membership_coalescing_holdout
```

Passing these gates would establish only the frozen completion-only workload
through N=15. It does not imply a universal sub-3N control bound, compatibility
with proactive release, or superiority on workloads outside this matrix.
