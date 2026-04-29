# Attribute conventions for Valence v4 (Phase F commit 4)

> Canonical conventions for attribute usage across the Valence v4 schema.
> Each convention names the rule, lists current-usage examples, calls
> out exceptions, and points to Commit 5's enforcement plan (schema
> constraints where TypeDB can capture them; documented prompt-side
> requirements where it cannot).
>
> Survey data (Commit 2): `docs/v4_schema_coherence_audit_data.json`.

## Convention 1 — Percentage values: decimal

**Convention:** Percentage attributes store the value as a decimal
fraction in the range `[0.0, 1.0]` (or, where >100% is meaningful for
grower formulas, in `[0.0, ~10.0]`). E.g., `0.3` represents 30%.

**Rationale:** The dominant pattern in extraction-stored data is
decimal (Phase E observed `individual_de_minimis_pct=0.3` and
`annual_de_minimis_pct=0.3`). Decimal is also the unambiguous form
for arithmetic — multiplying a numeric monetary attribute by a
decimal percentage gives a result in the same monetary unit, with no
implicit scaling.

**Current-usage compliance:**

| Attribute | Population | Sample values | Compliant? |
|---|---:|---|---|
| `individual_de_minimis_pct` | 1 | 0.3 | ✓ decimal |
| `annual_de_minimis_pct` | 1 | 0.3 | ✓ decimal |
| `cap_grower_pct` | ~10 (norms) | 1.0, 0.15, 100.0, 15.0 | **MIXED** |
| `basket_grower_pct` | ~30 | varies | **MIXED** |

**Exceptions (non-conforming):** `cap_grower_pct` and
`basket_grower_pct` have mixed conventions. Some values are stored as
decimals (1.0 = 100%, 0.15 = 15%) and some as numerics (100.0 =
100%, 15.0 = 15%). The Phase D2 README "cap_grower_pct extraction
scale convention (post-pilot)" entry (line 199) acknowledges this:
"v3 extraction stores basket_grower_pct as fractions (1.0 for 100%,
0.15 for 15%). Ground truth YAML authors the same concept as
percentages (100.0, 15.0)."

**Phase G prompt-side enforcement:** extraction prompts for
percentage attributes must explicitly require decimal form
("0.15 for 15%", "1.0 for 100%"). This is the canonical fix per
Phase D2's "post-pilot correct fix: update v3 extraction to emit
percentages directly, eliminating the coercion."

**Phase F Commit 5 enforcement options:**

- (a) Range constraint `[0.0, 100.0]` would accept both decimal
  (0.3) and numeric (30) forms — too loose; doesn't enforce the
  convention.
- (b) Range constraint `[0.0, ~10.0]` would block numeric values
  >10 but accept decimal `0.0..1.0` plus reasonable grower decimals
  up to ~1000% as `<= 10.0`. Existing data has values like 100.0,
  15.0 which would FAIL the constraint application.

Recommendation: defer schema-level enforcement to Phase G after
extraction prompts produce canonical decimals. Document the
convention in this file; track non-conforming values as Phase G
prompt-side work.

## Convention 2 — Monetary values: USD raw float, non-negative

**Convention:** Monetary `*_usd` attributes store USD as raw float
values (e.g., `130000000.0` for $130M), `>= 0.0`. No currency tag
(USD is implicit per attribute name). No millions or billions
abbreviation.

**Rationale:** Consistent across all current `*_usd` attributes.
Avoids ambiguity (130 could mean $130 or $130M without a documented
unit).

**Current-usage compliance:**

| Attribute | Population | Sample values | Compliant? |
|---|---:|---|---|
| `cap_usd` | ~10 norms | 130000000.0, 20000000.0 | ✓ |
| `individual_de_minimis_usd` | 0 (null on Duck Creek) | — | n/a |
| `annual_de_minimis_usd` | 1 | 40000000.0 | ✓ |
| `reallocation_amount_usd` | 2 | 130000000.0 | ✓ |
| `basket_amount_usd` | several | varies | ✓ (raw USD) |

**Exceptions:** None observed in current data.

**Phase F Commit 5 enforcement:** Add `>= 0.0` range constraint to
the canonical monetary attrs. The constraint application is safe (no
existing data violates).

## Convention 3 — Boolean naming: positive-preferred

**Convention:** Boolean attribute names are positive-framed
(`permits_X`, `requires_Y`, `includes_Z`) by default. Negative
framing (`restricts_X`, `excludes_Y`, `prohibits_Z`) is acceptable
ONLY when the attribute captures a restriction OF a default-permissive
scope.

**Rationale:** Positive framing reads cleanly: `permits_intercompany
= true` means "intercompany IS permitted." Negative framing requires
mental inversion: `restricts_borrower = true` means "the covenant
DOES restrict the borrower" — but the COVENANT is a restriction, so
"restricts_borrower" is the positive form OF the restriction. The
convention preserves intuition: each boolean's `true` value means the
NAMED thing IS the case.

**Current-usage compliance:**

| Family | Sample attrs | Framing |
|---|---|---|
| `permits_*` | `permits_to_borrower`, `permits_intercompany`, `permits_product_line_exemption_2_10_c_iv`, `permits_section_6_05_z_unlimited` | Positive |
| `requires_*` | `requires_no_default`, `requires_board_approval` | Positive |
| `includes_*` | `includes_cash_dividends`, `includes_share_buybacks`, `includes_distributions`, `includes_debt_prepayments`, `includes_investments`, `includes_guarantees` | Positive |
| `exempt_*` | `exempt_non_collateral`, `exempt_ordinary_course`, `exempt_casualty`, `exempt_below_threshold`, `exempt_ratio_basket` | Positive (about carveout) |
| `restricts_*` | `restricts_borrower`, `restricts_guarantors`, `restricts_holdings`, `restricts_restricted_subs` | Negative-frame (acceptable) |
| `rdp_includes_*` | `rdp_includes_voluntary_payments`, etc. | Positive |
| `is_*` | `is_categorical`, `is_uncapped`, `is_bidirectional` | Positive |

**Exceptions / acceptable negative-framings:**

`restricts_*` family on `rp_provision`: the RP covenant's default
state (in lawyer-speak) is "no Restricted Payments may be made,"
i.e., maximally restrictive. The `restricts_*` flags capture WHICH
parties / scopes the covenant restricts. `restricts_borrower=true`
correctly reads "the covenant restricts the borrower" — positive
framing of a restriction.

`exempt_*` family on `asset_sale_sweep`: asset-sale proceeds are
default-swept; the `exempt_*` flags capture which classes are
EXEMPT from the sweep. Positive framing of an exemption.

**Phase F Commit 5 enforcement:** No schema-level mechanism enforces
naming preferences in TypeDB 3.8. Document in this file (this
section) for ontology authors and Phase G prompt design. Future
attribute additions follow the convention; non-conforming names get
flagged in code review.

## Convention 4 — Identifier formats per identifier class

**Convention:** Each `*_id @key` attribute belongs to a CLASS with
a documented format. Format conventions per class:

| Class | Pattern | Example | Stable? |
|---|---|---|---|
| `norm_id` | `<deal_id>_<categorical_slug>` | `6e76ed06_general_rp_basket_permission` | Yes (Phase A) |
| `defeater_id` | `<deal_id>_<categorical_slug>` | `6e76ed06_jcrew_blocker_prohibition` | Yes |
| `provision_id` | `<deal_id>_<covenant_lower>` | `6e76ed06_rp` | Yes |
| `basket_id` | `<deal_id>_<covenant>_<basket_type>[_<idx>]` | `6e76ed06_rp_general_rp_basket` | Yes |
| `answer_id` | `ans_<deal_id>_<uuid8>` | `ans_6e76ed06_000ccc20` | Yes |
| `condition_id` | `<deal_id>_<short_slug>` | `6e76ed06_no_default_at_payment` | Yes |
| `question_id` | arbitrary slug | `rp_l24`, `duck_creek_q1`, `rp_el_reallocations` | Mixed (intentional) |
| `category_id` | single letter or short code | `A`, `L`, `T`, `JC1`, `MFN1`, `DI11` | Yes |
| `mapping_id` | `bcc_<basket_type>` | `bcc_general_rp_basket` | Yes |
| `extraction_question_id` | (alias for question_id; same conventions) | — | — |

**Current-usage compliance:** Survey data shows formats are followed
consistently except for `question_id` (intentionally heterogeneous —
different question seeds use different prefixes for backwards
compatibility with v3 vocabulary).

**Phase F Commit 5 enforcement:**

- Schema regex constraints feasible for stable formats: `norm_id`,
  `defeater_id`, `provision_id`, `basket_id`, `condition_id`,
  `answer_id`, `mapping_id`. Regex patterns to be added in Commit 5;
  pre-flight verifies existing data conforms.
- Schema regex NOT feasible for `question_id` (intentionally
  evolving), `category_id` (mixed length), `extraction_question_id`.

## Convention 5 — Source-text encoding and length

**Convention:** `source_text`, `source_section`, `description`,
`extraction_prompt`, and similar free-text attributes:

- Encoding: UTF-8.
- Escapes: TypeQL string-literal escaping (backslashes, quotes,
  newlines).
- Length cap: `source_text` ≤ 2000 chars (matches the implicit
  truncation in `graph_storage._escape`'s `[:2000]` slice).
- `source_section` ≤ 100 chars (canonical: section reference like
  "6.06(j)" or "Definition of Cumulative Amount").
- `description`, `extraction_prompt`: no hard cap; prompts can be
  long.

**Current-usage compliance:** All populated `source_text` values on
Duck Creek are under 2000 chars (truncated at insert time by
`_escape`). `source_section` values consistent with section-reference
format ("2.10(c)(iv)", "6.05(z)", "6.06(j)", "Definition of...").

**Phase F Commit 5 enforcement:** TypeDB 3.8 string attributes don't
have schema-level length constraints. Document in this file.
Existing `_escape` truncation enforces the 2000-char `source_text`
cap at insert time; that's the canonical enforcement point.

## Convention 6 — Enum-string vs enum-subtype threshold

**Convention:** When an attribute's value space is finite and stable:

- **< 5 distinct values, low evolution risk:** prefer enum subtype
  hierarchy (e.g., `restricted_purpose_basket sub basket`). The
  subtype IS the value semantically; queries match on type.
- **5-20 distinct values, low-to-medium evolution risk:** enum
  string with documented value list. Trade-off: less polymorphic
  introspection but easier to extend.
- **>20 distinct values OR high evolution risk:** open string.
  Document expected usage in attribute comment.

**Current-usage compliance:**

| Attribute | Distinct values today | Convention chosen | Compliant? |
|---|---:|---|---|
| `capacity_effect` | 1 (`additive`); design space ~3 | enum string (Phase F) | ✓ |
| `action_scope` | 3 (`specific`, `general`, `reallocable`) | enum string | ✓ (well-documented) |
| `capacity_composition` | 7 (`fungible`, `additive`, `computed_from_sources`, `unlimited_on_condition`, `categorical`, `n_a`, `standalone`) | enum string | ✓ |
| `modality` | 2-3 (`permission`, `prohibition`, `obligation`) | enum string | ✓ (canonical 3-value) |
| `pathway_source_type`, `pathway_target_type` | 5 each (loan_party, non_guarantor_rs, unrestricted_sub, holdco, foreign_sub) | enum string | ✓ |
| `proceeds_flow_kind` | 3 documented (`retained_after_sweep`, `declined_lender_payment`, `excluded_below_threshold`) | enum string | ✓ |

**Phase F Commit 5 enforcement:** TypeDB doesn't have native enum
constraints on string attribute values. Document the value list in
this file and in schema comments adjacent to the attribute
declaration. Future attribute value additions go through code review
(value list update + schema comment update).

For `capacity_effect` specifically: Commit 5 will update the schema
comment at `schema_unified.tql:298` to list the canonical value set
(`additive`, `fungible`).

## Compliance summary

| Convention | Compliance | Phase F Commit 5 enforcement |
|---|---|---|
| 1. Percentage decimal | Mixed (cap_grower_pct, basket_grower_pct) | Defer; Phase G prompt-side |
| 2. Monetary USD raw float, >= 0.0 | Compliant | Add `>= 0.0` range constraint |
| 3. Boolean positive-preferred | Compliant | Document; no schema enforcement |
| 4. Identifier formats per class | Compliant for stable classes | Add regex to stable classes |
| 5. Source-text 2000-char cap | Enforced at insert time | Document; insert-time enforcement |
| 6. Enum-string vs subtype threshold | Compliant | Document value lists in schema comments |

## Items going to Commit 5

**Schema constraints to add:**

1. `cap_usd >= 0.0` (and other monetary attrs as applicable)
2. `norm_id` regex `^[a-f0-9]{8}_[a-z_]+$` (or similar — verify
   existing data first)
3. Schema comment update at `schema_unified.tql` for
   `capacity_effect`: list canonical values `{additive, fungible}`

**Items deferred to Phase G prompt-side:**

1. Percentage convention enforcement (cap_grower_pct,
   basket_grower_pct, individual_de_minimis_pct,
   annual_de_minimis_pct) — extraction prompts must produce decimal
   form.

**Audit of `v3_data_normalization.py`:** Commit 5 reviews each
function in that module. Functions whose work is now redundant
(because schema or storage discipline does the equivalent) get
removed/emptied with a "removed Phase F commit 5" comment. Functions
that compensate for prompt-side issues get marked
"deprecating in Phase G."
