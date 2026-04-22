## Duck Creek RP condition-tree audit (pre-Prompt-05) — source-verified

The Duck Creek gold-standard answers (`app/data/gold_standard/lawyer_dc_rp.json`) were audited against the operative text of the Duck Creek agreement to determine condition-tree shapes the projection engine must represent. Current `condition_holds` implementation supports depth-2: atomic, OR of atomics, AND of atomics.

**Audit basis:** Duck Creek agreement (DISCO PARENT, INC. credit agreement, August 2025, 264 pages), Sections 1 (Definitions), 6.03, 6.05, 6.06, 6.09, and 2.10.

### Gold-answer prose corrections discovered during audit

Five material differences between gold-answer prose and operative agreement text. Ground-truth authoring in Prompt 05 must use operative text, not gold summary.

| Gold claim | Operative text | Correction |
|---|---|---|
| Q4: "ratio is 6.25x or less" for 6.05(z) | "First Lien Leverage Ratio... no greater than the greater of (x) 6.00 to 1.00 and (y) [pre-transaction ratio]" | Threshold is **6.00x**, not 6.25x |
| Q4: "sale of a product line AND ratio test" for 6.05(z) | No product-line requirement anywhere in 6.05(z) | **No product-line AND.** 6.05(z) is ratio-gated only. |
| Q4: "proceeds not swept when ratio ≤ 5.75x (50%)" | Applicable Net Cash Proceeds Percentage: >5.75x → 100% sweep, 5.50x–5.75x → 50% sweep, ≤5.50x → 0% sweep | Gold describes retention; operative defines sweep. Math equivalent but draft tiers opposite. |
| Q4: "de minimis of $20M/15% EBITDA **individual and** $40M/30% EBITDA **annual**" | Section 2.10(c)(i): "either (or both) (x) [individual] **and/or** (y) [annual]" | **Disjunctive** (either qualifies the sale as de minimis), not AND. |
| Q2: J.Crew blocker applies as defeater on 6.06(p) (implied by `requires_entities`) | J.Crew blocker text is at the Unrestricted Subsidiary designation step: "no Restricted Subsidiary may be designated as an Unrestricted Subsidiary if... such Unrestricted Subsidiary would own or have an exclusive license... to any Material Intellectual Property." | **Not a defeater on 6.06(p).** Blocker is a condition on `designate_unrestricted_subsidiary` action, upstream of 6.06(p). Once an Unsub exists legitimately, 6.06(p) is unconditional. |

### Per-norm classifications

| Gold Q | Norm | Operative shape | Depth | Source |
|---|---|---|---|---|
| Q1 | Builder basket (Cumulative Amount) | unconditional usage | — | Defn "Cumulative Amount" clauses (a)-(l); §6.06(f) usage |
| Q1 | CNI source | floor at $0 per fiscal quarter | — | Defn (b)(x) |
| Q1 | Available Retained ECF Amount source | see defined term; defaults floor at $0 per fiscal year | — | Defn (b)(y) |
| Q1 | EBITDA minus 140% Fixed Charges source | floor at $0 per fiscal quarter | — | Defn (b)(z) |
| Q2 | 6.06(p) unsub equity distribution | unconditional | — | §6.06(p) |
| Q2 | J.Crew blocker (upstream on Unsub designation) | `designation_excludes_material_ip_from_unsub` (positive predicate) | 1 | Defn "Unrestricted Subsidiary" / designation mechanics ~p.83 |
| Q3 | 6.06(j) general RP basket ($130m/100% EBITDA) | unconditional | — | §6.06(j) |
| Q3 | 6.09(a)(I) general RDP sub-basket ($130m/100% EBITDA) | unconditional | — | §6.09(a)(I) |
| Q3 | 6.03(y) general investment ($130m/100% EBITDA) | unconditional | — | §6.03(y) |
| Q3 | Reallocation edges (6.06(j) ← 6.09(a), 6.06(j) ← 6.03(y)) | structural (no condition tree) | — | §6.06(j) cross-refs |
| Q4 | 6.05(z) unlimited asset sale basket | `first_lien_ratio_at_or_below(6.00) OR pro_forma_no_worse(first_lien)` | 2 | §6.05(z) |
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
| AND of atomics (depth-2) | 1 |
| Deeper than depth-2 | **0** |

**Every condition in Duck Creek RP fits within depth-2 natively.** No Strategy A flattening required anywhere. Current `condition_holds` implementation (commit `7a295cc`: atomic + OR + AND) is sufficient without modification.

### Resolution plan — ground truth authoring

During Prompt 05:

1. Use operative text for all source_text fields in ground-truth YAML (not gold-answer paraphrase).
2. Apply the five corrections above when encoding Q2, Q4.
3. Model J.Crew blocker as a condition on `designate_unrestricted_subsidiary`, not as a defeater on 6.06(p).
4. If a condition tree deeper than depth-2 surfaces in a norm we haven't yet audited (e.g., a later-added Duck Creek covenant or a newly-surfaced exception), flag before encoding.

### Remaining open items for Prompt 05

- **J.Crew blocker exact wording location.** The blocker text appears in the definitional/designation mechanics (roughly page 83 of the agreement). Ground truth should record the exact source_section reference, which requires page-level verification during Prompt 05 authoring.
- **Cumulative Amount clauses (g), (h), (i), (j), (k), (l).** Several additional sources beyond the three "greatest of" tests and starter. For ground-truth completeness these should each map to a source entity (joint venture dividends, Unsub redesignation FMV, receivables/royalty/license collections, 50% cumulative deferred revenues, etc.). Not blocking condition-tree audit but needed for Q1 ground truth.
