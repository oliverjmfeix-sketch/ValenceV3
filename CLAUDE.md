# CLAUDE.md — Valence V3

> Auto-loaded by Claude Code at session start. Read this first.

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
- **Database:** TypeDB Cloud 3.x
- **Frontend:** Lovable

## What Valence Does

Legal technology platform analyzing credit agreements (leveraged finance).
Extracts covenant provisions from PDFs, stores structured data in TypeDB,
enables cross-deal comparison and loophole detection (J.Crew, Serta, etc.).
Currently covers MFN (42 questions) and Restricted Payments (429 questions, 24 categories).

## Core Principle: SSoT (Single Source of Truth)

**TypeDB is the Single Source of Truth.** Zero hardcoded field lists, category
names, or type mappings in Python or TypeScript.

Adding a new field = add to TypeDB schema + seed data. Pipeline auto-discovers via introspection.

### SSoT violations to NEVER introduce:
- Hardcoded `category_names` dicts (DELETED — was in extraction.py)
- Parsing question_id strings to derive category (DELETED — use TypeQL join)
- Flat attribute lists duplicating schema definitions
- Frontend category configs not sourced from TypeDB
- Any Python dict mapping category IDs to display names
- Duplicate TQL parsers (DELETED — was in main.py, init_schema.py is the sole parser)

## Schema: schema_unified.tql

**Single schema file** (996 lines, 156 attributes, 78 entities, 27 relations).
Replaces old schema.tql + schema_v2.tql + schema_expanded.tql (all deleted).

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
- jcrew_blocker → provision_has_blocker
- unsub_designation → provision_has_unsub
- sweep_tier → provision_has_sweep_tier
- de_minimis_threshold → provision_has_de_minimis
- basket_reallocation → provision_has_reallocation
- investment_pathway → provision_has_pathway (J.Crew chain analysis)

## Ontology System

Questions live in TypeDB as `ontology_question` entities grouped by `ontology_category`.
Categories use single-letter IDs: A through N, plus S, T, Z.

Category resolution uses TypeQL join through category_has_question — NOT prefix parsing.

## Key Files

| Purpose | File |
|---------|------|
| **Schema** (single file) | `app/data/schema_unified.tql` |
| V4 Pydantic models | `app/schemas/extraction_output_v4.py` |
| Graph storage (write) | `app/services/graph_storage.py` |
| Extraction pipeline | `app/services/extraction.py` |
| Deal API (read) | `app/routers/deals.py` |
| TypeDB client | `app/services/typedb_client.py` |
| App startup (connection only) | `app/main.py` |
| **DB seeding (SSoT)** | `app/scripts/init_schema.py` |
| **Frontend types** | `src/types/mfn.generated.ts` |

### Data Files
| File | Contents |
|------|----------|
| `app/data/schema_unified.tql` | THE schema (996 lines) |
| `app/data/questions.tql` | Base ontology (Categories A-K) |
| `app/data/ontology_expanded.tql` | Expanded questions (F9-F17, G5-G7, I, L, N) |
| `app/data/ontology_category_m.tql` | Category M: Unsub distributions (10 questions) |
| `app/data/concepts.tql` | Concept type seed instances |
| `app/data/rp_basket_metadata.tql` | Extraction metadata for new RP baskets |
| `app/data/rdp_basket_metadata.tql` | Extraction metadata for RDP baskets |
| `app/data/investment_pathway_metadata.tql` | Extraction metadata for investment pathways |

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
3. Railway auto-deploys and runs init_schema.py
4. **Do NOT** try to run seed scripts locally — typedb-driver is not installed locally
5. **Do NOT** write temp Python scripts to reseed — just commit and push

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

## TypeScript Types: mfn.generated.ts

613-line file reflecting all three data channels. Generated from schema_unified.tql
and extraction_output_v4.py. Includes:
- ProvisionAnswer + ProvisionAnswerMap (Channel 1)
- ConceptApplicability + 30 concept type literals (Channel 2)
- All basket/blocker/pathway entity interfaces (Channel 3)
- RPExtractionV4 matching Pydantic models

There is NO type_generator.py in this repo. To regenerate types, read
schema_unified.tql and extraction_output_v4.py directly and write mfn.generated.ts.

## Completed Migration Steps (Feb 2025)

### Steps 1-2: Schema Unification ✅
- Consolidated 3 schema files → `schema_unified.tql`
- Deleted old schema.tql, schema_v2.tql, schema_expanded.tql
- Removed deprecated concept types

### Steps 3A-3C: Channel 1 Refactor ✅
- `store_scalar_answer()` method in graph_storage.py
- All scalar writes go through provision_has_answer
- All reads query provision_has_answer (deals.py fully migrated)
- Deleted hardcoded category_names dict
- Net: -214 lines of hardcoded dicts, +163 lines of SSoT queries

### Steps 4-7: V4 Entity Expansion ✅
- 18 new attributes on existing entity types
- 3 new RP basket types, 5 new RDP basket types
- investment_pathway entity for J.Crew chain analysis
- All with Pydantic models, store methods, extraction metadata

### Step 8: TypeScript Type Regeneration ✅
- Generated mfn.generated.ts (613 lines) from unified schema
- Covers all three data channels
- Provisions as pure anchors (zero extracted values in TS types)

### Step 9: Full Verification ✅
- SSoT violation grep: zero violations across all .py and .ts files
- Pydantic model tests: 21/21 passed (extraction_output_v4.py)
- File consistency: all 11 TQL files + 9 key source files verified
- init_schema.py rewritten for TypeDB 3.x API, loads all 11 data files
- Schema gaps fixed: added extraction_metadata entity, restricted_party hierarchy,
  sweep_exemption hierarchy, license_back_exception, source_name, exception_name,
  requires_context, context_entities attributes
- Fixed ontology_expanded.tql: target_concept_type is attribute not entity
- Fixed seed_v4_data.tql: removed redundant ip_type entries (already in concepts.tql)
- Fixed _load_mixed_tql_file parser: match-insert pairs no longer split
- DB drop/reload: all checks passed (170 concepts, 91 questions, 17 categories,
  27 extraction metadata, 12 IP types, 5 party types)
- Note: Only 91 of 471 total questions currently seeded in TQL files.
  Remaining questions need to be added to seed data.

### Step 10: Remove Duplicate TQL Parser from main.py ✅
- Deleted ~430 lines of TQL parsing/seeding from main.py startup
- main.py now only verifies TypeDB connection + database existence
- init_schema.py is the sole SSoT for TQL parsing and DB seeding
- Fixes: 34 of 49 ontology_expanded inserts were silently failing on every startup

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
