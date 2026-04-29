# Schema-data coherence audit (Phase F commit 2)

> Read-only audit of `schema_unified.tql` + `schema_v4_deontic.tql` against
> `valence_v4`'s actual instance data. Method: schema-first; per-relation,
> per-entity, per-attribute-family. Survey data:
> `docs/v4_schema_coherence_audit_data.json`. Survey script:
> `app/scripts/phase_f_schema_survey.py` (re-runnable).
>
> This document captures categorized findings. Items in "fix in Commit 3"
> become the change set for the next commit; items in "convention question"
> feed into Commits 4-5; items in "deferred" land in `docs/v4_known_gaps.md`.

## Executive summary

Total schema declarations across both files (after de-duping subtype
overlaps; some entities inherit from a parent declared in
`schema_unified.tql`):

| Element | Declared | Populated on Duck Creek | Population rate |
|---|---:|---:|---:|
| Entities | 198 | 85 | 43% |
| Relations | 102 | 50 | 49% |
| Attributes | 676 | 257 | 38% |

Population rates around 40-50% are expected: the schema covers all
covenant types (RP, MFN, DI, Liens, Investments, etc.) plus the v4
deontic projection infrastructure, but Duck Creek has only RP+MFN+DI
extracted. Entities specific to deferred covenants (Liens, Asset Sales)
and subtypes for unused JCrew patterns are unpopulated by design.

Key finding categories:

| Category | Count | Notes |
|---|---:|---|
| Aligned | majority | population matches expected (RP-extracted + v4-projected) |
| Architectural bugs (fix in Commit 3) | 4 | listed below |
| Convention questions (Commits 4-5) | 5 | listed below |
| Over-constrained but benign (defer) | ~15 | feed `docs/v4_known_gaps.md` |
| Blocks Phase G | 0 | no synthesis-architecture blockers in current data |

## Method

The survey script (`phase_f_schema_survey.py`) parses both `.tql` schema
files for `entity`, `relation`, and `attribute` declarations. For each:

- **Entity**: `match $x isa! <type>;` count. Abstract types fail with
  INF11 (expected — they have no direct instances).
- **Relation**: same.
- **Attribute**: instance count + distinct value count + sample of up
  to 8 distinct values.

Output JSON is consulted manually for this audit document — full data
is in the JSON for any future deep dives.

Schema introspection via `match $t sub entity;` is blocked because
`entity` (and `relation`, `attribute`) are TypeQL reserved keywords.
Parsing the `.tql` files is the SSoT path for type enumeration.

## Findings

### Architectural bugs (fix in Commit 3)

#### 1. `basket_reallocates_to` storage append on re-extraction

**Symptom:** Re-running `rp_el_reallocations` on Duck Creek post-Phase
F-commit-1 successfully creates 2 `basket_reallocates_to` relations
(RP↔RDP both directions). A second run would create 4 (each pair
duplicated). The relation has no `@key`, no role-player tuple
uniqueness constraint, and `wire_reallocation_edges` calls INSERT
directly.

**Severity:** Architectural bug. `basket_reallocates_to` should be
unique by direction-specific role-player tuple (source, target).

**Recommendation for Commit 3:** Convert
`wire_reallocation_edges` in `app/services/graph_storage.py` to use
the new `_upsert_relation_by_role_players` helper (Phase F commit 1).
Match by (source_basket, target_basket) tuple before inserting.

#### 2. Phase C deferred `event_governed_by_norm` is still missing

`schema_v4_deontic.tql:354` still has the deferral comment:
```
# Pilot defers `event_governed_by_norm`: the source-side asset-sale norms
# (sweep tiers, de minimis, 6.05(z) carve-out) are not yet authored in GT.
```

**Severity:** Documented deferral, not a bug. Phase E commit 4 chose
not to add it (since Q4 carveout state lives on `asset_sale_sweep` v3
attrs). For Phase F commit 3 alignment with the eventual Phase B/C
plan, adding the relation type is schema-additive and zero-cost.

**Recommendation for Commit 3:** Add `event_governed_by_norm` relation
type. Update the deferral comment to reference where the rules that
populate it would be authored (a future event-class governance
phase). Schema-additive only.

#### 3. `provision_has_answer.answer_id` is `@key` but extraction creates
many duplicates by tuple (provision, question)

**Symptom:** `provision_has_answer` has 221 instances on Duck Creek.
`question_id` survey shows 256 distinct questions. The 221 < 256
discrepancy is benign (some questions returned null), BUT — many
distinct `answer_id` values exist for the same (provision, question)
tuple from prior re-extractions before Phase F commit 1's upsert
landed.

**Severity:** Pre-Commit-1 data state. Phase F commit 1's upsert
prevents future duplicates. Forward-only discipline locks in: don't
backfill-clean current data.

**Recommendation:** No action in Commit 3. Document in
`docs/v4_known_gaps.md` that pre-Phase-F duplicate
`provision_has_answer` instances may exist on `valence_v4` and won't
be cleaned up until/unless a re-extraction window allows it. This is
the only audit finding where the fix is "do nothing" because
forward-only discipline supersedes.

#### 4. `capacity_effect` value space is `string` (no enum constraint)

**Symptom:** `capacity_effect` is declared as `value string` in
`schema_unified.tql:298`. The codebase hardcodes `"additive"` for
all cross-covenant reallocation edges (Phase F commit 1's fix
preserves this), but the LLM extraction prompt format-spec for
`rp_el_reallocations` has no constraint on what `capacity_effect`
values are valid. Distinct sample values today: `["additive"]` only
(post-fix).

**Severity:** Convention question rather than bug. Documented in
Commit 4 as an enum-string vs subtype trade-off.

**Recommendation:** Convention question for Commit 4. Capacity_effect
should be one of `{"additive", "fungible"}` per the gold-answer
mechanics. Enum-string with documented value list (since only 2-3
values, low evolution risk).

### Convention questions (Commits 4-5)

#### A. Percentage convention: decimal (0.3 = 30%) or numeric (30)?

Phase E surfaced `individual_de_minimis_pct=0.3` interpreted by
synthesis as "0.3% of total assets" rather than "30% of EBITDA".
Survey shows percentage-named attrs:

- `individual_de_minimis_pct`: 1 instance, value `0.3`
- `annual_de_minimis_pct`: 1 instance, value `0.3`
- `cap_grower_pct`: ~10 instances on norms; values like `1.0`, `0.15`,
  `100.0`, `15.0` — **MIXED** (some decimal, some numeric)
- `basket_grower_pct`: similar mixed signals

**Convention question:** decimal or numeric? Schema currently
permits both because no value range is enforced.

**Recommendation:** Convention defined in Commit 4. Existing data has
both conventions; Commit 5 may need to either widen the range
constraint to accept both OR document as an extraction-prompt-side
fix (Phase G).

#### B. Monetary convention: USD raw integer

Survey of `*_usd` attrs:
- `cap_usd`: 130000000.0, 20000000.0, etc. (raw USD as float)
- `individual_de_minimis_usd`: null (not extracted in Duck Creek)
- `annual_de_minimis_usd`: 40000000.0
- `reallocation_amount_usd`: 130000000.0

Pattern: USD raw float values. Convention is consistent across
populated attrs.

**Recommendation:** Document as canonical in Commit 4. Range
constraint: `>= 0.0` is safe to enforce in Commit 5.

#### C. Boolean naming: positive vs negative framing

Survey of boolean-valued attrs (197 instances across 60+ attrs):

- `permits_*` family: `permits_product_line_exemption_2_10_c_iv`,
  `permits_section_6_05_z_unlimited`, `permits_intercompany`,
  `permits_to_borrower`, `permits_to_guarantors`, etc. — positive
  framing.
- `restricts_*` family: `restricts_borrower`, `restricts_guarantors`,
  `restricts_holdings`, `restricts_restricted_subs` — negative
  framing.
- `requires_*` family: positive (`requires_no_default`,
  `requires_board_approval`).
- `exempt_*` family: positive in spirit, but could be negative-framed
  (`exempt_non_collateral` means "non-collateral is exempt FROM the
  sweep"). Semantically positive (about the carveout) but reads
  negative-of-sweep.
- `includes_*` family: positive (`includes_cash_dividends`,
  `includes_share_buybacks`, etc.).

**Convention question:** When are negative-framed booleans acceptable?
The `restricts_*` family is the loud outlier — most attrs are
positive-framed.

**Recommendation:** Document in Commit 4: prefer positive framing
(`permits_*`, `requires_*`, `includes_*`); negative framing
(`restricts_*`, `excludes_*`) is acceptable when the SCHEMA's
default semantic is "permission" and the attribute captures a
restriction (e.g., `restricts_borrower=true` means borrower IS
restricted, narrowing the default-permissive scope). Schema-additive
work in Commit 5: add a narrative note in the schema comments rather
than enforce via constraint (no schema mechanism captures naming
preference).

#### D. Identifier formats

Survey of `*_id @key` attrs:

- `norm_id`: 23 instances; format `<deal_id>_<categorical_slug>` per
  Phase A. Sample: `6e76ed06_general_rp_basket_permission`.
- `defeater_id`: 5 instances; same format.
- `provision_id`: format `<deal_id>_<covenant_lower>` (e.g.,
  `6e76ed06_rp`).
- `answer_id`: format `ans_<deal_id>_<uuid8>`.
- `basket_id`: format `<deal_id>_<covenant>_<basket_type>[_<idx>]`
  for entity-list-extracted baskets.
- `extraction_question_id` / `question_id`: arbitrary slug
  (e.g., `rp_l24`, `duck_creek_q1`, `rp_el_reallocations`).
- `category_id`: single letter or short code (e.g., `A`, `L`, `T`,
  `P`, `JC1`, `MFN1`, `DI11`).

**Convention question:** identifier-class formats vary intentionally.
Commit 4 documents the per-class conventions and Commit 5 may add
regex constraints where formats are stable (e.g., `norm_id` regex is
enforceable).

**Recommendation:** Document each identifier class's format in Commit
4. Consider regex constraints in Commit 5 ONLY for classes whose
format is stable; skip for evolving classes (e.g., `question_id`).

#### E. Source-text encoding and length

`source_text` attribute: 156 instances on Duck Creek, distinct values
mostly under 200 chars but some exceed 2000 chars (truncated by the
`_escape` helper which slices to `[:2000]`). No length constraint
declared.

**Convention question:** what's the canonical max length? Should
schema enforce it?

**Recommendation:** Document Commit 4. Convention: 2000 chars max,
matching the existing implicit truncation. Commit 5: no schema
constraint (TypeDB string attrs don't have length limits enforceable
at the type level in 3.8); document as a prompt-side requirement
when extraction prompts reference source_text.

### Aligned (no action)

The following are explicitly aligned and require no action:

- All v4-deontic projection-rule infrastructure entities (`projection_rule`,
  `norm_template`, `attribute_emission`, etc.) match Phase C's design.
  Instance counts (30 rules, 25 norm_templates, 354 attribute_emissions)
  are stable across re-runs and match the Phase C handover spec.
- `norm_scopes_action` (2650), `norm_binds_subject` (2138),
  `norm_scopes_object` (1048): high-cardinality scope edges. Schema
  declares no cardinality constraints; data is unbounded as expected.
- `provision_has_extracted_entity` polymorphic relation: declared
  abstract; subtypes populated correctly (sweep_tier × 3,
  asset_sale_sweep × 1, etc.). Phase D2 commit 4 confirmed the fetch
  query works.
- All `cap_usd` attrs across basket entity types: USD raw float
  convention consistent. Sample values: 130000000.0, 20000000.0,
  40000000.0.
- All `*_id @key` declarations on entities have unique values per
  type (no duplicates surfaced by survey).

### Over-constrained but benign (defer to known-gaps)

- `provision_has_answer` has no role-player-tuple uniqueness
  constraint; relies on `_upsert_relation_by_role_players` storage
  discipline. Schema-level enforcement (via `@key` on a synthetic
  composite key attr) is possible but adds friction; deferred.
- `capacity_effect` is `value string` rather than enum subtypes; could
  be a subtype hierarchy (`additive_effect`, `fungible_effect`) for
  better polymorphic introspection but the 2-value space is below the
  threshold per Commit 4's planned convention. Deferred.
- ~10 attribute types are declared but unpopulated on Duck Creek
  (e.g., `intercompany_subordination_scope`, several MFN-specific
  attrs). These will populate when MFN/DI extractions land; not bugs.

## Items going to Commit 3

1. Convert `wire_reallocation_edges` to use
   `_upsert_relation_by_role_players` (or analogous helper for the
   matching pattern).
2. Add `event_governed_by_norm` relation type to
   `schema_v4_deontic.tql`. Schema-additive.
3. Add `docs/v4_known_gaps.md` entry for pre-Phase-F duplicate
   `provision_has_answer` instances.
4. Document `capacity_effect`'s 2-value enum-string convention in
   schema comments (Commit 4 will canonicalize the value list).

## Items going to Commits 4-5 (convention work)

1. Percentage convention (decimal vs numeric).
2. Monetary convention (USD raw float; range >= 0).
3. Boolean naming convention (positive-preferred; negative-acceptable
   for `restricts_*` family).
4. Identifier-class format conventions (norm_id regex enforceable;
   others documented but not enforced).
5. Source-text length convention (2000 chars; documented prompt-side).
