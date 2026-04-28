# Phase C Commit 3 — parallel run + benchmark gate

Verifies full structural parity between python projection and rule-based
projection on Duck Creek deal `6e76ed06`, and benchmarks runtime, before
Commit 4 deletes the python helpers.

## How to run

```bash
cd C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic
TYPEDB_DATABASE=valence_v4 \
  C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \
  -m app.scripts.phase_c_commit_3_parallel_run --deal 6e76ed06
```

Output JSON lands in this directory, timestamped. The script ends with a
cleanup step that wipes both `conv_*` and `pilot_*` rule-based output
for the deal so the validation harness baseline (A1=pass, A4
missing=45 spurious=6 mismatched=0, A5=pass, A6=pass) is preserved.

## Findings (run on 2026-04-28T14:12 UTC)

### Structural diff — 6-edge gap localized

| Section | shared | only_python | only_rule_based | attr_diffs |
|---|---:|---:|---:|---:|
| norms | 23 | 0 | 0 | 0 |
| defeaters | 5 | 0 | 0 | 0 |
| conditions | 3 | 0 | 0 | 0 |
| contributes_to | 9 | 0 | 0 | 0 |
| defeats | 5 | 0 | 0 | 0 |
| norm_has_root_condition | 1 | 0 | 0 | 0 |
| condition_has_child | 2 | 0 | 0 | 0 |
| condition_predicate | 2 | 0 | 0 | 0 |
| norm_binds_subject | 42 | **2** | 0 | — |
| norm_scopes_action | 53 | **2** | 0 | — |
| norm_scopes_object | 20 | **1** | 0 | — |
| norm_scopes_instrument | 11 | **1** | 0 | — |

**The 6 only-python scope edges all belong to
`6e76ed06_general_rp_basket_permission`.** Cause: pilot rule
(`rule_general_rp_basket`, Commit 1.5) was authored with scalar-only
emission and never extended with scope-edge templates. The converter
explicitly skips `general_rp_basket` because the pilot covers it
(`PILOT_SOURCE_TYPE` constant in `phase_c_commit_2_converter.py`).

**Fix path before Commit 4:** drop the `PILOT_SOURCE_TYPE` skip in the
converter and let it author `rule_conv_general_rp_basket` with full
templates. Retire the pilot rule (or keep as Commit-1.5 archive
artifact). The 6-edge gap closes immediately and parity drops to zero.

### Condition ID convention divergence

Python emits conditions as `<norm_id>__c0` / `__c0_0` / `__c0_1`.
Rule-based emits `__cond_root` / `__cond_root_0` / `__cond_root_1`.
Structurally identical (root + N indexed children); IDs differ.

The diff script normalizes both to `__root` / `__root_<idx>` for
comparison. Downstream consumers that hardcode `__c0` style
condition_id queries (none known so far in v4 code) would break
post-Commit-4. Audit before Commit 4.

### Benchmark — marginally over 10× threshold

Three runs on the same deal:

| Run | python | rule-based | ratio |
|---|---:|---:|---:|
| 1 | 19.18s | 195.33s | 10.19× |
| 2 | 19.18s | 205.64s | 10.72× |
| 3 | 19.54s | 196.74s | 10.07× |

Average ≈ 10.33×. Just above the design's 10× gate.

Rule-based per-rule cost is dominated by template-walk roundtrips:
each attribute emission resolves a chain of value_sources via separate
TypeDB transactions. A rule emitting 12 attributes does ~24-50 round
trips per matched v3 entity. With ~30 rules × 1 match each = 200-300
queries per full execution.

**Denormalization options for Commit 3.x or Commit 4:**

1. **Cache match-query strings on rule entities.** Add
   `compiled_match_query` attribute to `projection_rule`; rebuild on
   rule update. Skips the per-execution walk of match_criterion
   subgraph.
2. **Bulk-fetch all emissions for a rule in one query.** Replace the
   per-attribute `load_attribute_emissions` + per-vs `resolve_value_source`
   loop with a single query that pulls `(emission_name, vs_iid,
   vs_type, literal_value, v3_attr_name, ...)` rows.
3. **Reduce transaction creation.** The executor opens a fresh READ
   transaction for nearly every read. Reuse a long-lived READ
   transaction across the rule's full attribute resolution.

Option 3 is the cheapest and likely sufficient — most of rule-based's
cost is transaction setup, not query execution. Should be tried
before authoring an attribute migration (option 1).

### Scope cliffs — 1 edge, narrow

| Relation | python emits | rule-based emits |
|---|---:|---:|
| event_provides_proceeds_to_norm | 1 | 0 |
| norm_reallocates_capacity_from | 0 | 0 |
| norm_provides_carryforward_to | 0 | 0 |
| norm_provides_carryback_to | 0 | 0 |

Only 1 cliff edge for Duck Creek: the asset_sale_event proceeds-flow
edge. Hardcoded in
`deontic_projection._project_proceeds_flows`. Carryforward / carryback
come from `load_ground_truth.py` (separate emission path) — not from
`deontic_projection.py`, so not a Commit 4 concern. Reallocations are
zero because Duck Creek has no `basket_reallocates_to` v3 entities.

**Decision needed before Commit 4:** does
`event_provides_proceeds_to_norm` stay in `deontic_projection.py` as
a separate emission path post-switchover, or does it move into a
projection_rule (would need a new rule kind: source-from-event
rather than source-from-v3-entity)?

Lightweight call: keep proceeds_flows as a separate emission path
post-Commit 4. Adding event-source matching to the rule schema is
non-trivial and the surface is small (4 hardcoded asset_sale flow
records currently).

## Hard constraints respected

- No re-extraction (v3 entities untouched throughout)
- No Claude SDK calls
- No merge to main
- TYPEDB_DATABASE=valence_v4 used
- Python 3.11 venv
- Validation harness baseline preserved post-script
  (A1=pass, A4 missing=45 spurious=6 mismatched=0, A5=pass, A6=pass)

## Aside — orphan accumulation in projection_rule subgraphs

Sized during Commit 3 prep: `valence_v4` carries ~3204 orphan
`attribute_emission`, ~2012 orphan `role_assignment`, ~18 orphan
`predicate_specifier` entities from prior converter re-runs.
`cleanup_converted_rules` in `phase_c_commit_2_converter.py` deletes
the rules + templates but explicitly leaves emissions / value_sources /
role_fillers / criteria orphaned (see lines 1084–1097).

Orphans are NOT walked during rule execution (the executor goes
top-down from rules → templates → emissions; orphans have no inbound
from any current rule). So orphans do not affect the benchmark
numbers in this script.

**Planned as Commit 3.3** — extend `cleanup_converted_rules` with a
transitive sweep. Walk reachable entities from each remaining
projection_rule (top-down via the relations the executor uses), collect
their iids, then delete `attribute_emission` / `value_source` /
`role_assignment` / `role_filler` / `match_criterion` /
`predicate_specifier` instances NOT in the reachable set. Critical:
walk from EVERY remaining `projection_rule` (including pilot), not just
`rule_conv_*`, so pilot-rule subgraphs are preserved. Run as the final
step of converter cleanup so each re-run leaves a clean state.

Not load-bearing for correctness or benchmark — orphans have no inbound
edge from any current rule, so the executor never traverses them. Worth
landing for audit clarity (`select all attribute_emission` returns the
expected ~360 instead of the bloated ~3556) and to make schema
migrations cheaper as the schema evolves.

## Gate verdict

| Gate | Result | Status |
|---|---|---|
| Structural diff = zero | 6 only-python scope edges (general_rp_basket) | **fail** (localized cause + clear fix path) |
| Benchmark ≤ 10× | 10.07–10.72× across 3 runs | **fail** (marginal; 0.07× over) |
| Scope cliff inventory | 1 proceeds_flow only; clear decision pending | **documented** |
| Harness baseline preserved | A1/A5/A6 pass; A4 counts match | **pass** |

**Recommendation:** Do not start Commit 4 until both gates close.
Commit 3.x patches in order:

1. **Commit 3.1 — drop `PILOT_SOURCE_TYPE` skip.** Converter authors
   `rule_conv_general_rp_basket` with full scope-edge / condition
   templates. Retire the pilot rule (or keep as Commit 1.5 archive).
   Re-run parallel_run; expect zero structural diff.
2. **Commit 3.2 — executor transaction-reuse denormalization.**
   Cheapest path: open one long-lived READ transaction per rule's full
   attribute resolution instead of per `_read_attr_value` call. Re-run
   parallel_run; if benchmark drops below 10×, gate closes. If not, add
   `compiled_match_query` attribute on `projection_rule` to skip the
   per-execution match_criterion subgraph walk.
3. **Commit 3.3 — orphan sweep in converter cleanup.** Hygiene patch
   to `cleanup_converted_rules` (see "Aside — orphan accumulation"
   above). Not load-bearing for correctness or benchmark, but worth
   landing before Commit 4 so the audit baseline going forward
   reflects only live entities.
