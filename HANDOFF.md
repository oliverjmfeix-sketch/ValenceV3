# Valence V3 — Handoff Document
**Date**: 2026-03-23 (updated after Prompts 7b and 8)

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
| **1 — Scalar** | Boolean, string, number keyed by question_id | `provision_has_answer` | "Does covenant allow unrestricted distributions?" -> false |
| **2 — Multiselect** | Concepts from a closed set | `concept_applicability` | facility_prong -> term_loans |
| **3 — Entity** | Structured objects with multiple attributes | Typed entities + relations | builder_basket with 15 attributes, 8 source children |

**Provisions are pure anchors** — own ZERO extracted values. Only identity + computed pattern flags.

---

## Current State (as of 2026-03-23, post Prompts 7b and 8)

### What Works
- Full extraction pipeline: PDF -> RP universe -> V4 entity extraction -> TypeDB storage
- 66 entities created, 176 scalar answers stored for Duck Creek (deal 87852625)
- Schema: unified (~1010 lines, 80+ entities, 27+ relations, ~160+ attributes)
- Abstract parent `provision_has_extracted_entity` with 12 child relations
- 4 RP analytical functions deployed
- Polymorphic fetch query: single query returns all entities + children + annotations + links
- **Reallocation graph edges**: `basket_reallocates_to` typed relations with `capacity_effect` attribute
- **Cross-covenant wiring**: `investment_provision` + `general_investment_basket` auto-created
- **Capacity classification**: `capacity_category` on all RP/RDP baskets (SSoT seed data)
- **`shares_capacity_pool` relation**: absence-signal schema — baskets NOT linked share separate capacity
- **`no_worse_is_uncapped` boolean**: extracted on `ratio_basket`, drives Q6 synthesis improvement
- Entity booleans endpoint for frontend migration off concept_applicability

### Database State (Duck Creek, last re-extracted 2026-03-23)
- **5 `basket_reallocates_to` edges** with `capacity_effect: "additive"`, section_reference, reallocation_amount_usd ($130M each)
- **1 `investment_provision`** (87852625_investment) with `general_investment_basket` ($130M)
- **13 baskets** with correct `capacity_category` labels
- **`no_worse_is_uncapped: true`** on ratio_basket
- No `shares_capacity_pool` instances (correct — Duck Creek baskets are separate pools)

### Completed Phases

**Schema Migration (CC Prompt 1 of 3)** completed
- Abstract parent `provision_has_extracted_entity` with 12 child relations

**Polymorphic Fetch Read Path (CC Prompt 2 of 3)** completed
- Single `_FETCH_QUERY` in `graph_traversal.py`, includes links subquery for `basket_reallocates_to`

**RP Analytical Functions (Phase 4)** completed
- 4 functions: `blocker_binding_gap_evidence`, `blocker_exception_swallow_evidence`, `unsub_distribution_evidence`, `pathway_chain_summary`

**Prompt 6a: Reallocation Graph Edge Schema** completed
- Expanded `basket_reallocates_to` with 8 owned attributes (amount, grower_pct, reduces_source, dollar_for_dollar, while_outstanding, section_reference, source_page, source_text)
- Added `investment_provision sub provision`
- Added `cross_covenant_mapping` entity (SSoT for basket->provision type mapping)

**Prompt 6b: Extraction Chain — Template Expansion + Wiring** completed
- 3 SSoT introspection methods: `_get_basket_subtype_names()`, `_load_cross_covenant_mappings()`, `_get_relation_attr_types()`
- Template variable expansion: `{basket_subtypes}` in extraction prompts -> comma-separated list from schema
- `wire_reallocation_edges()` — post-extraction step creates `basket_reallocates_to` relations
- Cross-covenant provision/basket creation via `_ensure_cross_provision()` / `_ensure_cross_basket()`

**Prompt 7: Capacity Classification** completed
- `capacity_category` attribute on `rp_basket` and `rdp_basket` abstracts
- `basket_capacity_class` seed entity (15 classifications)
- `_load_capacity_classifications()` loader, auto-set after entity creation
- `capacity_category` excluded from extraction prompts (SSoT-only field)
- Rule 7(f) in synthesis prompt: capacity aggregation by category

**Prompt 7b: capacity_effect + System Prompt Cleanup** completed
- Added `capacity_effect` attribute to `basket_reallocates_to` relation in schema
- Value is `"additive"` — signals that reallocation adds capacity to target, does not share a pool
- Cleaned up hardcoded system prompt text in `deals.py` — removed redundant category explanations
- Rule 7(g) added then reverted (was not effective enough; see Q5 regression)

**Prompt 8: shares_capacity_pool + no_worse_is_uncapped** completed
- Added `shares_capacity_pool` relation to schema — `rp_basket` plays `pool_member`
- Designed as **absence signal**: if baskets are NOT linked, their capacity is separate/additive
- Duck Creek has no `shares_capacity_pool` instances (correct — 3 general baskets are separate pools)
- Added `no_worse_is_uncapped` boolean attribute on `ratio_basket`
- New ontology question `rp_g5b` in `seed_new_questions_008.tql` (Category G)
- Extraction now captures whether the no-worse test is uncapped
- Duck Creek extracts `no_worse_is_uncapped: true` (correct)

### Eval Results (Post Prompt 7b)

| Q# | Gold | Result | Notes |
|----|------|--------|-------|
| Q1 | Greatest of 3 tests, starts fiscal quarter of closing | Pass | |
| Q2 | Yes, under 6.06(p) categorical carve-out | Pass | |
| Q3 | Yes, Investment 6.03(y) + RDP 6.09(a) reallocate to 6.06(j) | Pass | Correctly identifies reallocation sources and amounts |
| Q4 | Yes, Retained Asset Sale Proceeds build Cumulative Amount | Pass | |
| Q5 | $520M (4x$130M) | Regressed to ~$390M | Claude sees reallocation as additive (~$390M) but still treats RP+builder starter as shared |
| Q6 | Yes, via ratio basket 6.06(o) no-worse test | Partially correct | Says "no" initially, then backtracks to "may be permitted"; hedges heavily |

### Eval Results (Post Prompt 8)

| Q# | Gold | Result | Notes |
|----|------|--------|-------|
| Q1 | Greatest of 3 tests, starts fiscal quarter of closing | Pass | |
| Q2 | Yes, under 6.06(p) categorical carve-out | Pass | |
| Q3 | Yes, Investment 6.03(y) + RDP 6.09(a) reallocate to 6.06(j) | Pass | Correctly identifies bidirectional reallocation |
| Q4 | Yes, Retained Asset Sale Proceeds build Cumulative Amount | Pass | |
| Q5 | $520M (4x$130M) | **Regressed to ~$150M** | Claude says "General RP / Builder Starter: $130M (shared across covenants)" — treats all 3 baskets as one pool |
| Q6 | Yes, via ratio basket 6.06(o) no-worse test | **Improved** | Now says "Yes" with clear reasoning: uncapped no-worse test, negative EBITDA removal improves leverage |

### THE OPEN PROBLEM: Q5 Synthesis ($150M instead of $520M)

**The data pipeline is correct.** Claude receives:
- `general_rp_basket` with 4 `basket_reallocates_to` links, each with `capacity_effect: "additive"` and `reallocation_amount_usd: 130000000`
- `general_rdp_basket` with similar links
- `general_investment_basket` visible via links with `basket_amount_usd: 130000000`
- All baskets have `capacity_category = "general_purpose"`
- No `shares_capacity_pool` instances linking them (absence = separate pools)
- Reallocation edges have `reduces_source_basket: true` and `reduction_is_dollar_for_dollar: true`

**Claude's persistent error:** Despite `capacity_effect: "additive"` on edges and the absence of `shares_capacity_pool`, Claude reads `reduces_source_basket: true` / `reduction_is_dollar_for_dollar: true` and concludes the baskets share a single pool of $130M. In reality:
- Each basket (RP, RDP, Investment) is a SEPARATE pool sized at $130M
- Reallocation is an ELECTION that moves capacity from one covenant to another
- "Dollar-for-dollar reduction" means the source loses what the target gains — it does NOT mean they share capacity
- The builder basket starter ($130M) is ALSO separate from the general RP basket
- Correct total: $130M (starter) + $130M (general RP) + $130M (RDP reallocation) + $130M (Investment reallocation) = $520M

**What was tried and did not work:**
- Rule 7(g) in synthesis prompt about reallocation = additive capacity (committed then reverted — Claude ignored it)
- `capacity_effect: "additive"` attribute on edges (Claude still overweights `reduces_source_basket`)
- `shares_capacity_pool` absence signal (Claude does not reason about absence of relations)

**What NOT to do:** The user has not rejected adding more synthesis rules, but simple rule additions have proven insufficient. The problem is that Claude's reading of `reduces_source_basket: true` creates a strong prior toward "shared pool" that overrides explicit contrary signals.

**Possible next steps:**
- Stronger, more explicit rule 7(g) that specifically addresses the "dollar-for-dollar reduction means source LOSES capacity, not shared pool" distinction
- Pre-computed finding in `_fetch_computed_findings()` that calculates $520M total and presents it as fact
- Remove `reduces_source_basket` from the context entirely (it confuses Claude more than it helps)
- Add a `capacity_is_separate: true` boolean directly on baskets

### Known Issues

| Issue | Severity | Notes |
|---|---|---|
| Q5 synthesis treats reallocation as shared pool ($150M) | HIGH | Data correct; Claude interpretation wrong. See "THE OPEN PROBLEM" above |
| Q6 improved but conclusion is hedged | LOW | Says "may be permitted" and "potentially yes" instead of definitive "yes" |
| Old individual fetcher functions still in graph_reader.py | CLEANUP | 10 functions, only `fetch_dividend_capacity` still used |
| J.Crew Tier 3 prompt too long (212K > 200K tokens) | ERROR | Need to trim context or split extraction |

---

## Key Decisions Made in Prompts 7b-8

1. **`capacity_effect` on edges, not baskets**: The `capacity_effect` attribute lives on `basket_reallocates_to` relation, not on the baskets themselves. Value `"additive"` means reallocation adds to target capacity.

2. **`shares_capacity_pool` as absence signal**: Rather than marking every basket pair as "separate", the relation exists only when baskets DO share a pool. Absence of the relation means separate capacity. This is a more natural graph modeling pattern but Claude struggles to reason about absence.

3. **`no_worse_is_uncapped` as extracted boolean**: Rather than relying on Claude to infer uncapped-ness from the contract text during synthesis, we extract it as a boolean during the extraction phase. This gives synthesis a clear signal.

4. **Rule 7(g) reverted**: The initial attempt at a synthesis rule about additive reallocation was committed then reverted because Claude ignored it in favor of its own reading of `reduces_source_basket`.

5. **System prompt cleanup**: Removed hardcoded category name explanations from the synthesis prompt in `deals.py` — these were SSoT violations that duplicated information already available from TypeDB.

---

## File Map

### Core Services (`app/services/`)

| File | Lines | Purpose |
|------|-------|---------|
| `extraction.py` | ~2,900 | 2-stage extraction pipeline: PDF -> RP universe -> V4 entities |
| `graph_storage.py` | ~2,200 | Write all 3 channels to TypeDB. Schema introspection. Includes `wire_reallocation_edges()`, `_load_capacity_classifications()`, `_get_basket_subtype_names()`, `_load_cross_covenant_mappings()`, `_get_relation_attr_types()` |
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
| `schema_unified.tql` | **THE schema** (~1010 lines, 80+ entities, 27+ relations) |
| `seed_cross_covenant_mappings.tql` | SSoT: basket_type -> provision_type (1 mapping) |
| `seed_capacity_classifications.tql` | SSoT: basket_type -> capacity_category (15 mappings) |
| `seed_entity_list_questions.tql` | Entity-list extraction questions incl. `rp_el_reallocations` with `{basket_subtypes}` template |
| `seed_new_questions_008.tql` | Prompt 8: `rp_g5b` question for `no_worse_is_uncapped` (Category G) |

### Scripts (`app/scripts/`)

| File | Purpose |
|------|---------|
| `init_schema.py` | **THE single entry point for all DB seeding.** Loads all seed files in order including cross_covenant_mappings, capacity_classifications, and seed_new_questions_008 |

---

## Entity Hierarchy

### RP Baskets (7 subtypes of `rp_basket`) -> `provision_has_basket`
builder_basket, ratio_basket, general_rp_basket, management_equity_basket, tax_distribution_basket, holdco_overhead_basket, equity_award_basket

**Also:** `general_investment_basket` sub `rp_basket` — cross-covenant basket, created by wiring step, linked to `investment_provision`

### RDP Baskets (5 subtypes of `rdp_basket`) -> `provision_has_rdp_basket`
refinancing_rdp_basket, general_rdp_basket, ratio_rdp_basket, builder_rdp_basket, equity_funded_rdp_basket

### Reallocation Graph (`basket_reallocates_to`)
Typed edges between basket entities. Each edge owns: `reallocation_amount_usd`, `reallocation_grower_pct`, `reduces_source_basket`, `reduction_is_dollar_for_dollar`, `reduction_while_outstanding_only`, `section_reference`, `source_page`, `source_text`, `capacity_effect`.

Duck Creek has 5 edges, all with `capacity_effect: "additive"`.

### Capacity Pool (`shares_capacity_pool`)
Relation with role `pool_member` played by `rp_basket`. Used as an **absence signal**: baskets NOT linked by this relation have separate, additive capacity. Duck Creek has no instances (correct).

### Capacity Classification (`capacity_category` on rp_basket/rdp_basket)
- `general_purpose`: builder_basket, general_rp_basket, general_rdp_basket, general_investment_basket, builder_rdp_basket
- `restricted_purpose`: management_equity, tax_distribution, equity_award, holdco_overhead, refinancing_rdp, equity_funded_rdp
- `unlimited_conditional`: ratio_basket, ratio_rdp_basket
- `categorical`: unsub_distribution_basket

### Cross-Covenant SSoT Entities
- `cross_covenant_mapping`: basket_type_name -> provision_type_name (e.g., general_investment_basket -> investment_provision)
- `basket_capacity_class`: basket_type_name -> capacity_category (15 mappings)

### Abstract Parent
All 12 entity-bearing relations sub `provision_has_extracted_entity` with role alias `as extracted`.

---

## Q&A Pipeline (`/ask-graph`)

```
User question
    |
TopicRouter: route to categories (SSoT from TypeDB)
    |
get_rp_entities(deal_id, trace):
    +-- Section 1: Computed Findings (analytical functions)
    |   +-- dividend_capacity_components()
    |   +-- blocker_binding_gap_evidence()
    |   +-- blocker_exception_swallow_evidence()
    |   +-- unsub_distribution_evidence()
    |   +-- pathway_chain_summary()
    |
    +-- Section 2: Supporting Entity Data (polymorphic fetch)
        +-- Single TypeDB fetch query -> JSON array of all entities
            with attributes, annotations, children, AND links
            (basket_reallocates_to edges with relation_attributes including capacity_effect)
    |
Claude synthesis (Opus 4.5) with system rules 7(a-f) + entity context
    |
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

---

## Gotchas for Next Session

1. **Do NOT re-extract Duck Creek** — just done 2026-03-23 (~$0.10). Data is correct; the problem is synthesis.
2. **Schema is fresh** — `init_schema --force` was run. No need to reseed unless changing schema.
3. **Eval files**: `eval_post_prompt7b.txt` and `eval_post_prompt8.txt` in repo root (not committed).
4. **Rule 7(g) was reverted** — commit `9a72d08`. The rule existed briefly in `9af02fd` but was backed out because Claude ignored it.
5. **`capacity_effect` is stored but ignored by Claude** — the attribute exists on edges and is set to `"additive"` but Claude still overweights `reduces_source_basket: true`.

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
736cb40 schema: add capacity_effect attribute + system prompt hardcoding cleanup
9fb1a20 docs: update HANDOFF.md and CLAUDE.md for P6/P7 state
9a72d08 Revert "api: add rule 7(g) — reallocation = additive capacity"
9af02fd api: add rule 7(g) — reallocation = additive capacity
7724265 fix: exclude capacity_category from extraction prompts and entity storage
b9a4b8b schema: add capacity_category classification + fix cross-covenant wiring
e25e387 test: add migration 006c edge verification script
637a22a fix: add typedb_client.connect() to verify_006b script
810ca38 extraction: add reallocation graph edge wiring pipeline
```

---

## Pending Work

### PRIORITY: Fix Q5 synthesis (reallocation = additive capacity, target $520M)
The data pipeline delivers correct typed edges with $130M amounts, `capacity_effect: "additive"`, and no `shares_capacity_pool` links. Claude still says "$130M shared across covenants" instead of $520M (4 x $130M). Most promising approach: pre-computed finding in `_fetch_computed_findings()` that calculates and presents the total as authoritative fact, or removing `reduces_source_basket` from the context to eliminate the confusing signal.

### Cleanup
- Delete 10 unused individual fetcher functions from `graph_reader.py`
- Remove unused imports from `graph_traversal.py`

### Other
- J.Crew Tier 3 prompt too long (212K > 200K tokens) — trim or split
- Frontend integration: wire entity_booleans into UI
- Strengthen Q6 synthesis to be definitive "yes" instead of hedged "may be permitted"

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
Last extracted: 2026-03-23 — 66 entities, 176 scalar answers, 5 reallocation edges.

## Eval Files (in repo root, not committed)

- `eval_post_prompt7b.txt` — Post P7b eval (Q5 at ~$390M, Q6 hedged)
- `eval_post_prompt8.txt` — Post P8 eval (Q5 regressed to $150M, Q6 improved)
- `eval_post_prompt6.txt` / `eval_post_prompt6_full.json` — Post P6 eval (Q5 hit $520M)
- `eval_post_prompt7.txt` / `eval_post_prompt7_full.json` — Post P7 eval (capacity categories)
