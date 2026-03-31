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
5. **NEVER trigger extraction or re-extraction without explicit user confirmation.** Always ask and wait for approval before calling `/extract/{covenant_type}`, `/upload`, or any endpoint that runs the Claude extraction pipeline. These cost money and take minutes to run.

## Repo & Deployment

* **Local:** `C:\Users\olive\ValenceV3`
* **GitHub:** `oliverjmfeix-sketch/ValenceV3`
* **Branch:** `main`
* **Backend:** Railway — auto-deploys from GitHub push
* **Backend URL:** `https://valencev3-production.up.railway.app`
* **Database:** TypeDB Cloud 3.x (`ip654h-0.cluster.typedb.com:80`, database `valence`)
* **Frontend:** Lovable

## What Valence Does

Legal technology platform analyzing credit agreements (leveraged finance).
Extracts covenant provisions from PDFs, stores structured data in TypeDB,
enables cross-deal comparison and loophole detection (J.Crew, Serta, etc.).
Currently covers RP (289 questions), MFN (43 questions), and DI (152 questions) across 39 categories.

## Core Principle: SSoT (Single Source of Truth)

**TypeDB is the Single Source of Truth.** Zero hardcoded field lists, category
names, or type mappings in Python or TypeScript.

Adding a new field = add to TypeDB schema + seed data. Pipeline auto-discovers via introspection.

### SSoT violations to NEVER introduce:

* Hardcoded `category_names` dicts
* Parsing question_id strings to derive category (use TypeQL join)
* Flat attribute lists duplicating schema definitions
* Frontend category configs not sourced from TypeDB
* Any Python dict mapping category IDs to display names
* Duplicate TQL parsers (init_schema.py is the sole parser)
* Hardcoded entity-relation maps (use TypeDB schema introspection)

## Schema: schema_unified.tql

**Single schema file** (~2360 lines, ~280 attributes, 125 entities, 51 relations).

### Provision Attributes

Provisions own identity attrs, computed pattern flags, and provision-scope scalars:

* `rp_provision`: identity + pattern flags + 16 provision-scope booleans
* `mfn_provision`: identity + pattern flags + 11 provision-scope scalars
* `di_provision`: identity + 7 pattern flags + 54 provision-scope scalars (binds_borrower, uses_first_lien_leverage, etc.)

### Data Storage (Three Relation Types)

ALL extracted data flows through exactly one of:

**Scalar answers (provision_has_answer)**
Boolean, string, number answers keyed by question_id.
Write: `graph_storage.store_scalar_answer(provision_id, question_id, value, answer_type)`
Read: Query provision_has_answer relation.

**Multiselect answers (concept_applicability)**
Which concepts from a closed set apply. Example: facility_prong → term_loans, revolver.

**Typed entities (provision_has_extracted_entity subtypes)**
Structured objects with multiple attributes: baskets, sources, blockers, pathways.

## Entity Storage

All extracted entities use typed relations that sub `provision_has_extracted_entity`.
This enables a single polymorphic fetch to retrieve all entities for a provision.

Entity types include baskets (RP, RDP, and DI), builder sources, blocker exceptions,
sweep tiers, investment pathways, incremental facilities, leverage tiers, and others.
See `schema_unified.tql` for the complete hierarchy.

Key architectural point: `shares_capacity_pool` relation links baskets that share
capacity — its **absence** signals separate/additive capacity (used in synthesis).

## Q&A Pipeline (`/ask-graph`) — Two-Stage Synthesis

```
User question
    -> TopicRouter (SSoT categories from TypeDB)
    -> get_provision_entities(deal_id, provision_type):
        Polymorphic fetch -> all entities JSON
    -> get_cross_covenant_entities(deal_id, "mfn_provision"):
        Walk provision_cross_reference MFN->RP (unidirectional)
        Loads RP entities for cross-covenant context
    -> Stage 1: Entity Filter (Opus)
        - Classifies entities into PRIMARY / SUPPLEMENTARY / EXCLUDE
        - Returns JSON: {"primary": [...], "supplementary": [...]}
    -> Stage 2: Synthesis (Opus)
        - Receives tiered context (primary + supplementary sections)
        - Category-specific synthesis guidance from TypeDB (SSoT)
        - Produces answer with citations + evidence block
```

The polymorphic fetch query lives in `graph_traversal.py` as `_FETCH_QUERY`. It uses
`provision_has_extracted_entity` abstract parent, `get_entity_annotations()` TypeDB function
for annotation lookup, and nested children subquery.

## Ontology System

Questions live in TypeDB as `ontology_question` entities grouped by `ontology_category`.
Categories: A-N, P, S, T, Z, JC1-JC3, MFN1-MFN6, DI1-DI12 (39 total, ~470 questions including MFN + DI).

Category resolution uses TypeQL join through category_has_question — NOT prefix parsing.

## Extraction Pipeline

Unified extraction for all covenant types:

```
1. get_or_build_universe(deal_id, covenant_type)
   -> Check JSON cache ({deal_id}_{covenant_type}_universe.json)
   -> If missing or force_rebuild: segment document + slice by pages
   -> Cache universe as JSON

2. extract_covenant(deal_id, covenant_type, universe)
   -> Load questions from TypeDB (SSoT)
   -> Ensure provision exists
   -> Call 0: Entity extraction (entity_list questions) — async
   -> Calls 1-N: Scalar extraction (dynamic batching, parallel via asyncio.gather)
   -> Store to TypeDB via GraphStorage
   -> Return ExtractionResult
```

Key classes:
* `CovenantUniverse` — document slice with sections dict, raw_text, segment_map, JSON cache
* `ExtractionResult` — unified result with answers_stored, entities_created, cost data

Key design: scalar batches run in parallel (`asyncio.gather` + `AsyncAnthropic`),
entity extraction runs first (entities must exist before scalar annotation routing).

## Key Files

| Purpose | File |
| --- | --- |
| **Schema** (single file) | `app/data/schema_unified.tql` |
| **DB seeding (SSoT)** | `app/scripts/init_schema.py` |
| **Entity context builder** | `app/services/graph_traversal.py` |
| **Entity read + annotation cache** | `app/services/graph_reader.py` |
| **Graph write (storage)** | `app/services/graph_storage.py` |
| **Extraction pipeline** | `app/services/extraction.py` |
| **Deal API + synthesis** | `app/routers/deals.py` |
| **TypeDB client** | `app/services/typedb_client.py` |
| **App startup** | `app/main.py` |
| **Config** | `app/config.py` |
| **Extraction response models** | `app/schemas/extraction_response.py` |
| **Trace collector** | `app/services/trace_collector.py` |
| **Topic router** | `app/services/topic_router.py` |
| **Segment introspector** | `app/services/segment_introspector.py` |
| **Graph eval runner** | `app/routers/graph_eval.py` |
| **Cost tracker** | `app/services/cost_tracker.py` |
| **Cross-covenant linking** | `app/services/cross_covenant.py` |
| **DI query service** | `app/services/di_query_service.py` |
| **Eval runner skill** | `app/skills/eval_runner.py` |

### Data Files (loaded by init_schema.py)

| File | Contents |
| --- | --- |
| `schema_unified.tql` | THE schema (~2360 lines) |
| `concepts.tql` | ~170 concept instances |
| `jcrew_concepts_seed.tql` | 72 J.Crew concept instances |
| `questions.tql` | Base ontology (Categories A-K) |
| `categories.tql` | Category definitions + category_has_question |
| `jcrew_questions_seed.tql` | J.Crew categories JC1-JC3 |
| `mfn_ontology_questions.tql` | 43 MFN questions across 6 categories |
| `di_ontology_questions.tql` | 151 DI questions across 12 categories (DI1-DI12) |
| `segment_types_seed.tql` | 21 document segment types (rp/mfn/di universe fields) |
| `seed_concept_entity_mapping.tql` | Concept -> entity attribute routing |
| `seed_entity_list_questions.tql` | Entity-list extraction questions (RP) |
| `seed_di_reference_entities.tql` | DI reference entities (35 instances: ratio tests, conditions, priorities, facility types) |
| `seed_di_annotations.tql` | DI entity attribute -> question annotations (152) |
| `seed_di_entity_list_questions.tql` | DI entity-list extraction questions (leverage_tier) |
| `seed_cross_covenant_mappings.tql` | SSoT: basket_type -> provision_type (RP + DI) |
| `seed_capacity_classifications.tql` | SSoT: basket_type -> capacity_category (RP + DI) |
| `seed_new_questions.tql` | ~66 new questions + ~55 annotation catch-ups |
| `seed_mfn_annotations.tql` | MFN entity attribute -> question annotations |
| `seed_mfn_entity_list_questions.tql` | MFN entity-list extraction questions |
| `seed_synthesis_guidance.tql` | Per-category synthesis guidance |
| `question_annotations.tql` | Question -> attribute annotations |
| `annotation_functions.tql` | `get_entity_annotations()` function |
| `di_functions.tql` | DI capacity + vulnerability TypeDB functions (9 functions) |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (used by Railway) |
| `/api/deals` | GET | List all deals |
| `/api/deals/{id}` | GET | Get deal with primitives |
| `/api/deals/{id}` | DELETE | Delete deal and all data |
| `/api/deals/upload` | POST | Upload PDF and extract RP + DI + MFN |
| `/api/deals/{id}/upload-pdf` | POST | Upload PDF for existing deal |
| `/api/deals/{id}/extract/{type}` | POST | Extract specific covenant (rp, mfn, di) |
| `/api/deals/{id}/link-covenants` | POST | Trigger cross-covenant linking (DI↔MFN, DI↔RP) |
| `/api/deals/{id}/{type}-universe` | GET | Get cached universe JSON |
| `/api/deals/{id}/ask-graph` | POST | Q&A via entity graph (primary) |
| `/api/deals/{id}/ask` | POST | Q&A via scalar answers |
| `/api/deals/{id}/answers` | GET | All answers for a deal |
| `/api/deals/{id}/status` | GET | Extraction status |
| `/api/ontology/questions` | GET | All questions by category |
| `/api/graph-eval/{deal_id}` | POST | Run gold standard eval |
| `/api/gold-standard` | GET | List all gold standard sets |
| `/api/gold-standard/{deal_id}` | GET/PUT | Get or update gold standard |
| `/api/eval-results/{deal_id}` | GET | List eval result files |

## Running Evals

Gold standard Q&A sets live in `app/data/gold_standard/*.json`. Three sets exist:
* `87852625.json` — Duck Creek RP (6 questions)
* `acp_tara_mfn.json` — ACP Tara MFN (11 questions)
* `duck_creek_rp_mfn.json` — Duck Creek combined (22 questions: 12 RP + 10 MFN)

To run an eval:
```bash
# Full eval against a gold standard set
curl -X POST "https://valencev3-production.up.railway.app/api/graph-eval/duck_creek_rp_mfn"

# Run subset of questions only
curl -X POST "https://valencev3-production.up.railway.app/api/graph-eval/duck_creek_rp_mfn" \
  -H "Content-Type: application/json" \
  -d '{"question_ids": ["duck_creek_q1", "duck_creek_q2"]}'
```

Results are saved to `/app/uploads/eval_results/` as three files per run:
`*_summary.txt`, `*_verbatim.txt`, `*_full.json`

Retrieve results:
```bash
curl "https://valencev3-production.up.railway.app/api/eval-results/duck_creek_rp_mfn"
```

## DB Seeding

`app/scripts/init_schema.py` is the **single entry point** for all DB seeding.
`main.py` does NOT load schema or seed data — it only verifies the connection.

* **Fresh deploy:** run `python -m app.scripts.init_schema` first, then start server
* **Server restart:** just starts, no re-seeding, fast startup
* **Schema changes:** run init_schema.py again (drop/recreate)

### Development Workflow for Seed Data Changes

1. Edit the `.tql` seed files locally
2. `git add` -> `git commit` -> `git push origin main`
3. **To reseed TypeDB** (when `.tql` files changed):
   a. Temporarily change `railway.toml` startCommand to:
      `"sh -c 'python -m app.scripts.init_schema --force && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"`
   b. Commit and push to `main` — Railway deploys and reseeds
   c. Immediately revert `railway.toml` back to normal startCommand
   d. Commit and push again — future deploys skip reseed
4. **Do NOT** install typedb-driver locally or run TypeDB scripts locally

## TypeDB 3.x Syntax

* Schema: `entity X sub Y`, `relation R, relates A, relates B`, `owns` not `has`
* Data queries: `has` for attribute access, `links` for relation roles
* `@key` for unique identifiers
* Variables scoped to single query — use match-insert for cross-references
* Use `try { }` blocks for optional attribute access in queries
* `define` is idempotent — use instead of `redefine` for adding sub + role alias
* `label($var)` converts a type variable to a string at runtime
* `fetch` returns JSON documents directly; `select` returns concept rows
* `$entity.*` fetches all attributes as key-value pairs
* Functions: `fun name($param: type) -> { return_vals }:`
* `isa!` for exact type match (excludes subtypes)

## Current State (2026-03-31)

### Debt Incurrence (DI) Expansion — Phases 1-5 Complete

Full DI covenant type added alongside RP and MFN. 6-phase plan, 5 phases complete:

* **Phase 1 — Schema:** §11 DI section in schema_unified.tql. 4 reference entities (ratio_test_type, debt_condition_type, di_lien_priority, di_facility_type), di_provision (54 owns), incremental_facility (66 owns), di_basket abstract + 20 subtypes, leverage_tier, 14 relations. ~97 new attributes. 35 seeded reference instances.
* **Phase 2 — Questions:** 12 categories (DI1-DI12), 151 questions, 1 entity-list question (leverage_tier), 152 annotations routing to di_provision + incremental_facility + 16 basket subtypes.
* **Phase 3 — Extraction pipeline:** graph_storage._PROVISION_TYPES includes di_provision, _provision_type_from_id handles _di suffix, segment_introspector reads di_universe_field, run_extraction runs RP → DI → MFN (DI before MFN for incremental_facility linkage).
* **Phase 4 — Cross-covenant:** CrossCovenantService (cross_covenant.py) creates di_provision_links_mfn, di_provision_links_rp, incremental_triggers_mfn, di_feeds_rp_builder. Step 6 in run_extraction(). Manual endpoint: POST /{id}/link-covenants. SSoT: all trigger data from TypeDB, no hardcoded defaults.
* **Phase 5 — TypeDB functions:** 9 functions in di_functions.tql (capacity: total_incremental, basket, projected_grower, total_capped; vulnerability: ied_priming_risk, trapdoor_basket; aggregation: count_permitted_baskets, count_grower_baskets, get_basket_types). DIQueryService wrapper returns None on missing data.
* **Phase 6 — Gold standard evals:** NOT YET STARTED.

**SSoT audit completed:** Fixed hardcoded valid_types, defaulted covenant routing to "both", added di_universe_field to schema + segment types, added 20 DI cross-covenant mappings + capacity classifications.

### Extraction Order

RP (30-50%) → DI (55-65%) → MFN (80-90%) → Cross-covenant linking (95%)

### Test Deals

* **Duck Creek** (RP+MFN+DI): deal_id `87852625`. Primary test deal. DI not yet extracted.
* **ACP Tara** (MFN): deal_id `8d0bf2f8`. Secondary MFN test deal.

## Open Violations

### SSoT Violations (Low Priority)

* `app/routers/deals.py` — hardcoded pattern flag name lists for RP and MFN provisions. Should introspect from TypeDB schema attributes.

### Deferred DI Schema (Liens Expansion)

* `di_basket_has_lien_basket` relation — requires Liens covenant schema (lien_basket entity)
* `sweep_reduces_debt` relation — requires Asset Sales covenant schema
* `has_intercompany_leakage_risk` TypeDB function — requires `permits_unrestricted_subs` attribute on intercompany_basket
* Liens-dependent DI functions (first_lien_debt_capacity, total_secured_capacity, baskets_missing_lien_correspondence)

### Actionable TODOs

* `app/routers/deals.py:1640` — `# TODO: Persist QA cost to TypeDB`
* `app/services/cost_tracker.py:130` — `# TODO: Persist extraction cost summaries`

## Next Steps

1. **Phase 6 — DI gold standard evals:** Create gold standard Q&A sets for DI extraction validation
2. **Test DI extraction on Duck Creek** — `POST /api/deals/87852625/extract/di` (~$TBD)
3. **Test cross-covenant linking** — `POST /api/deals/87852625/link-covenants`
4. **Run full RP+DI+MFN eval** — Create combined eval set
5. **DI synthesis guidance** — Add per-category synthesis guidance for DI1-DI12 in seed_synthesis_guidance.tql
6. **Introspect pattern flags** — Refactor hardcoded pattern flag lists to TypeDB schema introspection
7. **Liens expansion** — Add Liens covenant schema to enable DI↔Liens cross-covenant relations

## END-OF-DAY UPDATE WORKFLOW

**Trigger: When the user says `eod`, run the workflow below. No confirmation needed.**

1. Update CLAUDE.md:
   * "Current State" section: what was completed today
   * "Open Violations" section: grep for TODO/SSoT/hardcoded, cross-reference with schema
   * "Next Steps" section: based on what's unfinished
   * Fix any stale file references in Key Files table
   * Do NOT change the Rules, Schema, or Pipeline sections unless explicitly asked

2. Update README.md:
   * Sync the project structure tree with actual filesystem
   * Update API endpoints table if routes changed

3. Show me the diff for all changed files before committing.

## Cost Awareness

### Application API Calls

* Extraction (RP): ~$14 (Opus, 7 calls for ~190 answers + 21 entities)
* Extraction (MFN): ~$1-2 (fewer questions)
* Segmentation: ~$4.30 (3 Opus calls for large documents)
* /ask-graph: ~$0.55-0.71 per question (Opus for filter + synthesis)
* Use Sonnet for extraction if cost is a concern. Opus for synthesis.

### Claude Code Development Tasks

* **Opus**: Architecture decisions, multi-file refactors, complex debugging
* **Sonnet**: Standard feature implementation, single-file edits, tests
* **Haiku**: Syntax checks, file searches, simple grep/glob, commit formatting

## Commit Convention

```
type: concise description

Details of what changed.
Co-Authored-By: Claude <noreply@anthropic.com>
```

Types: schema, extraction, api, types, data, fix, refactor

**After EVERY commit, run:** `git push origin main`

## Test Deals

- **Duck Creek** (RP): deal_id `87852625`, provision_id `87852625_rp`. Last extracted: 2026-03-31 (unified pipeline). 189 answers, 21 entities. Needs re-extraction after entity SSoT refactor.
- **ACP Tara** (MFN): deal_id `8d0bf2f8`, provision_id `8d0bf2f8_mfn`. Last extracted: 2026-03-26 (post-reseed). 14 MFN entities, cross-reference to RP active.

## Eval: Gold Standard Questions

- **Duck Creek RP**: 6 questions. 6/6 OK as of 2026-03-31 (pre-SSoT-refactor), $4.40/run.
  Run via `POST /api/graph-eval/lawyer_dc_rp`
  Results: `app/data/eval_results/eval_lawyer_dc_rp_20260331_093203_*.{txt,json}`
- **Duck Creek RP+MFN**: 22 questions (Xtract report).
  Run via `POST /api/graph-eval/xtract_dc_rp_mfn`
- **ACP Tara MFN**: 11 questions. 11/11 OK, $3.57/run (Opus 4.6 filter + synthesis).
  Run via `POST /api/graph-eval/lawyer_acp_mfn`
  Results: `app/data/eval_results/eval_lawyer_acp_mfn_*.{txt,json}`
