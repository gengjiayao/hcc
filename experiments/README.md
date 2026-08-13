# Bounded reviewer experiment campaign

These tools run the corrected GUARD reviewer matrix serially and summarize it
without treating individual flows as independent experimental repetitions.
They use the Python standard library and do not change the simulator model.

## Safety-first workflow

First inspect the expanded matrix and generate each candidate traffic file in a
temporary location.  `--dry-run` never launches ns-3 or installs the generated
traffic input:

```bash
python3 experiments/run_campaign.py \
  experiments/campaigns/reviewer_minimal.json --dry-run
```

The checked-in campaign expands to 165 unique runs.  It covers full GUARD,
HPCC-only, receiver-rate-only, last-hop INT, lambda/beta/gamma sensitivity,
matched PFC/IRN settings, AliStorage, WebSearch, two offered loads, and five
seeds.  Identical traffic parameters reuse a byte-identical input.

OFLM exposes independent `guard_selective_registration` and
`guard_proactive_release` parameters.  Their four combinations isolate BDP
filtering from early release.  The legacy `guard_oflm=0` command-line parameter
still disables both, while `guard_oflm=1` leaves explicitly supplied component
values unchanged.

By default the runner enforces these hard bounds:

- 25,000 generated flows per run;
- 30 minutes wall time per run;
- 100 MiB output per run; and
- 5 GiB across the campaign directory, its simulator outputs, and traffic inputs.

It starts a single run at a time and terminates the whole simulator process
group if a bound is exceeded.  Override a bound only after inspecting a dry
run.  The runner never deletes an output automatically.

After reviewing the dry-run counts, launch a campaign into an explicit results
directory:

```bash
python3 experiments/run_campaign.py \
  experiments/campaigns/reviewer_minimal.json \
  --campaign-dir experiments/results/reviewer-minimal
```

Use `--resume` with the same directory to skip manifests already marked
`completed`.  Use `--max-runs 1` for an initial end-to-end smoke test.  To run
one bounded part of the matrix without repeating earlier stages, pass a stage
name (the option can be repeated):

```bash
python3 experiments/run_campaign.py \
  experiments/campaigns/reviewer_minimal.json \
  --stage components_websearch \
  --campaign-dir experiments/results/components-websearch
```

Each attempt writes `runs/<run-key>/manifest.json`.  A manifest records the
expanded parameters and CLI, Git SHA and dirty state, topology/CDF/flow SHA-256,
generated and completed flow counts, timestamps, exit status, stop reason,
output location and byte count, and all active safety limits.  `run.py` retains
the exact ns-3 `config.txt` in the simulator output directory.

## Validation and aggregation

After all runs finish:

```bash
python3 experiments/summarize_campaign.py \
  experiments/results/reviewer-minimal
```

This creates:

- `summary/runs.csv`: one validation row per simulator run;
- `summary/metrics.csv`: per-seed metrics, cross-seed aggregates, and paired
  differences; and
- `summary/rejections.txt`: failed manifests, invariant violations, incomplete
  pairs, and traffic-hash mismatches.

The summarizer reads raw FCT, `config.txt`, the extended GUARD/recovery/drop
statistics, seven-column per-priority PFC events, and the bounded
`out_queue_stats` summary.  It falls back to the legacy queue trace only for old
artifacts.  It rejects incomplete runs and checks that:

- full GUARD has grants, valid INT-hop feedback, and actually applied HPCC rate updates;
- HPCC-only has no grants and does have valid, applied HPCC updates;
- receiver-rate-only has grants and no HPCC feedback activity; and
- config values agree with the manifest.

FCT metrics are separated into `<=8 KB`, `8 KB--1 BDP`, `1 BDP--1 MiB`, and
`>1 MiB`.  P99.9 is emitted only with at least 1,000 observations.  The bounded
queue summary includes zero-valued samples and reports sample count, mean, P95,
P99, and maximum queue bytes.  New runs also append one constant-size row per
switch egress with its neighbor, zero-inclusive and positive-only queue
statistics, and transmitted bytes.  These rows support directed bottleneck
analysis without enabling an unbounded time series.  For legacy traces only,
queue metric names say
`nonzero`, because those traces omit zero-valued samples.  PFC results include
per-priority counts, matched intervals, cumulative and maximum pause duration,
and unmatched transitions.  Recovery results include switch drops, recovery
and IRN NACKs, retransmitted packets and bytes, and timeouts.

Every run first yields one value per metric and seed.  Aggregate rows compute a
two-sided 95% Student-t confidence interval over those seed-level values.
Paired algorithm differences require matching seed and an identical traffic
SHA-256; a mismatch is a hard rejection.

## Bounded custom workloads

`generate_workload.py` creates deterministic incast, hybrid incast/background,
ring-allreduce trace, and all-to-all inputs together with a JSON manifest.  A
custom `run.py --flow_file` invocation takes a private snapshot of that input in
its output directory.  After the run, validate and summarize both artifacts:

```bash
python3 experiments/summarize_workload.py mix/output/RUN_ID \
  --manifest experiments/results/WORKLOAD.manifest.json
```

The summarizer refuses incomplete or mismatched results.  It verifies the
manifest hash and flow count, the snapshot endpoints, priority, size and start
time, the configured preflight limit, and a one-to-one match between generated
flows and FCT completions.  Inputs remain capped at 25,000 flows, the snapshot
at 8 MiB, and each raw run artifact at 100 MiB.  Validation produces
`workload_summary.json` and `workload_summary.csv` atomically.

The outputs include trace completion time, per-flow FCT and slowdown mean/P95/
P99, aggregate goodput, per-destination aggregate incast goodput, each
destination's per-flow goodput Jain index/min/max, across-destination goodput
fairness, bounded queue statistics, and grant, PFC, switch-drop, NACK,
retransmission and timeout counters.  P99.9 is present only when at least 1,000
flows complete.

The generated ring-allreduce workload is an `open_loop_trace`: its configured
step start times do not enforce collective dependencies, so its trace span is
not a real collective or job completion time.  The all-to-all trace has no such
step dependency and its span may be reported specifically as communication-
phase CCT.

## Matched general-workload comparison

`campaigns/general_workloads_formal.json` freezes the AliStorage2019,
WebSearch, FbHdp, and GoogleRPC comparison of full GUARD, HPCC, and
receiver-only GUARD.  It uses PG3 inputs and disables GUARD size-priority
remapping in the full and receiver-only arms.  All three arms of a workload and
seed consume the same persistent `--flow_file` snapshot.

Run the phases separately so that traffic volume and mechanisms are checked
before any performance result is available:

```bash
RESULTS=/tmp/general-workloads-formal
python3 experiments/run_general_workloads.py \
  experiments/campaigns/general_workloads_formal.json \
  --campaign-dir "$RESULTS" --phase preflight
python3 experiments/run_general_workloads.py \
  experiments/campaigns/general_workloads_formal.json \
  --campaign-dir "$RESULTS" --phase admission
python3 experiments/summarize_general_workloads.py "$RESULTS" --admission-only
python3 experiments/run_general_workloads.py \
  experiments/campaigns/general_workloads_formal.json \
  --campaign-dir "$RESULTS" --phase formal
python3 experiments/summarize_general_workloads.py "$RESULTS"
```

The preflight phase generates five independent traces per CDF without invoking
ns-3 and enforces the 10,000-flow cap.  If any primary GoogleRPC trace exceeds
the cap, its only allowed fallback is the frozen 8-host OS1, 10ms, load-20
profile; if that also fails, GoogleRPC is excluded.  No other load tuning is
permitted.

Admission runs only seed 1.  Its full-GUARD arm is the only run with the
controller trace enabled; the trace has a 300,000-line hard bound and must
report no truncation.  A workload advances only if all flows complete, all
three mechanisms fire with the expected separation, drop/recovery counters are
zero, exact flow hashes match, and every run remains under 100 MiB.  Formal
runs use bulk monitoring and rely on bounded total counters.

The final summarizer emits per-run FCT and slowdown mean/P95/P99 for the
overall workload and four size buckets, aggregate queue/PFC/grant statistics,
five-seed arm means, and hash-matched paired Student-t 95% intervals.  The
seed-1 controller audit also groups reactive/grant binding events by flow-size
bucket for long-flow root-cause analysis.  Rejected CDFs remain recorded in
`summary/exclusions.json` and are never given performance results.

For a matched three-arm custom-workload component study, generate every input
at PG4 before running full GUARD, HPCC-only, and receiver-rate-only.  GUARD
maps flows larger than one BDP to PG4 internally; using PG4 in the source trace
keeps the HPCC arm on the same actual data priority.  Preserve the generator
manifest and use an identical flow snapshot hash across all three arms of each
seed.  With result names following
`formal-component-{full,hpcc,receiver}-s{seed}`, aggregate five seeds with:

```bash
python3 experiments/summarize_component_workload.py RESULTS \
  --simulator-sha SIMULATOR_COMMIT
```

The component summarizer rejects incomplete or hash/PG/config-mismatched runs,
checks each arm's mechanism counters, audits bounded full-GUARD controller
traces, and reports both overall and 15-flow incast metrics.  It extracts the
target downlink, both leaf uplinks, and both spine downlinks; computes grant
overhead and time-weighted binding; and emits five-seed Student-t intervals and
matched pair differences.  PFC counts are retained as outcomes rather than
used to select or retune an arm.

### Paired all-to-all component analysis

For a formal full-GUARD/HPCC/receiver-only comparison, write an explicit JSON
run index and pass it to the stricter component analyzer:

```bash
python3 experiments/summarize_alltoall_components.py \
  experiments/results/alltoall-formal/run_index.json \
  --analysis-dir experiments/results/alltoall-formal/analysis
```

The index has `schema_version: 1`, a `design` object, and exactly one `runs`
entry per arm and seed.  Design fields are `hosts`, `flows`,
`priority_group`, `flow_size_bytes`, `max_jitter_us`, `topology_file`, and the
seed list.  Every run entry names its `arm`, `seed`, raw `output_dir`, and
workload `manifest`; relative paths are resolved from the index directory.
The default aggregate raw-source limit is 100 MiB.

Admission requires all 240 ordered pairs once, 240/240 completions, explicit
PG4, the same manifest and traffic SHA-256 across arms within each seed, and a
different traffic hash across seeds.  It also checks config/seed/topology,
per-priority raw PFC counts against GUARD stats, zero drops and recovery, and
complete bounded lifecycle traces where applicable.  Full GUARD must exercise
grants, valid HPCC computations, actual rate changes, reactive binding, grant
binding, and INT stripping.  HPCC must exercise actual HPCC rate changes with
no grants; receiver-only must exercise grants with zero HPCC activity.

Only after every run passes, the analyzer atomically writes `per_run.csv`,
`arm_ci.csv`, `paired_difference.csv`, `paired_percent.csv`, and
`admission.json`.  Arm and paired intervals use one value per seed and a
two-sided Student-t 95% interval.  Paired percentages are computed as
`(arm_a-arm_b)/arm_b*100` within each seed before aggregation, not as a ratio
of cross-seed means.  `admission.json` includes raw artifact hashes and keeps
any unmatched PFC transition counts visible; those counts must not be silently
reclassified as matched pause intervals or recovery events.

For a directed controller audit, `run.py --guard_controller_trace 1` writes a
bounded CSV containing HPCC, grant, and completion events with computed,
granted, and final rates.  Its final comment records attempted, written, and
truncated rows; time-weighted analysis is valid only when `truncated=0`.  The
runner permits at most 300,000 rows and enables this trace only for full GUARD.
`hpcc_full_computations` and `hpcc_fast_computations` distinguish valid INT
computations from feedback calls and actual pacer changes.  The supplied
runner uses `FAST_REACT=0`, so an audited formal run must report zero fast
computations.

## Scope

This campaign deliberately excludes GoogleRPC because its small mean message
size exceeds the 25,000-flow safety cap at the selected scale.  It also does not
claim to supply missing RDMA hardware measurements, NDP/ExpressPass/pHost
implementations, a validated standard Homa baseline, or a dependency-aware
collective simulator.  Those require separate artifacts before they can
support paper claims.

Run the unit tests with:

```bash
python3 -m unittest discover -s experiments/tests -v
```
