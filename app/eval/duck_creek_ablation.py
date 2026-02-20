"""
Duck Creek gold standard questions for TypeDB structure ablation test.

Each question has a gold_answer written by a senior leveraged finance
lawyer. The ablation test compares Claude's answers using three evidence
formats (structured, flat, raw PDF) against these gold standards.
"""

DUCK_CREEK_ABLATION_QUESTIONS = [
    {
        "question": "What test is the build-up basket or available amount basket based on and when does the basket start growing?",
        "gold_answer": (
            "The Cumulative Amount is based on the greatest of three tests: "
            "(1) 50% of cumulative Consolidated Net Income (which amount shall not be less than zero for any fiscal quarter), "
            "(2) Excess Cash Flow not required to be applied to prepay Term Loans or any other debt "
            "(such amount cannot be less than $0 for any fiscal year), "
            "(3) cumulative Consolidated EBITDA minus 140% of cumulative Consolidated Fixed Charges. "
            "All tests start growing from the first day of the fiscal quarter in which the Closing Date occurs."
        ),
    },
    {
        "question": "Is the Borrower permitted to dividend the equity it owns in Unrestricted Subsidiaries?",
        "gold_answer": (
            "Yes, under 6.06(p) the Borrower can dividend shares of Equity Interest "
            "or any assets of an Unrestricted Subsidiary."
        ),
    },
    {
        "question": "Are there any investment, prepayment of other debt or other baskets that can be reallocated and used to make restricted payments or dividends?",
        "gold_answer": (
            "Yes, under 6.06(j) amount available for Restricted Debt Payment under 6.09(a) "
            "and amounts available for Investments under 6.03(y) can be reallocated to the making of Dividends. "
            "6.09(a) includes the greater of $130,000,000 and 100% of Consolidated EBITDA. "
            "6.09(a) also includes other more tailored baskets available for Restricted Debt Payments "
            "which may or may not be available for reallocation, including intercompany debt payments "
            "and payments in connection with a reorganization or IPO."
        ),
    },
    {
        "question": "Can any asset sale proceeds be used to make dividends?",
        "gold_answer": (
            "Yes, Retained Asset Sale Proceeds build the Cumulative Amount which consists of proceeds from: "
            "Net Cash Proceeds from asset sales not subject to prepayment on account of Section 2.10(c)(iv), "
            "permitting proceeds from any Asset Sale using the unlimited basket 6.05(z) if such Asset Sale "
            "is a sale of a product line and the pro forma First Lien Net Leverage Ratio is 6.25x or less "
            "or if such test is no worse pro forma. Also includes asset sale proceeds not swept when "
            "First Lien Net Leverage Ratio is 5.75x or less (50% of proceeds) or 5.50x or less "
            "(100% of proceeds). Also includes Net Cash Proceeds from non-collateral assets, "
            "ordinary course asset sales, asset sales from non-ratio baskets, casualty events, "
            "and proceeds from collateral assets below de minimis thresholds of $20M/15% EBITDA "
            "individual and $40M/30% EBITDA annual."
        ),
    },
    {
        "question": "Determine the total amount of quantifiable dividend capacity.",
        "gold_answer": (
            "$520m (or 409.9% of EBITDA) plus all assets that do not secure the Loans "
            "and all non-EBITDA producing assets. RP starter: $130m/100% EBITDA. "
            "General RP basket: $130m/100% EBITDA. General prepayment of debt basket: $130m/100% EBITDA. "
            "General investment basket: $130m/100% EBITDA."
        ),
    },
    {
        "question": (
            "If the Borrower owns an asset/business division that has assets worth $200m, "
            "but EBITDA of such business is negative, can the Borrower dividend the "
            "asset/business division to shareholders if the First Lien Net Leverage Ratio is 6.0x?"
        ),
        "gold_answer": (
            "Yes, because the Ratio RP basket 6.06(o) permits such transaction as long as "
            "the First Lien Net Leverage Ratio, even if above 5.75x, is no worse."
        ),
    },
]
