# V4 Phase A — Norm ID / norm_kind rename map

Date: 2026-04-24
Purpose: eliminate deal-specific identifiers (clause letters,
section-specific slugs) from norm_id and norm_kind. Per the Phase A
plan, identifiers must be:

- **Deal-agnostic**: no `dc_rp_` hardcode; use actual `deal_id` prefix
- **Categorical norm_kinds**: describe WHAT the norm IS, not WHICH
  clause it appears in
- **No clause letters in norm_ids**: disambiguators are permitted only
  when they encode a categorical attribute (threshold value, basket
  sub-type), never a section letter
- **Source_section unchanged**: clause letters belong exclusively in
  the `source_section` string attribute (e.g., `"6.06(p)"`)

Duck Creek's deal_id is `6e76ed06`; target prefix is `6e76ed06_`.

## Rename principles applied

1. `<deal_id>_<categorical_slug>[_<categorical_disambiguator>]`
2. Deal_id prefix replaces the `dc_rp_` prefix (which conflated deal
   with covenant — `dc` = Duck Creek, `rp` = Restricted Payments).
3. Categorical slug equals the norm_kind (the simplest way to keep
   them coherent) unless disambiguation is needed.
4. Disambiguators:
   - sweep tiers: threshold percentage (`_100pct`, `_50pct`, `_0pct`)
     is categorical (the threshold defines the tier's identity)
   - post-IPO basket components: `_ipo_proceeds_component`,
     `_market_cap_component` — categorical sub-type of the parent
5. `cumulative_amount` drops from norm_ids (it's Duck Creek's defined
   term for the builder-basket-aggregate; not categorical across
   agreements). Stays in `source_section` citations.

## Summary counts

- 63 norms total, all renamed
- 5 defeaters total, all renamed
- 54 norm_kinds kept as-is (already categorical)
- 10 norm_kinds renamed for categoricality
- Zero defeater_kind changes (defeater_type values already categorical:
  exception / lex_specialis / lex_posterior / higher_consent)

## Rename map — norms (63)

### Section 6.06 permissions (27 norms)

| # | Old norm_id | New norm_id | Old norm_kind | New norm_kind |
|---|---|---|---|---|
| 1 | `dc_rp_6_06_a_intercompany` | `6e76ed06_intercompany_rp_permission` | `intercompany_permission` | `intercompany_rp_permission` |
| 2 | `dc_rp_6_06_b_mgmt_equity_base` | `6e76ed06_management_equity_basket_permission` | `management_equity_basket_permission` | _(unchanged)_ |
| 3 | `dc_rp_6_06_b_mgmt_equity_carryforward` | `6e76ed06_management_equity_carryforward` | `management_equity_carryforward` | _(unchanged)_ |
| 4 | `dc_rp_6_06_b_mgmt_equity_carryback` | `6e76ed06_management_equity_carryback` | `management_equity_carryback` | _(unchanged)_ |
| 5 | `dc_rp_6_06_c_tax_distributions` | `6e76ed06_tax_distribution_basket_permission` | `tax_distribution_basket_permission` | _(unchanged)_ |
| 6 | `dc_rp_6_06_d_option_exercise_repurchase` | `6e76ed06_option_exercise_repurchase_permission` | `option_exercise_repurchase_permission` | _(unchanged)_ |
| 7 | `dc_rp_6_06_e_cash_equity_funded` | `6e76ed06_cash_equity_funded_rp_permission` | `cash_equity_funded_permission` | `cash_equity_funded_rp_permission` |
| 8 | `dc_rp_6_06_f_cumulative_amount_usage` | `6e76ed06_builder_usage_permission` | `builder_usage_permission` | _(unchanged)_ |
| 9 | `dc_rp_6_06_g_concurrent_dividend` | `6e76ed06_equity_in_kind_dividend_permission` | `concurrent_receipt_dividend_permission` | `equity_in_kind_dividend_permission` |
| 10 | `dc_rp_6_06_h_purchase_price_adjustment` | `6e76ed06_purchase_price_adjustment_rp_permission` | `purchase_price_adjustment_permission` | `purchase_price_adjustment_rp_permission` |
| 11 | `dc_rp_6_06_i_director_fees` | `6e76ed06_director_fees_permission` | `director_fees_permission` | _(unchanged)_ |
| 12 | `dc_rp_6_06_j_general_rp_basket` | `6e76ed06_general_rp_basket_permission` | `general_rp_basket_permission` | _(unchanged)_ |
| 13 | `dc_rp_6_06_k_parent_overhead` | `6e76ed06_holdco_overhead_basket_permission` | `holdco_overhead_basket_permission` | _(unchanged)_ |
| 14 | `dc_rp_6_06_l_permitted_reorganization` | `6e76ed06_reorganization_rp_permission` | `permitted_reorganization_permission` | `reorganization_rp_permission` |
| 15 | `dc_rp_6_06_m_parent_investment_funding` | `6e76ed06_parent_fund_distribution_permission` | `parent_investment_funding_permission` | `parent_fund_distribution_permission` |
| 16 | `dc_rp_6_06_n_transactions_equity_payment` | `6e76ed06_transaction_consideration_rp_permission` | `transactions_equity_payment_permission` | `transaction_consideration_rp_permission` |
| 17 | `dc_rp_6_06_o_ratio_rp_basket` | `6e76ed06_ratio_rp_basket_permission` | `ratio_basket_permission` | `ratio_rp_basket_permission` |
| 18 | `dc_rp_6_06_p_unsub_equity_distribution` | `6e76ed06_unrestricted_sub_equity_distribution_permission` | `unsub_distribution_basket_permission` | `unrestricted_sub_equity_distribution_permission` |
| 19 | `dc_rp_6_06_q_post_ipo_basket` | `6e76ed06_post_ipo_rp_basket_permission` | `post_ipo_basket_permission` | `post_ipo_rp_basket_permission` |
| 20 | `dc_rp_6_06_q_post_ipo_ipo_proceeds_component` | `6e76ed06_post_ipo_rp_basket_ipo_proceeds_component` | `post_ipo_basket_ipo_proceeds_component` | `post_ipo_rp_basket_ipo_proceeds_component` |
| 21 | `dc_rp_6_06_q_post_ipo_market_cap_component` | `6e76ed06_post_ipo_rp_basket_market_cap_component` | `post_ipo_basket_market_cap_component` | `post_ipo_rp_basket_market_cap_component` |
| 22 | `dc_rp_6_06_r_non_collateral_distribution` | `6e76ed06_non_collateral_asset_distribution_permission` | `non_collateral_distribution_permission` | `non_collateral_asset_distribution_permission` |
| 23 | `dc_rp_6_06_s_ahydo_catchup_rp` | `6e76ed06_ahydo_catchup_rp_permission` | `ahydo_catchup_permission` | `ahydo_catchup_rp_permission` |
| 24 | `dc_rp_6_06_t_transfer_pricing` | `6e76ed06_transfer_pricing_permission` | `transfer_pricing_permission` | _(unchanged)_ |
| 25 | `dc_rp_6_06_u_receivables_fees` | `6e76ed06_receivables_financing_fee_permission` | `receivables_fee_permission` | `receivables_financing_fee_permission` |
| 26 | `dc_rp_6_06_v_fractional_shares` | `6e76ed06_fractional_share_permission` | `fractional_share_permission` | _(unchanged)_ |
| 27 | `dc_rp_6_06_w_asset_disposition_dividend` | `6e76ed06_asset_disposition_proceeds_distribution_permission` | `asset_disposition_dividend_permission` | `asset_disposition_proceeds_distribution_permission` |

### Section 6.09 RDP sub-clauses (9 norms)

| # | Old norm_id | New norm_id | Old norm_kind | New norm_kind |
|---|---|---|---|---|
| 28 | `dc_rp_6_09_a_general_rdp_basket` | `6e76ed06_general_rdp_basket_permission` | `general_rdp_basket_permission` | _(unchanged)_ |
| 29 | `dc_rp_6_09_a_A_cumulative_amount_usage` | `6e76ed06_builder_usage_rdp_permission` | `cumulative_amount_rdp_usage_permission` | `builder_usage_rdp_permission` |
| 30 | `dc_rp_6_09_a_B_permitted_refinancing` | `6e76ed06_permitted_refinancing_rdp_permission` | `permitted_refinancing_rdp_permission` | _(unchanged)_ |
| 31 | `dc_rp_6_09_a_C_like_for_like_refinancing` | `6e76ed06_like_for_like_refinancing_rdp_permission` | `like_for_like_refinancing_rdp_permission` | _(unchanged)_ |
| 32 | `dc_rp_6_09_a_D_intragroup_payments` | `6e76ed06_intragroup_rdp_permission` | `intragroup_rdp_permission` | _(unchanged)_ |
| 33 | `dc_rp_6_09_a_E_scheduled_payments` | `6e76ed06_scheduled_rdp_permission` | `scheduled_rdp_permission` | _(unchanged)_ |
| 34 | `dc_rp_6_09_a_F_cash_equity_funded` | `6e76ed06_cash_equity_funded_rdp_permission` | `cash_equity_funded_rdp_permission` | _(unchanged)_ |
| 35 | `dc_rp_6_09_a_G_conversion_qualified_capital_stock` | `6e76ed06_conversion_rdp_permission` | `conversion_rdp_permission` | _(unchanged)_ |
| 36 | `dc_rp_6_09_a_H_ahydo_catchup` | `6e76ed06_ahydo_catchup_rdp_permission` | `ahydo_catchup_rdp_permission` | _(unchanged)_ |

### Section 6.03 investment basket + J.Crew blocker (2 norms)

| # | Old norm_id | New norm_id | Old norm_kind | New norm_kind |
|---|---|---|---|---|
| 37 | `dc_rp_6_03_y_general_investment_basket` | `6e76ed06_general_investment_basket_permission` | `general_investment_basket_permission` | _(unchanged)_ |
| 38 | `dc_rp_jcrew_blocker` | `6e76ed06_jcrew_blocker_prohibition` | `jcrew_blocker_prohibition` | _(unchanged)_ |

### Cumulative Amount (builder basket) and its sub-sources (18 norms)

| # | Old norm_id | New norm_id | Old norm_kind | New norm_kind |
|---|---|---|---|---|
| 39 | `dc_rp_cumulative_amount` | `6e76ed06_builder_basket_aggregate` | `builder_basket_aggregate` | _(unchanged)_ |
| 40 | `dc_rp_cumulative_amount_a_starter` | `6e76ed06_builder_source_starter` | `builder_source_starter` | _(unchanged)_ |
| 41 | `dc_rp_cumulative_amount_b_aggregate` | `6e76ed06_builder_source_three_test_aggregate` | `builder_source_b_aggregate` | `builder_source_three_test_aggregate` |
| 42 | `dc_rp_cumulative_amount_b_x_cni` | `6e76ed06_builder_source_cni` | `builder_source_cni` | _(unchanged)_ |
| 43 | `dc_rp_cumulative_amount_b_y_ecf` | `6e76ed06_builder_source_ecf` | `builder_source_ecf` | _(unchanged)_ |
| 44 | `dc_rp_cumulative_amount_b_z_ebitda_fc` | `6e76ed06_builder_source_ebitda_fc` | `builder_source_ebitda_fc` | _(unchanged)_ |
| 45 | `dc_rp_cumulative_amount_b_z_ebitda_component` | `6e76ed06_builder_source_ebitda_component` | `builder_source_ebitda_component` | _(unchanged)_ |
| 46 | `dc_rp_cumulative_amount_b_z_fixed_charges_component` | `6e76ed06_builder_source_fixed_charges_component` | `builder_source_fixed_charges_component` | _(unchanged)_ |
| 47 | `dc_rp_cumulative_amount_c_declined_ecf` | `6e76ed06_builder_source_declined_ecf` | `builder_source_declined_ecf` | _(unchanged)_ |
| 48 | `dc_rp_cumulative_amount_d_declined_asset_sale` | `6e76ed06_builder_source_declined_asset_sale` | `builder_source_declined_asset_sale` | _(unchanged)_ |
| 49 | `dc_rp_cumulative_amount_e_sale_leaseback` | `6e76ed06_builder_source_sale_leaseback` | `builder_source_sale_leaseback` | _(unchanged)_ |
| 50 | `dc_rp_cumulative_amount_f_retained_asset_sale_proceeds` | `6e76ed06_builder_source_retained_asset_sale_proceeds` | `builder_source_retained_asset_sale` | `builder_source_retained_asset_sale_proceeds` |
| 51 | `dc_rp_cumulative_amount_g_joint_venture_returns` | `6e76ed06_builder_source_joint_venture_returns` | `builder_source_joint_venture_returns` | _(unchanged)_ |
| 52 | `dc_rp_cumulative_amount_h_unsub_redesignation_fmv` | `6e76ed06_builder_source_unsub_redesignation_fmv` | `builder_source_unsub_redesignation_fmv` | _(unchanged)_ |
| 53 | `dc_rp_cumulative_amount_i_receivables_royalty_license` | `6e76ed06_builder_source_receivables_royalty_license` | `builder_source_receivables_royalty_license` | _(unchanged)_ |
| 54 | `dc_rp_cumulative_amount_j_investment_returns` | `6e76ed06_builder_source_investment_returns` | `builder_source_investment_returns` | _(unchanged)_ |
| 55 | `dc_rp_cumulative_amount_k_cumulative_deferred_revenues` | `6e76ed06_builder_source_deferred_revenues` | `builder_source_deferred_revenues` | _(unchanged)_ |
| 56 | `dc_rp_cumulative_amount_l_other` | `6e76ed06_builder_source_other` | `builder_source_other` | _(unchanged)_ |
| 57 | `dc_rp_cumulative_amount_m_netting` | `6e76ed06_builder_source_netting` | `builder_source_netting` | _(unchanged)_ |

### Asset sale + sweep (6 norms)

| # | Old norm_id | New norm_id | Old norm_kind | New norm_kind |
|---|---|---|---|---|
| 58 | `dc_rp_6_05_z_unlimited_asset_sale_basket` | `6e76ed06_unlimited_asset_sale_basket_permission` | `unlimited_asset_sale_basket_permission` | _(unchanged)_ |
| 59 | `dc_rp_2_10_c_iv_product_line_sweep_exemption` | `6e76ed06_sweep_exemption_product_line` | `sweep_exemption_product_line` | _(unchanged)_ |
| 60 | `dc_rp_2_10_c_i_de_minimis_exemption` | `6e76ed06_sweep_exemption_de_minimis` | `sweep_exemption_de_minimis` | _(unchanged)_ |
| 61 | `dc_rp_sweep_tier_100pct` | `6e76ed06_sweep_tier_100pct` | `sweep_tier` | _(unchanged — disambiguator `_100pct` is categorical threshold)_ |
| 62 | `dc_rp_sweep_tier_50pct` | `6e76ed06_sweep_tier_50pct` | `sweep_tier` | _(unchanged)_ |
| 63 | `dc_rp_sweep_tier_0pct` | `6e76ed06_sweep_tier_0pct` | `sweep_tier` | _(unchanged)_ |

## Rename map — defeaters (5)

| # | Old defeater_id | New defeater_id | defeater_type | Notes |
|---|---|---|---|---|
| D1 | `dc_rp_jcrew_ordinary_course_exception` | `6e76ed06_jcrew_ordinary_course_exception` | exception | no kind change |
| D2 | `dc_rp_jcrew_immaterial_ip_exception` | `6e76ed06_jcrew_immaterial_ip_exception` | exception | no kind change |
| D3 | `dc_rp_jcrew_fair_value_exception` | `6e76ed06_jcrew_fair_value_exception` | exception | no kind change |
| D4 | `dc_rp_jcrew_nonexclusive_license_exception` | `6e76ed06_jcrew_nonexclusive_license_exception` | exception | no kind change |
| D5 | `dc_rp_jcrew_intercompany_exception` | `6e76ed06_jcrew_intercompany_exception` | exception | no kind change |

Defeater `defeats_norm_id` attribute renames from
`dc_rp_jcrew_blocker` → `6e76ed06_jcrew_blocker_prohibition` on all 5
defeaters (per rename of norm #38).

## Edge cases resolved

**`cumulative_amount` — clause-specific or categorical?**
Resolved: Duck Creek's "Cumulative Amount" is the defined-term name for
the builder-basket-aggregate concept. Another deal could call it
"Available Amount" or "Cumulative Credit". The categorical concept is
`builder_basket_aggregate`. Renamed from `dc_rp_cumulative_amount` to
`6e76ed06_builder_basket_aggregate`. The term "Cumulative Amount"
lives only in `source_section` (e.g., `"Definition of 'Cumulative
Amount'"`) and in `source_text` excerpts — both correct.

**Builder sub-source clause letters (`_a_`, `_b_`, `_c_`, ...)**.
Resolved: clause letters stripped from norm_ids. Each sub-source keeps
a categorical slug based on WHAT it is (starter, cni, ecf, ebitda_fc,
declined_ecf, sale_leaseback, retained_asset_sale_proceeds, etc.).
The `builder_source_b_aggregate` kind renames to
`builder_source_three_test_aggregate` because `b_aggregate` carried the
clause letter into the kind.

**Sweep tier `_100pct` / `_50pct` / `_0pct`**.
Resolved: the percentage IS the tier's categorical identity — there
will always be one "full-sweep" tier, one "partial-sweep" tier, and
one "retention" tier in an asset-sale-sweep regime, and they differ
by percentage. Kept as disambiguator.

**Section 6.06 clause-letter drift (pre-existing Option-A correction)**.
Resolved: the 19 GT norms whose `source_section` was corrected in
Option A retain their authoring norm_id patterns (e.g.,
`dc_rp_6_06_e_cash_equity_funded` whose source_section is now
`"6.06(i)"`). Under the new scheme these become
`6e76ed06_cash_equity_funded_rp_permission` — no clause letter in the
id, `source_section` keeps the corrected `6.06(i)` citation. The
norm_id-vs-source_section drift documented in the YAML header becomes
moot: both are now clean and categorical.

## Norms with no kind change (count: 54)

Most GT norms already carry categorical kind labels. The 10 kinds that
renamed:

1. `intercompany_permission` → `intercompany_rp_permission`
2. `cash_equity_funded_permission` → `cash_equity_funded_rp_permission`
3. `concurrent_receipt_dividend_permission` → `equity_in_kind_dividend_permission`
4. `purchase_price_adjustment_permission` → `purchase_price_adjustment_rp_permission`
5. `permitted_reorganization_permission` → `reorganization_rp_permission`
6. `parent_investment_funding_permission` → `parent_fund_distribution_permission`
7. `transactions_equity_payment_permission` → `transaction_consideration_rp_permission`
8. `ratio_basket_permission` → `ratio_rp_basket_permission`
9. `unsub_distribution_basket_permission` → `unrestricted_sub_equity_distribution_permission`
10. `post_ipo_basket_permission` → `post_ipo_rp_basket_permission`
11. `post_ipo_basket_ipo_proceeds_component` → `post_ipo_rp_basket_ipo_proceeds_component`
12. `post_ipo_basket_market_cap_component` → `post_ipo_rp_basket_market_cap_component`
13. `non_collateral_distribution_permission` → `non_collateral_asset_distribution_permission`
14. `ahydo_catchup_permission` → `ahydo_catchup_rp_permission`
15. `receivables_fee_permission` → `receivables_financing_fee_permission`
16. `asset_disposition_dividend_permission` → `asset_disposition_proceeds_distribution_permission`
17. `cumulative_amount_rdp_usage_permission` → `builder_usage_rdp_permission`
18. `builder_source_b_aggregate` → `builder_source_three_test_aggregate`
19. `builder_source_retained_asset_sale` → `builder_source_retained_asset_sale_proceeds`

19 kind renames (not 10). `_rp_` suffix added to several kinds to
disambiguate from the eventual MFN/DI extensions (where
`ahydo_catchup_rdp_permission` and similar already exist).

## Cross-references to update

Every YAML field that stores a norm_id as a value must be rewritten
with the new id:

- `contributes_to_norm_id: <norm_id>` — on the 20 `norm_contributes_to_capacity` entries
- `defeats_norm_id: <norm_id>` — on all 5 defeaters
- `norm_provides_carryforward_to.carryforward_recipient` target — 1 edge
- `norm_provides_carryback_to.carryback_recipient` target — 1 edge
- `serves_questions.<>` question_id — UNCHANGED (question_ids are
  a separate namespace, e.g., `duck_creek_q1`)

State predicate IDs are unchanged (they carry their own composite-id
scheme and aren't affected by this rename).

## Post-rename invariant checks

After Commits 2-3 land, these queries must hold:

```tql
# 1. Every norm_id begins with the deal_id
match $n isa norm, has norm_id $nid; select $nid;
# EXPECTED: every row starts with "6e76ed06_"

# 2. No norm_id contains a section letter or number
# (grep-side check; no TypeQL regex needed)

# 3. Every norm_kind is in the approved categorical set
# (enforced by construction of the rename table above)

# 4. A4 harness round-trip is unchanged
#    m=45 s=6 mm=0 (pre-rename baseline)
```

Phase B (data model additions) consumes these IDs; getting them
right before Phase B avoids a cascading rename later.
