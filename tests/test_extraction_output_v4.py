"""Tests for V4 extraction output schema."""
import pytest
from app.schemas.extraction_output_v4 import (
    RPExtractionV4, BuilderBasket, BuilderSource,
    RatioBasket, JCrewBlocker, BlockerException,
    SweepTier, DeMinimisThreshold, BasketReallocation,
    UnsubDesignation, GeneralRPBasket, ManagementEquityBasket,
    TaxDistributionBasket, Provenance
)


class TestMinimalExtraction:
    """Test parsing minimal valid output."""

    def test_empty_extraction(self):
        """Test empty extraction creates valid object."""
        result = RPExtractionV4.model_validate({})
        assert result.builder_basket is None
        assert result.extraction_version == "4.0"

    def test_minimal_baskets(self):
        """Test parsing minimal basket data."""
        data = {
            "builder_basket": {"exists": True},
            "ratio_basket": {"exists": False}
        }
        result = RPExtractionV4.model_validate(data)
        assert result.builder_basket.exists is True
        assert result.ratio_basket.exists is False


class TestBuilderBasket:
    """Test builder basket with sources."""

    def test_builder_with_sources(self):
        """Test builder basket with multiple sources."""
        data = {
            "builder_basket": {
                "exists": True,
                "basket_name": "Available Amount",
                "uses_greatest_of_tests": True,
                "sources": [
                    {"source_type": "starter_amount", "dollar_amount": 130000000},
                    {"source_type": "cni", "percentage": 0.5},
                    {"source_type": "ebitda_fc", "fc_multiplier": 1.4}
                ]
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert len(result.builder_basket.sources) == 3
        assert result.builder_basket.sources[0].dollar_amount == 130000000
        assert result.builder_basket.sources[1].percentage == 0.5
        assert result.builder_basket.sources[2].fc_multiplier == 1.4

    def test_builder_source_types(self):
        """Test all builder source types are valid."""
        source_types = [
            "starter_amount", "cni", "ecf", "ebitda_fc",
            "equity_proceeds", "asset_sale_proceeds",
            "investment_returns", "declined_proceeds", "debt_conversion"
        ]
        for st in source_types:
            source = BuilderSource(source_type=st)
            assert source.source_type == st

    def test_builder_with_floor_amount(self):
        """Test ECF source with floor amount."""
        data = {
            "builder_basket": {
                "exists": True,
                "sources": [
                    {"source_type": "ecf", "floor_amount": 0}
                ]
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.builder_basket.sources[0].floor_amount == 0


class TestRatioBasket:
    """Test ratio basket parsing."""

    def test_ratio_basket_with_no_worse(self):
        """Test ratio basket with no worse test."""
        data = {
            "ratio_basket": {
                "exists": True,
                "ratio_threshold": 5.75,
                "ratio_type": "first_lien",
                "has_no_worse_test": True,
                "no_worse_threshold": 99.0
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.ratio_basket.ratio_threshold == 5.75
        assert result.ratio_basket.ratio_type == "first_lien"
        assert result.ratio_basket.has_no_worse_test is True
        assert result.ratio_basket.no_worse_threshold == 99.0

    def test_ratio_types(self):
        """Test different ratio types."""
        ratio_types = ["first_lien", "secured", "total", "senior_secured", "net"]
        for rt in ratio_types:
            basket = RatioBasket(exists=True, ratio_type=rt)
            assert basket.ratio_type == rt


class TestJCrewBlocker:
    """Test J.Crew blocker parsing."""

    def test_jcrew_with_exceptions(self):
        """Test J.Crew blocker with exceptions."""
        data = {
            "jcrew_blocker": {
                "exists": True,
                "covers_transfer": True,
                "covers_designation": False,
                "covered_ip_types": ["patents", "trademarks"],
                "exceptions": [
                    {"exception_type": "nonexclusive_license", "scope_limitation": "ordinary course"}
                ]
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.jcrew_blocker.covers_transfer is True
        assert result.jcrew_blocker.covers_designation is False
        assert "patents" in result.jcrew_blocker.covered_ip_types
        assert result.jcrew_blocker.exceptions[0].exception_type == "nonexclusive_license"

    def test_jcrew_exception_types(self):
        """Test all exception types are valid."""
        exception_types = [
            "nonexclusive_license", "ordinary_course", "intercompany",
            "fair_value", "license_back", "immaterial_ip", "required_by_law"
        ]
        for et in exception_types:
            exc = BlockerException(exception_type=et)
            assert exc.exception_type == et

    def test_jcrew_ip_types(self):
        """Test all IP types are valid."""
        ip_types = ["patents", "trademarks", "copyrights", "trade_secrets", "licenses", "domain_names"]
        blocker = JCrewBlocker(exists=True, covered_ip_types=ip_types)
        assert len(blocker.covered_ip_types) == 6

    def test_jcrew_bound_parties(self):
        """Test all bound party types are valid."""
        parties = ["borrower", "guarantors", "restricted_subs", "loan_parties", "holdings"]
        blocker = JCrewBlocker(exists=True, bound_parties=parties)
        assert len(blocker.bound_parties) == 5


class TestUnsubDesignation:
    """Test unrestricted subsidiary designation."""

    def test_unsub_with_conditions(self):
        """Test unsub designation with conditions."""
        data = {
            "unsub_designation": {
                "permitted": True,
                "dollar_cap": 40000000,
                "requires_no_default": True,
                "requires_board_approval": False,
                "permits_equity_dividend": True,
                "permits_asset_dividend": True
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.unsub_designation.permitted is True
        assert result.unsub_designation.dollar_cap == 40000000
        assert result.unsub_designation.permits_equity_dividend is True


class TestSweepTiers:
    """Test sweep tier parsing."""

    def test_multiple_sweep_tiers(self):
        """Test multiple sweep tiers."""
        data = {
            "sweep_tiers": [
                {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": True},
                {"leverage_threshold": 5.5, "sweep_percentage": 0.25},
                {"leverage_threshold": 5.0, "sweep_percentage": 0.0}
            ]
        }
        result = RPExtractionV4.model_validate(data)
        assert len(result.sweep_tiers) == 3
        assert result.sweep_tiers[0].is_highest_tier is True
        assert result.sweep_tiers[2].sweep_percentage == 0.0

    def test_sweep_applies_to(self):
        """Test sweep applies_to field."""
        applies_options = ["asset_sales", "ecf", "debt_issuance", "all"]
        for opt in applies_options:
            tier = SweepTier(leverage_threshold=5.0, sweep_percentage=0.5, applies_to=opt)
            assert tier.applies_to == opt


class TestDeMinimis:
    """Test de minimis threshold parsing."""

    def test_de_minimis_thresholds(self):
        """Test de minimis threshold parsing."""
        data = {
            "de_minimis_thresholds": [
                {"threshold_type": "individual", "dollar_amount": 20000000},
                {"threshold_type": "annual", "dollar_amount": 40000000, "permits_carryforward": True}
            ]
        }
        result = RPExtractionV4.model_validate(data)
        assert len(result.de_minimis_thresholds) == 2
        assert result.de_minimis_thresholds[0].threshold_type == "individual"
        assert result.de_minimis_thresholds[1].permits_carryforward is True


class TestReallocations:
    """Test basket reallocation parsing."""

    def test_reallocations(self):
        """Test basket reallocation parsing."""
        data = {
            "reallocations": [
                {
                    "source_basket": "investment",
                    "target_basket": "general_rp",
                    "reallocation_cap": 130000000,
                    "is_bidirectional": True
                },
                {
                    "source_basket": "rdp",
                    "target_basket": "general_rp"
                }
            ]
        }
        result = RPExtractionV4.model_validate(data)
        assert len(result.reallocations) == 2
        assert result.reallocations[0].is_bidirectional is True
        assert result.reallocations[1].source_basket == "rdp"

    def test_reallocation_basket_types(self):
        """Test all basket types are valid."""
        source_baskets = ["investment", "rdp", "builder", "general_rp", "prepayment", "intercompany"]
        target_baskets = ["general_rp", "investment", "rdp", "builder"]

        for sb in source_baskets:
            for tb in target_baskets:
                realloc = BasketReallocation(source_basket=sb, target_basket=tb)
                assert realloc.source_basket == sb
                assert realloc.target_basket == tb


class TestProvenance:
    """Test provenance tracking."""

    def test_provenance_on_basket(self):
        """Test provenance attached to basket."""
        data = {
            "builder_basket": {
                "exists": True,
                "provenance": {
                    "section_reference": "6.06(f)",
                    "source_page": 145,
                    "confidence": "high"
                }
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.builder_basket.provenance.section_reference == "6.06(f)"
        assert result.builder_basket.provenance.source_page == 145
        assert result.builder_basket.provenance.confidence == "high"

    def test_provenance_on_source(self):
        """Test provenance on builder source."""
        data = {
            "builder_basket": {
                "exists": True,
                "sources": [
                    {
                        "source_type": "cni",
                        "percentage": 0.5,
                        "provenance": {"section_reference": "6.06(f)(i)", "verbatim_text": "50% of CNI"}
                    }
                ]
            }
        }
        result = RPExtractionV4.model_validate(data)
        assert result.builder_basket.sources[0].provenance.verbatim_text == "50% of CNI"


class TestFullExtraction:
    """Test complete extraction output."""

    def test_full_extraction(self):
        """Test complete extraction output."""
        data = {
            "builder_basket": {
                "exists": True,
                "basket_name": "Available Amount",
                "start_date_language": "first day of fiscal quarter",
                "sources": [{"source_type": "cni", "percentage": 0.5}]
            },
            "ratio_basket": {
                "exists": True,
                "ratio_threshold": 5.75,
                "has_no_worse_test": True
            },
            "general_rp_basket": {
                "exists": True,
                "dollar_cap": 130000000
            },
            "management_equity_basket": {
                "exists": True,
                "annual_cap": 25000000,
                "permits_carryforward": True
            },
            "tax_distribution_basket": {
                "exists": True,
                "standalone_taxpayer_limit": True
            },
            "jcrew_blocker": {
                "exists": True,
                "covers_designation": False
            },
            "unsub_designation": {
                "permitted": True,
                "dollar_cap": 40000000
            },
            "sweep_tiers": [
                {"leverage_threshold": 5.75, "sweep_percentage": 0.5}
            ],
            "de_minimis_thresholds": [
                {"threshold_type": "individual", "dollar_amount": 20000000}
            ],
            "reallocations": [
                {"source_basket": "investment", "target_basket": "general_rp"}
            ],
            "extraction_version": "4.0",
            "extraction_confidence": "high"
        }
        result = RPExtractionV4.model_validate(data)

        assert result.builder_basket.exists
        assert result.ratio_basket.has_no_worse_test
        assert result.general_rp_basket.dollar_cap == 130000000
        assert result.management_equity_basket.permits_carryforward
        assert result.tax_distribution_basket.standalone_taxpayer_limit
        assert result.jcrew_blocker.covers_designation is False
        assert result.unsub_designation.dollar_cap == 40000000
        assert len(result.sweep_tiers) == 1
        assert len(result.de_minimis_thresholds) == 1
        assert len(result.reallocations) == 1
        assert result.extraction_confidence == "high"

    def test_example_from_schema(self):
        """Test that the schema example validates."""
        example = RPExtractionV4.model_config.get("json_schema_extra", {}).get("example", {})
        if example:
            result = RPExtractionV4.model_validate(example)
            assert result.builder_basket.exists
            assert len(result.builder_basket.sources) == 4
