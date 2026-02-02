"""
Seed Ontology Questions into TypeDB.

Run after schema initialization:
    python -m app.scripts.seed_ontology

This creates the ontology_question entities that power the UI.
Questions are the Single Source of Truth (SSoT).
"""
import os
import sys
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.services.typedb_client import typedb_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Define all ontology questions
QUESTIONS = [
    # ==============================================================================
    # MFN PROVISIONS (category_order: 1)
    # ==============================================================================
    {
        "question_id": "mfn_q1",
        "question_text": "Does an MFN (Most Favored Nation) provision exist?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 1,
        "target_attribute": "mfn_exists",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q2",
        "question_text": "Is there a sunset provision that terminates MFN protection?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 2,
        "target_attribute": "sunset_exists",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q3",
        "question_text": "What is the sunset period in months?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 3,
        "target_attribute": "sunset_period_months",
        "answer_type": "integer"
    },
    {
        "question_id": "mfn_q4",
        "question_text": "Is the sunset tied to maturity date?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 4,
        "target_attribute": "sunset_tied_to_maturity",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q5",
        "question_text": "What is the MFN threshold in basis points?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 5,
        "target_attribute": "threshold_bps",
        "answer_type": "integer"
    },
    {
        "question_id": "mfn_q6",
        "question_text": "Does the threshold apply only to margin (not all-in yield)?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 6,
        "target_attribute": "threshold_applies_to_margin_only",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q7",
        "question_text": "Is OID (Original Issue Discount) included in yield calculation?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 7,
        "target_attribute": "oid_included_in_yield",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q8",
        "question_text": "Is interest rate floor included in yield calculation?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 8,
        "target_attribute": "floor_included_in_yield",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q9",
        "question_text": "Are upfront fees included in yield calculation?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 9,
        "target_attribute": "upfront_fees_included_in_yield",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q10",
        "question_text": "Does MFN cover Term Loan A facilities?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 10,
        "target_attribute": "covers_term_loan_a",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q11",
        "question_text": "Does MFN cover Term Loan B facilities?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 11,
        "target_attribute": "covers_term_loan_b",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q12",
        "question_text": "Does MFN cover incremental facilities?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 12,
        "target_attribute": "covers_incremental_facilities",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q13",
        "question_text": "Does MFN cover ratio debt?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 13,
        "target_attribute": "covers_ratio_debt",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q14",
        "question_text": "Is acquisition debt excluded from MFN?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 14,
        "target_attribute": "excludes_acquisition_debt",
        "answer_type": "boolean"
    },
    {
        "question_id": "mfn_q15",
        "question_text": "Is there a yield exclusion pattern (OID and floor both excluded)?",
        "question_category": "MFN Provisions",
        "category_order": 1,
        "question_order": 15,
        "target_attribute": "yield_exclusion_pattern",
        "answer_type": "boolean"
    },
    
    # ==============================================================================
    # RESTRICTED PAYMENTS (category_order: 2)
    # ==============================================================================
    {
        "question_id": "rp_q1",
        "question_text": "Does a Restricted Payments provision exist?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 1,
        "target_attribute": "rp_exists",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q2",
        "question_text": "Does the dividend covenant apply to Holdings?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 2,
        "target_attribute": "dividend_applies_to_holdings",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q3",
        "question_text": "Does a builder basket (Cumulative Amount) exist for dividends?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 3,
        "target_attribute": "builder_basket_exists",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q4",
        "question_text": "What is the builder basket starter amount in USD?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 4,
        "target_attribute": "builder_starter_amount_usd",
        "answer_type": "double"
    },
    {
        "question_id": "rp_q5",
        "question_text": "Does the builder include retained Excess Cash Flow?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 5,
        "target_attribute": "builder_includes_retained_ecf",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q6",
        "question_text": "Does the builder include returns from unrestricted subsidiaries?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 6,
        "target_attribute": "builder_includes_sub_returns",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q7",
        "question_text": "Is there a ratio-based unlimited dividend basket?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 7,
        "target_attribute": "ratio_dividend_basket_exists",
        "answer_type": "boolean"
    },
    {
        "question_id": "rp_q8",
        "question_text": "What leverage ratio threshold triggers unlimited dividends?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 8,
        "target_attribute": "ratio_dividend_leverage_threshold",
        "answer_type": "double"
    },
    {
        "question_id": "rp_q9",
        "question_text": "What is the general dividend basket amount in USD?",
        "question_category": "Restricted Payments",
        "category_order": 2,
        "question_order": 9,
        "target_attribute": "general_dividend_basket_usd",
        "answer_type": "double"
    },
    
    # ==============================================================================
    # J.CREW RISK (category_order: 3)
    # ==============================================================================
    {
        "question_id": "jc_q1",
        "question_text": "Is unrestricted subsidiary designation permitted?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 1,
        "target_attribute": "unrestricted_sub_designation_permitted",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q2",
        "question_text": "Does unrestricted sub designation require no Event of Default?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 2,
        "target_attribute": "unrestricted_sub_requires_no_default",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q3",
        "question_text": "Is there an EBITDA cap on unrestricted subsidiaries?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 3,
        "target_attribute": "unrestricted_sub_has_ebitda_cap",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q4",
        "question_text": "Are IP transfers to subsidiaries permitted?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 4,
        "target_attribute": "ip_transfers_to_subs_permitted",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q5",
        "question_text": "Do IP transfers require fair value consideration?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 5,
        "target_attribute": "ip_transfers_require_fair_value",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q6",
        "question_text": "Is a J.Crew blocker present in the agreement?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 6,
        "target_attribute": "jcrew_blocker_present",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q7",
        "question_text": "Does the J.Crew blocker cover intellectual property?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 7,
        "target_attribute": "jcrew_blocker_covers_ip",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q8",
        "question_text": "Does the J.Crew blocker cover material assets?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 8,
        "target_attribute": "jcrew_blocker_covers_material_assets",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q9",
        "question_text": "Does the IP definition include trade secrets?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 9,
        "target_attribute": "ip_definition_includes_trade_secrets",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q10",
        "question_text": "Does the IP definition include know-how?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 10,
        "target_attribute": "ip_definition_includes_know_how",
        "answer_type": "boolean"
    },
    {
        "question_id": "jc_q11",
        "question_text": "Is there J.Crew pattern risk (trapdoor combination present)?",
        "question_category": "J.Crew Risk",
        "category_order": 3,
        "question_order": 11,
        "target_attribute": "jcrew_pattern",
        "answer_type": "boolean"
    },
    
    # ==============================================================================
    # PATTERN DETECTION (category_order: 4)
    # ==============================================================================
    {
        "question_id": "pat_q1",
        "question_text": "Is there a yield exclusion pattern in MFN?",
        "question_category": "Pattern Detection",
        "category_order": 4,
        "question_order": 1,
        "target_attribute": "yield_exclusion_pattern",
        "answer_type": "boolean"
    },
    {
        "question_id": "pat_q2",
        "question_text": "Is there a weak MFN pattern?",
        "question_category": "Pattern Detection",
        "category_order": 4,
        "question_order": 2,
        "target_attribute": "weak_mfn_pattern",
        "answer_type": "boolean"
    },
    {
        "question_id": "pat_q3",
        "question_text": "Is there J.Crew pattern risk?",
        "question_category": "Pattern Detection",
        "category_order": 4,
        "question_order": 3,
        "target_attribute": "jcrew_pattern",
        "answer_type": "boolean"
    },
]


def seed_ontology():
    """Seed ontology questions into TypeDB."""
    logger.info("Connecting to TypeDB...")
    
    if not typedb_client.connect():
        logger.error(f"Failed to connect: {typedb_client.connection_error}")
        sys.exit(1)
    
    logger.info(f"Seeding {len(QUESTIONS)} ontology questions...")
    
    try:
        with typedb_client.write_transaction() as tx:
            for q in QUESTIONS:
                query = f"""
                    insert $q isa ontology_question,
                        has question_id "{q['question_id']}",
                        has question_text "{q['question_text']}",
                        has question_category "{q['question_category']}",
                        has category_order {q['category_order']},
                        has question_order {q['question_order']},
                        has target_attribute "{q['target_attribute']}",
                        has answer_type "{q['answer_type']}";
                """
                tx.query(query).resolve()
                logger.info(f"  Added: {q['question_id']}")
        
        logger.info("Ontology seeded successfully!")
        
    except Exception as e:
        logger.error(f"Failed to seed ontology: {e}")
        sys.exit(1)
    finally:
        typedb_client.close()


if __name__ == "__main__":
    seed_ontology()
