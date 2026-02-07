# CLAUDE.md — Valence V3

> Auto-loaded by Claude Code at session start. Read this first.

## Repo & Deployment

- **Local:** `C:\Users\olive\ValenceV3`
- **GitHub:** `oliverjmfeix-sketch/ValenceV3`
- **Branch:** `feature/graph-native-v4`
- **Backend:** Railway — auto-deploys from GitHub. NEVER use `railway up`.
- **Backend URL:** `https://mfnnavigatorbackend-production.up.railway.app`
- **Database:** TypeDB Cloud 3.x
- **Frontend:** Lovable

## CRITICAL: Always push to GitHub

After EVERY commit, run: `git push origin feature/graph-native-v4`
Railway deploys from GitHub. If you don't push, the code only exists locally.

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
- Hardcoded `category_names` dicts
- Parsing question_id strings to derive category
- Flat attribute lists duplicating schema definitions
- Frontend category configs not sourced from TypeDB

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
Which concepts from a closed set apply.

**Channel 3 — Entity (typed entities + relations)**
Structured objects: baskets, sources, blockers, pathways.

## Entity Hierarchy

### RP Baskets (7 subtypes of rp_basket) → provision_has_basket
builder_basket, ratio_basket, general_rp_basket, management_equity_basket,
tax_distribution_basket, holdco_overhead_basket, equity_award_basket

### RDP Baskets (5 subtypes of rdp_basket) → provision_has_rdp_basket
SEPARATE hierarchy — uses provision_has_rdp_basket, NOT provision_has_basket.

### Builder Basket Sources (8 subtypes) → basket_has_source
### Blocker Exceptions (5 subtypes) → blocker_has_exception
### Other: jcrew_blocker, unsub_designation, sweep_tier, de_minimis_threshold,
    basket_reallocation, investment_pathway

## Key Files

| Purpose | File |
|---------|------|
| Schema (single file) | `app/data/schema_unified.tql` |
| V4 Pydantic models | `app/schemas/extraction_output_v4.py` |
| Graph storage (write) | `app/services/graph_storage.py` |
| Extraction pipeline | `app/services/extraction.py` |
| Deal API (read) | `app/routers/deals.py` |
| App startup | `app/main.py` |

## TypeDB 3.x Syntax

- Schema: `entity X sub Y`, `owns` not `has`
- Data: `has` for attributes, `links` for roles
- `@key` for unique identifiers
- Variables scoped to single query — use match-insert for cross-references

## Cost Awareness

- RP Universe extraction: ~$0.50 (cache, don't re-run)
- V4 entity extraction: ~$0.10 (fine to iterate)
- Use Sonnet for extraction. Opus only for complex analysis.

## Commit Convention
type: concise description
Co-Authored-By: Claude Opus 4.6 noreply@anthropic.com
Types: schema, extraction, api, types, data, fix, refactor

After committing, ALWAYS run: git push origin feature/graph-native-v4
