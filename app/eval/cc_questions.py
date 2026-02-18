# =============================================================================
# Credit Committee Eval Questions (Fixed)
#
# 8 questions (5 RP + 3 J.Crew) from CLO analyst perspective.
# Bypass Sonnet question generation — deterministic across deals.
# =============================================================================

CC_RP_QUESTIONS = [
    # RP-1: Day-one capacity aggregation
    (
        "What is the total day-one Restricted Payment capacity available "
        "across all capped baskets — starter amount, general basket, "
        "management equity, tax distribution, and any other fixed-dollar "
        "baskets — before any builder basket accumulation? Express as a "
        "single dollar figure and list each component basket with its "
        "individual amount."
    ),
    # RP-2: ECF sweep → builder interaction
    (
        "At what First Lien Leverage Ratio does the Applicable ECF "
        "Percentage drop to 0%, and once it does, does 100% of Excess "
        "Cash Flow become Available Retained ECF Amount that builds "
        "Cumulative Amount capacity? Identify all leverage tiers and "
        "their corresponding ECF sweep percentages."
    ),
    # RP-3: Reinvestment period and Declined Proceeds
    (
        "What is the reinvestment period for asset sale proceeds before "
        "mandatory prepayment is triggered, and do Declined Proceeds "
        "build the Cumulative Amount on the same basis as Retained Asset "
        "Sale Proceeds? Cite the specific section and any dollar or time "
        "thresholds."
    ),
    # RP-4: Basket stacking
    (
        "Can the borrower use the ratio-based unlimited RP basket and "
        "the builder basket / Cumulative Amount simultaneously in the "
        "same fiscal quarter to fund separate Restricted Payments — i.e., "
        "is there anti-stacking language that prevents combining capacity "
        "from multiple baskets, or are they independent? Cite any "
        "anti-duplication or basket coordination provisions."
    ),
    # RP-5: Default-condition overlay
    (
        "Which Restricted Payment baskets remain available during a "
        "continuing Event of Default? Is there a distinction between "
        "payment defaults and other defaults for purposes of basket "
        "availability? List each basket that survives default with its "
        "applicable condition."
    ),
]

CC_JCREW_QUESTIONS = [
    # JC-1: Licensing loophole
    (
        "Can the borrower grant an exclusive, perpetual, royalty-free "
        "license of Material Intellectual Property to an Unrestricted "
        "Subsidiary without triggering the J.Crew blocker? Specifically: "
        "is 'Transfer' defined, and if so does the definition include "
        "exclusive licensing? If Transfer is undefined, what prevents "
        "economic transfer of IP value through licensing arrangements?"
    ),
    # JC-2: Total unsub investment pathway capacity
    (
        "What is the maximum aggregate investment capacity into "
        "Unrestricted Subsidiaries across all available pathways — "
        "dedicated unrestricted subsidiary basket, general investment "
        "basket, builder basket / Available Amount, ratio-based basket, "
        "and any reallocation from other covenants? Can these pathways "
        "be stacked? Express the total as a dollar figure or formula."
    ),
    # JC-3: Enforcement and sacred rights
    (
        "If Material IP is transferred to an Unrestricted Subsidiary in "
        "breach of the J.Crew blocker, does the breach constitute an "
        "Event of Default that could trigger acceleration? Can Required "
        "Lenders (simple majority) waive the breach, or is the blocker "
        "a sacred right requiring all-lender consent to amend or waive? "
        "Cite the amendment section."
    ),
]

CC_QUESTIONS = CC_RP_QUESTIONS + CC_JCREW_QUESTIONS
