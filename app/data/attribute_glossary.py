"""
Maps entity attributes to their source ontology question IDs.

The graph reader uses this at display time to annotate entity
attributes with their question text. The question text carries
meaning, scope, and relationship to other provisions.

SSoT: if an entity attribute has a source scalar question, the
mapping belongs here. If missing, the attribute renders without
annotation (graceful degradation).

Keys: entity_type (TypeDB type label) → attr_key (schema attribute name) → question_id
"""

ATTRIBUTE_GLOSSARY: dict[str, dict[str, str]] = {
    # ── Ratio Basket ────────────────────────────────────────────
    "ratio_basket": {
        "ratio_threshold": "rp_g2",
        "is_unlimited_if_met": "rp_g4",
        "has_no_worse_test": "rp_g5",
        "no_worse_is_uncapped": "rp_g6",
    },
    # ── General RP Basket ───────────────────────────────────────
    "general_rp_basket": {
        "basket_amount_usd": "rp_n1",
        "basket_grower_pct": "rp_j2",
    },
    # ── Management Equity Basket ────────────────────────────────
    "management_equity_basket": {
        "annual_cap_usd": "rp_c4",
        "annual_cap_pct_ebitda": "rp_c5",
        "cap_uses_greater_of": "rp_c6",
        "carryforward_permitted": "rp_c7",
    },
    # ── Builder Basket ──────────────────────────────────────────
    "builder_basket": {
        "uses_greatest_of_tests": "rp_f14",
        "start_date_language": "rp_f15",
    },
    # ── Builder Sources (keyed by subtype) ──────────────────────
    "starter_amount_source": {
        "dollar_amount": "rp_f2",
        "ebitda_percentage": "rp_f3",
        "uses_greater_of": "rp_f4",
    },
    "cni_source": {
        "percentage": "rp_f5",
    },
    "equity_proceeds_source": {
        "percentage": "rp_f6",
    },
    "ecf_source": {
        "retained_ecf_formula": "rp_f11",
    },
    "ebitda_fc_source": {
        "fc_multiplier": "rp_f13",
    },
    # ── J.Crew Blocker ──────────────────────────────────────────
    "jcrew_blocker": {
        "covers_transfer": "rp_k9",
        "covers_designation": "rp_k10",
        "covers_ip": "rp_k2",
        "covers_material_assets": "rp_k3",
        "is_sacred_right": "rp_k8",
    },
    # ── Unsub Designation ───────────────────────────────────────
    "unsub_designation": {
        "dollar_cap_usd": "rp_j4",
        "pct_cap_assets": "rp_j5",
    },
    # ── Unsub Distribution Basket (Category P) ──────────────────
    "unsub_distribution_basket": {
        "is_categorical": "rp_p4",
        "covers_equity_interests": "rp_p2",
        "covers_assets": "rp_p3",
    },
}
