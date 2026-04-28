# Phase C handover — projection_rule SSoT (Phase C complete)

> **Phase C complete.** All 17 migration commits landed on `origin/v4-deontic`
> as of 2026-04-28. Rule-based projection is the only path; python projection
> (`deontic_projection.py`) is gone. This doc supersedes the in-progress
> 2026-04-28 handover that was written after Commit 2.5 — that snapshot
> exists in git history at SHA `cf2744d` if you need to see what the
> migration looked like mid-flight.

**Branch:** `v4-deontic`, mirrored to `origin/v4-deontic`.
**Worktree:** `C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic`.
**HEAD:** `d730da1` (Phase C Commit 5 — deprecation + architecture docs).

## Outcome summary

Phase C replaced `deontic_projection.py` (1,662 lines of python projection
helpers) with a typed-dispatch interpreter over `projection_rule` subgraphs
in TypeDB. Net code change across the 17 commits: **-2,939 lines**. All
v3-to-v4 mapping content lives as typed graph entities.

Two architectural goals from the design (`docs/v4_phase_c_design.md`)
both achieved:

- **All domain content in graph state** — 24 new entity types + 27 new
  relations encode every projection operation. No JSON-in-attribute, no
  embedded query strings, no per-entity-type Python branches.
- **Python role minimal** — the executor
  (`app/services/projection_rule_executor.py`) is a typed-dispatch
  interpreter: ~1,600 lines including the new `project_deal` orchestration,
  `clear_v4_projection_for_deal` cleanup with explicit relation pre-delete
  for TypeDB 3.x non-cascading roles, and `emit_asset_sale_proceeds_flows`
  Rule 5.2 concession loader.

## Commit ledger

| # | SHA | Coverage |
|---|---|---|
| 0a | `db63798` | Heuristics → extraction post-processing (`_normalize_v3_data`) |
| 0b | `bd479d3` | One-time fixup of `valence_v4` (4 baskets cleaned) |
| 1 | `2f6f60b` | `projection_rule` schema (24 entity types, 27 relations) |
| 1.5 | `69cd388` | Pilot rule end-to-end (gate PASSED — `general_rp_basket` scalar parity) |
| 2 | `15239e9` | Mechanical converter — 13 rules pass scalar parity |
| 2.1 | `22e1566` | Relation templates (scope edges) — 20/20 edge-set match |
| 2.2 | `77364b4` | Condition templates with dynamic predicates (ratio basket) |
| 2.3 | `c54769f` | Defeater templates — 5 defeaters + 5 `defeats` edges |
| 2.4 | `89850f4` | Builder sub-source rules + criterion filtering + edge attrs |
| 2.5 | `525436a` | Provenance — 27 `produced_by_rule` edges |
| in-flight handover | `cf2744d` | Cross-session continuation (superseded by this doc) |
| 3 | `8a5d11f` | Parallel run + benchmark gate (gates fail; 3.x patches identified) |
| README update | `537c300` | Add 3.3 to planned 3.x sequence |
| 3.1 | `996e139` | Retire pilot; converter authors `rule_conv_general_rp_basket`; **structural-diff gate closes** |
| 3.2 | `44616c0` | Executor transaction reuse; **benchmark gate closes** (10.26× → 7.99–8.69×) |
| 3.3 | `b1abc72` | Orphan sweep in converter cleanup (~16k orphans deleted) |
| 4 | `5bbfca1` | Delete `deontic_projection.py` + `phase_c_commit_3_parallel_run.py`; rule-based is the only path |
| 5 | `d730da1` | Delete completed one-time scripts (commit_0b_fixup, commit_1_5_pilot, pilot TQL); rewrite architecture §4.12; add §4.14 |

## Final state of `valence_v4` (Duck Creek deal `6e76ed06`)

### Rule corpus (post-converter, post-orphan-sweep)

| Type | Count |
|---|---:|
| `projection_rule` | 30 |
| `norm_template` | 25 |
| `defeater_template` | 6 |
| `relation_template` | 152 |
| `condition_template` | 3 |
| `attribute_emission` | 354 |
| `value_source` | 668 |
| `role_assignment` | 304 |
| `role_filler` | 304 |
| `match_criterion` | 42 |
| `predicate_specifier` | 2 |

Rule corpus breakdown: **14 mapping-derived + 1 b_aggregate + 8 builder
sub-source + 6 defeater + 1 unrecognized = 30 rules.** (The "1
unrecognized" slot is reserved for any future rule kind that doesn't fit
the existing `rule_conv_*` patterns; currently empty.)

### Per-deal emissions

| Type | Count |
|---|---:|
| `norm` (canonical `<deal_id>_<kind>` IDs) | 23 |
| `defeater` | 5 |
| `condition` | 3 |
| `norm_contributes_to_capacity` (polymorphic) | 9 |
| `defeats` (polymorphic) | 5 |
| `norm_has_condition` root edges | 1 |
| `norm_extracted_from` (mapping rules only) | 14 |
| `produced_by_rule` (polymorphic) | 28 |
| `event_provides_proceeds_to_norm` | 1 |

Counts are stable across re-runs of `project_deal` — verified by 3
consecutive runs returning identical totals. The `clear_v4_projection_for_deal`
function explicitly deletes `produced_by_rule`, `norm_extracted_from`, and
`event_provides_proceeds_to_norm` before the entity deletes (TypeDB 3.x
doesn't cascade-delete relations where the deleted entity plays a
non-owner role; the relation pre-delete uses the `links (role: $x)`
syntax).

### Validation harness baseline (preserved)

```
A1_structural        -> pass
A4_round_trip        -> fail (missing=45 spurious=6 mismatched=0)
A5_rule_selection    -> pass (aggregate_accuracy=1.0)
A6_graph_invariants  -> pass
```

A2 / A3 verdicts also fail per Phase B-era expectations (specific count
baselines unrelated to Phase C). The hard baseline that every Phase C
commit preserved — A1, A4 specific counts, A5, A6 — matches exactly.

## How to resume work

### Worktree setup

```bash
cd C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic
git fetch origin
git status   # should be clean, on v4-deontic, HEAD d730da1
git log --oneline -5  # confirm Phase C commits
```

### Environment

`.env` at `C:/Users/olive/ValenceV3/.env` (TypeDB Cloud credentials).
Override `TYPEDB_DATABASE=valence_v4` for v4 work; the .env defaults to
`valence` for v3.

```bash
export TYPEDB_DATABASE=valence_v4
```

`.venv` Python is **3.11** (NOT 3.12 — older docs misremember). Path:
`C:/Users/olive/ValenceV3/.venv/Scripts/python.exe`. typedb-driver +
pyyaml + dotenv installed. Anthropic SDK NOT installed (Phase C
constraint; Phase D may install it for extraction prompt work).

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
for tp in ('projection_rule', 'norm_template', 'attribute_emission',
           'value_source', 'condition_template', 'predicate_specifier',
           'defeater_template', 'role_filler', 'match_criterion'):
    r = tx.query(f'match \$x isa {tp}; select \$x;').resolve()
    print(f'  {tp}: {len(list(r.as_concept_rows()))}')
tx.close()
d.close()
"
```

Expected output (post-Phase-C):

```
projection_rule: 30
norm_template: 25
attribute_emission: 354
value_source: 668
condition_template: 3
predicate_specifier: 2
defeater_template: 6
role_filler: 304
match_criterion: 42
```

If `projection_rule` returns 0, the schema or converter run didn't
complete — re-run the converter (next section).

### Standard operations

```bash
# Re-author rule corpus from deontic_mapping archive seed; sweep orphans
TYPEDB_DATABASE=valence_v4 "C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" \
  -m app.scripts.phase_c_commit_2_converter --deal 6e76ed06

# Run rule-based projection for a deal (clear → execute all rules → seed)
TYPEDB_DATABASE=valence_v4 "C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" \
  -m app.services.projection_rule_executor --deal 6e76ed06

# Run validation harness
TYPEDB_DATABASE=valence_v4 "C:/Users/olive/ValenceV3/.venv/Scripts/python.exe" \
  -m app.services.validation_harness --deal 6e76ed06
```

## Hard constraints (still in effect for Phase D)

- **No re-extraction.** `valence_v4` extraction is a $12.95 artifact.
  Phase D will not re-extract Duck Creek; if extraction prompts change,
  the change validates on a fresh deal.
- **TypeDB Cloud only.** `ip654h-0.cluster.typedb.com:80`. No local
  TypeDB.
- **No merge to main.** `v4-deontic` stays on origin only. Railway
  auto-deploys from main and the v4 schema is pre-production.
- **Validation harness baseline must remain preserved.** A1=pass, A4
  missing=45 spurious=6 mismatched=0, A5=pass (accuracy=1.0), A6=pass
  on `valence_v4`. Every Phase D commit needs to verify this.

Phase C's "no Claude SDK calls" constraint **lifts** for Phase D —
Phase D's scope is extraction prompt work, which legitimately needs
Anthropic SDK.

## Open known-gaps (relevant to Phase D)

See `docs/v4_known_gaps.md` for the full list. The three sections
landed during Phase C:

1. **TQL-seed-based proceeds-flow emission (Rule 5.2 concession).**
   `app/data/asset_sale_proceeds_seed.tql` + `emit_asset_sale_proceeds_flows`
   in the executor stand in for a projection_rule that would match
   `event_class` entities. Revisit when the executor's fetch path
   gains deal-agnostic `event_class` matching.
2. **No automated benchmark coverage post-Commit-4.**
   `phase_c_commit_3_parallel_run.py` was deleted in Commit 4. Validation
   harness checks correctness only; no executor wall-clock baseline.
   Reintroduce a slim benchmark utility if perf becomes a concern.
3. **Reallocation projection rule (deferred from Commit 4).**
   `_project_reallocations` in the deleted python projection was a
   no-op for Duck Creek (zero `basket_reallocates_to` v3 entities).
   Future deal with reallocation v3 data needs `match_criterion`
   extended to support v3-relation matching.

## Phase D entry points

Phase D's scope (per design): **extraction prompt improvements** that
make `app/services/v3_data_normalization.py`'s post-processing
unnecessary. Once extraction emits canonical values directly,
`_normalize_v3_data` becomes dead code.

Touchpoints for Phase D:

- `app/services/extraction.py` — extraction pipeline; Phase D edits
  prompts here.
- `app/services/v3_data_normalization.py` — current scale-coercion
  heuristic (fraction → percentage). Target for deletion when Phase D
  delivers clean extraction.
- `BUILDER_OTHER_DISAMBIGUATOR` patterns or similar in extraction —
  candidates for prompt-side resolution.

Phase D does not block on Phase C; Phase C does not block on Phase D.
Both can iterate independently.

## Bundle status (pre-Phase-C-completion)

`transfers/v4-deontic_2026-04-24` in the main repo is a git bundle from
**before Commits 3–5 landed** — captures up to Commit 2.5 only. It is
NOT auto-refreshed and should be considered stale.

Canonical sync path: `git pull origin v4-deontic` (the branch is
mirrored). The bundle is a no-network bootstrap fallback only;
ad-hoc-refresh-before-use policy.

## Files of interest (paths verified post-Commit-5)

### Schema

- [app/data/schema_v4_deontic.tql](../app/data/schema_v4_deontic.tql) — full v4 schema with §C.1–§C.8 Phase C additions

### Executor (rule-based emission)

- [app/services/projection_rule_executor.py](../app/services/projection_rule_executor.py) — typed-dispatch interpreter + `project_deal` + `clear_v4_projection_for_deal` + `emit_asset_sale_proceeds_flows` (Rule 5.2 concession loader)
- [app/services/projection_utils.py](../app/services/projection_utils.py) — `temporal_defaults_for_norm_kind` (shared with `load_ground_truth.py`)

### Converter (re-authoring + orphan sweep)

- [app/scripts/phase_c_commit_2_converter.py](../app/scripts/phase_c_commit_2_converter.py) — generates 30 rules from deontic_mapping + builder/defeater spec; runs `sweep_orphans` at end of `main()`

### Seed data (Rule 5.2)

- [app/data/asset_sale_proceeds_seed.tql](../app/data/asset_sale_proceeds_seed.tql) — templated proceeds-flow seed loaded per-deal during `project_deal`

### Archive seed (still loaded by `seed_loader.py` at startup)

- [app/data/rp_deontic_mappings.tql](../app/data/rp_deontic_mappings.tql) — 15 source mappings; consumed by harness A5 + converter
- [app/data/rp_condition_builders.tql](../app/data/rp_condition_builders.tql) — 5 condition_builder_specs; same role
- [app/data/rp_mapping_kind_fixes.tql](../app/data/rp_mapping_kind_fixes.tql) — kind-name alignment patch

### Design + handover docs

- [docs/v4_phase_c_design.md](v4_phase_c_design.md) — full design spec (read for architectural rationale)
- [docs/v4_phase_c_commit_3/README.md](v4_phase_c_commit_3/README.md) — Commit 3.x migration verdicts (frozen archive)
- [docs/v4_handover.md](v4_handover.md) — Phase B handover (still relevant for primitive-layer / function-library context)
- [docs/v4_deontic_architecture.md](v4_deontic_architecture.md) — §4.12 + §4.14 describe the post-Phase-C projection layer
- [docs/v4_known_gaps.md](v4_known_gaps.md) — known gaps including Phase C concessions

## Known schema-state gotchas (carry-over for Phase D)

These tripped the Phase C migration; future work should know them:

- **`literal_long_value_source`** is the entity name in schema, even
  though its `literal_long_value` attribute uses `value integer`. Don't
  write `literal_integer_value_source` — that type doesn't exist.
- **`condition_operator` not `operator`.** The condition entity owns
  `condition_operator`. Queries that reference `operator` on a condition
  fail INF2.
- **TypeDB 3.x INF4 on iid-based matching.** `match $x iid <iid>` does
  not constrain $x's type at compile time. Add `$x isa <type>` to the
  match when the role being filled requires a specific type. Pattern
  used in `emit_provenance` after this exact failure.
- **Variable name collisions** in match-insert queries when using
  truncated iid suffixes. Two role_filler iids can share the last 8 hex
  digits if created sequentially. Use longer suffix (12+ chars) plus
  role_name in var_name for uniqueness.
- **TypeDB 3.x non-cascading role-played relations.** Deleting a norm
  does NOT auto-delete relations where it plays a non-owner role
  (`produced_by_rule:produced_entity`,
  `norm_extracted_from:norm`,
  `event_provides_proceeds_to_norm:proceeds_target_norm`). Pre-delete
  these explicitly using the `links (role: $x)` syntax — the older
  `$r (role: $x) isa relation` form parses but fails at execution with
  empty TypeDBDriverException. See `clear_v4_projection_for_deal`.
- **Match-delete-insert in single query** is required for attribute
  updates where the entity owns the attribute (TypeDB 3.x INF4 fires
  on split match-delete then match-insert). Pattern in
  `app/services/v3_data_normalization.py`.
- **Match-criterion-group OR semantics** are evaluated **post-fetch** in
  Python (`matches_filters` in executor), not in TypeQL. Query-time
  match is broad (entity_type only); filter groups narrow in Python.
  Design-time decision per Phase C design Q1.
