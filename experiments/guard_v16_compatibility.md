# GUARD V16 frozen-vector PG compatibility

V15 remains rejected. Its 24 simulator runs completed, but the frozen gate
incorrectly required the first high-fan-in transition to contain PG4--PG7.
The raw record correctly contained only the earliest PG4 cohort; later target
vectors exercised PG5--PG7 and incremented the runtime mixed-vector counter.
V16 does not reinterpret that failed campaign.

V16 changes evidence only. At each frozen target vector the receiver records
the maximum number of exact runtime `GuardQpIdentity.pg` values, the union mask
over mixed vectors, and the union mask over all vectors. The mixed-size
scenario is preregistered to require maximum cardinality 3, mixed mask `0xe0`
(PG5--PG7), and all-vector mask `0xf0` (PG4--PG7). The initial transition audit
may still contain only PG4.

The ACK-clock timeout policy and every other V15 mechanism parameter remain
unchanged. Fresh seeds are 149--151. This is a candidate-only, mechanism-only
campaign: `--smoke` is mandatory and the runner/analyzer do not open FCT or
queue artifacts.

Run serially:

```bash
python3 experiments/run_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --phase preflight
python3 experiments/run_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --phase replay
python3 experiments/analyze_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --scope replay \
  --json-out /tmp/guard-v16-compat-formal-v1/replay-admission.json
python3 experiments/run_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --phase seal-replay \
  --replay-admission /tmp/guard-v16-compat-formal-v1/replay-admission.json
python3 experiments/run_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --phase compatibility
python3 experiments/analyze_guard_v16_compatibility.py \
  experiments/guard_v16_compatibility.json \
  --campaign-dir /tmp/guard-v16-compat-formal-v1 --scope compatibility \
  --json-out /tmp/guard-v16-compat-formal-v1/compatibility-admission.json
```

Any failed run or gate rejects V16 without changing seeds, masks, timeout
constants, scenarios, or admission thresholds. A performance comparison is
authorized only by a separate, fresh preregistration after V16 admission.

## Frozen outcome

V16 is **rejected at the mechanism gate**. The four V13 replay cells and all
24 fresh simulator cells completed, but `mixed_pg_n15` seed 151 recorded
`max_priority_groups=2` and `mixed_priority_group_mask=0xc0`, rather than the
preregistered exact values 3 and `0xe0`. Its all-vector mask was still `0xf0`
and it recorded five mixed freezes. Seeds 149 and 150 recorded the exact
preregistered `3/0xe0/0xf0` tuple.

The campaign was not rerun and no seed, timeout, or scenario was changed. A
mechanism-only diagnostic that replaced only the failed exact-shape predicate
with the generic internal-consistency checks admitted all 24 raw cells; it did
not read FCT or queue artifacts. This diagnostic does not reclassify V16. A
later version must use fresh seeds and preregister the actual property needed
for mixed-PG safety: runtime PG4--PG7 coverage and at least one vector spanning
two or more PGs, without requiring three particular classes to arrive in one
transaction.
