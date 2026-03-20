# Valence V3 — Handoff Document
**Date**: 2026-03-20 (updated after Prompts 6–7)

---

## What Valence Does

Legal technology platform analyzing credit agreements (leveraged finance). Extracts covenant provisions from PDFs, stores structured data in TypeDB graph database, enables cross-deal comparison and loophole detection (J.Crew, Serta, etc.). Currently covers MFN (42 questions) and Restricted Payments (289 questions, 27 categories).

---

## Architecture

| Component | Technology | Location |
|-----------|-----------|----------|
| Backend | FastAPI + Uvicorn | Railway (auto-deploy from GitHub) |
| Database | TypeDB Cloud 3.x | `ip654h-0.cluster.typedb.com:80` |
| LLM | Anthropic Claude (Sonnet 4.6 extraction, Opus 4.5 synthesis) | API |
| PDF Processing | PyMuPDF (fitz) | |
| Frontend | Lovable (TypeScript/React) | Separate repo |
| GitHub | `oliverjmfeix-sketch/ValenceV3` | Branch: `main` |
| Backend URL | `https://valencev3-production.up.railway.app` | |

---

## Critical Rules

1. **NEVER touch `C:\Users\olive\mfn-lens-main`** — DEPRECATED repo
2. **ALWAYS push after commit:** `git push origin main`
3. Railway auto-deploys from GitHub. **NEVER use `railway up`**
4. **NEVER install typedb-driver locally.** Run TypeDB scripts via `railway ssh` only
5. **TypeDB is the Single Source of Truth (SSoT).** Zero hardcoded field lists, category names, or type mappings in Python

---

## Core Principle: Three Data Channels

ALL extracted data flows through exactly one of:

| Channel | What | Relation | Example |
|---------|------|----------|---------|
| **1 — Scalar** | Boolean, string, number keyed by question_id | `provision_has_answer` | "Does covenant allow unrestricted distributions?" → false |
| **2 — Multiselect** | Concepts from a closed set | `concept_applicability` | facility_prong → term_loans |
| **3 — Entity** | Structured objects with multiple attributes | Typed entities + relations | builder_basket with 15 attributes, 8 source children |

**Provisions are pure anchors** — own ZERO extracted values. Only identity + computed pattern flags.

---

## Current State (as of 2026-03-20, post Prompt 7)

### What Works
- Full extraction pipeline: PDF → RP universe → V4 entity extraction → TypeDB storage
- 47 entities created, 175 scalar answers stored for Duck Creek (deal 87852625)
- Schema: unified (~1010 lines, 80 entities, 27 relations, ~160 attributes)
- Abstract parent `provision_has_extracted_entity` with 12 child relations
- 4 RP analytical functions deployed
- Polymorphic fetch query: single query returns all entities + children + annotations + **links** (43 docs, ~80KB JSON)
- **Reallocation graph edges**: `basket_reallocates_to` typed relations between actual basket entities (5 edges for Duck Creek)
- **Cross-covenant wiring**: `investment_provision` + `general_investment_basket` auto-created, linked to deal, with $130M amount
- **Capacity classification**: `capacity_category` attribute on all RP/RDP baskets (general_purpose, restricted_purpose, unlimited_conditional, categorical) — set from SSoT seed data, excluded from extraction prompts
- Entity booleans endpoint for frontend migration off concept_applicability

### Database State (Duck Creek, last re-extracted 2026-03-20)
- **5 `basket_reallocates_to` edges** with section_reference and reallocation_amount_usd ($130M each)
- **1 `investment_provision`** (87852625_investment) with `general_investment_basket` ($130M)
- **13 baskets** with correct `capacity_category` labels
- Links visible in polymorphic fetch: general_rp_basket and general_rdp_basket show links to each other and to general_investment_basket

### Completed Phases

**Schema Migration (CC Prompt 1 of 3)** ✅
- Abstract parent `provision_has_extracted_entity` with 12 child relations

**Polymorphic Fetch Read Path (CC Prompt 2 of 3)** ✅
- Single `_FETCH_QUERY` in `graph_traversal.py`, includes links subquery for `basket_reallocates_to`

**RP Analytical Functions (Phase 4)** ✅
- 4 functions: `blocker_binding_gap_evidence`, `blocker_exception_swallow_evidence`, `unsub_distribution_evidence`, `pathway_chain_summary`

**Prompt 6a: Reallocation Graph Edge Schema** ✅
- Expanded `basket_reallocates_to` with 8 owned attributes (amount, grower_pct, reduces_source, dollar_for_dollar, while_outstanding, section_reference, source_page, source_text)
- Added `investment_provision sub provision`
- Added `cross_covenant_mapping` entity (SSoT for basket→provision type mapping)
- New attributes: `reallocation_grower_pct`, `basket_type_name`, `provision_type_name`, `mapping_id`

**Prompt 6b: Extraction Chain — Template Expansion + Wiring** ✅
- 3 SSoT introspection methods: `_get_basket_subtype_names()`, `_load_cross_covenant_mappings()`, `_get_relation_attr_types()`
- Template variable expansion: `{basket_subtypes}` in extraction prompts → comma-separated list from schema
- `wire_reallocation_edges()` — post-extraction step creates `basket_reallocates_to` relations
- Cross-covenant provision/basket creation via `_ensure_cross_provision()` / `_ensure_cross_basket()`
- Cleanup handles investment provision data

**Prompt 7: Capacity Classification** ✅
- `capacity_category` attribute on `rp_basket` and `rdp_basket` abstracts
- `basket_capacity_class` seed entity (15 classifications)
- `_load_capacity_classifications()` loader, auto-set after entity creation
- `capacity_category` excluded from extraction prompts (SSoT-only field)
- `reallocation_amount_usd` marked REQUIRED in `rp_el_reallocations` extraction prompt
- Fixed `_ensure_cross_provision` (removed non-existent `provision_type` attr)
- Fixed `_ensure_cross_basket` (use specific provision_type in match, not `isa provision`)
- `seed_cross_covenant_mappings.tql` and `seed_capacity_classifications.tql` added to `init_schema.py`
- Rule 7(f) in synthesis prompt: capacity aggregation by category

### Eval Results (Post Prompt 7)

| Q# | Gold | Result | Notes |
|----|------|--------|-------|
| Q1 | Greatest of 3 tests, starts fiscal quarter of closing | ✅ Correct | |
| Q2 | Yes, under 6.06(p) categorical carve-out | ✅ Correct | |
| Q3 | Yes, Investment 6.03(y) + RDP 6.09(a) reallocate to 6.06(j) | ✅ Correct data, wrong conclusion | Claude sees typed edges but says "practical maximum is $130M, not a multiple" |
| Q4 | Yes, Retained Asset Sale Proceeds build Cumulative Amount | ✅ Correct | |
| Q5 | $520M (4x$130M) | ⚠️ Data correct, synthesis wrong | Claude sees all 4 $130M components but says "these share a pool — cannot be stacked" |
| Q6 | Yes, via ratio basket 6.06(o) no-worse test | ⚠️ Partially correct | Gets to right answer via no-worse test but hedges |

### THE OPEN PROBLEM: Q3/Q5 Synthesis

**The data pipeline is correct.** Claude receives:
- `general_rp_basket` with 4 `basket_reallocates_to` links (to/from rdp and investment), each with `reallocation_amount_usd: 130000000` and `section_reference`
- `general_rdp_basket` with similar links
- `general_investment_basket` visible via links with `basket_amount_usd: 130000000`
- All baskets have correct `capacity_category = "general_purpose"`
- Flat `basket_reallocation` entities also present (legacy, 5 of them) with `reduces_source_basket: true`

**Claude's error:** It reads `reduces_source_basket: true` and `reduction_is_dollar_for_dollar: true` on the edge relation_attributes and concludes the baskets share a single pool. In reality, each basket is a SEPARATE pool sized at $130M, and reallocation is an *election* that moves capacity from one covenant to another — additive for dividend purposes.

**What NOT to do:** Do not add more rules to the Python synthesis prompt (`deals.py`). The user explicitly rejected this approach.

**Possible approaches (not yet attempted):**
- Add a TypeDB analytical function that computes total reallocation capacity
- Add a computed finding in `_fetch_computed_findings()` that pre-calculates the $520M total
- Modify the `basket_reallocation` flat entity to include a field explaining additivity
- Remove `reduces_source_basket` from the `basket_reallocates_to` relation (it confuses Claude)

### Known Issues

| Issue | Severity | Notes |
|---|---|---|
| Q3/Q5 synthesis treats reallocation as shared pool | HIGH | Data is correct; Claude interpretation is wrong. See "THE OPEN PROBLEM" above |
| Old individual fetcher functions still in graph_reader.py | CLEANUP | 10 functions, only `fetch_dividend_capacity` still used |
| J.Crew Tier 3 prompt too long (212K > 200K tokens) | ERROR | Need to trim context or split extraction |
| 14 of 15 capacity classifications loaded (unsub_distribution_basket may be missing) | LOW | Check `_load_multi_insert_file` for edge case |

---

## File Map

### Core Services (`app/services/`)

| File | Lines | Purpose |
|------|-------|---------|
| `extraction.py` | ~2,900 | 2-stage extraction pipeline: PDF → RP universe → V4 entities |
| `graph_storage.py` | ~2,200 | Write all 3 channels to TypeDB. Schema introspection. **New:** `wire_reallocation_edges()`, `_load_capacity_classifications()`, `_get_basket_subtype_names()`, `_load_cross_covenant_mappings()`, `_get_relation_attr_types()` |
| `graph_reader.py` | ~1,200 | Read entities from TypeDB. Legacy fetchers (being replaced) |
| `graph_traversal.py` | ~370 | **Entry point for Q&A context.** Computed findings + polymorphic JSON fetch with links |
| `topic_router.py` | ~390 | Route questions to TypeDB categories (SSoT) |
| `graph_queries.py` | ~360 | Query builders |
| `typedb_client.py` | ~135 | TypeDB 3.x driver wrapper |

### Routers (`app/routers/`)

| File | Lines | Key Endpoints |
|------|-------|---------------|
| `deals.py` | ~3,300 | Main Q&A (`/ask-graph`), extract, re-extract, answers. Rules 7(a-f) in synthesis prompt |

### Data Files (`app/data/`)

| File | Purpose |
|------|---------|
| `schema_unified.tql` | **THE schema** (~1010 lines, 80 entities, 27 relations) |
| `migration_006_reallocation_graph.tql` | P6a migration (for reference, not used by init_schema) |
| `seed_cross_covenant_mappings.tql` | SSoT: basket_type → provision_type (1 mapping) |
| `seed_capacity_classifications.tql` | SSoT: basket_type → capacity_category (15 mappings) |
| `seed_entity_list_questions.tql` | Entity-list extraction questions incl. `rp_el_reallocations` with `{basket_subtypes}` template |

### Scripts (`app/scripts/`)

| File | Purpose |
|------|---------|
| `init_schema.py` | **THE single entry point for all DB seeding.** Now loads cross_covenant_mappings + capacity_classifications |
| `migrate_006_reallocation.py` | P6a migration (ran once, kept for reference) |
| `verify_006.py` | Verify P6a schema migration |
| `verify_006b.py` | Verify P6b extraction chain methods |
| `verify_006c_edges.py` | Verify reallocation edges in TypeDB |

---

## Entity Hierarchy

### RP Baskets (7 subtypes of `rp_basket`) → `provision_has_basket`
builder_basket, ratio_basket, general_rp_basket, management_equity_basket, tax_distribution_basket, holdco_overhead_basket, equity_award_basket

**New:** `general_investment_basket` sub `rp_basket` — cross-covenant basket, created by wiring step, linked to `investment_provision`

### RDP Baskets (5 subtypes of `rdp_basket`) → `provision_has_rdp_basket`
refinancing_rdp_basket, general_rdp_basket, ratio_rdp_basket, builder_rdp_basket, equity_funded_rdp_basket

### Reallocation Graph (`basket_reallocates_to`)
Typed edges between basket entities. Each edge owns: `reallocation_amount_usd`, `reallocation_grower_pct`, `reduces_source_basket`, `reduction_is_dollar_for_dollar`, `reduction_while_outstanding_only`, `section_reference`, `source_page`, `source_text`.

Duck Creek has 5 edges: RP↔RDP, RP↔Investment, RDP↔Investment (bidirectional minus one direction).

### Capacity Classification (`capacity_category` on rp_basket/rdp_basket)
- `general_purpose`: builder_basket, general_rp_basket, general_rdp_basket, general_investment_basket, builder_rdp_basket
- `restricted_purpose`: management_equity, tax_distribution, equity_award, holdco_overhead, refinancing_rdp, equity_funded_rdp
- `unlimited_conditional`: ratio_basket, ratio_rdp_basket
- `categorical`: unsub_distribution_basket

### Cross-Covenant SSoT Entities
- `cross_covenant_mapping`: basket_type_name → provision_type_name (e.g., general_investment_basket → investment_provision)
- `basket_capacity_class`: basket_type_name → capacity_category (15 mappings)

### Abstract Parent
All 12 entity-bearing relations sub `provision_has_extracted_entity` with role alias `as extracted`.

---

## Q&A Pipeline (`/ask-graph`)

```
User question
    ↓
TopicRouter: route to categories (SSoT from TypeDB)
    ↓
get_rp_entities(deal_id, trace):
    ├── Section 1: Computed Findings (analytical functions)
    │   ├── dividend_capacity_components()
    │   ├── blocker_binding_gap_evidence()
    │   ├── blocker_exception_swallow_evidence()
    │   ├── unsub_distribution_evidence()
    │   └── pathway_chain_summary()
    │
    └── Section 2: Supporting Entity Data (polymorphic fetch)
        └── Single TypeDB fetch query → JSON array of all entities
            with attributes, annotations, children, AND links
            (basket_reallocates_to edges with relation_attributes)
    ↓
Claude synthesis (Opus 4.5) with system rules 7(a-f) + entity context
    ↓
Answer with citations + evidence block
```

---

## Deployment

| Action | Command |
|--------|---------|
| Deploy | `git push origin main` (Railway auto-deploys) |
| Reseed DB (wipe + rebuild) | `railway ssh --service ValenceV3 -- python -m app.scripts.init_schema --force` |
| Re-extract (from cached RP universe) | `POST /api/deals/87852625/re-extract` |
| Full extract (reads PDF, ~$6, ~8min) | `POST /api/deals/87852625/extract` |
| Check health | `GET /health` |
| Verify edges | `railway ssh --service ValenceV3 -- python -m app.scripts.verify_006c_edges` |
| Verify capacity methods | `railway ssh --service ValenceV3 -- python -m app.scripts.verify_006b` |

---

## Commit Convention

```
type: concise description

Details.
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```
Types: schema, extraction, api, types, data, fix, refactor

---

## Recent Commit History

```
9a72d08 Revert "api: add rule 7(g) — reallocation = additive capacity"
9af02fd api: add rule 7(g) — reallocation = additive capacity
7724265 fix: exclude capacity_category from extraction prompts and entity storage
b9a4b8b schema: add capacity_category classification + fix cross-covenant wiring
e25e387 test: add migration 006c edge verification script
637a22a fix: add typedb_client.connect() to verify_006b script
810ca38 extraction: add reallocation graph edge wiring pipeline
55fa9ca test: add migration 006 verification script
8fa1294 fix: use TypeDB 3.x delete syntax ($old of $q) for attribute removal
ab1a883 fix: remove redundant deal_has_provision play from investment_provision
4aba043 fix: use single idempotent define block instead of define+redefine split
f32d4df schema: migration 006 — reallocation graph edges
```

---

## Pending Work

### PRIORITY: Fix Q3/Q5 synthesis (reallocation = additive capacity)
The data pipeline delivers correct typed edges with $130M amounts. Claude misinterprets `reduces_source_basket: true` as evidence of shared capacity. Need an approach that does NOT involve adding more rules to the Python synthesis prompt in `deals.py`.

### Cleanup
- Delete 10 unused individual fetcher functions from `graph_reader.py`
- Remove unused imports from `graph_traversal.py`

### Other
- J.Crew Tier 3 prompt too long (212K > 200K tokens) — trim or split
- Frontend integration: wire entity_booleans into UI
- Verify all 15 capacity classifications loaded (check unsub_distribution_basket)

---

## Key TypeDB 3.x Syntax Notes

- Schema: `entity X sub Y`, `relation R, relates A`, `owns` (not `has`)
- Data queries: `has` for attributes, `links` for relation roles
- `@key` for unique identifiers
- `try { }` for optional attribute access
- `define` is idempotent (use instead of `redefine` for adding sub + role alias)
- `label($var)` converts type variable to string at runtime
- `fetch` returns JSON documents; `select` returns concept rows
- `$e.*` wildcard fetches all attributes
- Functions: `fun name($param: type) -> { return_vals }:`

---

## Test Deal

Duck Creek (deal_id: `87852625`, provision_id: `87852625_rp`).
Last extracted: 2026-03-20 — 47 entities, 175 scalar answers, 5 reallocation edges.

## Eval Files (in repo root, not committed)

- `eval_post_prompt6.txt` / `eval_post_prompt6_full.json` — Post P6 eval (Q5 hit $520M)
- `eval_post_prompt7.txt` / `eval_post_prompt7_full.json` — Post P7 eval (capacity categories correct, Q5 regressed)
- `eval_post_prompt7_categories.txt` — Capacity category verification
- `debug_links_context.txt` — What Claude sees in entity context (links, relation_attributes)
