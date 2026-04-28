"""
Phase C Commit 4 — small utilities surviving the deletion of
`deontic_projection.py`. Provides temporal-anchor defaults shared by
`load_ground_truth.py` and (historically) the python projection.
"""
from __future__ import annotations


# Phase B — temporal anchor defaults by norm_kind.
# Builder-related kinds describe the Cumulative Amount and its sub-sources;
# they accumulate from closing-date fiscal-quarter-start. Ratio-gated kinds
# measure leverage at a test date (LTM). Everything else: not_applicable.
BUILDER_TEMPORAL_KINDS: frozenset[str] = frozenset({
    "builder_usage_permission",
    "builder_usage_rdp_permission",
    "builder_basket_aggregate",
    "builder_source_starter",
    "builder_source_three_test_aggregate",
    "builder_source_cni",
    "builder_source_ecf",
    "builder_source_ebitda_fc",
    "builder_source_ebitda_component",
    "builder_source_fixed_charges_component",
    "builder_source_declined_ecf",
    "builder_source_declined_asset_sale",
    "builder_source_sale_leaseback",
    "builder_source_retained_asset_sale_proceeds",
    "builder_source_joint_venture_returns",
    "builder_source_unsub_redesignation_fmv",
    "builder_source_receivables_royalty_license",
    "builder_source_investment_returns",
    "builder_source_deferred_revenues",
    "builder_source_other",
    "builder_source_netting",
})

LTM_TEST_DATE_KINDS: frozenset[str] = frozenset({
    "ratio_rp_basket_permission",
})


def temporal_defaults_for_norm_kind(norm_kind: str) -> tuple[str, str]:
    """Return (growth_start_anchor, reference_period_kind) defaults for
    a norm_kind. Used by load_ground_truth.py to populate default values
    when the YAML doesn't specify them; previously also used by python
    projection (deleted in Phase C Commit 4).
    """
    if norm_kind in BUILDER_TEMPORAL_KINDS:
        return ("closing_date_fiscal_quarter_start", "cumulative_since_anchor")
    if norm_kind in LTM_TEST_DATE_KINDS:
        return ("not_applicable", "ltm_at_test_date")
    return ("not_applicable", "not_applicable")
