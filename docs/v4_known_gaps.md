## Document history

- `752c847` — Initial audit committed. Contained errors (see below).
- (correction commit, superseding `752c847`) — Audit corrected. Earlier claims that the gold-answer file contained five prose errors were wrong. Gold answers are accurate. Audit now includes Section 2.10(c)(iv) as a distinct norm (missing from initial version) and identifies one depth-3 condition tree requiring Strategy A flattening.
- Pre-Prompt-06 — Added two new concerns: formulaic-cap scalability and source-text/page verification deadline. J.Crew blocker row in per-norm table updated to reflect modality=prohibition + new predicate name (see Prompt 06 pre-work Part 2).

## Duck Creek RP condition-tree audit (pre-Prompt-05) — source-verified

The Duck Creek gold-standard answers (`app/data/gold_standard/lawyer_dc_rp.json`) were audited against the operative text of the Duck Creek agreement to determine condition-tree shapes the projection engine must represent. Current `condition_holds` implementation supports depth-2: atomic, OR of atomics, AND of atomics.

### Audit basis and review note

This audit was verified against the operative text of the DISCO PARENT, INC. credit agreement (posting version 7/30/25), Sections 1 (Definitions), 2.10 (Prepayments), 6.03, 6.05, 6.06, and 6.09. Primary sources are cited per-norm below.

An earlier version of this audit (committed in `752c847`) asserted five discrepancies between the gold-answer file and operative text. On re-review, those claims were incorrect — the gold is accurate. The most consequential error in the earlier audit was conflating Section 6.05(z) (general unlimited asset sale basket, 6.00x threshold, no product-line requirement) with Section 2.10(c)(iv) (product-line-sale sweep exemption, 6.25x threshold, product-line AND requirement). Both provisions exist and both contribute to Retained Asset Sale Proceeds via different pathways. The gold answer for Q4 was describing 2.10(c)(iv); the earlier audit read only 6.05(z) and treated the discrepancy as gold error.

### Per-norm classifications

| Gold Q | Norm | Operative shape | Depth | Source |
|---|---|---|---|---|
| Q1 | Builder basket (Cumulative Amount) | unconditional usage | — | Defn "Cumulative Amount" clauses (a)-(l); §6.06(f) usage |
| Q1 | CNI source | floor at $0 per fiscal quarter | — | Defn (b)(x) |
| Q1 | Available Retained ECF Amount source | see defined term; defaults floor at $0 per fiscal year | — | Defn (b)(y) |
| Q1 | EBITDA minus 140% Fixed Charges source | floor at $0 per fiscal quarter | — | Defn (b)(z) |
| Q2 | 6.06(p) unsub equity distribution | unconditional | — | §6.06(p) |
| Q2 | J.Crew blocker (upstream on Unsub designation) | `unsub_would_own_or_license_material_ip_at_designation` (atomic; prohibition fires when true) | 1 | Defn "Unrestricted Subsidiary" / designation mechanics ~p.83 |
| Q3 | 6.06(j) general RP basket ($130m/100% EBITDA) | unconditional | — | §6.06(j) |
| Q3 | 6.09(a)(I) general RDP sub-basket ($130m/100% EBITDA) | unconditional | — | §6.09(a)(I) |
| Q3 | 6.03(y) general investment ($130m/100% EBITDA) | unconditional | — | §6.03(y) |
| Q3 | Reallocation edges (6.06(j) ← 6.09(a), 6.06(j) ← 6.03(y)) | structural (no condition tree) | — | §6.06(j) cross-refs |
| Q4 | 6.05(z) unlimited asset sale basket | `first_lien_ratio_at_or_below(6.00) OR pro_forma_no_worse(first_lien)` | 2 | §6.05(z) |
| Q4 | 2.10(c)(iv) product-line sweep exemption | `is_product_line_or_line_of_business_sale AND (first_lien_ratio_at_or_below(6.25) OR pro_forma_no_worse(first_lien))` | 3 (requires Strategy A flattening) | §2.10(c)(iv) |
| Q4 | Sweep tier 100% (>5.75x) | `first_lien_ratio_above(5.75)` | 1 | Defn "Applicable Net Cash Proceeds Percentage" clause (a) |
| Q4 | Sweep tier 50% (5.50–5.75x) | `first_lien_ratio_above(5.50) AND first_lien_ratio_at_or_below(5.75)` | 2 | Defn "Applicable Net Cash Proceeds Percentage" clause (b) |
| Q4 | Sweep tier 0% (≤5.50x) | `first_lien_ratio_at_or_below(5.50)` | 1 | Defn "Applicable Net Cash Proceeds Percentage" clause (c) |
| Q4 | De minimis exemption | `individual_proceeds_at_or_below(20M, 15%EBITDA) OR annual_aggregate_at_or_below(40M, 30%EBITDA)` | 2 | §2.10(c)(i) |
| Q4 | Retained Asset Sale Proceeds (builder source) | structural carry-through of non-swept proceeds | — | Defn "Retained Asset Sale Proceeds" |
| Q5 | Total capacity aggregate | N/A — arithmetic at operations layer | — | — |
| Q6 | 6.06(o) ratio RP basket | `first_lien_ratio_at_or_below(5.75) OR pro_forma_no_worse(first_lien)` | 2 | §6.06(o) |

### Bonus finding: 6.03(bb) (not in gold but relevant)

The agreement's 6.03(bb) (unlimited investments) has a 4-way OR that may surface in future ground truth:

`first_lien_ratio_at_or_below(6.00) OR pro_forma_no_worse(first_lien) OR interest_coverage_at_or_above(1.75) OR pro_forma_no_worse(interest_coverage)` — depth-2, flat 4-way disjunction.

Not in current Duck Creek RP gold scope (investment-side, not RP-side), but confirms the flat-OR pattern scales cleanly.

### Depth distribution — final

| Shape | Count |
|---|---|
| Unconditional | 11 norms |
| Atomic (depth-1) | 3 |
| OR of atomics (depth-2) | 3 |
| AND of atomics (depth-2) | 1 (de minimis OR across two atomic thresholds — both components are per-fiscal-year structural tests) |
| Depth-3 (AND of atomic and OR-of-atomics) | 1 (Section 2.10(c)(iv)) |

One norm (Section 2.10(c)(iv)) is depth-3 pre-flattening. Strategy A flattening is required for this one case.

### Strategy A flattening for Section 2.10(c)(iv)

Section 2.10(c)(iv) exempts product-line-sale proceeds from the mandatory prepayment sweep if both (A) the sale is of all or substantially all of a product line or line of business AND (B) the First Lien Leverage Ratio is ≤ 6.25x on a Pro Forma Basis (after giving pro forma effect to the sweep) OR no-worse pro forma.

The depth-3 condition tree:

```
AND
├── atomic: is_product_line_or_line_of_business_sale
└── OR
    ├── atomic: first_lien_ratio_at_or_below, threshold=6.25
    └── atomic: pro_forma_no_worse, reference=first_lien_net_leverage
```

Under Strategy A (Boolean distribution at ground-truth-authoring time), this becomes:

```
OR
├── AND
│   ├── atomic: is_product_line_or_line_of_business_sale
│   └── atomic: first_lien_ratio_at_or_below, threshold=6.25
└── AND
    ├── atomic: is_product_line_or_line_of_business_sale
    └── atomic: pro_forma_no_worse, reference=first_lien_net_leverage
```

Each branch is depth-2 (AND of atomics), which fits current `condition_holds` support. The `is_product_line_or_line_of_business_sale` atomic appears twice (one per branch) — this is the duplication cost of Strategy A; acceptable for this one case.

Prompt 05 ground truth encodes this flattened form directly. Projection (Prompt 07) must emit the same flattened shape.

### Resolution plan — ground truth authoring

During Prompt 05:

1. Use operative text for all source_text fields in ground-truth YAML.
2. Ground truth should reference the correct operative provision for each gold claim — in particular, Q4's gold answer describes Section 2.10(c)(iv) (product-line exemption, 6.25x) and Section 6.05(z) (general basket, 6.00x) as two distinct pathways feeding Retained Asset Sale Proceeds.
3. Model J.Crew blocker as an atomic positive condition on `designate_unrestricted_subsidiary` (the operative text placement). Q2's gold answer does not require a defeater on 6.06(p); the `requires_entities` metadata reflects upstream relevance of the blocker, which is preserved by encoding the blocker as a separate norm.
4. Encode Section 2.10(c)(iv) in the Strategy A flattened form described above — a two-branch OR, each branch an AND of the product-line atomic and one of the ratio atomics.
5. If a condition tree deeper than depth-2 surfaces in a norm we haven't yet audited (e.g., a later-added Duck Creek covenant or a newly-surfaced exception), flag before encoding.

### Remaining open items for Prompt 05

- **J.Crew blocker exact wording location.** The blocker text appears in the definitional/designation mechanics (roughly page 83 of the agreement). Ground truth should record the exact source_section reference, which requires page-level verification during Prompt 05 authoring.
- **Cumulative Amount clauses (g), (h), (i), (j), (k), (l).** Several additional sources beyond the three "greatest of" tests and starter. For ground-truth completeness these should each map to a source entity (joint venture dividends, Unsub redesignation FMV, receivables/royalty/license collections, 50% cumulative deferred revenues, etc.). Not blocking condition-tree audit but needed for Q1 ground truth.

## Formulaic caps — scalability concern

Current schema supports `cap_usd` (scalar dollar) and `cap_grower_pct` (scalar percentage) on norm. For caps expressed as formulas — e.g., 6.06(q) post-IPO "7% of IPO proceeds + 7% of market cap" — ground truth uses a `cap_formula` string attribute that the operations layer parses at query time.

This is acceptable for 2-3 norms. If formulaic caps exceed 5 norms during v4 extension to other covenants, add structured `cap_formula_components` attributes to norm (typed fields rather than string parsing). MFN and DI covenants may surface additional formulaic patterns; revisit at extension time.

Tracked for post-pilot review.

## Source text and source_page verification

Ground-truth YAML (commits `3466fb9`, `bb4e6c8`, `7890eac`) contains `<page_unknown>` and `<source_text_verification_required>` placeholders on norms where operative text or page references were not available during authoring. As of commit `7890eac`: **55 of 57 norms** carry `<page_unknown>`; **54 of 57** carry a source_text placeholder. The only fully-verified norms are §2.10(c)(i), §2.10(c)(iv), and the J.Crew blocker (operative-text excerpts available during authoring).

These placeholders MUST be resolved before Prompt 08 runs. The Prompt 08 round-trip check compares extraction's `source_text` output to ground truth's `source_text` — placeholder strings will fail the check trivially.

Resolution approach: dedicated PDF-reading pass before Prompt 08, reading the Duck Creek agreement section by section and filling in verbatim text + page numbers for every placeholder. Estimated effort: 1-2 hours. The pass also double-checks the audit's per-norm classifications against operative text for any norms added during ground truth extension (the 22 new norms in `7890eac` use provisional norm_kind mappings that may need reclassification).

Tracked for pre-Prompt-08 completion.

## `action_scope` taxonomic gap for capacity contributors (post-pilot)

The three-way enum (`specific | general | reallocable`) conflates
single-purpose permissions with capacity contributors that inherit their
parent's scope. Audit `fff8e0b` ruled Candidate A (`specific`) as the
pilot solution, preserving GT's 20/20 authoring convention. Post-pilot,
revisit: if operations-layer queries reveal friction from the
conflation (e.g., "list specific-scope permissions for dividends"
returns both usable permissions and internal contributors, confusing
users), introduce a fourth value (`contributory` or `n_a_for_scope`).

Scope: enum comment update in §4.1 + ~20 GT YAML edits + projection
branch + V3 classification prompt. Order of magnitude 90 minutes of
work. Tracked for post-pilot review.

## `cap_grower_pct` extraction scale convention (post-pilot)

v3 extraction stores `basket_grower_pct` as fractions (1.0 for 100%,
0.15 for 15%). Ground truth YAML authors the same concept as
percentages (100.0, 15.0). Prompt 08 Fix 5 and Prompt 10 Fix 4 apply a
projection-time coercion heuristic (`value ≤ 5.0 → multiply by 100`)
that safely normalizes extracted fractions to GT's percentage
convention for the three affected Duck Creek norms (general_rp_basket,
management_equity_basket, general_rdp_basket).

The heuristic works because legitimate percentage values in agreements
are always ≥ 5% and legitimate fraction values are always ≤ 2.0. Real
grower-pct values span 1-200%.

Post-pilot correct fix: update v3 extraction to emit percentages
directly, eliminating the coercion. Requires re-extraction to
re-populate. Tracked for post-pilot extraction-pass review.
