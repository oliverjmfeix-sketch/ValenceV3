"""
V4 Graph-Native Extraction Output Schema

Pydantic models that map directly to TypeDB entities and relations.
Claude returns JSON matching these schemas, which then maps 1:1 to graph storage.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# PROVENANCE - Attached to every extracted element
# ═══════════════════════════════════════════════════════════════════════════════

class Provenance(BaseModel):
    """Source tracking for any extracted data point."""
    section_reference: Optional[str] = None
    verbatim_text: Optional[str] = None
    source_page: Optional[int] = None
    confidence: Literal["high", "medium", "low"] = "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# BUILDER BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class BuilderSource(BaseModel):
    """
    Maps to TypeDB: cni_source, ecf_source, ebitda_fc_source, etc.
    Relation: builder_has_source
    """
    source_type: Literal[
        "starter_amount", "cni", "ecf", "ebitda_fc",
        "equity_proceeds", "asset_sale_proceeds",
        "investment_returns", "declined_proceeds"
    ]
    percentage: Optional[float] = Field(None, description="e.g., 0.5 for 50% CNI")
    dollar_amount: Optional[float] = Field(None, description="e.g., 130000000 starter")
    ebitda_percentage: Optional[float] = Field(None, description="e.g., 1.0 for 100% EBITDA")
    fc_multiplier: Optional[float] = Field(None, description="e.g., 1.4 for 140% (ebitda_fc only)")
    uses_greater_of: bool = False
    is_primary_test: bool = Field(False, description="For 'greatest of' tracking")
    provenance: Optional[Provenance] = None


class BuilderBasket(BaseModel):
    """
    Maps to TypeDB: builder_basket entity
    The cumulative/growth basket that accrues from multiple sources.
    """
    exists: bool = False
    start_date_language: Optional[str] = Field(None, description="Verbatim accrual start date")
    uses_greatest_of_tests: bool = Field(False, description="True if 'greatest of' multiple tests")
    sources: List[BuilderSource] = Field(default_factory=list)
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# RATIO BASKET
# ═══════════════════════════════════════════════════════════════════════════════

class RatioBasket(BaseModel):
    """
    Maps to TypeDB: ratio_basket entity
    Unlimited dividends if leverage ratio below threshold.
    """
    exists: bool = False
    ratio_threshold: Optional[float] = Field(None, description="e.g., 5.75 for 5.75x")
    is_unlimited_if_met: bool = False
    has_no_worse_test: bool = Field(False, description="'No worse' test exists")
    no_worse_threshold: Optional[float] = Field(None, description="99.0 for unlimited, or specific cap")
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# J.CREW BLOCKER
# ═══════════════════════════════════════════════════════════════════════════════

class BlockerException(BaseModel):
    """
    Maps to TypeDB: blocker_exception subtypes
    Relation: blocker_has_exception
    """
    exception_type: Literal[
        "nonexclusive_license", "ordinary_course", "intercompany",
        "fair_value", "license_back", "immaterial_ip", "required_by_law"
    ]
    scope_limitation: Optional[str] = Field(None, description="Any scope limitation text")
    provenance: Optional[Provenance] = None


class JCrewBlocker(BaseModel):
    """
    Maps to TypeDB: jcrew_blocker entity
    Relations: blocker_covers (IP types), blocker_binds (parties), blocker_has_exception
    """
    exists: bool = False
    covers_transfer: bool = Field(False, description="Covers IP transfer to unsubs")
    covers_designation: bool = Field(False, description="Covers designating IP-holder as unsub")
    covered_ip_types: List[str] = Field(
        default_factory=list,
        description="['patents', 'trademarks', 'copyrights', 'trade_secrets', 'licenses', 'domain_names']"
    )
    bound_parties: List[str] = Field(
        default_factory=list,
        description="['borrower', 'guarantors', 'restricted_subs', 'loan_parties', 'holdings']"
    )
    exceptions: List[BlockerException] = Field(default_factory=list)
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# UNRESTRICTED SUBSIDIARY DESIGNATION
# ═══════════════════════════════════════════════════════════════════════════════

class UnsubDesignation(BaseModel):
    """
    Maps to TypeDB: unsub_designation entity
    Relation: provision_has_unsub_designation
    Critical for J.Crew pathway analysis.
    """
    exists: bool = False
    dollar_cap: Optional[float] = Field(None, description="Max value that can be designated")
    ebitda_percentage: Optional[float] = Field(None, description="If 'greater of' formulation")
    uses_greater_of: bool = False
    requires_no_default: bool = False
    requires_board_approval: bool = False
    requires_ratio_test: bool = False
    ratio_threshold: Optional[float] = Field(None, description="If ratio test required")
    permits_equity_dividend: bool = Field(False, description="Can dividend unsub equity to shareholders")
    permits_asset_dividend: bool = Field(False, description="Can dividend unsub assets to shareholders")
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# SWEEP TIERS & DE MINIMIS
# ═══════════════════════════════════════════════════════════════════════════════

class SweepTier(BaseModel):
    """
    Maps to TypeDB: sweep_tier entity
    Relation: provision_has_sweep_tier
    """
    leverage_threshold: float = Field(..., description="e.g., 5.75 for 5.75x")
    sweep_percentage: float = Field(..., description="0.5 = 50% swept, 0 = 100% retained")
    is_highest_tier: bool = Field(False, description="True for strictest tier")
    provenance: Optional[Provenance] = None


class DeMinimisThreshold(BaseModel):
    """
    Maps to TypeDB: de_minimis_threshold entity
    Relation: provision_has_de_minimis
    """
    threshold_type: Literal["individual", "annual"]
    dollar_amount: float = Field(..., description="Fixed dollar floor")
    ebitda_percentage: Optional[float] = Field(None, description="If 'greater of'")
    uses_greater_of: bool = False
    permits_carryforward: bool = Field(False, description="Unused amounts carry forward")
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# REALLOCATION
# ═══════════════════════════════════════════════════════════════════════════════

class BasketReallocation(BaseModel):
    """
    Maps to TypeDB: basket_reallocates_to relation
    Captures cross-basket capacity reallocation paths.
    """
    source_basket: Literal["investment", "rdp", "builder", "general_rp"]
    target_basket: Literal["general_rp", "investment", "rdp"]
    reallocation_cap: Optional[float] = Field(None, description="Cap if different from source")
    is_bidirectional: bool = Field(False, description="Can flow both directions")
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# OTHER BASKETS
# ═══════════════════════════════════════════════════════════════════════════════

class ManagementEquityBasket(BaseModel):
    """Maps to TypeDB: management_equity_basket entity"""
    exists: bool = False
    annual_cap: Optional[float] = Field(None, description="Annual dollar cap")
    ebitda_percentage: Optional[float] = Field(None, description="If 'greater of'")
    uses_greater_of: bool = False
    permits_carryforward: bool = Field(False, description="Unused amounts carry forward")
    covered_persons: List[str] = Field(
        default_factory=list,
        description="['employees', 'directors', 'consultants', 'estates']"
    )
    provenance: Optional[Provenance] = None


class TaxDistributionBasket(BaseModel):
    """Maps to TypeDB: tax_distribution_basket entity"""
    exists: bool = False
    standalone_taxpayer_limit: bool = Field(False, description="Capped at standalone taxpayer amount")
    covered_tax_groups: List[str] = Field(
        default_factory=list,
        description="['consolidated', 'combined', 'unitary']"
    )
    provenance: Optional[Provenance] = None


class GeneralRPBasket(BaseModel):
    """Maps to TypeDB: general_rp_basket entity"""
    exists: bool = False
    dollar_cap: Optional[float] = None
    ebitda_percentage: Optional[float] = None
    uses_greater_of: bool = False
    requires_no_default: bool = False
    provenance: Optional[Provenance] = None


# ═══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL EXTRACTION OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

class RPExtractionV4(BaseModel):
    """
    Complete RP extraction output - maps 1:1 to TypeDB graph.

    Each field maps to an entity type, nested objects map to relations.
    """

    # Baskets
    builder_basket: Optional[BuilderBasket] = None
    ratio_basket: Optional[RatioBasket] = None
    general_rp_basket: Optional[GeneralRPBasket] = None
    management_equity_basket: Optional[ManagementEquityBasket] = None
    tax_distribution_basket: Optional[TaxDistributionBasket] = None

    # Blockers
    jcrew_blocker: Optional[JCrewBlocker] = None

    # Unsub
    unsub_designation: Optional[UnsubDesignation] = None

    # Sweeps
    sweep_tiers: List[SweepTier] = Field(default_factory=list)
    de_minimis_thresholds: List[DeMinimisThreshold] = Field(default_factory=list)

    # Reallocations
    reallocations: List[BasketReallocation] = Field(default_factory=list)

    class Config:
        json_schema_extra = {
            "example": {
                "builder_basket": {
                    "exists": True,
                    "start_date_language": "first day of the fiscal quarter in which the Closing Date occurs",
                    "uses_greatest_of_tests": True,
                    "sources": [
                        {"source_type": "starter_amount", "dollar_amount": 130000000, "ebitda_percentage": 1.0, "uses_greater_of": True},
                        {"source_type": "cni", "percentage": 0.5},
                        {"source_type": "ecf"},
                        {"source_type": "ebitda_fc", "fc_multiplier": 1.4}
                    ],
                    "provenance": {"section_reference": "6.06(f)", "source_page": 145}
                },
                "ratio_basket": {
                    "exists": True,
                    "ratio_threshold": 5.75,
                    "is_unlimited_if_met": True,
                    "has_no_worse_test": True,
                    "no_worse_threshold": 99.0,
                    "provenance": {"section_reference": "6.06(n)", "source_page": 147}
                },
                "jcrew_blocker": {
                    "exists": True,
                    "covers_transfer": True,
                    "covers_designation": False,
                    "covered_ip_types": ["patents", "trademarks", "copyrights", "trade_secrets"],
                    "bound_parties": ["restricted_subs"],
                    "exceptions": [
                        {"exception_type": "nonexclusive_license", "scope_limitation": "ordinary course of business"}
                    ],
                    "provenance": {"section_reference": "6.06(k)"}
                },
                "sweep_tiers": [
                    {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": True},
                    {"leverage_threshold": 5.5, "sweep_percentage": 0.0}
                ],
                "de_minimis_thresholds": [
                    {"threshold_type": "individual", "dollar_amount": 20000000, "ebitda_percentage": 0.15, "uses_greater_of": True},
                    {"threshold_type": "annual", "dollar_amount": 40000000, "ebitda_percentage": 0.30, "uses_greater_of": True}
                ]
            }
        }
