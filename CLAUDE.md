# CLAUDE.md — Valence V3

> Auto-loaded by Claude Code at session start. Read this first.
> For full project context, see HANDOFF.md.

## CRITICAL WARNINGS

1. **NEVER touch `C:\Users\olive\mfn-lens-main`** — that is a DEPRECATED repo. All work happens here in ValenceV3.
2. **ALWAYS push after commit:** `git push origin main`
3. Railway auto-deploys from GitHub. **NEVER use `railway up`.**
4. **NEVER install typedb-driver locally.** It only exists on Railway. Do NOT run TypeDB scripts locally — make code changes, commit, push. Railway runs them on deploy.

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
Currently covers MFN (42 questions) and Restricted Payments (289 questions, 27 categories).

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

## Q&A Pipeline (`/ask-graph`)

```
User question
    → TopicRouter (SSoT categories from TypeDB)
    → get_rp_entities(deal_id):
        Section 1: Computed Findings (TypeDB analytical functions)
            - dividend_capacity_components, blocker_binding_gap_evidence,
              blocker_exception_swallow_evidence, unsub_distribution_evidence,
              pathway_chain_summary
        Section 2: Supporting Entity Data (single polymorphic TypeDB fetch → JSON)
            - All entities + attributes + annotations + children in one query
    → Claude synthesis (system rules + entity context)
    → Answer with citations + evidence block
```

The polymorphic fetch query lives in `graph_traversal.py` as `_FETCH_QUERY`. It uses
`provision_has_extracted_entity` abstract parent, `get_entity_annotations()` TypeDB function
for annotation lookup, and nested children subquery. Returns ~39 docs, 79KB JSON, 0.12s.

## Ontology System

Questions live in TypeDB as `ontology_question` entities grouped by `ontology_category`.
Categories: A-N, P, S, T, Z, JC1-JC3, MFN1-MFN6 (27 total, 289 questions).

Category resolution uses TypeQL join through category_has_question — NOT prefix parsing.

## Key Files

| Purpose | File |
|---------|------|
| **Schema** (single file) | `app/data/schema_unified.tql` |
| **DB seeding (SSoT)** | `app/scripts/init_schema.py` |
| **Entity context builder** | `app/services/graph_traversal.py` |
| **Entity read (legacy fetchers)** | `app/services/graph_reader.py` |
| **Graph write (all 3 channels)** | `app/services/graph_storage.py` |
| **Extraction pipeline** | `app/services/extraction.py` |
| **Deal API + synthesis prompt** | `app/routers/deals.py` |
| **TypeDB client** | `app/services/typedb_client.py` |
| **App startup (connection only)** | `app/main.py` |
| **Config** | `app/config.py` |
| **V4 Pydantic models** | `app/schemas/extraction_output_v4.py` |
| **Frontend types** | `src/types/mfn.generated.ts` |
| **Trace collector** | `app/services/trace_collector.py` |
| **Topic router** | `app/services/topic_router.py` |
| **Annotation function** | `app/data/annotation_functions.tql` |
| **RP analytical functions** | `app/data/rp_analysis_functions.tql` |

### Data Files (loaded by init_schema.py in order)
| File | Contents |
|------|----------|
| `schema_unified.tql` | THE schema (996 lines) |
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
| `mfn_ontology_questions.tql` | 42 MFN questions across 6 categories |
| `segment_types_seed.tql` | 21 document segment types |
| `mfn_extraction_metadata.tql` | MFN extraction metadata |
| `seed_attribute_annotations.tql` | Question → attribute annotations (batch 1) |
| `seed_complete_annotations.tql` | Complete annotation coverage (batch 2) |
| `seed_new_questions.tql` | ~66 new questions + ~55 annotation catch-ups |
| `seed_entity_list_questions.tql` | Entity-list extraction questions (incl. `rp_el_reallocations` with `{basket_subtypes}` template) |
| `seed_cross_covenant_mappings.tql` | SSoT: basket_type → provision_type (1 mapping) |
| `seed_capacity_classifications.tql` | SSoT: basket_type → capacity_category (15 mappings) |
| `mfn_functions.tql` | 10 MFN pattern detection functions |
| `rp_functions.tql` | Dividend capacity functions |
| `rp_analysis_functions.tql` | 4 RP analytical functions |
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
3. To reseed TypeDB: `railway ssh --service ValenceV3 -- python -m app.scripts.init_schema --force`
4. **Do NOT** install typedb-driver locally or run TypeDB scripts locally
5. **Do NOT** write temp Python scripts to reseed — use init_schema.py via `railway ssh`

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

613-line file reflecting all three data channels. Generated from schema_unified.tql
and extraction_output_v4.py. Includes:
- ProvisionAnswer + ProvisionAnswerMap (Channel 1)
- ConceptApplicability + 30 concept type literals (Channel 2)
- All basket/blocker/pathway entity interfaces (Channel 3)
- RPExtractionV4 matching Pydantic models

There is NO type_generator.py in this repo. To regenerate types, read
schema_unified.tql and extraction_output_v4.py directly and write mfn.generated.ts.

## Known Issues

- **Old fetcher functions in graph_reader.py**: 10 individual fetcher functions (fetch_rp_baskets, etc.) are still present but no longer called. Only `fetch_dividend_capacity` is still used. Safe to delete in cleanup pass.
- **J.Crew Tier 3 prompt too long**: 212K > 200K token limit. Needs context trimming or split extraction.

## Cost Awareness

### Application API Calls (extraction pipeline, /ask endpoint)
- RP Universe extraction: ~$0.50 (cache it, avoid re-running)
- V4 entity extraction: ~$0.10 (fine to iterate)
- Use Sonnet for extraction. Opus only for complex analysis.

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

## Test Deal

Duck Creek (deal_id: `87852625`, provision_id: `87852625_rp`).
Last extracted: 2026-03-20 — 66 entities, 176 scalar answers.

## Eval: Gold Standard Questions

6 questions for Duck Creek. Pre-refactor baseline: 4 correct, 1 partial, 1 wrong.
Run via `POST /api/graph-eval/87852625` or manually via `/ask-graph`.
