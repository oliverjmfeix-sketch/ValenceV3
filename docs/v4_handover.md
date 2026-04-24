# v4 Deontic Pilot — Handover

Short-form summary for a fresh session continuing the work. Authoritative detail lives in the docs listed below; this page exists so you don't have to read 50+ commits to pick up.

## Where we are (as of 2026-04-24, post-Prompt-10)

**Branch:** `v4-deontic` (local-only, never pushed). Running in worktree `C:\Users\olive\ValenceV3\.claude\worktrees\sweet-raman-b5be00`.

**Scope:** Valence v4 pilot — covenant-analysis platform refactor from v3's attribute-heavy model to a deontic graph model. Pilot target: RP only, Duck Creek deal (`6e76ed06`), 6 lawyer gold-standard questions. Other covenants (MFN, DI, Liens, Asset Sales, …) remain v3 behavior until the RP pilot passes its acceptance test (Rule 7.1).

**What exists now (all three layers live in TypeDB Cloud):**

- **Foundational rules + architecture:** [docs/v4_foundational_rules.md](v4_foundational_rules.md), [docs/v4_deontic_architecture.md](v4_deontic_architecture.md)
- **Schema:** [app/data/schema_v4_deontic.tql](../app/data/schema_v4_deontic.tql) — deontic entities, conditions, defeaters, event_instance, segment types, classification_field_config (Prompt 10), projection infrastructure (deontic_mapping + condition_builder_spec), per-deal party seeding (Prompt 08)
- **Function library:** 6 files in `app/data/deontic_*_functions.tql` — condition / norm / capacity / pathway / validation / pattern functions
- **Projection engine:** [app/services/deontic_projection.py](../app/services/deontic_projection.py) — mapping-driven plus per-entity-type concessions for builder sub-sources and J.Crew defeaters. Includes `clear_v4_projection_for_deal` for idempotent re-runs
- **Classification harness:** [app/services/classification_measurement.py](../app/services/classification_measurement.py) — V2 prompts, tuple-join to GT, per-field dimension relevance (Prompt 10), accuracy-on-matched headline metric (Prompt 09)
- **Validation harness:** [app/services/validation_harness.py](../app/services/validation_harness.py) — A1–A5 completeness checks. A5 implemented via `norm_extracted_from:fact`
- **Ground truth:** [app/data/duck_creek_rp_ground_truth.yaml](../app/data/duck_creek_rp_ground_truth.yaml) — 63 norms. Loaded into `valence_v4_ground_truth` via [app/scripts/load_ground_truth.py](../app/scripts/load_ground_truth.py)
- **$12.95 extracted artifact:** Duck Creek RP fully extracted into `valence_v4` (Part 5 of Prompt 07). **Must not be re-extracted.**
- **Extraction snapshot (post-Prompt-10):** [app/data/extraction_snapshots/6e76ed06.tql](../app/data/extraction_snapshots/6e76ed06.tql) — 30 entities + 29 relations dumped to TQL, committed in git. Recoverable via `restore_extraction_snapshot` if the cloud DB ever disappears. Round-trip verified (30/30 inserts, 29/29 match-inserts, 6.9s).

## TypeDB Cloud databases

| DB | Purpose | Populated? |
|---|---|---|
| `valence` | v3 live data (untouched) | yes |
| `valence_v4` | v4 extraction target + projection output | 8 rp_baskets + 1 jcrew_blocker + 5 blocker_exceptions + 3 sweep_tiers + 6 investment_pathways + 22 projected norms + 5 defeaters + 7 parties |
| `valence_v4_ground_truth` | authored ground-truth graph | 63 norms + 25 conditions + 18 gold_questions + per-deal party instances |

## Current baseline (end of Prompt 10)

**Classification accuracy (V2 prompts, structural-tuple match to GT):**

| Field | Accuracy-on-matched | Aggregate |
|---|---|---|
| `capacity_composition` | 75.0% (12/16) | 54.5% |
| `action_scope` | 87.5% (14/16) | 63.6% |
| `condition_structure` | 93.8% (15/16) | 68.2% (D1–D4 relevant only) |
| Rule-selection | **100% (via A5)** | — |

**A1–A5 verdicts:**

| Check | Verdict | Notes |
|---|---|---|
| A1 structural | **pass** | 22/22 norms structurally complete |
| A2 segment counts | fail | Coverage gaps (extraction produces fewer norms than gold expects) |
| A3 kind coverage | fail | 2 always-expected kinds missing: `builder_basket_aggregate`, `intercompany_permission` |
| A4 round-trip | fail | missing=46, spurious=6, mismatched=0. Missing is coverage; spurious is GT's narrower RDP scope |
| A5 rule-selection | **pass** | 100% per-entity-type accuracy |

## What's pending

**Prompt 11** = operations layer. The query engine that answers user questions by composing a finite set of typed operations over the graph. Target is Duck Creek's 6 gold-standard questions (`app/data/gold_standard/lawyer_dc_rp.json`). Per architecture doc §6, 11 operations:

1. `describe_norm`
2. `get_attribute`
3. `enumerate_linked`
4. `evaluate_capacity`
5. `evaluate_feasibility`
6. `enumerate_defeaters`
7. `trace_pathways`
8. `describe_relation`
9. `lookup_definition`
10. `filter_norms`
11. `enumerate_patterns`

Entry point is `app/services/deontic_operations.py` (doesn't exist yet). Plus `app/services/intent_parser.py` (natural-language → IntentObject) and `app/services/deontic_renderer.py` (result → prose).

**After Prompt 11:** Prompt 12+ scope TBD; acceptance test is the final gate (all 6 questions produce answers that substantively match the gold answers per Rule 7.4).

## Key commits since the previous handover (`e290fc4`)

27 commits across Prompts 07–10. Most recent first:

```
a027700  v4: Prompt 10 report + known-gaps follow-ups
cced216  v4: diagnostic — cap_usd + grower-pct scale fix
2be78a6  v4: A1 harness — provenance inheritance on sub-sources
71b03b6  v4: per-field dimension relevance
18ee63d  v4: projection — specific action_scope for contributors per audit
fff8e0b  v4: audit — action_scope semantics for capacity contributions
11ddc56  v4: Prompt 09 final metrics report
ea50a9f  v4: classification reporting — accuracy-on-matched as headline
8b158ab  v4: A5 harness marker — report real rule-selection accuracy
a496308  v4: projection — builder sub-source tuple population
b182a9c  v4: classification prompts V2 — vocabulary alignment
dbe7ad7  v4: Prompt 08 final metrics report
d8d907f  v4: projection — J.Crew blocker defeater emission
c54bc8f  v4: projection — builder sub-source emission with b_aggregate
388f6d0  v4: norm_kind alignment
f4ba847  v4: projection — norm_in_segment via prefix patterns
4437566  v4: projection — per-deal party seeding + subject edges
a697dc1  v4: classification harness — tuple join, not norm_id
7e5d02b  v4: init_schema_v4 safeguards — refuse to drop extraction
7ba9589  v4: Duck Creek RP — FIRST DATA LANDING ($12.95 extraction)
047ae82  v4: per-subtype extraction questions (Option A)
2959f8e  v4: classification harness — wire Claude SDK
0039967  v4: projection engine — v3 entities to v4 norms
8a05c4e  v4: projection infrastructure — extraction additions + mappings
1ac2d3f  v4: seed_loader — factored shared seed loading
0a54d21  v4: loader — full condition tree recursion + multi-scope edges
69890a6  v4: state_predicate_id integrity check
```

Read commit messages for design rationale — institutional memory lives there.

## Hard-learned gotchas

Documented in memory file `typedb-patterns.md` under "v4 session gotchas." Key ones from Prompts 07–10:

- **Multiple `define` blocks in one SCHEMA transaction fail.** Keep one `define` per schema file.
- **TypeDB 3.x WRITE transactions silently execute only the first match-insert when multiple are bundled.** `seed_loader._load_write_file` splits statements and runs each in its own tx (ported from v3's `_load_mixed_tql_file`).
- **INF11 type-inference errors on three-hop role-aliased relation joins.** Workaround: filter by attribute prefix (e.g., `$bid contains "<deal_id>_"`) instead of joining through abstract relations.
- **`isa` is polymorphic; `isa!` is exact.** Use `isa!` to avoid over-broad matches on abstract parents.
- **Schema additions apply in-place; type hierarchy changes require rebuild.** `init_schema_v4 --schema-only` is safe against preserved extraction.
- **`str(int)` vs `str(float)` differ.** `construct_state_predicate_id` coerces numeric inputs to float for consistent id format across YAML-int and Python-float callers.
- **`load_dotenv(override=True)` for CLI invocation.** `override=False` can lose the API key when launched via `py -3.12 -m`.
- **Delete queries silently fail when the referenced type isn't in the schema** (e.g., defeater_id before Fix 6 lands). Run each delete in its own tx so one failure doesn't roll back the others.

## Post-pilot follow-ups (in `docs/v4_known_gaps.md`)

- **action_scope fourth-value taxonomy.** Audit `fff8e0b` ruled Candidate A (`specific`) for capacity contributors as the pilot solution. Post-pilot, revisit Candidate C (`contributory`) if operations-layer queries reveal conflation friction.
- **cap_grower_pct extraction convention.** v3 stores fractions (1.0 = 100%), GT authors percentages (100.0). Projection coerces via `value ≤ 5.0 → ×100` heuristic. Ideal fix is extraction-side, requires re-extraction.
- **Source text / source_page verification.** 54 of 63 GT norms carry `<source_text_verification_required>` and 55 of 63 carry `<page_unknown>`. Pre-Prompt-8 pass was deferred — if operations layer surfaces issues, do the 1–2 hour PDF-reading pass.
- **Norm count drift 23→22.** Low-priority investigation. Duplicate `builder_source_other` handling suspected.
- **2 residual Claude action_scope misses** on `general_rp_basket_permission` (exp=reallocable, Claude=specific). V3 prompt candidate if measurement becomes a bottleneck.

## How to start a new session

1. `cd "C:/Users/olive/ValenceV3/.claude/worktrees/sweet-raman-b5be00"` — this is the v4 worktree
2. `git status` to confirm clean working tree on `v4-deontic`
3. `git log --oneline -5` to confirm latest is `a027700`
4. Read [docs/v4_foundational_rules.md](v4_foundational_rules.md) — governing invariants
5. Read [docs/v4_deontic_architecture.md](v4_deontic_architecture.md) — authoritative spec (esp. §6 operations for Prompt 11)
6. Read [docs/v4_prompt10_report.md](v4_prompt10_report.md) — latest baseline metrics
7. Read [docs/v4_known_gaps.md](v4_known_gaps.md) — open items
8. Read this file — summary + entry points
9. Await next prompt (expected: Prompt 11, operations layer)

### Verify TypeDB connectivity + extraction preservation

```bash
py -3.12 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from dotenv import load_dotenv
load_dotenv(Path('C:/Users/olive/ValenceV3/.env'), override=True)
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
d = TypeDB.driver(os.environ['TYPEDB_ADDRESS'],
                  Credentials(os.environ['TYPEDB_USERNAME'], os.environ['TYPEDB_PASSWORD']),
                  DriverOptions())
try:
    print('databases:', sorted([db.name for db in d.databases.all()]))
    tx = d.transaction('valence_v4', TransactionType.READ)
    try:
        for tp in ('rp_basket','jcrew_blocker','norm','defeater','party'):
            r = tx.query(f'match \$e isa {tp}; select \$e;').resolve()
            print(f'  {tp}: {len(list(r.as_concept_rows()))}')
    finally:
        tx.close()
finally:
    d.close()
"
```

**Expected output:**
- `databases:` includes `valence`, `valence_v4`, `valence_v4_ground_truth`
- `rp_basket: 8`, `jcrew_blocker: 1`, `norm: 22`, `defeater: 5`, `party: 7`

If rp_basket or jcrew_blocker returns 0, the $12.95 extraction has been lost — stop and investigate before doing any work.

## Recovery path (if cloud `valence_v4` is ever lost)

```bash
# 1. Rebuild schema + seeds into fresh valence_v4
py -3.12 -m app.scripts.init_schema_v4

# 2. Restore v3 extraction from the committed snapshot
py -3.12 -m app.scripts.restore_extraction_snapshot --deal 6e76ed06

# 3. Regenerate projection output (norms, conditions, defeaters, etc.)
py -3.12 -m app.services.deontic_projection --deal 6e76ed06

# 4. Sanity-check: measurement + harness should report same numbers
#    as docs/v4_prompt10_report.md
```

The snapshot captures only the irreplaceable v3 extraction (deal, provision, baskets, blocker, exceptions, sweep_tiers, pathways, and all relations wiring them). Projection output regenerates from it.

To refresh the snapshot after a future re-extraction or mutation:

```bash
py -3.12 -m app.scripts.export_extraction_snapshot --deal 6e76ed06
# commits overwrite app/data/extraction_snapshots/6e76ed06.tql
```

## Hard constraints

- **No re-extraction.** `valence_v4` extraction is a $12.95 artifact. `init_schema_v4` refuses to drop without `--preserve-extraction`. For seed/schema updates use `--schema-only`. Local snapshot at `app/data/extraction_snapshots/6e76ed06.tql` is the git-backed safety net.
- **TypeDB Cloud only.** `ip654h-0.cluster.typedb.com:80`. `.env` at `C:/Users/olive/ValenceV3/.env`.
- **Branch is local-only.** Do not push to remote.
- **`py -3.12`** required (not system `py`) — typedb-driver needs Python 3.12 on Windows.
- **Before any TypeQL/TypeDB Python work**, read `typedb-patterns.md` in memory.
