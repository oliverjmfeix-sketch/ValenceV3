# Phase C handover — projection_rule SSoT

> Session handover for cross-session continuation of Phase C work.
> Written 2026-04-28 after Commit 2.5 landed. Read this first if
> resuming Phase C in a fresh session.

## Where we are

**Branch:** `v4-deontic`, mirrored to `origin/v4-deontic` since
2026-04-28. Worktree: `C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic`.
**HEAD:** `525436a` (Phase C Commit 2.5 — provenance edges).

**Pilot state:** Phase C — projection rules as graph SSoT. 10 commits
landed; **3 remaining** (3, 4, 5). All commits preserve the validation
harness baseline (A1=pass, A4 missing=45 spurious=6 mismatched=0,
A5=pass, A6=pass).

The full design lives at [docs/v4_phase_c_design.md](v4_phase_c_design.md).
Read it before continuing — it's the single source of truth for
schema decisions, executor architecture, and the migration plan.

## Phase C commits already done

| # | SHA | Coverage |
|---|---|---|
| 0a | `db63798` | Heuristics → extraction post-processing (`_normalize_v3_data`) |
| 0b | `bd479d3` | One-time fixup of valence_v4 (4 baskets cleaned) |
| 1 | `2f6f60b` | projection_rule schema (24 entity types, 27 relations) |
| 1.5 | `69cd388` | Pilot rule end-to-end gate PASSED (general_rp_basket scalar parity) |
| 2 | `15239e9` | Mechanical converter — 13 rules pass scalar parity |
| 2.1 | `22e1566` | Relation templates (scope edges) — 20/20 edge-set match |
| 2.2 | `77364b4` | Condition templates with dynamic predicates (ratio basket) |
| 2.3 | `c54769f` | Defeater templates — 5 defeaters + 5 defeats edges |
| 2.4 | `89850f4` | Builder sub-source rules + criterion filtering + edge attrs |
| 2.5 | `525436a` | Provenance — 27 `produced_by_rule` edges |

## What rule-based projection currently emits

For Duck Creek deal `6e76ed06`, when the converter runs against an
empty-ish graph state, the rule-based path produces:

- **23 norms** (vs python's 23): 14 from norm rules + 9 from builder rules
- **5 defeaters** + 5 `defeats` edges (vs python's 5)
- **3 condition entities** for the `ratio_rp_basket_permission` condition tree (vs python's 3)
- **9 `norm_contributes_to_capacity` edges** — all match by source/pool/aggregation_function/child_index
- **All scope edges** (subjects, actions, objects, instruments) — byte-identical to python
- **27 `produced_by_rule` provenance edges** (new — python doesn't emit these)

All converter-emitted entities use `conv_*` prefix on their IDs to avoid
collision with python-projected entities currently in valence_v4. After
Commit 4 (switchover), the prefix goes away and rule-based emission
replaces python projection entirely.

## What's still missing for byte-identical full parity

These items are emitted by python's `deontic_projection.py` but NOT yet
by rule-based projection:

- **`carryforward` / `carryback` edges** (1 each, on management_equity_basket).
  These come from GT YAML authoring, not v3 entities. Handled by
  `load_ground_truth.py` separately, not deontic_projection.py — so
  they shouldn't be a blocker for switchover. **Verify before Commit 4:**
  query `valence_v4` for `norm_provides_carryforward_to` and
  `norm_provides_carryback_to` instances; confirm they come from
  load_ground_truth not deontic_projection.

- Phase B additions (already separate from the 15 deontic_mappings):
  reallocation edges, proceeds_flow edges, temporal anchors. These
  flow through different paths in `deontic_projection.py`. Audit
  before Commit 4 to ensure rule-based path covers them or they're
  preserved as separate emission paths.

## Critical schema state

valence_v4 schema currently has 971 distinct types. Phase C added:

- **24 projection_rule entity types** (rule, criteria, value sources,
  templates, fillers, etc.)
- **27 relations** (rule_has_match_criterion, attribute_emission_uses_value,
  template_emits_relation, role_assignment_filled_by, ...)
- **`cleaned_by_phase_c_commit_0`** marker on RP basket subtypes
  (Commit 0b)
- **`specifies_operator` and `specifies_reference_label`** on
  predicate_specifier (Commit 2.2 — for dynamic predicate construction)
- **rp_basket / rdp_basket / jcrew_blocker / blocker_exception**
  now play `produced_by_rule:triggering_v3_entity` (Commit 2.5)

valence_v4 data state (post Commit 2.5, pilot run):
- 24 projection_rules (15 norm rules + 6 defeater rules + 1 b_aggregate
  + 8 builder sub-source). NOTE: 1 retained pilot rule from Commit 1.5
  (`rule_general_rp_basket`) coexists with 14 converter-rebuilt rules.
- ~140 relation_templates
- ~50 attribute_value_criterion entries
- ~80 role_assignments + role_fillers
- ~250 value_sources (literal_string + literal_long + literal_boolean +
  v3_attribute + deal_id + concatenation)
- Pilot rule's emitted norm (`pilot_*` prefix) cleaned up; rule subgraph
  retained.
- Converter-emitted norms/defeaters/conditions cleaned up; all 24 rule
  subgraphs retained.

## How to resume

### Worktree setup

```bash
cd C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic
git fetch origin
git status   # should be clean, on v4-deontic, HEAD 525436a
git log --oneline -5  # confirm 525436a is HEAD
```

### Environment

`.env` at `C:/Users/olive/ValenceV3/.env` (already restored). For v4 work
override `TYPEDB_DATABASE`:

```bash
export TYPEDB_DATABASE=valence_v4   # or set per-command
```

`.venv` Python is 3.11 (NOT 3.12 as some older docs say). typedb-driver
+ pyyaml + dotenv installed. Anthropic SDK NOT installed (Phase C's
"no Claude SDK calls" rule still applies).

### Verify connectivity + schema state

```bash
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -c "
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('C:/Users/olive/ValenceV3/.env'), override=True)
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
d = TypeDB.driver(os.environ['TYPEDB_ADDRESS'],
                  Credentials(os.environ['TYPEDB_USERNAME'], os.environ['TYPEDB_PASSWORD']),
                  DriverOptions())
tx = d.transaction('valence_v4', TransactionType.READ)
for tp in ('projection_rule', 'norm_template', 'relation_template',
           'attribute_emission', 'value_source', 'condition_template',
           'predicate_specifier', 'defeater_template', 'role_filler',
           'match_criterion'):
    r = tx.query(f'match \$x isa {tp}; select \$x;').resolve()
    print(f'  {tp}: {len(list(r.as_concept_rows()))}')
tx.close()
d.close()
"
```

Expected: ~24 projection_rules, ~15 norm_templates, ~140 relation_templates,
~250 value_sources, etc. If projection_rule returns 0, the schema or
data didn't load — investigate before proceeding.

### Re-run the converter (idempotent — cleanup + re-author all rules)

```bash
TYPEDB_DATABASE=valence_v4 "C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" \
  -m app.scripts.phase_c_commit_2_converter --deal 6e76ed06
```

Expected output (last lines):
```
AGGREGATE NORMS: 13 PASS, 0 FAIL, 1 no_v3_data, 0 no_reference, 0 emit_failed
AGGREGATE BUILDER: 9 norms emitted (b_agg + sub-sources)
AGGREGATE DEFEATERS: 5 emitted
```

Then run the harness:

```bash
TYPEDB_DATABASE=valence_v4 "C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" \
  -m app.services.validation_harness --deal 6e76ed06
```

Expected: A1=pass, A4 missing=45 spurious=6 mismatched=0, A5=pass, A6=pass.

If the harness shows extra spurious entities, you forgot the cleanup
step — `conv_*` norms/defeaters/conditions need deletion before harness
runs. Or run cleanup directly:

```bash
"C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" -c "
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('C:/Users/olive/ValenceV3/.env'), override=True)
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
d = TypeDB.driver(os.environ['TYPEDB_ADDRESS'],
                  Credentials(os.environ['TYPEDB_USERNAME'], os.environ['TYPEDB_PASSWORD']),
                  DriverOptions())
for q in [
    'match \$n isa norm, has norm_id \$nid; \$nid like \"conv_.*\"; delete \$n;',
    'match \$d isa defeater, has defeater_id \$did; \$did like \"conv_.*\"; delete \$d;',
    'match \$c isa condition, has condition_id \$cid; \$cid like \"conv_.*\"; delete \$c;',
]:
    wtx = d.transaction('valence_v4', TransactionType.WRITE)
    try: wtx.query(q).resolve(); wtx.commit()
    except Exception:
        if wtx.is_open(): wtx.close()
d.close()
"
```

## Remaining commits

### Commit 3 — parallel run + benchmark gate

Goal: compare full output between python projection and rule-based
projection on a fresh DB. Performance benchmark per Phase C design's
Q6 gate (>10× regression triggers denormalization).

Approach:
- Create a scratch DB `valence_v4_pilot` (or reuse existing pattern).
  Apply schema, copy v3 entities, copy projection_rule subgraphs.
- Run python projection → write to scratch DB
- Cleanup, then run rule-based projection → write to same scratch DB
  (with `conv_` prefix; or delete python output and let rule-based emit
  to non-prefixed IDs)
- Structural diff: compare norm count, scope edge count,
  contributes_to edge count, defeater count, condition count, edge
  attribute values
- Benchmark: time both runs end-to-end on the same Duck Creek deal

Expected output: zero structural diff (modulo prefix), benchmark within
10× of python's runtime. If >10×, denormalization (e.g., precomputed
match_query strings) needed before Commit 4.

Implementation pointer: `app/scripts/phase_c_commit_3_parallel_run.py`
(new file). Should reuse `phase_c_commit_2_converter.py`'s execute flow.

### Commit 4 — switchover

Goal: route `app/services/deontic_projection.py:project_deal()` through
the new executor. Delete `project_entity()`, `_project_builder_sub_sources()`,
`_project_jcrew_defeaters()`. The python projection becomes a thin
wrapper around the rule-based executor.

**HARD GATE:** Commit 4 cannot start until Commit 3's parallel run shows
zero structural diff AND benchmark passes the 10× threshold.

The new emission path needs to drop the `conv_` prefix on emitted IDs
(or the converter's rule subgraphs need to be re-authored without the
prefix). Decision point in Commit 3 prep.

Risk: this is a destructive change to the projection code path. Rollback
plan: keep `project_entity` etc. behind a feature flag for one commit
cycle in case rule-based has unforeseen edge cases.

### Commit 5 — deprecation + architecture docs

Goal:
- Delete `deontic_mapping`-consuming code that Commit 4 made dead.
- Document the new schema in `docs/v4_deontic_architecture.md` §4.
- Update `docs/v4_known_gaps.md` with residual items surfaced.
- Update `docs/v4_phase_c_design.md` if any design decisions changed
  during implementation.

`deontic_mapping` seed files (`rp_deontic_mappings.tql`,
`rp_condition_builders.tql`, etc.) STAY — they're archive. Only the
Python code consuming them is deleted.

## Hard constraints (still in effect)

- **No re-extraction.** `valence_v4` extraction is a $12.95 artifact.
- **TypeDB Cloud only.** `ip654h-0.cluster.typedb.com:80`. `.env` at
  `C:/Users/olive/ValenceV3/.env`.
- **No Claude SDK calls.** Phase C is fully deterministic / mechanical.
- **No merge to main.** Branch lives on `origin/v4-deontic`. Railway
  auto-deploys from main and the v4 schema is pre-production.
- **Validation harness baseline must remain preserved.** Every commit
  needs A1=pass, A4 missing=45 spurious=6 mismatched=0, A5=pass, A6=pass
  on `valence_v4` after the converter's `conv_*` outputs are cleaned up.

## Known schema-state gotchas

These tripped me during the session — note them so future-Claude can
avoid:

- **`literal_long_value_source`** is the entity name in schema, even
  though its `literal_long_value` attribute uses `value integer` (not
  `long`). The name was kept for backwards compat when `value long` was
  renamed to `value integer` in Commit 1. Don't write
  `literal_integer_value_source` — that type doesn't exist.

- **`condition_operator` not `operator`.** The condition entity owns
  `condition_operator` (renamed in earlier work). Queries that reference
  `operator` on a condition fail INF2.

- **TypeDB 3.x INF4 on iid-based matching.** `match $x iid <iid>` does
  not constrain $x's type at compile time. If the role being filled
  requires a specific type, add `$x isa <type>` to the match. Pattern
  used in `emit_provenance` after this exact failure.

- **Variable name collisions** in match-insert queries when using
  truncated iid suffixes. Two role_filler iids can share the last 8
  hex digits if created sequentially. Use longer suffix (12+ chars)
  plus role_name in var_name for uniqueness.

- **TypeDB 3.x `delete` cascade.** Deleting a norm deletes its outgoing
  relations (norm_binds_subject, etc.) automatically. Deleting a
  projection_rule does NOT cascade-delete its norm_template / criteria /
  value_sources. The converter cleanup must explicitly delete
  rule_conv_*, nt_conv_*, rt_conv_*, ct_conv_*, dt_conv_*-prefixed
  entities.

- **Match-delete-insert in single query** is required for attribute
  updates where the entity owns the attribute (TypeDB 3.x INF4 fires
  on split match-delete then match-insert because the second match
  doesn't carry the type narrowing). Pattern in
  `app/services/v3_data_normalization.py`.

- **Match-criterion-group OR semantics** are evaluated **post-fetch**
  in Python (`matches_filters` in executor), not in TypeQL. The
  query-time match is broad (entity_type only); filter groups narrow
  in Python. This is a design-time decision per Phase C design Q1.

## Files of interest (paths verified)

### Schema
- [app/data/schema_v4_deontic.tql](../app/data/schema_v4_deontic.tql) — full v4 schema with §C.1-§C.8 Phase C additions

### Executor
- [app/services/projection_rule_executor.py](../app/services/projection_rule_executor.py) — typed-dispatch interpreter (now ~1000 lines)

### Converter
- [app/scripts/phase_c_commit_2_converter.py](../app/scripts/phase_c_commit_2_converter.py) — generates 24 rules from deontic_mapping + builder spec

### Pilot (Commit 1.5)
- [app/data/pilot_rule_general_rp_basket.tql](../app/data/pilot_rule_general_rp_basket.tql) — hand-authored pilot rule TQL
- [app/scripts/phase_c_commit_1_5_pilot.py](../app/scripts/phase_c_commit_1_5_pilot.py) — pilot runner with parity check

### One-time fixup (Commit 0)
- [app/services/v3_data_normalization.py](../app/services/v3_data_normalization.py) — scale-coercion logic
- [app/scripts/phase_c_commit_0b_fixup.py](../app/scripts/phase_c_commit_0b_fixup.py) — one-time data fixup
- [docs/v4_phase_c_commit_0b/](v4_phase_c_commit_0b/) — pre/post snapshot artifacts

### Design + handover docs
- [docs/v4_phase_c_design.md](v4_phase_c_design.md) — full design spec (read first)
- [docs/v4_handover.md](v4_handover.md) — Phase B handover (also still relevant)
- [docs/v4_known_gaps.md](v4_known_gaps.md) — residual gaps from Phase B

### Existing code being replaced
- [app/services/deontic_projection.py](../app/services/deontic_projection.py) — current python projection (target of Commit 4 switchover)
- [app/data/rp_deontic_mappings.tql](../app/data/rp_deontic_mappings.tql) — 15 source mappings (kept as archive after Commit 5)
- [app/data/rp_condition_builders.tql](../app/data/rp_condition_builders.tql) — 5 condition_builder_specs (also archive)
