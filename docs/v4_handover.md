# v4 Deontic Pilot — Handover

Short-form summary for a fresh session continuing the work. Authoritative detail lives in the docs listed below; this page exists so you don't have to read 24 commits to pick up.

## Where we are (as of 2026-04-23)

**Branch:** `v4-deontic` (local-only, never pushed). Running in worktree `C:\Users\olive\ValenceV3\.claude\worktrees\sweet-raman-b5be00`.

**Scope:** Valence v4 pilot — covenant-analysis platform refactor from v3's attribute-heavy model to a deontic graph model. Pilot target: RP only, Duck Creek deal, 6+12 gold-standard questions. Other covenants (MFN, DI, Liens, Asset Sales, …) remain v3 behavior until RP pilot passes its acceptance test (Rule 7.1).

**What exists now:**

- **Foundational rules + architecture:** [docs/v4_foundational_rules.md](v4_foundational_rules.md), [docs/v4_deontic_architecture.md](v4_deontic_architecture.md)
- **Schema:** [app/data/schema_v4_deontic.tql](../app/data/schema_v4_deontic.tql) — deontic entities (party, action_class, object_class, state_predicate, condition, norm, defeater, event_instance, gold_question, document_segment_type, segment_norm_expectation, expected_norm_kind) + relations (norm_binds_subject, norm_scopes_action/instrument/object, norm_has_condition, norm_contributes_to_capacity with aggregation_direction + aggregation_function on edges, norm_provides_carryforward_to / _carryback_to, norm_in_segment, norm_serves_question, defeats, etc.)
- **Function library:** 6 files in `app/data/deontic_*_functions.tql` — 25 functions; see [docs/v4_function_library_audit.md](v4_function_library_audit.md) for per-function verdicts (17 graph-native, 3 minor-concern/park, 2 needs-restructure deferred to Prompt 07, 3 stubs)
- **Seeds:** deontic_primitives (18 singletons: 9 object + 9 action), state_predicates (22 per-threshold instances), segment_types (21), segment_norm_expectations (7), expected_norm_kinds (14), gold_questions (18 — 6 lawyer + 12 Xtract RP)
- **Ground truth:** [app/data/duck_creek_rp_ground_truth.yaml](../app/data/duck_creek_rp_ground_truth.yaml) — 63 norms with scoped actions/objects, conditions with topology, capacity aggregation trees, carryforward/back relations, serves_questions relationships. Loaded into TypeDB via [app/scripts/load_ground_truth.py](../app/scripts/load_ground_truth.py).
- **Harnesses:** [app/services/validation_harness.py](../app/services/validation_harness.py) runs A1–A5 completeness checks. [app/services/classification_measurement.py](../app/services/classification_measurement.py) runs six-dimensional (Horner et al. 2025) classification measurement + rule-selection accuracy (DeonticBench 2026) with short-circuit grading and confusion matrices. Claude API calls stubbed with `NotImplementedError` — wire the Anthropic SDK in Prompt 08.

## TypeDB Cloud databases

| DB | Purpose | Populated? |
|---|---|---|
| `valence` | v3 live data (untouched) | yes — v3 extractions present |
| `valence_v4` | v4 extraction target | schema + seeds only; no extracted data yet |
| `valence_v4_ground_truth` | authored ground-truth graph | 63 norms + 78 serves-question edges + 11 conditions + 20 capacity-contributor edges + 22 state predicates + 18 gold questions + 6 predicate-reference edges |

Per-deal party instances (3 roles in use: borrower, loan_party, restricted_sub) are seeded in `valence_v4_ground_truth` but NOT in `valence_v4`.

## What's pending

**Prompt 07** = projection engine + v3 extraction additions + first data load into `valence_v4`. The prompt itself hasn't been authored yet; the shape from the architecture doc §8 is:

- v3 extraction (builder_basket, ratio_basket, jcrew_blocker, etc.) stays untouched — v3's extractor remains the source of raw facts.
- Four surgical extraction-side additions (per arch doc §8.2): `capacity_aggregation_function`, `object_class`, `partial_applicability` on reallocation edges, `capacity_composition_validation` classification.
- **Projection engine** (new): declarative mapping rules (seeded in `app/data/rp_deontic_mappings.tql`) translate v3 extracted entities into v4 norms. Engine reads the mapping, iterates extracted entities, emits norm instances + relations + condition trees, gates on `norm_is_structurally_complete`.
- After projection runs, `valence_v4` has extracted v4 norms. Harness runs graph-to-graph against `valence_v4_ground_truth`.

**After Prompt 07:** Prompt 08 wires the Anthropic client into `classification_measurement.py`, runs classification measurement for real, calibrates the v1 prompts. Prompt 09–12 scope TBD but aim at the acceptance test.

## Key commits

24 commits on v4-deontic since fork from main. Most recent first:

```
928dcc6  v4: classification fills on 21 definitional norms
9955adf  v4: ground-truth-to-graph loader — graph is source of truth
5bc7fac  v4: function library graph-native audit
1fa1e13  v4: norm_serves_question relation — multi-question multi-role graph-native
a72d169  v4: norm_in_segment relation — typed segment membership
f1c2cf4  v4: condition_topology attribute — graph-native topology classification
cb8b71d  v4: cap_grower_reference + formulaic-cap + carryforward/carryback decomposition
27ecfdd  v4: aggregation_direction on norm_contributes_to_capacity — native subtract
93d8031  v4: state_predicate composite key — per-threshold instances
ed61576  v4: harnesses with six-dimensional eval, confusion matrices, rule-selection accuracy
3466fb9  v4: Duck Creek RP ground truth
c120620  v4: deontic schema + init script + snapshots
10cd890  v4: architecture spec
4bcab03  v4: establish foundational rules
8f1fe98  v4: backup v3 to sibling dir, establish v4 database + branch
```

Read commit messages for design rationale — they carry institutional memory that isn't in the docs.

## Hard-learned gotchas

Documented in memory file `typedb-patterns.md` under "v4 session gotchas." Summary:

- **`@abstract` cannot combine with `sub`** in one statement — two statements required
- **Composite `@key` / `@unique`** across attributes is unsupported; `double`-valued attrs can't be key-constrained. Composite-id string attribute (computed via [app/services/predicate_id.py](../app/services/predicate_id.py)) is the fallback
- **`FUN9`** — TypeDB 3.x rejects recursive function cycles through negation/reduction/single-return. Rewrite to avoid self-recursion through those forms
- **Function params** — underscore-prefix names rejected; unused params rejected (add parameter-use guards); `long` is not a valid type (`integer`); one `reduce` per function body
- **Value/attribute boundary** — variables bound via `has X $v` can't cross into a function declared `string` parameter. Take the entity concept instead
- **Disjunction branches** — variables appearing in some but not all branches must use unique per-branch names
- **Retroactive `@key`** — can't add to an attribute on an entity type that already has instances without the attribute populated first. Drop + re-init
- **Additive schema migrations** work in-place; type-renames/hierarchy-changes require drop/rebuild

## Open gaps / known issues

Authoritative: [docs/v4_known_gaps.md](v4_known_gaps.md). Highlights:

- **55 of 63 norms carry `<page_unknown>` placeholders** for `source_page`; **54** carry `<source_text_verification_required>` placeholders. PDF-reading pass needed before Prompt 08's round-trip check runs meaningfully. Estimated 1–2 hours of manual agreement reading.
- **Norm_kind names for §6.06(d)–(w) are provisional** — may need reclassification after PDF pass confirms letter→clause content.
- **Function library audit** flagged 3 park-worthy restructures (post-pilot) + 2 needs-restructure items (`norm_enables_hop`, `state_reachable`) deferred to Prompt 07 when typed state-transition modeling lands.
- **`condition_references_predicate` has only 6 edges in the ground-truth graph** for 11 conditions — the 5 non-atomic conditions have children that the loader doesn't yet construct. Prompt 07 projection should emit the full nested tree.
- **Harness Claude-API stub** — `_call_claude_classify` raises `NotImplementedError`; wire in Prompt 08.
- **`action_class.is_critical` et al.** — park-worthy graph-native improvements if the relevant sets grow.

## How to start

1. `git status` to confirm clean working tree on `v4-deontic`
2. `git log --oneline -5` to confirm latest is `928dcc6`
3. Read [docs/v4_foundational_rules.md](v4_foundational_rules.md) — governing invariants
4. Read [docs/v4_deontic_architecture.md](v4_deontic_architecture.md) — 800+ lines but authoritative
5. Read [docs/v4_known_gaps.md](v4_known_gaps.md) — open items
6. Read this file — summary + entry points
7. Await next prompt (expected: Prompt 07)

Verify TypeDB connectivity before any data work:

```bash
py -3.12 -c "
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('C:/Users/olive/ValenceV3/.env'), override=True)
from typedb.driver import TypeDB, Credentials, DriverOptions
d = TypeDB.driver(os.environ['TYPEDB_ADDRESS'],
                  Credentials(os.environ['TYPEDB_USERNAME'], os.environ['TYPEDB_PASSWORD']),
                  DriverOptions())
print('databases:', sorted([db.name for db in d.databases.all()]))
d.close()
"
```

Expected output includes `valence`, `valence_v4`, and `valence_v4_ground_truth`.
