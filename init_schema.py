"""
Initialize TypeDB Schema.

Run this once after creating your TypeDB database:
    python -m app.scripts.init_schema

This creates all entity types, attributes, relations, and inference rules.
"""
import os
import sys
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.services.typedb_client import typedb_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SCHEMA = """
################################################################################
# VALENCE v2 - TypeDB Schema
# Architecture: Typed Primitives + Provenance + Inference Rules
# NO JSON BLOBS - Every value is a typed attribute
################################################################################

define

# ==============================================================================
# CORE ATTRIBUTES
# ==============================================================================

# Deal attributes
attribute deal_id, value string;
attribute deal_name, value string;
attribute borrower, value string;
attribute signing_date, value datetime;
attribute upload_date, value datetime;
attribute pdf_filename, value string;

# Provenance attributes (for every extracted primitive)
attribute attribute_name, value string;
attribute source_text, value string;
attribute source_page, value integer;
attribute source_section, value string;
attribute extraction_confidence, value string;
attribute extracted_at, value datetime;

# Ontology question attributes
attribute question_id, value string;
attribute question_text, value string;
attribute question_category, value string;
attribute category_order, value integer;
attribute question_order, value integer;
attribute target_attribute, value string;
attribute answer_type, value string;

# ==============================================================================
# MFN PROVISION - TYPED PRIMITIVES
# ==============================================================================

# Existence
attribute mfn_exists, value boolean;
attribute mfn_section_reference, value string;

# Sunset
attribute sunset_exists, value boolean;
attribute sunset_period_months, value integer;
attribute sunset_reference_date, value string;
attribute sunset_tied_to_maturity, value boolean;

# Threshold
attribute threshold_bps, value integer;
attribute threshold_applies_to_margin_only, value boolean;
attribute threshold_applies_to_all_in_yield, value boolean;

# Yield Components
attribute oid_included_in_yield, value boolean;
attribute floor_included_in_yield, value boolean;
attribute upfront_fees_included_in_yield, value boolean;
attribute amendment_fees_included_in_yield, value boolean;
attribute commitment_fees_included_in_yield, value boolean;

# Debt Coverage
attribute covers_term_loan_a, value boolean;
attribute covers_term_loan_b, value boolean;
attribute covers_incremental_facilities, value boolean;
attribute covers_refinancing_facilities, value boolean;
attribute covers_ratio_debt, value boolean;

# Exclusions
attribute excludes_acquisition_debt, value boolean;
attribute excludes_bridge_loans, value boolean;
attribute excludes_bilateral_amendments, value boolean;

# Pattern Flags
attribute yield_exclusion_pattern, value boolean;
attribute weak_mfn_pattern, value boolean;

# ==============================================================================
# RP PROVISION - TYPED PRIMITIVES
# ==============================================================================

# Existence
attribute rp_exists, value boolean;
attribute dividend_covenant_section, value string;
attribute rdp_covenant_section, value string;

# Dividend Scope
attribute dividend_applies_to_holdings, value boolean;
attribute dividend_applies_to_borrower, value boolean;
attribute dividend_applies_to_restricted_subs, value boolean;

# Builder Basket
attribute builder_basket_exists, value boolean;
attribute builder_starter_amount_usd, value double;
attribute builder_starter_ebitda_pct, value double;
attribute builder_includes_net_income, value boolean;
attribute builder_includes_retained_ecf, value boolean;
attribute builder_includes_equity_proceeds, value boolean;
attribute builder_includes_sub_returns, value boolean;

# Unrestricted Subsidiary (J.Crew Risk)
attribute unrestricted_sub_designation_permitted, value boolean;
attribute unrestricted_sub_requires_no_default, value boolean;
attribute unrestricted_sub_requires_no_payment_default, value boolean;
attribute unrestricted_sub_has_ebitda_cap, value boolean;
attribute unrestricted_sub_ebitda_cap_pct, value double;

# IP Transfer (J.Crew Risk)
attribute ip_transfers_to_subs_permitted, value boolean;
attribute ip_transfers_require_fair_value, value boolean;

# J.Crew Blocker
attribute jcrew_blocker_present, value boolean;
attribute jcrew_blocker_covers_ip, value boolean;
attribute jcrew_blocker_covers_material_assets, value boolean;
attribute jcrew_blocker_binds_loan_parties, value boolean;
attribute jcrew_blocker_binds_restricted_subs, value boolean;

# IP Definition Quality
attribute ip_definition_includes_trademarks, value boolean;
attribute ip_definition_includes_patents, value boolean;
attribute ip_definition_includes_copyrights, value boolean;
attribute ip_definition_includes_trade_secrets, value boolean;
attribute ip_definition_includes_know_how, value boolean;
attribute ip_definition_includes_licenses, value boolean;

# Ratio Dividend Basket
attribute ratio_dividend_basket_exists, value boolean;
attribute ratio_dividend_leverage_threshold, value double;
attribute ratio_dividend_is_unlimited, value boolean;

# Management Equity
attribute management_equity_basket_exists, value boolean;
attribute management_equity_annual_cap_usd, value double;
attribute management_equity_covers_death, value boolean;
attribute management_equity_covers_disability, value boolean;
attribute management_equity_covers_termination, value boolean;

# General Basket
attribute general_dividend_basket_usd, value double;
attribute general_dividend_basket_ebitda_pct, value double;

# Pattern Flags
attribute jcrew_pattern, value boolean;
attribute serta_pattern, value boolean;

# ==============================================================================
# ENTITIES
# ==============================================================================

entity deal,
    owns deal_id @key,
    owns deal_name,
    owns borrower,
    owns signing_date,
    owns upload_date,
    owns pdf_filename,
    plays deal_has_provision:deal,
    plays deal_has_provenance:deal;

entity mfn_provision,
    owns mfn_exists,
    owns mfn_section_reference,
    owns sunset_exists,
    owns sunset_period_months,
    owns sunset_reference_date,
    owns sunset_tied_to_maturity,
    owns threshold_bps,
    owns threshold_applies_to_margin_only,
    owns threshold_applies_to_all_in_yield,
    owns oid_included_in_yield,
    owns floor_included_in_yield,
    owns upfront_fees_included_in_yield,
    owns amendment_fees_included_in_yield,
    owns commitment_fees_included_in_yield,
    owns covers_term_loan_a,
    owns covers_term_loan_b,
    owns covers_incremental_facilities,
    owns covers_refinancing_facilities,
    owns covers_ratio_debt,
    owns excludes_acquisition_debt,
    owns excludes_bridge_loans,
    owns excludes_bilateral_amendments,
    owns yield_exclusion_pattern,
    owns weak_mfn_pattern,
    plays deal_has_provision:provision,
    plays has_provenance:provision;

entity rp_provision,
    owns rp_exists,
    owns dividend_covenant_section,
    owns rdp_covenant_section,
    owns dividend_applies_to_holdings,
    owns dividend_applies_to_borrower,
    owns dividend_applies_to_restricted_subs,
    owns builder_basket_exists,
    owns builder_starter_amount_usd,
    owns builder_starter_ebitda_pct,
    owns builder_includes_net_income,
    owns builder_includes_retained_ecf,
    owns builder_includes_equity_proceeds,
    owns builder_includes_sub_returns,
    owns unrestricted_sub_designation_permitted,
    owns unrestricted_sub_requires_no_default,
    owns unrestricted_sub_requires_no_payment_default,
    owns unrestricted_sub_has_ebitda_cap,
    owns unrestricted_sub_ebitda_cap_pct,
    owns ip_transfers_to_subs_permitted,
    owns ip_transfers_require_fair_value,
    owns jcrew_blocker_present,
    owns jcrew_blocker_covers_ip,
    owns jcrew_blocker_covers_material_assets,
    owns jcrew_blocker_binds_loan_parties,
    owns jcrew_blocker_binds_restricted_subs,
    owns ip_definition_includes_trademarks,
    owns ip_definition_includes_patents,
    owns ip_definition_includes_copyrights,
    owns ip_definition_includes_trade_secrets,
    owns ip_definition_includes_know_how,
    owns ip_definition_includes_licenses,
    owns ratio_dividend_basket_exists,
    owns ratio_dividend_leverage_threshold,
    owns ratio_dividend_is_unlimited,
    owns management_equity_basket_exists,
    owns management_equity_annual_cap_usd,
    owns management_equity_covers_death,
    owns management_equity_covers_disability,
    owns management_equity_covers_termination,
    owns general_dividend_basket_usd,
    owns general_dividend_basket_ebitda_pct,
    owns jcrew_pattern,
    owns serta_pattern,
    plays deal_has_provision:provision,
    plays has_provenance:provision;

entity attribute_provenance,
    owns attribute_name,
    owns source_text,
    owns source_page,
    owns source_section,
    owns extraction_confidence,
    owns extracted_at,
    plays has_provenance:provenance;

entity ontology_question,
    owns question_id @key,
    owns question_text,
    owns question_category,
    owns category_order,
    owns question_order,
    owns target_attribute,
    owns answer_type;

# ==============================================================================
# RELATIONS
# ==============================================================================

relation deal_has_provision,
    relates deal,
    relates provision;

relation has_provenance,
    relates provision,
    relates provenance;

relation deal_has_provenance,
    relates deal,
    relates provenance;
"""


def init_schema():
    """Initialize the TypeDB schema."""
    logger.info("Connecting to TypeDB...")
    
    if not typedb_client.connect():
        logger.error(f"Failed to connect: {typedb_client.connection_error}")
        sys.exit(1)
    
    logger.info("Applying schema...")
    
    try:
        with typedb_client.schema_transaction() as tx:
            tx.query(SCHEMA).resolve()
        
        logger.info("Schema applied successfully!")
        
    except Exception as e:
        logger.error(f"Failed to apply schema: {e}")
        sys.exit(1)
    finally:
        typedb_client.close()


if __name__ == "__main__":
    init_schema()
