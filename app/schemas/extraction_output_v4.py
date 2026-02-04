"""
V4 Extraction Output Schema

Maps Claude's JSON output directly to TypeDB graph entities.
Every model corresponds to a TypeDB entity or relation.
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE - Attached to every extracted element
# ═══════════════════════════════════════════════════════════════════════════════

class Provenance(BaseModel):
    """Source tracking for every extracted fact."""
    section_reference: Optional[str] = Field(None, description="e.g., 'Section 6.06(f)'")
    verbatim_text: Optional[str] = Field(None, description="Exact quote from document (max 500 chars)")
    source_page: Optional[int] = Field(None, description="PDF page number")
    confidence: Literal["high", "medium", "low"] = "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER BASKET + SOURCES
# ═══════════════════════════════════════════════════════════════════════════════

class BuilderSource(BaseModel):
    """
    Maps to TypeDB: cni_source, ecf_source, ebitda_fc_source,
    equity_proceeds_source, asset_sale_proceeds_source, etc.
    """
    source_type: Literal[
        "starter_amount",       # Base amount
        "cni",                  # Consolidated Net Income
        "ecf",                  # Excess Cash Flow (retained)
        "ebitda_fc",            # EBITDA minus Fixed Charges
        "equity_proceeds",      # Equity issuance proceeds
        "asset_sale_proceeds",  # Retained asset sale proceeds
        "investment_returns",   # Returns/dividends from investments
        "declined_proceeds",    # Proceeds borrower chose not to use
        "debt_conversion"       # Debt converted to equity
    ]
    percentage: Optional[float] = Field(None, description="e.g., 0.5 for 50% of CNI")
    dollar_amount: Optional[float] = Field(None, description="Fixed dollar amount")
    ebitda_percentage: Optional[float] = Field(None, description="e.g., 1.0 for 100% EBITDA")
    fc_multiplier: Optional[float] = Field(None, description="For ebitda_fc: e.g., 1.4 for 140%")
    floor_amount: Optional[float] = Field(None, description="Minimum floor (e.g., ECF cannot be negative)")
    uses_greater_of: bool = Field(False, description="Uses 'greater of' formulation")
    is_primary_test: bool = Field(False, description="Is this one of the 'greatest of' tests")
    provenance: Optional[Provenance] = None


class BuilderBasket(BaseModel):
    """
    Maps to TypeDB: builder_basket entity
    Also called: Available Amount, Cumulative Amount, Growth Basket
    """
    exists: bool = False
    basket_name: Optional[str] = Field(None, description="How document refers to it")
    start_date_language: Optional[str] = Field(
        None,
        description="Verbatim: when accumulation begins"
    )
    uses_greatest_of_tests: bool = Field(
        False,
        description="Multiple tests with 'greatest of' logic"
    )
    sources: List[BuilderSource] = Field(
        default_factory=list,
        description="All sources that feed the basket"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# RATIO BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class RatioBasket(BaseModel):
    """
    Maps to TypeDB: ratio_basket entity
    Unlimited dividends if leverage ratio meets threshold.
    """
    exists: bool = False
    ratio_threshold: Optional[float] = Field(
        None,
        description="Leverage threshold for unlimited (e.g., 5.75)"
    )
    ratio_type: Literal[
        "first_lien", "secured", "total", "senior_secured", "net"
    ] = "first_lien"
    is_unlimited_if_met: bool = Field(
        False,
        description="True if unlimited dividends when ratio met"
    )
    has_no_worse_test: bool = Field(
        False,
        description="CRITICAL: Can dividend if ratio not worse pro forma"
    )
    no_worse_threshold: Optional[float] = Field(
        None,
        description="Max ratio for 'no worse' test (99.0 = unlimited)"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# GENERAL RP BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class GeneralRPBasket(BaseModel):
    """
    Maps to TypeDB: general_rp_basket entity
    Fixed dollar basket for general restricted payments.
    """
    exists: bool = False
    dollar_cap: Optional[float] = Field(None, description="Fixed dollar amount")
    ebitda_percentage: Optional[float] = Field(None, description="If 'greater of'")
    uses_greater_of: bool = False
    requires_no_default: bool = False
    requires_ratio_test: bool = False
    ratio_threshold: Optional[float] = None
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# MANAGEMENT EQUITY BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class ManagementEquityBasket(BaseModel):
    """
    Maps to TypeDB: management_equity_basket entity
    For repurchasing management/employee equity.
    """
    exists: bool = False
    annual_cap: Optional[float] = Field(None, description="Per-year limit")
    ebitda_percentage: Optional[float] = None
    uses_greater_of: bool = False
    permits_carryforward: bool = Field(
        False,
        description="Can unused amounts carry to next year"
    )
    post_ipo_increase: Optional[float] = Field(
        None,
        description="Increased cap after IPO"
    )
    covered_persons: List[str] = Field(
        default_factory=list,
        description="['employees', 'directors', 'consultants', 'estates']"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# TAX DISTRIBUTION BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class TaxDistributionBasket(BaseModel):
    """
    Maps to TypeDB: tax_distribution_basket entity
    For pass-through entity tax payments.
    """
    exists: bool = False
    is_unlimited: bool = Field(False, description="No cap on tax distributions")
    standalone_taxpayer_limit: bool = Field(
        False,
        description="Limited to standalone taxpayer amount"
    )
    covered_tax_groups: List[str] = Field(
        default_factory=list,
        description="['consolidated', 'combined', 'unitary']"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# J.CREW BLOCKER
# ═══════════════════════════════════════════════════════════════════════════════

class BlockerException(BaseModel):
    """
    Maps to TypeDB: blocker_exception subtypes
    (nonexclusive_license_exception, ordinary_course_exception, etc.)
    """
    exception_type: Literal[
        "nonexclusive_license",  # Non-exclusive licenses permitted
        "ordinary_course",       # Ordinary course of business
        "intercompany",          # Transfers within restricted group
        "fair_value",            # If fair market value received
        "license_back",          # Can license IP back to credit parties
        "immaterial_ip",         # Immaterial IP excluded
        "required_by_law"        # Legally required transfers
    ]
    scope_limitation: Optional[str] = Field(
        None,
        description="Any limitation on the exception"
    )
    provenance: Optional[Provenance] = None


class JCrewBlocker(BaseModel):
    """
    Maps to TypeDB: jcrew_blocker entity
    Restricts transfer of IP to unrestricted subsidiaries.
    """
    exists: bool = False
    covers_transfer: bool = Field(
        False,
        description="Blocks TRANSFER of IP ownership"
    )
    covers_designation: bool = Field(
        False,
        description="CRITICAL: Blocks DESIGNATION of IP-holding sub as Unrestricted"
    )
    covered_ip_types: List[Literal[
        "patents", "trademarks", "copyrights",
        "trade_secrets", "licenses", "domain_names"
    ]] = Field(default_factory=list)
    bound_parties: List[Literal[
        "borrower", "guarantors", "restricted_subs",
        "loan_parties", "holdings"
    ]] = Field(default_factory=list, description="Who is bound by blocker")
    exceptions: List[BlockerException] = Field(default_factory=list)
    material_ip_definition: Optional[str] = Field(
        None,
        description="How 'Material IP' is defined"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# UNRESTRICTED SUBSIDIARY DESIGNATION
# ═══════════════════════════════════════════════════════════════════════════════

class UnsubDesignation(BaseModel):
    """
    Maps to TypeDB: unsub_designation entity
    Rules for designating subsidiaries as Unrestricted.
    CRITICAL for J.Crew pathway analysis.
    """
    permitted: bool = Field(False, description="Can subs be designated Unrestricted")
    dollar_cap: Optional[float] = Field(
        None,
        description="Max aggregate value that can become Unrestricted"
    )
    ebitda_percentage: Optional[float] = None
    uses_greater_of: bool = False

    # Conditions
    requires_no_default: bool = Field(False, description="No default can exist")
    requires_board_approval: bool = Field(False, description="Board must approve")
    requires_ratio_test: bool = Field(False, description="Pro forma ratio compliance")
    ratio_threshold: Optional[float] = None

    # Dividend permissions (J.Crew path)
    permits_equity_dividend: bool = Field(
        False,
        description="Can dividend EQUITY in unsubs to shareholders"
    )
    permits_asset_dividend: bool = Field(
        False,
        description="Can dividend ASSETS from unsubs to shareholders"
    )

    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# SWEEP TIERS
# ═══════════════════════════════════════════════════════════════════════════════

class SweepTier(BaseModel):
    """
    Maps to TypeDB: sweep_tier entity
    Leverage-based mandatory prepayment tiers.
    """
    leverage_threshold: float = Field(..., description="Ratio threshold (e.g., 5.75)")
    sweep_percentage: float = Field(
        ...,
        description="Percent swept to prepay debt (0.5 = 50%, 0 = 100% retained)"
    )
    is_highest_tier: bool = Field(
        False,
        description="True if this is the strictest tier"
    )
    applies_to: Literal["asset_sales", "ecf", "debt_issuance", "all"] = "asset_sales"
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# DE MINIMIS THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

class DeMinimisThreshold(BaseModel):
    """
    Maps to TypeDB: de_minimis_threshold entity
    Minimum thresholds below which no prepayment required.
    """
    threshold_type: Literal["individual", "annual"]
    dollar_amount: float = Field(..., description="Fixed dollar floor")
    ebitda_percentage: Optional[float] = Field(
        None,
        description="EBITDA percentage if 'greater of'"
    )
    uses_greater_of: bool = False
    permits_carryforward: bool = Field(
        False,
        description="Unused amounts carry forward"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# BASKET REALLOCATION
# ═══════════════════════════════════════════════════════════════════════════════

class BasketReallocation(BaseModel):
    """
    Maps to TypeDB: basket_reallocates_to relation
    Capacity that can be moved between baskets.
    """
    source_basket: Literal[
        "investment", "rdp", "builder", "general_rp",
        "prepayment", "intercompany"
    ]
    target_basket: Literal[
        "general_rp", "investment", "rdp", "builder"
    ]
    reallocation_cap: Optional[float] = Field(
        None,
        description="Max amount that can be reallocated"
    )
    ebitda_percentage: Optional[float] = None
    uses_greater_of: bool = False
    is_bidirectional: bool = Field(
        False,
        description="Can capacity flow both ways"
    )
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL EXTRACTION OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

class RPExtractionV4(BaseModel):
    """
    Complete RP covenant extraction output.
    Maps directly to TypeDB V4 graph schema.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # BASKETS
    # ─────────────────────────────────────────────────────────────────────────
    builder_basket: Optional[BuilderBasket] = None
    ratio_basket: Optional[RatioBasket] = None
    general_rp_basket: Optional[GeneralRPBasket] = None
    management_equity_basket: Optional[ManagementEquityBasket] = None
    tax_distribution_basket: Optional[TaxDistributionBasket] = None

    # ─────────────────────────────────────────────────────────────────────────
    # BLOCKERS
    # ─────────────────────────────────────────────────────────────────────────
    jcrew_blocker: Optional[JCrewBlocker] = None

    # ─────────────────────────────────────────────────────────────────────────
    # UNRESTRICTED SUBSIDIARY
    # ─────────────────────────────────────────────────────────────────────────
    unsub_designation: Optional[UnsubDesignation] = None

    # ─────────────────────────────────────────────────────────────────────────
    # SWEEP & PREPAYMENT
    # ─────────────────────────────────────────────────────────────────────────
    sweep_tiers: List[SweepTier] = Field(default_factory=list)
    de_minimis_thresholds: List[DeMinimisThreshold] = Field(default_factory=list)

    # ─────────────────────────────────────────────────────────────────────────
    # REALLOCATION
    # ─────────────────────────────────────────────────────────────────────────
    reallocations: List[BasketReallocation] = Field(default_factory=list)

    # ─────────────────────────────────────────────────────────────────────────
    # METADATA
    # ─────────────────────────────────────────────────────────────────────────
    extraction_version: str = "4.0"
    extraction_confidence: Literal["high", "medium", "low"] = "medium"

    class Config:
        json_schema_extra = {
            "example": {
                "builder_basket": {
                    "exists": True,
                    "basket_name": "Available Amount",
                    "start_date_language": "the first day of the fiscal quarter in which the Closing Date occurs",
                    "uses_greatest_of_tests": True,
                    "sources": [
                        {"source_type": "starter_amount", "dollar_amount": 130000000, "ebitda_percentage": 1.0, "uses_greater_of": True},
                        {"source_type": "cni", "percentage": 0.5, "is_primary_test": True},
                        {"source_type": "ecf", "floor_amount": 0},
                        {"source_type": "ebitda_fc", "fc_multiplier": 1.4, "is_primary_test": True}
                    ],
                    "provenance": {"section_reference": "6.06(f)", "source_page": 145}
                },
                "ratio_basket": {
                    "exists": True,
                    "ratio_threshold": 5.75,
                    "ratio_type": "first_lien",
                    "is_unlimited_if_met": True,
                    "has_no_worse_test": True,
                    "no_worse_threshold": 99.0,
                    "provenance": {"section_reference": "6.06(n)", "source_page": 147}
                },
                "general_rp_basket": {
                    "exists": True,
                    "dollar_cap": 130000000,
                    "ebitda_percentage": 1.0,
                    "uses_greater_of": True,
                    "provenance": {"section_reference": "6.06(j)"}
                },
                "jcrew_blocker": {
                    "exists": True,
                    "covers_transfer": True,
                    "covers_designation": False,
                    "covered_ip_types": ["patents", "trademarks", "copyrights", "trade_secrets"],
                    "bound_parties": ["restricted_subs"],
                    "exceptions": [
                        {"exception_type": "nonexclusive_license", "scope_limitation": "in the ordinary course of business"}
                    ],
                    "provenance": {"section_reference": "6.06(k)"}
                },
                "unsub_designation": {
                    "permitted": True,
                    "dollar_cap": 40000000,
                    "requires_no_default": True,
                    "requires_board_approval": False,
                    "permits_equity_dividend": True,
                    "permits_asset_dividend": True,
                    "provenance": {"section_reference": "5.15, 6.06(p)"}
                },
                "sweep_tiers": [
                    {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": True, "applies_to": "asset_sales"},
                    {"leverage_threshold": 5.5, "sweep_percentage": 0.0, "is_highest_tier": False}
                ],
                "de_minimis_thresholds": [
                    {"threshold_type": "individual", "dollar_amount": 20000000, "ebitda_percentage": 0.15, "uses_greater_of": True},
                    {"threshold_type": "annual", "dollar_amount": 40000000, "ebitda_percentage": 0.30, "uses_greater_of": True, "permits_carryforward": True}
                ],
                "reallocations": [
                    {"source_basket": "investment", "target_basket": "general_rp", "reallocation_cap": 130000000, "is_bidirectional": True},
                    {"source_basket": "rdp", "target_basket": "general_rp", "reallocation_cap": 130000000, "is_bidirectional": True}
                ],
                "extraction_version": "4.0",
                "extraction_confidence": "high"
            }
        }
