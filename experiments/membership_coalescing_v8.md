# Membership-coalescing V8 geometric-release holdout

`membership_coalescing_ladder_v8.json` freezes a geometric release-batching
pilot and formal holdout. It retains the V7 topology, workload, GUARD
configuration, selective generation-ACK policy, resource limits, PFC policy,
and paired non-regression bounds. The intended mechanism change is that an
ACK-optional release vector is emitted only after the active membership has
fallen by at least half relative to the previous emitted vector.

## Execution outcome

The frozen seed-107 pilot passed without opening performance files.  Its
coalesced arm completed all 15 flows with 22 grants and 15 grant ACKs
(`37 < 3N`), one required and one optional generation, ten geometric-release
deferrals, no retry, drop, recovery, timeout, or PFC event, and no terminal
pending state.  The immediate control sent 225 grants and recorded 50 matched
PFC intervals.  The paired flow SHA-256 was
`4b639336d67e2b101dfd1d0c7f3444b4a7559ce1e96828e071eb8d035b5f42f7`.

The 40 formal processes were then run serially, but the mechanism gate rejected
the holdout before reading FCT or queue files.  In the first failing cell
(seed 108, N=15, output 653819546), generation one covered only 13 of the 15
registered flows.  The preceding registration occurred at 2.002024721 s; the
last two registrations arrived at 2.002037771 s and 2.002038206 s, after the
12.480-us quiet window had emitted the 13-flow vector at 2.002037201 s.  The
later join therefore produced a second required 15-flow generation.  This
violates the single-initial-batch premise and invalidates the V8 holdout; no
performance aggregate is authorized.

## Simulator and configuration boundary

The runner requires simulator commit
`51cabdd8b61d45c0c43455665212d6f23443bb32` or a descendant and records the
exact clean simulator SHA used by a campaign. The capability probe requires
the selective-ACK trace/stats fields, `release_threshold_deferrals`, and the
simulator's hard rejection of membership coalescing with proactive release.

V8 explicitly freezes `guard_proactive_release=0` in the JSON contract and in
every generated simulator command. Changing it to one makes spec validation
fail before preflight. The simulator independently rejects that combination,
so a default or caller cannot silently exercise the unsupported path.

## Frozen pilot and holdout

The qualification pilot contains exactly two seed-107, N=15 cells: `control`
and `membership_coalescing_v8`, sharing one traffic hash. Formal seeds are
108--112 at N in {2, 4, 8, 15}, with both arms and paired traffic, for exactly
40 serial cells. The seed-107 pilot is a separate identity and cannot be
reused as a formal cell. The runner exposes no subset, arm, run-count, or
parallel-worker escape.

## Fail-closed V8 mechanism contract

For every coalesced run, the analyzer joins the completion-only lifecycle,
bounded grant trace, and membership stats before performance files can be
opened.

- The lifecycle must contain exactly N unique registrations and N
  completion-only releases. Generation one must grant exactly those N flow
  IDs, require ACKs, and be emitted after every lifecycle registration.
- Every later generation must contain no join or decreased grant. It must be
  an ACK-optional pure release, emit no ACK, and have zero pending state.
- If a later generation has A active flows and the previous emitted vector had
  P, the raw trace must show A <= floor(P/2). The parser also requires the
  `release_threshold_deferrals` stats field; the N=15 pilot must exercise at
  least one deferral.
- Every ACK-required `(generation, flow)` grant send, grant receive, ACK send,
  and ACK receive must close in order. Required and optional trace counts must
  exactly reconcile with their stats counters.
- Normal-path retries, stale or zero/mismatched generations, final pending
  state, truncation, drops, recovery, and timeout must be zero. Candidate grant
  sends plus ACK sends must be strictly below 3N for every N and seed.
- The final N-way grants must be within 1 Mbps of C/N. Raw and stats PFC fields
  must match with no unmatched events; only the coalesced arm has the frozen
  zero-PFC gate.

The known N=8 mechanism shape `8(required), 3(optional), 1(optional)` is
accepted as 12 grant frames plus 8 ACK frames, or 20 < 3N. This is a synthetic
tool test and not a formal experimental result.

## Sealed workflow

No V8 ns-3 process is launched while building or testing these tools. To run a
future campaign against a clean compatible simulator:

```bash
python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v8.json \
  --campaign-dir /absolute/path/to/v8-campaign \
  --phase preflight

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v8.json \
  --campaign-dir /absolute/path/to/v8-campaign \
  --phase pilot

python3 experiments/analyze_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v8.json \
  --campaign-dir /absolute/path/to/v8-campaign \
  --scope pilot \
  --json-out /absolute/path/to/v8-campaign/pilot_admission_v8.json

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v8.json \
  --campaign-dir /absolute/path/to/v8-campaign \
  --phase seal-pilot \
  --pilot-admission /absolute/path/to/v8-campaign/pilot_admission_v8.json

python3 experiments/run_membership_coalescing_holdout.py \
  experiments/membership_coalescing_ladder_v8.json \
  --campaign-dir /absolute/path/to/v8-campaign \
  --phase run
```

Pilot analysis emits mechanism evidence only. Pilot sealing reruns that
analysis and binds the resulting artifact hash into preflight. Formal analysis
first admits all 40 mechanism cells and verifies each paired traffic hash. It
opens FCT and receiver switch-egress queue files only after the entire matrix
passes. Then the aggregator applies the unchanged V7/V6 five-seed paired
Student-t 95% non-regression thresholds.

Passing the V8 gates supports only synchronized receiver-share traces through
N=15. It does not establish a general asymptotic bound, and proactive release
is outside this experiment.
