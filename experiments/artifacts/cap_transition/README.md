# Same-flow cap-transition artifact

This bounded artifact supports one mechanism claim: in each of five seeded
runs, target flow `0 -> 8` first changes its sender pacing rate when a receiver
grant sets a 25-Gb/s receiver-downlink cap and later changes pacing again when
shared-fabric telemetry becomes binding.

The simulation used commit `88f9a76b7dc0e76241d2a785586969cd93e221ec`.
The trace contains 11 four-MiB PG4 flows on the 16-host OS4 topology.  The
first four form a receiver fan-in; seven more flows start 100 us later and
create an eight-flow shared-fabric bottleneck.  GUARD uses lambda 1, no
proactive release, no size priority, no sender SRPT, no unused-share
reclamation, PFC on, and IRN off.

`analysis/cap_transition_per_seed.csv` is the admission summary and
`analysis/cap_transition_per_flow.csv` contains all 55 flow-level transition
counts.  `raw/seed*/` contains the exact input snapshot, normalized config,
FCT, GUARD counters, queue/PFC summaries, and a deterministic gzip-compressed
controller trace.  The controller CSV footer records attempted, written, and
truncated rows; admission requires attempted equals written and truncated is
zero.

Verify file integrity from the repository root:

```sh
sha256sum -c experiments/artifacts/cap_transition/SHA256SUMS
```

Recreate the traffic inputs with `experiments/generate_cap_transition.py` and
re-run them through `mix/run.py --flow_file ...`; validate new output IDs with
`experiments/summarize_cap_transition.py`.  The five admitted output IDs are
listed in `analysis/cap_transition_admission.json`.
