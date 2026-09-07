# Membership-coalescing V10 sliding-initial holdout

V10 replaces only V9's fixed initial collection deadline with a bounded
sliding-quiet timer. Its initial quiet window is 16,640 ns, derived before any
V10 run as twice the 8,320-ns OS4 cross-leaf base RTT. The initial hard
deadline is five such windows, or 83,200 ns. Post-initial completion releases
retain the V8/V9 12,480-ns membership window, five-window hard deadline,
selective ACK policy, and geometric release rule.

The simulator exposes the two initial modes independently. V10 requires
`guard_initial_collection_full_deadline=0` and
`guard_initial_collection_quiet_ns=16640`; V9 retains the default full-deadline
mode and its exact 62,400-ns behavior. These modes cannot be exercised in the
same receiver-local membership epoch.

## Execution outcome

The frozen seed-108/N=15 replay and the independent seed-119/N=15 pilot both
passed without opening performance files. In each V10 run, the initial
generation covered all 15 flows after 16,640 ns of membership silence. The
controller sent 22 grants and 15 grant acknowledgments (`37 < 3N`) with no
PFC, drop, recovery, retry, stale generation, or terminal pending state. The
pilot control sent 225 grants and recorded 40 matched PFC intervals, whereas
the paired V10 arm recorded none.

All 40 formal mechanism cells (seeds 120--124 and N in {2, 4, 8, 15}) then
passed, so performance was unsealed exactly once. The preregistered
non-regression gate rejected V10. At N=2 and N=4, the paired 95% CI upper
bounds for mean FCT were +1.69% and +1.48%, above the +1% limit. Mean-queue
CI upper bounds also exceeded the +5% limit at N=2, N=4, and N=8. N=15 passed
every gate and reduced mean queue by 23.11% and queue p99 by 31.71% on average,
while changing mean FCT by +0.034%; that high-fan-in result does not override
the small-N failures. V10 is therefore not admitted as the default
optimization.

The bounded 1.3-MiB campaign remains at
`/tmp/guard-membership-v10.ZrTKs6/campaign`. Its result artifacts have
SHA-256 values
`b2402b0f70703e346a9c824f51e1ddab027d2dfa9b4e4e9a87d79f6cddf7b9d1`
(replay),
`963b877cc592751531e4df1580f49d2fe304da285a97c130fadf60e7dafc96b5`
(pilot),
`b4a5b65b792e4af2739bc13a5910cab1144cf2e4dcd417ca38c44a9d1b156587`
(40-run analysis),
`593dadeb0316283f9cc59a7c5c5ec9ae7d3559973574faa4fbfd6d4cf15a9ea3`
(admission),
`442fc262d40957e9e4559e799c5cf193a3dc493c7cec7f733d2e4677a9b6a377`
(paired intervals), and
`25879197e98347aff3c3806fda18c27fc501a7e965e3baa4a2e6e0e45a301ae0`
(per-seed rows).

## Frozen identities and stage chain

Replay is candidate-only seed 108 at N=15. Its flow SHA-256 must remain
`1993661ded923372651ee25a4d9e5e2fb18cad0b0c5182edb758e71d80abc7a9`,
and its first generation must contain all 15 registered flows. Pilot is fresh
seed 119 at N=15 with paired control and V10 arms. Formal evaluation is seeds
120--124 at N in {2, 4, 8, 15}, with both arms, for exactly 40 cells.

The only permitted chain is `preflight -> replay -> seal-replay -> pilot ->
seal-pilot -> run`. Replay and pilot analyzers read mechanism artifacts only.
Each seal reruns the analyzer and hash-binds the passing artifact, frozen spec,
clean simulator SHA, and preceding seal. Formal analysis admits the complete
40-cell mechanism matrix before opening any FCT or queue file.

## Sliding-initial mechanism gate

Let `d0 = first_flush - first_registration` and `dlast = first_flush -
last_registration`. Every candidate run requires one initial start, one flush,
no epoch cancellation, N-1 deferred changes, N-1 reschedules, and exactly one
quiet-or-hard flush reason. Initial wait and maximum wait must both equal d0.

- Always require `0 < d0 <= 83200`, `0 <= dlast <= 16640`, and the last
  registration no later than the flush.
- A quiet flush requires `dlast=16640`, `d0<83200`, quiet count one, and hard
  count zero.
- A hard flush requires `d0=83200`, hard count one, quiet count zero, and
  `dlast<=16640`. A quiet target tied with the hard deadline is classified as
  hard.
- Generation one must contain exactly the N lifecycle flow IDs at one grant
  send timestamp, require ACKs, and close all N ACKs. Every later generation,
  if present, must be an ACK-optional pure completion release with no joins and
  active count at most half the previous emitted vector.
- Retry, stale generation, terminal pending state, trace truncation, drop,
  recovery, timeout, and candidate PFC counters must be zero. Grants plus ACKs
  must be strictly below 3N. Replay and pilot additionally require at least one
  optional release generation and one release-threshold deferral.

After all 40 formal mechanism cells pass, aggregation applies the unchanged
five-seed paired Student-t 95% gates: mean FCT and completion-span upper bounds
at +1%, queue mean and p99 upper bounds at +5%, and Jain-index difference lower
bound at -0.005.

## Bounded execution

The frozen cap is 1,000 generated flows, 4,096 grant-trace lines, 32 lifecycle
lines, 16 MiB per run, 1,800 seconds and 8 GiB RSS per process, and 1 GiB for
the complete campaign. The 43 simulator identities (one replay, two pilot,
and 40 formal) run serially. There are 22 unique generated traffic inputs;
paired arms reuse the same input.

Against a clean simulator at commit
`dc252d964a64617065a74ec9dc9d31a4b5339b21` or a descendant:

```bash
SPEC=experiments/membership_coalescing_ladder_v10.json
CAMPAIGN=/absolute/path/to/v10-campaign

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase preflight
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase replay
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope replay \
  --json-out "$CAMPAIGN/known_replay_admission_v10.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-replay \
  --replay-admission "$CAMPAIGN/known_replay_admission_v10.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase pilot
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope pilot \
  --json-out "$CAMPAIGN/pilot_admission_v10.json"
python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase seal-pilot \
  --pilot-admission "$CAMPAIGN/pilot_admission_v10.json"

python3 experiments/run_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --phase run
python3 experiments/analyze_membership_coalescing_holdout.py "$SPEC" \
  --campaign-dir "$CAMPAIGN" --scope holdout \
  --json-out "$CAMPAIGN/holdout_analysis_v10.json"
python3 experiments/aggregate_membership_coalescing_holdout.py "$SPEC" \
  "$CAMPAIGN/holdout_analysis_v10.json" \
  --output-dir "$CAMPAIGN/summary"
```

No V10 failure authorizes changing W, the hard deadline, seed, N, arm, or
matrix subset. Such a change requires a new preregistered version with fresh
pilot and formal seeds. Passing V10 would support only the frozen synchronized,
single-epoch, completion-only matrix through N=15. It would not establish a
universal sub-3N bound or final-GUARD superiority; remaining-aware refresh,
proactive release, size priority, and tail bypass are outside this experiment.
