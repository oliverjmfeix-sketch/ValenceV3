# CLAUDE.md — Valence V3

> Auto-loaded by Claude Code at session start. Read this first.

## SESSION START: Always run first

Before doing ANYTHING else, run `git pull origin main` to get the latest code.
Do NOT rely on cached/stale versions.

## CRITICAL WARNINGS

1. **NEVER touch `C:\Users\olive\mfn-lens-main`** — that is a DEPRECATED repo. All work happens here in ValenceV3.
2. **ALWAYS push after commit:** `git push origin main`
3. Railway auto-deploys from GitHub. **NEVER use `railway up`.**
4. **NEVER install typedb-driver locally.** It only exists on Railway. Do NOT run TypeDB scripts locally — make code changes, commit, push. Railway runs them on deploy.
5. **NEVER trigger extraction or re-extraction without explicit user confirmation.** Always ask and wait for approval before calling `/re-extract`, `/upload`, or any endpoint that runs the Claude extraction pipeline. These cost money and take minutes to run.

## Repo & Deployment

- **Local:** `C:\Users\olive\ValenceV3`
- **GitHub:** `oliverjmfeix-sketch/ValenceV3`
- **Branch:** `main`
- **Backend:** Railway — auto-deploys from GitHub push
- **Backend URL:** `https://valencev3-production.up.railway.app`
- **Database:** TypeDB Cloud 3.x (`ip654h-0.cluster.typedb.com:80`, database `valence`)
- **Frontend:** Lovable

## What Valence Does

Legal technology platform analyzing credit agreements (leveraged finance).
Extracts covenant provisions from PDFs, stores structured data in TypeDB,
enables cross-deal comparison and loophole detection (J.Crew, Serta, etc.).
Currently covers MFN (43 questions) and Restricted Payments (289 questions, 27 categories).

## Core Principle: SSoT (Single Source of Truth)

**TypeDB is the Single Source of Truth.** Zero hardcoded field lists, category
names, or type mappings in Python or TypeScript.

Adding a new field = add to TypeDB schema + seed data. Pipeline auto-discovers via introspection.

### SSoT violations to NEVER introduce:
- Hardcoded `category_names` dicts
- Parsing question_id strings to derive category (use TypeQL join)
- Flat attribute lists duplicating schema definitions
- Frontend category configs not sourced from TypeDB
- Any Python dict mapping category IDs to display names
- Duplicate TQL parsers (init_schema.py is the sole parser)
- Hardcoded entity-relation maps (use TypeDB schema introspection)

## Schema: schema_unified.tql

**Single schema file** (996 lines, 156 attributes, 78 entities, 27 relations).

### Provisions are PURE ANCHORS

Provisions own ZERO extracted values. Only identity + computed pattern flags:

- `rp_provision`: provision_id @key, section_reference, source_page, extracted_at,
  jcrew_pattern_detected, serta_pattern_detected, collateral_leakage_pattern_detected
- `mfn_provision`: provision_id @key, section_reference, source_page, extracted_at,
  yield_exclusion_pattern_detected

### Three Data Channels

ALL extracted data flows through exactly one of:

**Channel 1 — Scalar (provision_has_answer)**
Boolean, string, number answers keyed by question_id.
Write: `graph_storage.store_scalar_answer(provision_id, question_id, value, answer_type)`
Read: Query provision_has_answer relation. NEVER read flat attributes on provisions.

**Channel 2 — Multiselect (concept_applicability)**
Which concepts from a closed set apply. Example: facility_prong → term_loans, revolver.

**Channel 3 — Entity (typed entities + relations)**
Structured objects with multiple attributes: baskets, sources, blockers, pathways.

### Abstract Parent: provision_has_extracted_entity

All 12 entity-bearing relations sub `provision_has_extracted_entity` with role alias `as extracted`.
This enables a single polymorphic fetch query to retrieve ALL entities for a provision.

## Entity Hierarchy

### RP Baskets (7 subtypes of rp_basket) → provision_has_basket
builder_basket, ratio_basket, general_rp_basket, management_equity_basket,
tax_distribution_basket, holdco_overhead_basket, equity_award_basket

### RDP Baskets (5 subtypes of rdp_basket) → provision_has_rdp_basket
refinancing_rdp_basket, general_rdp_basket, ratio_rdp_basket,
builder_rdp_basket, equity_funded_rdp_basket
**SEPARATE hierarchy** — uses provision_has_rdp_basket, NOT provision_has_basket.

### Builder Basket Sources (8 subtypes) → basket_has_source
starter_amount_source, cni_source, ecf_source, ebitda_fc_source,
equity_proceeds_source, investment_returns_source, asset_proceeds_source,
debt_conversion_source

### Blocker Exceptions (5 subtypes) → blocker_has_exception
nonexclusive_license_exception, intercompany_exception, immaterial_ip_exception,
fair_value_exception, ordinary_course_exception

### Capacity Pool (`shares_capacity_pool`)
Relation linking baskets that share a single capacity pool. `rp_basket` plays `pool_member`.
Used as an **absence signal** in synthesis: if baskets are NOT linked by `shares_capacity_pool`,
their capacity is separate and additive for dividend purposes.

### Other Entities
- jcrew_blocker → provision_has_blocker (~25 boolean coverage attributes)
- unsub_designation → provision_has_unsub
- sweep_tier → provision_has_sweep_tier
- de_minimis_threshold → provision_has_de_minimis
- basket_reallocation → provision_has_reallocation
- investment_pathway → provision_has_pathway (J.Crew chain analysis)
- sweep_exemption (5 subtypes) → provision_has_sweep_exemption
- intercompany_permission → provision_has_intercompany_permission
- definition_clause → provision_has_definition
- lien_release_mechanics → provision_has_lien_release

## Q&A Pipeline (`/ask-graph`) — Two-Stage Synthesis

```
User question
    → TopicRouter (SSoT categories from TypeDB)
    → get_provision_entities(deal_id, provision_type):
        Polymorphic fetch → all entities JSON
    → get_cross_covenant_entities(deal_id, "mfn_provision"):
        Walk provision_cross_reference MFN→RP (unidirectional)
        Loads RP entities for cross-covenant context
    → Stage 1: Entity Filter (Opus 4.6)
        - Classifies entities into PRIMARY / SUPPLEMENTARY / EXCLUDE
        - Returns JSON: {"primary": [...], "supplementary": [...]}
    → Stage 2: Synthesis (Opus 4.6)
        - Receives tiered context (primary + supplementary sections)
        - Category-specific synthesis guidance from TypeDB (SSoT)
        - Produces answer with citations + evidence block
```

The polymorphic fetch query lives in `graph_traversal.py` as `_FETCH_QUERY`. It uses
`provision_has_extracted_entity` abstract parent, `get_entity_annotations()` TypeDB function
for annotation lookup, and nested children subquery. Returns ~43 docs, ~80KB JSON, 0.12s.

## Ontology System

Questions live in TypeDB as `ontology_question` entities grouped by `ontology_category`.
Categories: A-N, P, S, T, Z, JC1-JC3, MFN1-MFN6 (27 total, 332 questions including MFN).

Category resolution uses TypeQL join through category_has_question — NOT prefix parsing.

## Key Files

| Purpose | File |
|---------|------|
| **Schema** (single file) | `app/data/schema_unified.tql` |
| **DB seeding (SSoT)** | `app/scripts/init_schema.py` |
| **Entity context builder** | `app/services/graph_traversal.py` |
| **Entity read + annotation cache** | `app/services/graph_reader.py` |
| **Graph write (all 3 channels)** | `app/services/graph_storage.py` |
| **Extraction pipeline** | `app/services/extraction.py` |
| **Deal API + synthesis prompt** | `app/routers/deals.py` |
| **TypeDB client** | `app/services/typedb_client.py` |
| **App startup (connection only)** | `app/main.py` |
| **Config** | `app/config.py` |
| **Extraction response models** | `app/schemas/extraction_response.py` |
| **Frontend types** | `src/types/mfn.generated.ts` |
| **Trace collector** | `app/services/trace_collector.py` |
| **Topic router** | `app/services/topic_router.py` |
| **Annotation function** | `app/data/annotation_functions.tql` |
| **Segment introspector** | `app/services/segment_introspector.py` |
| **Graph eval runner** | `app/routers/graph_eval.py` |
| **MFN eval (legacy)** | `app/routers/mfn_eval.py` |
| **Gold standard data** | `app/data/gold_standard/*.json` |
| **Cost tracker** | `app/services/cost_tracker.py` |

### Data Files (loaded by init_schema.py — 26 steps)
| File | Contents |
|------|----------|
| `schema_unified.tql` | THE schema (~1800 lines) |
| `concepts.tql` | ~170 concept instances |
| `jcrew_concepts_seed.tql` | 72 J.Crew concept instances |
| `questions.tql` | Base ontology (Categories A-K) |
| `categories.tql` | Category definitions + category_has_question |
| `ontology_expanded.tql` | Extended questions (F9-F17, G5-G7, I, L, N) |
| `ontology_category_m.tql` | Category M: Unsub distributions (10 questions) |
| `ontology_category_p.tql` | Category P: Unsub distribution basket carve-outs |
| `jcrew_questions_seed.tql` | J.Crew categories JC1-JC3 (69 questions) |
| `seed_v4_data.tql` | IP types, source types, reference instances |
| `seed_concept_entity_mapping.tql` | Concept → entity attribute routing |
| `mfn_concepts_extended.tql` | MFN-specific concepts |
| `mfn_ontology_questions.tql` | 43 MFN questions across 6 categories (incl. mfn_44) |
| `segment_types_seed.tql` | 21 document segment types |
| `seed_attribute_annotations.tql` | Question → attribute annotations (batch 1) |
| `seed_complete_annotations.tql` | Complete annotation coverage (batch 2) |
| `seed_new_questions.tql` | ~66 new questions + ~55 annotation catch-ups |
| `seed_entity_list_questions.tql` | Entity-list extraction questions |
| `seed_cross_covenant_mappings.tql` | SSoT: basket_type → provision_type (1 mapping) |
| `seed_capacity_classifications.tql` | SSoT: basket_type → capacity_category (15 mappings) |
| `seed_new_questions_008.tql` | Prompt 8: `rp_g5b` question for `no_worse_is_uncapped` boolean |
| `seed_mfn_annotations.tql` | MFN entity attribute → question annotations |
| `seed_mfn_entity_list_questions.tql` | MFN entity-list extraction questions |
| `seed_synthesis_guidance.tql` | Per-category synthesis guidance (28 entries) |
| `mfn_functions.tql` | 10 MFN pattern detection functions |
| `annotation_functions.tql` | `get_entity_annotations()` function |

## DB Seeding

`app/scripts/init_schema.py` is the **single entry point** for all DB seeding.
`main.py` does NOT load schema or seed data — it only verifies the connection and
that the database exists.

- **Fresh deploy:** run `python -m app.scripts.init_schema` first, then start server
- **Server restart:** just starts, no re-seeding, fast startup
- **Schema changes:** run init_schema.py again (drop/recreate)

### Development Workflow for Seed Data Changes
1. Edit the `.tql` seed files locally
2. `git add` → `git commit` → `git push origin main`
3. **To reseed TypeDB** (when `.tql` files changed):
   a. Temporarily change `railway.toml` startCommand to:
      `"sh -c 'python -m app.scripts.init_schema --force && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"`
   b. Commit and push to `main` — Railway deploys and reseeds
   c. Immediately revert `railway.toml` back to normal startCommand:
      `"sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"`
   d. Commit and push again — future deploys skip reseed
4. **Do NOT** install typedb-driver locally or run TypeDB scripts locally
5. **Do NOT** write temp Python scripts to reseed — use the workflow above

## Extraction Pipeline

Two-stage approach:
1. **RP Universe Extraction** — Claude reads full PDF, extracts RP-relevant sections (~$0.50, 12 min)
2. **V4 Entity Extraction** — Claude extracts structured entities from cached RP universe (~$0.10, 12 sec)

Extraction metadata loaded from TypeDB (SSoT). Pydantic validates. graph_storage stores.

- Scalar answers → `store_scalar_answer()` → provision_has_answer relation
- Entity answers → typed store methods → typed entities + relations
- Category grouping → TypeQL join through category_has_question (no hardcoded dicts)

## TypeDB 3.x Syntax

- Schema: `entity X sub Y`, `relation R, relates A, relates B`, `owns` not `has`
- Data queries: `has` for attribute access, `links` for relation roles
- `@key` for unique identifiers
- Variables scoped to single query — use match-insert for cross-references
- Use `try { }` blocks for optional attribute access in queries
- `define` is idempotent — use instead of `redefine` for adding sub + role alias
- `label($var)` converts a type variable to a string at runtime
- `fetch` returns JSON documents directly; `select` returns concept rows
- `$entity.*` fetches all attributes as key-value pairs
- Functions: `fun name($param: type) -> { return_vals }:`
- `isa!` for exact type match (excludes subtypes)

## TypeScript Types: mfn.generated.ts

Reflects all three data channels. Generated from schema_unified.tql
and extraction_response.py. Includes:
- ProvisionAnswer + ProvisionAnswerMap (Channel 1)
- ConceptApplicability + 30 concept type literals (Channel 2)
- All basket/blocker/pathway entity interfaces (Channel 3)

There is NO type_generator.py in this repo. To regenerate types, read
schema_unified.tql and extraction_response.py directly and write mfn.generated.ts.

## Current State (2026-03-26)

### Eval Pipeline Overhaul (2026-03-26)
- Removed `key_signals` substring scoring from graph_eval.py, gold standard JSON, mfn_eval.py
- Each eval run now saves 3 files: `_summary.txt`, `_verbatim.txt`, `_full.json` (shared timestamp)
- Eval results endpoint serves `.txt` as PlainTextResponse, `.json` as parsed JSON

### MFN Schema Cleanup (2026-03-26)
- Removed 7 computed pattern flags from `mfn_provision` (deleted `_compute_mfn_pattern_flags()`)
- Removed 6 duplicated MFN2 Debt Scope attributes from `mfn_provision` (refinancing_debt_excluded, ratio_debt_excluded, bridge_financing_excluded, revolving_excluded, same_currency_only, mfn_de_minimis_threshold) — data lives in `mfn_exclusion` entities
- Fixed "PURE ANCHORS" schema comment to accurately describe provision-scope scalars + pattern flags
- Reseeded TypeDB to pick up schema changes

### Cross-Covenant + Synthesis Pipeline Fixes (2026-03-26)
- Fixed `_create_cross_references()` — now idempotent with explicit logging
- Added `POST /{deal_id}/create-cross-references` admin endpoint for backfill
- Fixed cross-covenant `.get()` crash in `graph_traversal.py` (was `.get_value()`)
- Fixed provision header dropout: `prov_header` and `cross_context` now tracked as separate variables through metadata filter + entity filter + tiered context assembly
- Fixed "both"-routed questions getting only RP header — now extracts both provision headers at fetch time
- Entity filter now uses `all_docs` directly instead of re-parsing entity_context JSON

### MFN Extraction Reordering (2026-03-26)
- Deferred MFN scalar storage until after entity extraction (matches RP pattern)
- Added annotation routing to `_store_mfn_answers()` via `_load_question_to_entity_map()` + `_set_entity_attribute()`
- Re-extracted ACP Tara (RP + MFN) after reseed

### Eval Results (2026-03-26)
- ACP Tara MFN: 11/11 OK, $3.57, 739s (latest run with all fixes)
- q2 (mfn-routed): now gets cross-covenant RP context in synthesis (107K char prompt)
- q8 (both-routed): now gets both MFN + RP provision headers
- `threshold_bps` still not in q8 prompt — MFN provision attributes not yet populated (next step)
- Eval results saved locally in `app/data/eval_results/`

### Previous Sessions
- (2026-03-26 AM) Codebase cleanup: deleted 24 scripts, 7 retired .tql files, dead types, placeholder router
- (2026-03-24) Cross-covenant graph walk, MFN gold standard eval, synthesis guidance SSoT

### Test Deals
- **Duck Creek** (RP): deal_id `87852625`, provision_id `87852625_rp`. Last extracted: 2026-03-26 (post-reseed).
- **ACP Tara** (MFN): deal_id `8d0bf2f8`, provision_id `8d0bf2f8_mfn`. Last extracted: 2026-03-26 (post-reseed). 14 MFN entities, cross-reference to RP active.

### Eval Baselines
- Duck Creek RP: 6/6 gold standard questions pass (Prompt 8d + Opus 4.6)
- ACP Tara MFN: 11/11 OK, $3.57 per run (Opus 4.6 filter + synthesis)

## Open Violations

### Actionable TODOs in Code
- `app/routers/ablation.py:721` — `total_cost_usd=0.0  # TODO: aggregate from cost_tracker`
- `app/routers/deals.py:1913` — `# TODO: Persist QA cost to TypeDB`
- `app/services/cost_tracker.py:130` — `# TODO: Persist extraction cost summaries`
- `app/routers/mfn_eval.py:4` — `TODO: Delete this entire file once graph-eval handles MFN fully`

### Stale Code
- `app/routers/health.py:443` — hardcoded `target_fields` dict (legacy, now SSoT via TypeDB)

### Schema Gap
- **RP root category missing** — `category_id "RP"` entity not in TypeDB (27 categories exist, RP root not among them). Defined in `categories.tql` but insert may be failing silently. Non-blocking: no questions link to RP root directly.

### Known Data Gap
- **MFN provision scalars not populated** — `threshold_bps`, `mfn_exists`, etc. not on provision entity after extraction. Annotation routing added to `_store_mfn_answers()` but may need re-extraction to take effect. q8 synthesis prompt missing threshold data.

## Next Steps

1. **Re-extract MFN for ACP Tara** — `POST /api/deals/8d0bf2f8/re-extract-mfn` to populate provision scalars via annotation routing (code fix deployed, data not yet updated).
2. **Re-run MFN eval** — verify `threshold_bps` appears in q8 synthesis prompt.
3. **RP regression test** — `POST /api/graph-eval/87852625` to confirm Duck Creek RP still passes 6/6 after reseed.
4. **Delete mfn_eval.py** — once graph-eval covers MFN fully.
5. **Fix RP root category** — investigate why `category_id "RP"` insert fails in `categories.tql`.

## END-OF-DAY UPDATE WORKFLOW

**Trigger: When the user says `eod`, run the workflow below. No confirmation needed.**

1. Update CLAUDE.md:
   - "Current State" section: what was completed today, merge any HANDOFF.md content here
   - "Open Violations" section: grep for TODO/SSoT/hardcoded, cross-reference with schema
   - "Next Steps" section: based on what's unfinished
   - Fix any stale file references in Key Files table
   - Do NOT change the Rules, Schema, or Pipeline sections unless I explicitly ask

2. Update README.md:
   - Sync the project structure tree with actual filesystem (run: `find app -type f -name '*.py' | sort`)
   - Update API endpoints table if routes changed
   - Do NOT change setup/deploy instructions unless they're wrong

3. If HANDOFF.md still exists, merge its content into CLAUDE.md Current State, then `git rm HANDOFF.md`.

4. Show me the diff for all changed files before committing.

## Known Issues

- **Q5 now passes ($520M)**: Two-stage synthesis (Prompt 8d) + Opus 4.6 correctly identifies 4 × $130M general-purpose capacity. Self-verification block catches shared-pool errors.
- **Q6 now passes (Yes, permitted)**: Opus 4.6 correctly reasons that removing negative-EBITDA asset improves leverage, passing the uncapped no-worse test. Opus 4.5 got this wrong (said removing negative EBITDA "worsens" leverage).
- **All 6 gold standard questions pass** as of Prompt 8d + Opus 4.6 (2026-03-23).
- **J.Crew Tier 3 prompt too long**: 212K > 200K token limit. Needs context trimming or split extraction.
- **Filter cost**: Using Opus 4.6 for both filter and synthesis costs ~$0.55–0.71 per question (vs ~$0.35 with Sonnet filter). Worth it for reasoning quality.

## Cost Awareness

### Application API Calls (extraction pipeline, /ask endpoint)
- RP Universe extraction: ~$0.50 (cache it, avoid re-running)
- V4 entity extraction: ~$0.10 (fine to iterate)
- /ask-graph: ~$0.55–0.71 per question (Opus 4.6 for both filter + synthesis)
- Use Sonnet for extraction. Opus 4.6 for synthesis and entity filtering.

### Claude Code Development Tasks
- **Opus**: Architecture decisions, multi-file refactors, complex debugging,
  writing extraction prompts or synthesis rules, anything requiring deep
  understanding of the codebase or legal domain
- **Sonnet**: Standard feature implementation, single-file edits, writing
  tests, code review, moderate-complexity tasks
- **Haiku**: Syntax checks, file searches, simple grep/glob, commit
  formatting, running shell commands, validating JSON/TQL, any task
  where speed matters more than reasoning depth

## Commit Convention

```
type: concise description

Details of what changed.
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

Types: schema, extraction, api, types, data, fix, refactor

**After EVERY commit, run:** `git push origin main`

## Test Deals

- **Duck Creek** (RP): deal_id `87852625`, provision_id `87852625_rp`. Last extracted: 2026-03-26 (post-reseed). 36 entities, 165 scalar answers.
- **ACP Tara** (MFN): deal_id `8d0bf2f8`, provision_id `8d0bf2f8_mfn`. Last extracted: 2026-03-26 (post-reseed). 14 MFN entities, cross-reference to RP active.

## Eval: Gold Standard Questions

- **Duck Creek RP**: 6 questions. All pass as of Prompt 8d + Opus 4.6.
  Run via `POST /api/graph-eval/87852625`
- **ACP Tara MFN**: 11 questions. 11/11 OK, $3.57/run (Opus 4.6 filter + synthesis).
  Run via `POST /api/graph-eval/acp_tara_mfn`
  Results saved locally: `app/data/eval_results/eval_acp_tara_mfn_20260326_*.{txt,json}`
