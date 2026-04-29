# Phase F outcome — architectural cleanup

> Phase F is **architectural cleanup**, not question-fixing. Six commits
> on `v4-deontic` (`5fdfa3a` → `283b3aa` → `761f908` → `fb436a2` →
> `ad30c7b` → final) addressing storage discipline, schema-data
> coherence, and attribute conventions.

## Summary

| Workstream | Outcome |
|---|---|
| A — Storage idempotency | ✓ landed; canonical case + reallocation case fixed |
| B — Schema-data coherence | ✓ audit complete; 3 architectural-bug fixes applied; 0 deferred |
| C — Attribute conventions | ✓ 6 conventions defined; 1 schema annotation applied; remainder deferred to Phase G prompt-side |
| Validation harness | ✓ baseline preserved across all 6 commits |

## Per-commit deliverables

### Commit 1 (`5fdfa3a`) — Storage idempotency

**Files changed:**
- `app/services/graph_storage.py`: `capacity_effect` double-write fix
  (`_build_reallocation_edge_query` line 1602-1620) + new
  `_upsert_relation_by_role_players` helper + `store_scalar_answer`
  conversion to upsert
- `app/scripts/phase_f_probe_put_semantics.py` (new): probe script
- `docs/v4_storage_patterns.md` (new): probe findings + chosen patterns

**TypeDB 3.8 `put` semantics (probe-verified):**
- Idempotent for entities with `@key` (probe 1)
- FAILS `[CNT5] @card(0..1)` for attribute updates with different
  values (probe 2)
- FAILS `[CNT9] @unique`-violation for relations with `@unique` edge
  attributes (probe 3)
- match-delete-insert in single tx is the universal upsert pattern
  (probe 4 + production)

**Verification:** Phase E's `rp_el_reallocations` re-run (post-fix)
created 2 `basket_reallocates_to` relations cleanly (was: fatal
`[CNT5] @card(0..1)` violation). Cost: $1.84.

### Commit 2 (`283b3aa`) — Schema-data coherence audit

**Files added:**
- `app/scripts/phase_f_schema_survey.py`: re-runnable read-only audit
- `docs/v4_schema_coherence_audit.md`: human audit doc
- `docs/v4_schema_coherence_audit_data.json`: programmatic survey data

**Survey scope:** 198 entities, 102 relations, 676 attributes.
**Population on Duck Creek:** 85 / 50 / 257 (43% / 49% / 38% — expected
because schema covers all covenant types but Duck Creek has only
RP+MFN+DI extracted).

**Findings categorized:**
- 4 architectural bugs → Commit 3
- 5 convention questions → Commits 4-5
- ~15 over-constrained-but-benign → known-gaps
- 0 Phase-G blockers

### Commit 3 (`761f908`) — Schema-data coherence fixes

**Files changed:**
- `app/services/graph_storage.py`: `wire_reallocation_edges`
  pre-delete pass for idempotency (line 1420 area)
- `app/data/schema_v4_deontic.tql`: `event_governed_by_norm` relation
  type added (Phase B Commit 3 deferral closed)
- `app/scripts/phase_f_apply_schema_fixes.py` (new): migration script
- `docs/v4_known_gaps.md`: 3 entries added (pre-Phase-F duplicates,
  event_governed_by_norm rules pending, remaining store_* paths)

**Idempotency proof:** `rp_el_reallocations` re-run twice;
`basket_reallocates_to` count stayed at 2 across both runs (pre-fix:
would have doubled).

**Cost:** $1.84 for the second verification re-run.

### Commit 4 (`fb436a2`) — Attribute convention definitions

**Files added:**
- `docs/v4_attribute_conventions.md`: 6 conventions + compliance
  report

**Conventions (in document):**
1. Percentage decimal (0.15 = 15%) — MIXED in current data; Phase G
2. Monetary USD raw float, non-negative — compliant
3. Boolean positive-preferred — compliant; `restricts_*` and
   `exempt_*` families documented as acceptable negative-framings
4. Identifier formats per class — compliant for stable classes
5. Source-text 2000-char cap — enforced at insert time
6. Enum-string vs subtype threshold — documented decision rule

### Commit 5 (`ad30c7b`) — Convention enforcement

**Files changed:**
- `app/data/schema_unified.tql`: `capacity_effect` canonical-value
  list comment (line 348 area)
- `app/services/v3_data_normalization.py`: docstring annotated
  "DEPRECATING IN PHASE G" with revisit trigger
- `docs/v4_known_gaps.md`: 2 entries added (Phase G percentage
  reconciliation, monetary range deferral)

**Schema range constraints deferred** (TypeDB 3.8 supports them but
no observed need; schema-additive whenever justified later).

### Commit 6 (this commit) — Outcome doc + push

**Files added:**
- `docs/v4_phase_f_outcome.md` (this file)

**Files updated:**
- `docs/v4_deontic_architecture.md`: pointer to Phase F deliverables

## Validation harness baseline (all 6 commits)

| Check | Status |
|---|---|
| A1_structural | pass |
| A4_round_trip | fail m=45 s=6 mm=0 (canonical baseline; documented expected) |
| A5_rule_selection | pass aggregate_accuracy=1.0 |
| A6_graph_invariants | pass |

Baseline preserved across every commit. No projection-layer changes;
storage-layer changes are forward-only and don't affect existing
projections.

## Phase F total cost

| Commit | Cost |
|---|---|
| 1 (probe + idempotency verification re-run) | $1.84 |
| 2 (audit, read-only) | $0 |
| 3 (schema fixes + idempotency proof re-run) | $1.84 |
| 4 (documentation) | $0 |
| 5 (annotations) | $0 |
| 6 (verification, no extraction) | $0 |
| **Total** | **$3.68** |

Within the planned $3-7 range.

## What Phase F changed in the architecture

- **Storage path is now idempotent for the canonical case.**
  `store_scalar_answer` re-runs are safe; re-running the same question
  for the same provision doesn't create duplicate
  `provision_has_answer` relations. `wire_reallocation_edges` for the
  same direction-tuple pair likewise stays at one relation.
- **`capacity_effect` cardinality bug eliminated at the source.** The
  `_build_reallocation_edge_query` no longer double-writes the
  attribute when the LLM happens to supply a value the introspection
  loop also picks up.
- **Schema-data coherence audit is a re-runnable artifact.** Future
  changes can re-run `phase_f_schema_survey.py` and get a fresh
  diff against the audit's findings.
- **`event_governed_by_norm` relation exists.** Closes the Phase B
  Commit 3 deferral. Schema-additive; populated by future
  event-class governance phase rules.
- **Conventions are documented.** Future ontology authoring,
  extraction prompts, and schema additions follow the documented
  conventions; non-conformity gets caught in code review against
  `docs/v4_attribute_conventions.md`.
- **`v3_data_normalization.py` has a revisit trigger.** When Phase G
  reconciles the percentage convention with extraction prompt output,
  the function becomes dead code and is removed.

## What Phase F deferred (now explicit known-gaps)

- Pre-Phase-F duplicate `provision_has_answer` instances —
  forward-only discipline; no backfill cleanup.
- Remaining `store_*` paths in `graph_storage.py` (`store_extraction`,
  `_store_entity_list`, `_store_single_entity`) — INSERT directly,
  but only run under full `extract_covenant` flow which is preceded
  by `delete_deal()`. No incremental-extraction trigger.
- Schema range constraints (cap_usd >= 0.0, etc.) — supported by
  TypeDB 3.8 but no observed violations; deferred until justified.
- Identifier-class regex constraints — schema-additive whenever
  justified; no observed format violations today.
- Percentage convention reconciliation (Convention 1) — Phase G
  prompt-side work; either update extraction to emit decimal form
  OR flip the convention to numeric.

## Phase G prerequisites (what Phase F establishes)

Phase G is the synthesis architecture diagnostic + entity inventory +
lawyer eval re-run. Phase F established:

- Storage discipline that allows incremental re-extraction without
  data corruption (Phase G can iterate extraction prompts and
  re-run individual questions safely).
- Schema-data coherence audit + survey script that Phase G can
  re-run after each prompt-iteration commit to verify no schema
  violations emerged.
- Attribute conventions that Phase G's prompt iterations follow.
- Documented `v3_data_normalization` revisit trigger so Phase G
  knows when to remove the module.

Phase G is not blocked on any Phase F deferral.

## Branch state at Phase F end

- Branch: `v4-deontic`
- HEAD: this commit
- Commits ahead of `origin/v4-deontic`: 6
- Push planned at end of Phase F (per Q&A locked scope: end-of-phase
  push only)
