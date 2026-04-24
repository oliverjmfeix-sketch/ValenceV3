# Duck Creek RP Ground Truth — Source Verification Findings

Date: 2026-04-24
Branch: `v4-deontic`
Scope: Pre-Prompt-11 source verification pass on
`app/data/duck_creek_rp_ground_truth.yaml` (63 norms).
PDF source: `C:/Users/olive/OneDrive/Documents/LegalVue/Credit
Agreements/Duck Creek_07_2025.pdf` (264 pages, extracted via PyMuPDF).

## Summary

| State | Before | After |
|---|---|---|
| `<page_unknown>` placeholders | 61 / 63 | **0** |
| `<source_text_verification_required>` placeholders | 34 / 63 | 12 (deferred) |
| `<operative-text verification required ...>` placeholders | 7 | 7 (deferred — clause mismatch) |
| sub_clause descriptions pending | 4 | 0 |

Page numbers were filled for all 61 previously-unknown norms using the
agreement's internal pagination (PDF page - 6) — the same convention the
GT already used for the two pre-verified norms
(`dc_rp_2_10_c_i_de_minimis_exemption` → p.105 and
`dc_rp_2_10_c_iv_product_line_sweep_exemption` → p.106).

Source text was filled verbatim from the agreement for 41 norms plus the
four `6.06(m)` sub-clauses. Source text was **not** filled for 19 norms
where the GT's declared `source_section` (clause letter) disagreed with
the agreement's operative text at that letter — these are documented in
§ Clause-letter mismatches below and are flagged as a pre-Prompt-11
decision point.

## v3 source-reference methodology (Step 0)

Per `app/services/graph_storage.py` (lines 550-590, 1050-1081), v3
stores per-extraction provenance as three attributes on every storable
concept:

- `source_text`: verbatim quote, max 500 chars, no paraphrase, no
  "See Section X" placeholders.
- `source_page`: integer page number of the agreement (extracted from
  `[PAGE X]` markers in the document text).
- `source_section` (called `section_reference` in v3 schema): the
  specific clause reference, e.g. `6.06(p)`, `6.09(a)(I)`,
  `Definition of 'Cumulative Amount', clause (h)`. v3 enforces
  specificity: "6.06(p)" not just "6.06".
- `confidence`: decimal 0-1 (not used by the GT YAML).

v4 GT YAML uses the same three attributes (`source_section`,
`source_page`, `source_text`) with the v3 rename from
`section_reference` → `source_section`. Projection's
`project_entity` already normalises the rename at emit time
(`graph_storage.py` propagates `section_reference` as `source_section`).

The GT YAML's page-number convention matches v3's: the
agreement's internal printed page, not the PDF index. The fills in this
pass followed that convention.

## Norms fully verified (page + verbatim source_text filled)

Section 6.06 (clause letter matches agreement):

- `dc_rp_6_06_a_intercompany` — p.189
- `dc_rp_6_06_b_mgmt_equity_base` — p.189
- `dc_rp_6_06_b_mgmt_equity_carryforward` — p.189 (within b(i)(x))
- `dc_rp_6_06_b_mgmt_equity_carryback` — p.189 (within b(i)(y))
- `dc_rp_6_06_c_tax_distributions` — p.190
- `dc_rp_6_06_d_option_exercise_repurchase` — p.190
- `dc_rp_6_06_f_cumulative_amount_usage` — p.190
- `dc_rp_6_06_j_general_rp_basket` — p.190
- `dc_rp_6_06_k_parent_overhead` — p.191
- `dc_rp_6_06_m_parent_investment_funding` — p.191 (sub_clauses (i)-(iv) filled)
- `dc_rp_6_06_o_ratio_rp_basket` — p.192
- `dc_rp_6_06_p_unsub_equity_distribution` — p.192
- `dc_rp_6_06_q_post_ipo_basket` — p.192
- `dc_rp_6_06_q_post_ipo_ipo_proceeds_component` — p.192
- `dc_rp_6_06_q_post_ipo_market_cap_component` — p.192
- `dc_rp_6_06_w_asset_disposition_dividend` — p.193

Section 6.09 (all clause letters match):

- `dc_rp_6_09_a_general_rdp_basket` — p.194 (6.09(a)(I))
- `dc_rp_6_09_a_A_cumulative_amount_usage` — p.193
- `dc_rp_6_09_a_B_permitted_refinancing` — p.193
- `dc_rp_6_09_a_C_like_for_like_refinancing` — p.193
- `dc_rp_6_09_a_D_intragroup_payments` — p.194
- `dc_rp_6_09_a_E_scheduled_payments` — p.194
- `dc_rp_6_09_a_F_cash_equity_funded` — p.194
- `dc_rp_6_09_a_G_conversion_qualified_capital_stock` — p.194
- `dc_rp_6_09_a_H_ahydo_catchup` — p.194

Section 6.03 / 6.05 / 2.10:

- `dc_rp_6_03_y_general_investment_basket` — p.184
- `dc_rp_6_05_z_unlimited_asset_sale_basket` — p.189
- `dc_rp_2_10_c_i_de_minimis_exemption` — p.105 (pre-verified)
- `dc_rp_2_10_c_iv_product_line_sweep_exemption` — p.106 (pre-verified)

Sweep tiers (definition of "Applicable Net Cash Proceeds Percentage"):

- `dc_rp_sweep_tier_100pct` — p.5 clause (a)
- `dc_rp_sweep_tier_50pct` — p.5 clause (b)
- `dc_rp_sweep_tier_0pct` — p.6 clause (c)

Cumulative Amount (clause letters matching agreement):

- `dc_rp_cumulative_amount` — p.26 (top-level definition)
- `dc_rp_cumulative_amount_a_starter` — p.26
- `dc_rp_cumulative_amount_b_aggregate` — p.26
- `dc_rp_cumulative_amount_b_x_cni` — p.26
- `dc_rp_cumulative_amount_b_y_ecf` — p.26
- `dc_rp_cumulative_amount_b_z_ebitda_fc` — p.26
- `dc_rp_cumulative_amount_b_z_ebitda_component` — p.26
- `dc_rp_cumulative_amount_b_z_fixed_charges_component` — p.26
- `dc_rp_cumulative_amount_f_retained_asset_sale_proceeds` — p.27
- `dc_rp_cumulative_amount_g_joint_venture_returns` — p.27
- `dc_rp_cumulative_amount_m_netting` — p.28

J.Crew blocker (page verified; operative text was already verbatim):

- `dc_rp_jcrew_blocker` — p.83

## Norms filled with page only (source_text deferred)

Nineteen norms carry a clause-letter mismatch between their GT
`source_section` and the agreement's operative text at that letter.
For these, page was filled (to the page where the GT's stated letter
actually appears in the PDF — consistent with "verify the declared
citation") but source_text was left as a placeholder to avoid silently
rewriting either (a) the clause letter or (b) the norm's semantic
identity without reviewer sign-off.

### Section 6.06 (11 norms)

The GT's clause letters (e), (g)?, (h), (i), (l), (n), (r), (s), (t),
(u), (v) do not line up with the agreement's clauses. Mapping observed:

| GT norm_id | GT says | Agreement clause at that letter | Norm's semantic content → actual agreement clause |
|---|---|---|---|
| `dc_rp_6_06_e_cash_equity_funded` | 6.06(e) | Receivables Fees and Securitization Fees | 6.06(i) — cash equity funded dividends |
| `dc_rp_6_06_g_concurrent_dividend` | 6.06(g) | Dividends solely in Equity Interests of Holdings | ambiguous — see § Ambiguous |
| `dc_rp_6_06_h_purchase_price_adjustment` | 6.06(h) | Dividends under §5.17(d) + director fees | 6.06(l) — PPA/working-capital distributions |
| `dc_rp_6_06_i_director_fees` | 6.06(i) | Dividends from cash equity contributions | 6.06(h) — director fees |
| `dc_rp_6_06_l_permitted_reorganization` | 6.06(l) | PPA/working-capital distributions | 6.06(r) — Permitted Reorganization + IPO Reorganization |
| `dc_rp_6_06_n_transactions_equity_payment` | 6.06(n) | AHYDO catch-up payments | 6.06(v) — Transactions |
| `dc_rp_6_06_r_non_collateral_distribution` | 6.06(r) | Permitted Reorganization | 6.06(u) — Dividends in assets not constituting Collateral |
| `dc_rp_6_06_s_ahydo_catchup_rp` | 6.06(s) | transfer pricing / shared services | 6.06(n) — AHYDO catch-up |
| `dc_rp_6_06_t_transfer_pricing` | 6.06(t) | fractional shares | 6.06(s) — transfer pricing / shared services |
| `dc_rp_6_06_u_receivables_fees` | 6.06(u) | Dividends in non-Collateral assets | 6.06(e) — Receivables / Securitization Fees |
| `dc_rp_6_06_v_fractional_shares` | 6.06(v) | Transactions (Closing Date) | 6.06(t) — fractional shares |

Pattern: the GT author assigned letters to each norm by some criterion
(alphabetical by norm_kind? working-order during authoring?) that drifted
from the agreement's actual clause ordering. Most of the mismatches are
pairwise swaps (e↔u, h↔i, l↔r, n↔s, t↔v).

### Cumulative Amount clauses (8 norms)

Agreement-clause-letter vs GT-clause-letter in Definition of Cumulative
Amount. GT splits clause (f) into three sub-norms (c_declined_ecf,
d_declined_asset_sale, f_retained_asset_sale_proceeds) and has generic
catch-all semantics that don't line up with the agreement's specific
clause ordering for (h)-(l).

| GT norm_id | GT says | Agreement clause at that letter | Norm's semantic content → actual clause |
|---|---|---|---|
| `dc_rp_cumulative_amount_c_declined_ecf` | clause (c) | Eligible Equity Issuance Net Cash Proceeds | clause (f) — Retained Declined Proceeds (part of (f)) |
| `dc_rp_cumulative_amount_d_declined_asset_sale` | clause (d) | Indebtedness converted to Qualified Capital Stock | clause (f) — Retained Declined Proceeds (part of (f)) |
| `dc_rp_cumulative_amount_e_sale_leaseback` | clause (e) | fair market value of assets contributed by Sponsor/Affiliates | clause (i) (partial) — Sale Leaseback Transactions |
| `dc_rp_cumulative_amount_h_unsub_redesignation_fmv` | clause (h) | proceeds from sale of JV/Unsub equity interests | clause (j) — Unsub re-designation FMV |
| `dc_rp_cumulative_amount_i_receivables_royalty_license` | clause (i) | Sale Leaseback + Investment returns | clause (k) — Receivables / royalty / license |
| `dc_rp_cumulative_amount_j_investment_returns` | clause (j) | Unsub re-designation FMV | clause (i) (partial) — Investment returns |
| `dc_rp_cumulative_amount_k_cumulative_deferred_revenues` | clause (k) | Receivables / royalty / license | clause (l) — 50% deferred revenues |
| `dc_rp_cumulative_amount_l_other` | clause (l) | 50% deferred revenues | no clean match — catch-all not present in agreement |

Additional complication: clauses (c)_declined_ecf and (d)_declined_asset_sale
semantically decompose agreement clause (f) into its "Retained Declined
Proceeds" and "Retained Asset Sale Proceeds" sub-components, which the
agreement leaves bundled.

### Ambiguous

One further norm is flagged as ambiguous rather than definitively
mismatched:

- `dc_rp_6_06_g_concurrent_dividend` — agreement (g) reads "Dividends
  made solely in Equity Interests of Holdings (other than Disqualified
  Capital Stock)". The norm's label "concurrent_receipt_dividend" could
  arguably describe this (equity-in-kind dividends are typically made
  concurrent with some triggering event), but the match is not
  unambiguous. Left for reviewer sign-off.

## Recommended resolution options (user decision)

Three options for the 19 mismatched-letter norms:

**Option A — Rewrite source_section to match operative text.** Minimal
change: update each mismatched norm's `source_section` attribute to the
agreement's actual clause letter for the semantic content the norm
describes. Re-author source_text verbatim from the new clause. Leave
`norm_id` labels intact (they're internal identifiers, not citations).

*Pros:* preserves norm_ids, tuple keys, and harness match behaviour.
Citations become legally correct. Low projection-layer risk.

*Cons:* 19 norms touched; creates visual inconsistency between norm_id
(says `_6_06_e_cash_equity_funded`) and source_section (says `6.06(i)`).
Future reviewers need to know the norm_id suffix is an internal label,
not a clause citation.

**Option B — Rewrite norm_id labels to match clause letters.**
Rename each affected norm so that `_e_cash_equity_funded` →
`_i_cash_equity_funded`, etc. Update all `contributes_to_norm_id`
references in the same pass. Update `lawyer_dc_rp.json` if it cites any
of these norm_ids.

*Pros:* internal consistency restored; cleaner for future readers.

*Cons:* larger touch surface; risk of missing a cross-reference;
harness regression potential if match keys are accidentally disturbed.
6 gold-question tuples need re-audit for serves_questions alignment.

**Option C — Defer to post-pilot and re-author source_text as
"conceptual" fills.** Fill source_text verbatim from the agreement
clause the norm's semantic content actually describes, but do NOT
change `source_section`. This produces a citation-vs-content mismatch
inside each affected norm (source_section says (e), source_text is the
operative text of (i)).

*Pros:* minimal mechanical effort; keeps all norm_ids and clause
letters stable.

*Cons:* rendering layer would show users inconsistent citations — "per
6.06(e), [text from 6.06(i)]" is worse than a clean placeholder.
Not recommended.

**Recommendation: Option A.** Scope is small, the change is
semantically accurate, and downstream behaviour is preserved. Option B
can follow post-pilot if visual consistency matters for the renderer.

## Impact on current metrics

Ran validation harness + classification measurement after GT reload.
Changes from Prompt 10 are attributable to the J.Crew restoration (Part
1 fix), not GT verification:

| Metric | Prompt 10 | Post-verification | Δ |
|---|---|---|---|
| A1 structural | pass | pass | — |
| A2 segment counts | fail | fail | — |
| A3 kind coverage | fail | fail | — |
| A4 round_trip | missing=46 spurious=6 mismatched=0 | missing=45 spurious=6 mismatched=0 | -1 missing (J.Crew) |
| A5 rule selection | pass (100%) | pass (100%) | — |
| cap_comp matched | 75.0% (12/16) | 76.5% (13/17) | +0.01 |
| action_scope matched | 87.5% (14/16) | 88.2% (15/17) | +0.01 |
| condition_struct matched | 93.8% (15/16) | 94.1% (16/17) | +0.01 |
| cap_comp aggregate | 54.5% | 56.5% | +2.0 |
| action_scope aggregate | 63.6% | 65.2% | +1.6 |
| condition_struct aggregate | 68.2% | 69.6% | +1.4 |

All metric improvements trace to the restored J.Crew prohibition norm
joining the matched set. Source verification itself doesn't affect
classification or round-trip since those compare structural tuples, not
source text.

## Residual placeholders

After this pass:

- 12 norms retain `<source_text_verification_required>` exactly as
  stored (11 §6.06 mismatches + 1 Cumulative Amount `k_cumulative_deferred_revenues`).
- 7 norms retain `<operative-text verification required for ...>`
  descriptive placeholders (Cumulative Amount c, d, e, h, i, j, l).

All 19 correspond to clause-letter mismatches above. Their source_page
attributes are filled to match the GT's stated clause letter in the
PDF, so a reviewer opening the PDF to that page will see whichever
clause text sits there — which is intentional, because that's how
verification should work.

Decision point before Prompt 11: pick Option A / B / C and apply. None
of the downstream Prompt 11 operations are blocked by the remaining
placeholders — the operations layer consumes structural tuples, not
source text — but user-facing citations will be wrong until resolved.
