"""
Graph Storage Service - V4 Graph-Native Schema

Handles inserting extracted covenant data as entities and relations
instead of flat attributes.
"""
import json
import logging
import uuid
from typing import Dict, Any, List, Optional
from typedb.driver import TransactionType

from app.services.typedb_client import typedb_client
from app.config import settings
from app.schemas.extraction_output_v4 import RPExtractionV4

logger = logging.getLogger(__name__)


class GraphStorage:
    """Insert extracted covenant data as graph entities and relations."""

    def __init__(self, deal_id: str):
        self.deal_id = deal_id
        self.driver = typedb_client.driver
        self.db_name = settings.typedb_database

    @classmethod
    def load_extraction_metadata(cls) -> List[Dict[str, Any]]:
        """
        Load extraction instructions from TypeDB (SSoT).

        Returns:
            List of extraction metadata dicts sorted by priority
        """
        driver = typedb_client.driver
        db_name = settings.typedb_database

        if not driver:
            logger.warning("No TypeDB driver available for loading extraction metadata")
            return []

        query = """
            match
                $em isa extraction_metadata,
                    has metadata_id $id,
                    has target_entity_type $type,
                    has extraction_prompt $prompt;
            select $id, $type, $prompt, $em;
        """

        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            tx.close()

            metadata = []
            for row in rows:
                item = {
                    "metadata_id": cls._get_attr(row, "id"),
                    "target_entity_type": cls._get_attr(row, "type"),
                    "extraction_prompt": cls._get_attr(row, "prompt"),
                }

                # Fetch optional attributes with separate queries
                meta_id = item["metadata_id"]
                item.update(cls._get_optional_metadata_attrs(meta_id))
                metadata.append(item)

            # Sort by priority (lower = higher priority)
            metadata.sort(key=lambda x: x.get("extraction_priority", 99))
            return metadata

        except Exception as e:
            tx.close()
            logger.error(f"Error loading extraction metadata: {e}")
            return []

    @classmethod
    def _get_optional_metadata_attrs(cls, metadata_id: str) -> Dict[str, Any]:
        """Fetch optional attributes for extraction metadata."""
        driver = typedb_client.driver
        db_name = settings.typedb_database

        attrs = {}
        optional_attrs = [
            ("target_attribute", "attr"),
            ("extraction_section_hint", "section"),
            ("extraction_priority", "priority"),
            ("requires_context", "req_ctx"),
            ("context_entities", "ctx"),
        ]

        for attr_name, var_name in optional_attrs:
            try:
                tx = driver.transaction(db_name, TransactionType.READ)
                query = f'''
                    match $em isa extraction_metadata,
                        has metadata_id "{metadata_id}",
                        has {attr_name} ${var_name};
                    select ${var_name};
                '''
                result = tx.query(query).resolve()
                rows = list(result.as_concept_rows())
                tx.close()

                if rows:
                    attrs[attr_name] = cls._get_attr(rows[0], var_name)
            except Exception:
                pass

        return attrs

    @staticmethod
    def _get_attr(row, key: str, default=None):
        """Safely get attribute value from TypeDB row."""
        try:
            concept = row.get(key)
            if concept is None:
                return default
            return concept.as_attribute().get_value()
        except Exception:
            return default

    @classmethod
    def build_claude_prompt(cls, metadata: List[Dict], document_text: str) -> str:
        """
        Build Claude extraction prompt from TypeDB metadata.

        Args:
            metadata: List of extraction metadata from load_extraction_metadata()
            document_text: The covenant document text to analyze

        Returns:
            Formatted prompt string for Claude
        """
        prompt = '''You are extracting Restricted Payment covenant data from a credit agreement.

## OUTPUT FORMAT

Return a JSON object. Include ONLY fields where you found data. Example structure:
```json
{
  "builder_basket": {
    "exists": true,
    "basket_name": "Available Amount",
    "start_date_language": "the first day of the fiscal quarter in which the Closing Date occurs",
    "uses_greatest_of_tests": true,
    "sources": [
      {
        "source_type": "starter_amount",
        "dollar_amount": 130000000,
        "ebitda_percentage": 1.0,
        "uses_greater_of": true,
        "provenance": {"section_reference": "6.06(f)", "source_page": 145}
      },
      {
        "source_type": "cni",
        "percentage": 0.5,
        "is_primary_test": true
      },
      {
        "source_type": "ecf",
        "floor_amount": 0
      },
      {
        "source_type": "ebitda_fc",
        "fc_multiplier": 1.4,
        "is_primary_test": true
      }
    ],
    "provenance": {"section_reference": "6.06(f)", "source_page": 145}
  },
  "ratio_basket": {
    "exists": true,
    "ratio_threshold": 5.75,
    "ratio_type": "first_lien",
    "is_unlimited_if_met": true,
    "has_no_worse_test": true,
    "no_worse_threshold": 99.0,
    "provenance": {"section_reference": "6.06(n)", "source_page": 147}
  },
  "general_rp_basket": {
    "exists": true,
    "dollar_cap": 130000000,
    "ebitda_percentage": 1.0,
    "uses_greater_of": true,
    "provenance": {"section_reference": "6.06(j)"}
  },
  "management_equity_basket": {
    "exists": true,
    "annual_cap": 25000000,
    "permits_carryforward": true,
    "post_ipo_increase": 50000000
  },
  "tax_distribution_basket": {
    "exists": true,
    "is_unlimited": false,
    "standalone_taxpayer_limit": true
  },
  "holdco_overhead_basket": {
    "exists": true,
    "annual_cap_usd": 5000000,
    "covers_management_fees": true,
    "covers_admin_expenses": true,
    "covers_franchise_taxes": true,
    "management_fee_recipient_scope": "permitted_holders_only",
    "requires_arms_length": true,
    "provenance": {"section_reference": "6.06(b)(ii)"}
  },
  "equity_award_basket": {
    "exists": true,
    "annual_cap_usd": 10000000,
    "covers_cashless_exercise": true,
    "covers_tax_withholding": true,
    "carryforward_permitted": false,
    "provenance": {"section_reference": "6.06(c)(ii)"}
  },
  "jcrew_blocker": {
    "exists": true,
    "covers_transfer": true,
    "covers_designation": false,
    "covered_ip_types": ["patents", "trademarks", "copyrights", "trade_secrets"],
    "bound_parties": ["restricted_subs"],
    "exceptions": [
      {
        "exception_type": "nonexclusive_license",
        "scope_limitation": "in the ordinary course of business"
      }
    ],
    "provenance": {"section_reference": "6.06(k)"}
  },
  "unsub_designation": {
    "permitted": true,
    "dollar_cap": 40000000,
    "requires_no_default": true,
    "requires_board_approval": false,
    "permits_equity_dividend": true,
    "permits_asset_dividend": true,
    "provenance": {"section_reference": "5.15, 6.06(p)"}
  },
  "sweep_tiers": [
    {
      "leverage_threshold": 5.75,
      "sweep_percentage": 0.5,
      "is_highest_tier": true,
      "applies_to": "asset_sales",
      "provenance": {"section_reference": "2.10(f)"}
    },
    {
      "leverage_threshold": 5.5,
      "sweep_percentage": 0.0,
      "is_highest_tier": false
    }
  ],
  "de_minimis_thresholds": [
    {
      "threshold_type": "individual",
      "dollar_amount": 20000000,
      "ebitda_percentage": 0.15,
      "uses_greater_of": true
    },
    {
      "threshold_type": "annual",
      "dollar_amount": 40000000,
      "ebitda_percentage": 0.30,
      "uses_greater_of": true,
      "permits_carryforward": true
    }
  ],
  "reallocations": [
    {
      "source_basket": "investment",
      "target_basket": "general_rp",
      "reallocation_cap": 130000000,
      "is_bidirectional": true,
      "provenance": {"section_reference": "6.06(j)"}
    },
    {
      "source_basket": "rdp",
      "target_basket": "general_rp",
      "reallocation_cap": 130000000,
      "is_bidirectional": true
    }
  ],
  "refinancing_rdp_basket": {
    "exists": true,
    "requires_same_or_lower_priority": true,
    "requires_same_or_later_maturity": true,
    "requires_no_increase_in_principal": true,
    "permits_refinancing_with_equity": false,
    "subject_to_intercreditor": true,
    "provenance": {"section_reference": "6.09(b)"}
  },
  "general_rdp_basket": {
    "exists": true,
    "basket_amount_usd": 130000000,
    "basket_grower_pct": 1.0,
    "provenance": {"section_reference": "6.09(g)"}
  },
  "ratio_rdp_basket": {
    "exists": true,
    "ratio_threshold": 5.75,
    "ratio_type": "first_lien",
    "is_unlimited_if_met": true,
    "pro_forma_basis": true,
    "provenance": {"section_reference": "6.09(j)"}
  },
  "builder_rdp_basket": {
    "exists": true,
    "shares_with_rp_builder": true,
    "subject_to_intercreditor": false,
    "provenance": {"section_reference": "6.09(c)"}
  },
  "equity_funded_rdp_basket": {
    "exists": true,
    "requires_qualified_stock_only": true,
    "requires_cash_common_equity": false,
    "not_otherwise_applied": true,
    "provenance": {"section_reference": "6.09(d)"}
  },
  "investment_pathways": [
    {
      "pathway_source_type": "loan_party",
      "pathway_target_type": "non_guarantor_rs",
      "cap_dollar_usd": 200000000,
      "cap_pct_total_assets": 0.15,
      "cap_uses_greater_of": true,
      "is_uncapped": false,
      "provenance": {"section_reference": "6.03(e)"}
    },
    {
      "pathway_source_type": "non_guarantor_rs",
      "pathway_target_type": "unrestricted_sub",
      "is_uncapped": true,
      "can_stack_with_other_baskets": true,
      "provenance": {"section_reference": "6.03(j)"}
    }
  ]
}
```

## FIELD DEFINITIONS

### source_type values for builder_basket.sources:
- "starter_amount": Base/starting amount (usually "greater of $X and Y% EBITDA")
- "cni": Consolidated Net Income (usually 50%)
- "ecf": Excess Cash Flow not applied to mandatory prepayment
- "ebitda_fc": EBITDA minus Fixed Charges (note the fc_multiplier, e.g., 1.4 = 140%)
- "equity_proceeds": Proceeds from equity issuances (usually 100%)
- "asset_sale_proceeds": Retained asset sale proceeds
- "investment_returns": Returns/dividends received from investments
- "declined_proceeds": Proceeds borrower elected not to accept
- "debt_conversion": Debt converted to equity

### ratio_type values:
- "first_lien" | "secured" | "total" | "senior_secured" | "net"

### exception_type values for jcrew_blocker.exceptions:
- "nonexclusive_license": Non-exclusive licenses (often "in ordinary course")
- "ordinary_course": Ordinary course of business transfers
- "intercompany": Transfers within restricted group
- "fair_value": Transfers for fair market value
- "license_back": Licenses back to Credit Parties (LOOPHOLE)
- "immaterial_ip": Immaterial IP excluded
- "required_by_law": Legally mandated transfers

### covered_ip_types values:
- "patents" | "trademarks" | "copyrights" | "trade_secrets" | "licenses" | "domain_names"

### bound_parties values:
- "borrower" | "guarantors" | "restricted_subs" | "loan_parties" | "holdings"

### threshold_type values:
- "individual": Per-transaction threshold
- "annual": Annual aggregate threshold

### applies_to values for sweep_tiers:
- "asset_sales" | "ecf" | "debt_issuance" | "all"

### source_basket / target_basket values:
- "investment": Investment covenant basket (Section 6.03)
- "rdp": Restricted Debt Payment basket (Section 6.09)
- "general_rp": General RP basket (Section 6.06)
- "builder": Builder/Available Amount basket
- "prepayment": Debt prepayment basket
- "intercompany": Intercompany loan basket

## EXTRACTION INSTRUCTIONS

'''

        # Append extraction_metadata prompts sorted by priority
        for m in sorted(metadata, key=lambda x: x.get('extraction_priority', 99)):
            prompt += f"### {m['target_entity_type']}"
            if m.get('target_attribute'):
                prompt += f".{m['target_attribute']}"
            prompt += f"\n{m['extraction_prompt']}\n"
            if m.get('extraction_section_hint'):
                prompt += f"(Look in: {m['extraction_section_hint']})\n"
            prompt += "\n"

        prompt += f"""
## DOCUMENT TEXT

{document_text}

## RESPONSE

Return ONLY the JSON object. No markdown, no explanation."""

        return prompt

    @classmethod
    def parse_claude_response(cls, response_text: str) -> RPExtractionV4:
        """
        Parse Claude's JSON response into typed Pydantic model.

        Args:
            response_text: Raw text response from Claude

        Returns:
            Validated RPExtractionV4 model

        Raises:
            ValueError: If response cannot be parsed or validated
        """
        # Extract JSON from response (handle markdown code blocks)
        json_text = response_text.strip()

        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            # Try to extract from generic code block
            parts = response_text.split("```")
            if len(parts) >= 2:
                json_text = parts[1]
                # Remove language identifier if present
                if json_text.startswith("JSON") or json_text.startswith("json"):
                    json_text = json_text[4:]

        json_text = json_text.strip()

        # Find JSON object boundaries
        start_idx = json_text.find("{")
        end_idx = json_text.rfind("}") + 1

        if start_idx == -1 or end_idx == 0:
            logger.error(f"No JSON object found in response: {response_text[:500]}")
            raise ValueError("No JSON object found in Claude response")

        json_text = json_text[start_idx:end_idx]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response text: {json_text[:500]}")
            raise ValueError(f"Failed to parse Claude response as JSON: {e}")

        try:
            return RPExtractionV4.model_validate(data)
        except Exception as e:
            logger.error(f"Pydantic validation error: {e}")
            logger.error(f"Data: {json.dumps(data, indent=2)[:1000]}")
            raise ValueError(f"Failed to validate extraction output: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # V4 TYPED STORAGE - Works with RPExtractionV4 Pydantic model
    # ═══════════════════════════════════════════════════════════════════════════

    def store_rp_extraction_v4(self, extraction: RPExtractionV4) -> Dict[str, Any]:
        """
        Store V4 extraction as TypeDB graph entities and relations.

        Args:
            extraction: Validated RPExtractionV4 Pydantic model

        Returns:
            Dict with provision_id and counts of created entities
        """
        results = {
            "provision_id": None,
            "baskets_created": 0,
            "rdp_baskets_created": 0,
            "sources_created": 0,
            "blockers_created": 0,
            "exceptions_created": 0,
            "sweep_tiers_created": 0,
            "de_minimis_created": 0,
            "reallocations_created": 0,
            "pathways_created": 0,
            "errors": []
        }

        try:
            # Create provision and link to deal
            provision_id = self._gen_id("rp_prov")
            self._create_rp_provision_v4(provision_id)
            results["provision_id"] = provision_id

            # Store builder basket
            if extraction.builder_basket and extraction.builder_basket.exists:
                try:
                    basket_id = self._store_builder_basket_v4(provision_id, extraction.builder_basket)
                    results["baskets_created"] += 1
                    results["sources_created"] += len(extraction.builder_basket.sources)
                except Exception as e:
                    results["errors"].append(f"Builder basket: {str(e)[:100]}")

            # Store ratio basket
            if extraction.ratio_basket and extraction.ratio_basket.exists:
                try:
                    self._store_ratio_basket_v4(provision_id, extraction.ratio_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Ratio basket: {str(e)[:100]}")

            # Store general RP basket
            if extraction.general_rp_basket and extraction.general_rp_basket.exists:
                try:
                    self._store_general_rp_basket_v4(provision_id, extraction.general_rp_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"General RP basket: {str(e)[:100]}")

            # Store management equity basket
            if extraction.management_equity_basket and extraction.management_equity_basket.exists:
                try:
                    self._store_management_basket_v4(provision_id, extraction.management_equity_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Management basket: {str(e)[:100]}")

            # Store tax distribution basket
            if extraction.tax_distribution_basket and extraction.tax_distribution_basket.exists:
                try:
                    self._store_tax_basket_v4(provision_id, extraction.tax_distribution_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Tax basket: {str(e)[:100]}")

            # Store holdco overhead basket
            if extraction.holdco_overhead_basket and extraction.holdco_overhead_basket.exists:
                try:
                    self._store_holdco_overhead_basket_v4(provision_id, extraction.holdco_overhead_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Holdco overhead basket: {str(e)[:100]}")

            # Store equity award basket
            if extraction.equity_award_basket and extraction.equity_award_basket.exists:
                try:
                    self._store_equity_award_basket_v4(provision_id, extraction.equity_award_basket)
                    results["baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Equity award basket: {str(e)[:100]}")

            # Store RDP baskets (separate hierarchy — provision_has_rdp_basket)
            if extraction.refinancing_rdp_basket and extraction.refinancing_rdp_basket.exists:
                try:
                    self._store_refinancing_rdp_basket_v4(provision_id, extraction.refinancing_rdp_basket)
                    results["rdp_baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Refinancing RDP basket: {str(e)[:100]}")

            if extraction.general_rdp_basket and extraction.general_rdp_basket.exists:
                try:
                    self._store_general_rdp_basket_v4(provision_id, extraction.general_rdp_basket)
                    results["rdp_baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"General RDP basket: {str(e)[:100]}")

            if extraction.ratio_rdp_basket and extraction.ratio_rdp_basket.exists:
                try:
                    self._store_ratio_rdp_basket_v4(provision_id, extraction.ratio_rdp_basket)
                    results["rdp_baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Ratio RDP basket: {str(e)[:100]}")

            if extraction.builder_rdp_basket and extraction.builder_rdp_basket.exists:
                try:
                    self._store_builder_rdp_basket_v4(provision_id, extraction.builder_rdp_basket)
                    results["rdp_baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Builder RDP basket: {str(e)[:100]}")

            if extraction.equity_funded_rdp_basket and extraction.equity_funded_rdp_basket.exists:
                try:
                    self._store_equity_funded_rdp_basket_v4(provision_id, extraction.equity_funded_rdp_basket)
                    results["rdp_baskets_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Equity-funded RDP basket: {str(e)[:100]}")

            # Store J.Crew blocker
            if extraction.jcrew_blocker and extraction.jcrew_blocker.exists:
                try:
                    self._store_jcrew_blocker_v4(provision_id, extraction.jcrew_blocker)
                    results["blockers_created"] += 1
                    results["exceptions_created"] += len(extraction.jcrew_blocker.exceptions)
                except Exception as e:
                    results["errors"].append(f"J.Crew blocker: {str(e)[:100]}")

            # Store unsub designation
            if extraction.unsub_designation and extraction.unsub_designation.permitted:
                try:
                    self._store_unsub_designation_v4(provision_id, extraction.unsub_designation)
                except Exception as e:
                    results["errors"].append(f"Unsub designation: {str(e)[:100]}")

            # Store sweep tiers
            for tier in extraction.sweep_tiers:
                try:
                    self._store_sweep_tier_v4(provision_id, tier)
                    results["sweep_tiers_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Sweep tier: {str(e)[:100]}")

            # Store de minimis thresholds
            for threshold in extraction.de_minimis_thresholds:
                try:
                    self._store_de_minimis_v4(provision_id, threshold)
                    results["de_minimis_created"] += 1
                except Exception as e:
                    results["errors"].append(f"De minimis: {str(e)[:100]}")

            # Store sweep exemptions (link to pre-seeded reference entities)
            for exemption_id in extraction.sweep_exemptions:
                try:
                    self._link_exemption_to_provision(provision_id, exemption_id)
                except Exception as e:
                    results["errors"].append(f"Sweep exemption '{exemption_id}': {str(e)[:100]}")

            # Store reallocations (note: need basket IDs, may fail if baskets don't exist)
            for realloc in extraction.reallocations:
                try:
                    self._store_reallocation_v4(provision_id, realloc)
                    results["reallocations_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Reallocation: {str(e)[:100]}")

            # Store investment pathways
            for i, pathway in enumerate(extraction.investment_pathways):
                try:
                    self._store_investment_pathway_v4(provision_id, pathway, i)
                    results["pathways_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Investment pathway: {str(e)[:100]}")

            logger.info(
                f"V4 extraction stored for {self.deal_id}: "
                f"{results['baskets_created']} baskets, {results['rdp_baskets_created']} rdp_baskets, "
                f"{results['sources_created']} sources, {results['pathways_created']} pathways, "
                f"{results['blockers_created']} blockers, {results['sweep_tiers_created']} tiers"
            )

        except Exception as e:
            results["errors"].append(f"Top-level error: {str(e)[:200]}")
            logger.exception(f"Error storing V4 extraction for deal {self.deal_id}")

        return results

    def _create_rp_provision_v4(self, provision_id: str):
        """Create RP provision and link to deal."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        query = f'''
            match
                $deal isa deal, has deal_id "{self.deal_id}";
            insert
                $prov isa rp_provision,
                    has provision_id "{provision_id}",
                    has extracted_at {now_iso};
                (deal: $deal, provision: $prov) isa deal_has_provision;
        '''
        self._execute_query(query)
        logger.debug(f"Created rp_provision {provision_id}")

    def store_scalar_answer(
        self,
        provision_id: str,
        question_id: str,
        value: Any,
        *,
        source_text: Optional[str] = None,
        source_page: Optional[int] = None,
        source_section: Optional[str] = None,
        confidence: Optional[str] = None,
    ) -> str:
        """
        Store a scalar answer via the provision_has_answer relation.

        Creates a provision_has_answer relation linking a provision to an
        ontology_question, with the answer value in the appropriate typed field.

        Args:
            provision_id: The provision to link the answer to
            question_id: The ontology_question question_id (e.g., "rp_m1")
            value: The answer value (bool, int, float, str)
            source_text: Optional verbatim source text
            source_page: Optional page number
            source_section: Optional section reference
            confidence: Optional confidence level (high | medium | low)

        Returns:
            The generated answer_id
        """
        answer_id = self._gen_id("ans")

        attrs = [f'has answer_id "{answer_id}"']

        if isinstance(value, bool):
            attrs.append(f'has answer_boolean {str(value).lower()}')
        elif isinstance(value, int):
            attrs.append(f'has answer_integer {value}')
        elif isinstance(value, float):
            attrs.append(f'has answer_double {value}')
        elif isinstance(value, str):
            attrs.append(f'has answer_string "{self._escape(value)}"')
        else:
            attrs.append(f'has answer_string "{self._escape(str(value))}"')

        if source_text:
            attrs.append(f'has source_text "{self._escape(source_text[:2000])}"')
        if source_page is not None:
            attrs.append(f'has source_page {source_page}')
        if source_section:
            attrs.append(f'has source_section "{self._escape(source_section)}"')
        if confidence:
            attrs.append(f'has confidence "{confidence}"')

        attrs_str = ",\n                ".join(attrs)

        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $q isa ontology_question, has question_id "{question_id}";
            insert
                (provision: $prov, question: $q) isa provision_has_answer,
                {attrs_str};
        '''
        self._execute_query(query)
        logger.debug(f"Stored answer {answer_id}: {question_id} = {value}")
        return answer_id

    def _store_builder_basket_v4(self, provision_id: str, basket) -> str:
        """Store builder basket with all sources."""
        from app.schemas.extraction_output_v4 import BuilderBasket, BuilderSource

        basket_id = f"builder_{provision_id}"
        attrs = [f'has basket_id "{basket_id}"']

        if basket.basket_name:
            attrs.append(f'has basket_name "{self._escape(basket.basket_name)}"')
        if basket.start_date_language:
            attrs.append(f'has start_date_language "{self._escape(basket.start_date_language)}"')
        if basket.uses_greatest_of_tests:
            attrs.append('has uses_greatest_of_tests true')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Add provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')
            if basket.provenance.verbatim_text:
                attrs.append(f'has verbatim_text "{self._escape(basket.provenance.verbatim_text[:500])}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa builder_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

        # Store each source
        for i, source in enumerate(basket.sources):
            self._store_builder_source_v4(basket_id, source, i)

        return basket_id

    def _store_builder_source_v4(self, basket_id: str, source, index: int):
        """Store a builder source entity."""
        from app.schemas.extraction_output_v4 import BuilderSource

        source_id = f"{basket_id}_src_{index}"

        # Map source_type to TypeDB entity
        type_map = {
            "starter_amount": "starter_amount_source",
            "cni": "cni_source",
            "ecf": "ecf_source",
            "ebitda_fc": "ebitda_fc_source",
            "equity_proceeds": "equity_proceeds_source",
            "asset_sale_proceeds": "asset_sale_proceeds_source",
            "investment_returns": "investment_returns_source",
            "declined_proceeds": "declined_proceeds_source",
            "debt_conversion": "builder_source"  # Fallback for new type
        }
        entity_type = type_map.get(source.source_type, "builder_source")

        attrs = [
            f'has source_id "{source_id}"',
            f'has source_name "{source.source_type}"'
        ]

        if source.percentage is not None:
            attrs.append(f'has percentage {source.percentage}')
        if source.dollar_amount is not None:
            attrs.append(f'has floor_amount {source.dollar_amount}')  # Use floor_amount for dollar amount
        if source.ebitda_percentage is not None:
            attrs.append(f'has ebitda_percentage {source.ebitda_percentage}')
        if source.fc_multiplier is not None:
            attrs.append(f'has fc_multiplier {source.fc_multiplier}')
        if source.floor_amount is not None:
            attrs.append(f'has floor_amount {source.floor_amount}')
        if source.uses_greater_of:
            attrs.append('has uses_greater_of true')
        if source.not_otherwise_applied is not None:
            attrs.append(f'has not_otherwise_applied {str(source.not_otherwise_applied).lower()}')
        if source.excludes_cure_contributions is not None:
            attrs.append(f'has excludes_cure_contributions {str(source.excludes_cure_contributions).lower()}')
        if source.excludes_disqualified_stock is not None:
            attrs.append(f'has excludes_disqualified_stock {str(source.excludes_disqualified_stock).lower()}')

        # Provenance
        if source.provenance:
            if source.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(source.provenance.section_reference)}"')
            if source.provenance.source_page is not None:
                attrs.append(f'has source_page {source.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)

        # Relation attribute for is_primary_test
        rel_attrs = ""
        if source.is_primary_test:
            rel_attrs = ", has is_primary_test true"

        query = f'''
            match
                $basket isa builder_basket, has basket_id "{basket_id}";
            insert
                $src isa {entity_type},
                {attrs_str};
                (builder: $basket, source: $src) isa builder_has_source{rel_attrs};
        '''
        self._execute_query(query)

    def _store_ratio_basket_v4(self, provision_id: str, basket):
        """Store ratio basket entity."""
        basket_id = f"ratio_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.ratio_threshold is not None:
            attrs.append(f'has ratio_threshold {basket.ratio_threshold}')
        if basket.is_unlimited_if_met:
            attrs.append('has is_unlimited_if_met true')
        if basket.has_no_worse_test:
            attrs.append('has has_no_worse_test true')
        if basket.no_worse_threshold is not None:
            attrs.append(f'has no_worse_threshold {basket.no_worse_threshold}')
        if basket.test_date_type:
            attrs.append(f'has test_date_type "{self._escape(basket.test_date_type)}"')
        if basket.lct_treatment_available is not None:
            attrs.append(f'has lct_treatment_available {str(basket.lct_treatment_available).lower()}')
        if basket.pro_forma_basis is not None:
            attrs.append(f'has pro_forma_basis {str(basket.pro_forma_basis).lower()}')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa ratio_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    def _store_general_rp_basket_v4(self, provision_id: str, basket):
        """Store general RP basket entity."""
        basket_id = f"general_rp_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.dollar_cap is not None:
            attrs.append(f'has dollar_cap {basket.dollar_cap}')
        if basket.ebitda_percentage is not None:
            attrs.append(f'has ebitda_percentage {basket.ebitda_percentage}')
        if basket.uses_greater_of:
            attrs.append('has uses_greater_of true')
        if basket.requires_no_default:
            attrs.append('has requires_no_default true')
        if basket.requires_ratio_test:
            attrs.append('has requires_ratio_test true')
        if basket.ratio_threshold is not None:
            attrs.append(f'has ratio_threshold {basket.ratio_threshold}')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa general_rp_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    def _store_management_basket_v4(self, provision_id: str, basket):
        """Store management equity basket."""
        basket_id = f"mgmt_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.annual_cap is not None:
            attrs.append(f'has annual_cap {basket.annual_cap}')
        if basket.ebitda_percentage is not None:
            attrs.append(f'has ebitda_percentage {basket.ebitda_percentage}')
        if basket.uses_greater_of:
            attrs.append('has uses_greater_of true')
        if basket.permits_carryforward:
            attrs.append('has permits_carryforward true')
        if basket.eligible_person_scope:
            attrs.append(f'has eligible_person_scope "{self._escape(basket.eligible_person_scope)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa management_equity_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    def _store_tax_basket_v4(self, provision_id: str, basket):
        """Store tax distribution basket."""
        basket_id = f"tax_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.standalone_taxpayer_limit:
            attrs.append('has standalone_taxpayer_limit true')
        if basket.hypothetical_tax_rate is not None:
            attrs.append(f'has hypothetical_tax_rate {basket.hypothetical_tax_rate}')
        if basket.tax_sharing_permitted is not None:
            attrs.append(f'has tax_sharing_permitted {str(basket.tax_sharing_permitted).lower()}')
        if basket.estimated_taxes_permitted is not None:
            attrs.append(f'has estimated_taxes_permitted {str(basket.estimated_taxes_permitted).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa tax_distribution_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    def _store_holdco_overhead_basket_v4(self, provision_id: str, basket):
        """Store holdco overhead basket."""
        basket_id = f"holdco_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.annual_cap_usd is not None:
            attrs.append(f'has annual_cap_usd {basket.annual_cap_usd}')
        if basket.covers_management_fees is not None:
            attrs.append(f'has covers_management_fees {str(basket.covers_management_fees).lower()}')
        if basket.covers_admin_expenses is not None:
            attrs.append(f'has covers_admin_expenses {str(basket.covers_admin_expenses).lower()}')
        if basket.covers_franchise_taxes is not None:
            attrs.append(f'has covers_franchise_taxes {str(basket.covers_franchise_taxes).lower()}')
        if basket.management_fee_recipient_scope:
            attrs.append(f'has management_fee_recipient_scope "{self._escape(basket.management_fee_recipient_scope)}"')
        if basket.requires_arms_length is not None:
            attrs.append(f'has requires_arms_length {str(basket.requires_arms_length).lower()}')
        if basket.requires_board_approval is not None:
            attrs.append(f'has requires_board_approval {str(basket.requires_board_approval).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa holdco_overhead_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    def _store_equity_award_basket_v4(self, provision_id: str, basket):
        """Store equity award basket."""
        basket_id = f"eqaward_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.annual_cap_usd is not None:
            attrs.append(f'has annual_cap_usd {basket.annual_cap_usd}')
        if basket.covers_cashless_exercise is not None:
            attrs.append(f'has covers_cashless_exercise {str(basket.covers_cashless_exercise).lower()}')
        if basket.covers_tax_withholding is not None:
            attrs.append(f'has covers_tax_withholding {str(basket.covers_tax_withholding).lower()}')
        if basket.carryforward_permitted is not None:
            attrs.append(f'has carryforward_permitted {str(basket.carryforward_permitted).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa equity_award_basket,
                {attrs_str};
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # RDP BASKETS - Separate hierarchy using provision_has_rdp_basket relation
    # ═══════════════════════════════════════════════════════════════════════════

    def _store_refinancing_rdp_basket_v4(self, provision_id: str, basket):
        """Store refinancing RDP basket."""
        basket_id = f"rdp_refi_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.requires_same_or_lower_priority is not None:
            attrs.append(f'has requires_same_or_lower_priority {str(basket.requires_same_or_lower_priority).lower()}')
        if basket.requires_same_or_later_maturity is not None:
            attrs.append(f'has requires_same_or_later_maturity {str(basket.requires_same_or_later_maturity).lower()}')
        if basket.requires_no_increase_in_principal is not None:
            attrs.append(f'has requires_no_increase_in_principal {str(basket.requires_no_increase_in_principal).lower()}')
        if basket.permits_refinancing_with_equity is not None:
            attrs.append(f'has permits_refinancing_with_equity {str(basket.permits_refinancing_with_equity).lower()}')
        if basket.requires_qualified_stock_only is not None:
            attrs.append(f'has requires_qualified_stock_only {str(basket.requires_qualified_stock_only).lower()}')
        if basket.not_otherwise_applied is not None:
            attrs.append(f'has not_otherwise_applied {str(basket.not_otherwise_applied).lower()}')
        if basket.subject_to_intercreditor is not None:
            attrs.append(f'has subject_to_intercreditor {str(basket.subject_to_intercreditor).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa refinancing_rdp_basket,
                {attrs_str};
                (provision: $prov, rdp_basket: $basket) isa provision_has_rdp_basket;
        '''
        self._execute_query(query)

    def _store_general_rdp_basket_v4(self, provision_id: str, basket):
        """Store general RDP basket."""
        basket_id = f"rdp_general_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.basket_amount_usd is not None:
            attrs.append(f'has basket_amount_usd {basket.basket_amount_usd}')
        if basket.basket_grower_pct is not None:
            attrs.append(f'has basket_grower_pct {basket.basket_grower_pct}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa general_rdp_basket,
                {attrs_str};
                (provision: $prov, rdp_basket: $basket) isa provision_has_rdp_basket;
        '''
        self._execute_query(query)

    def _store_ratio_rdp_basket_v4(self, provision_id: str, basket):
        """Store ratio RDP basket."""
        basket_id = f"rdp_ratio_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.ratio_threshold is not None:
            attrs.append(f'has ratio_threshold {basket.ratio_threshold}')
        if basket.ratio_type:
            attrs.append(f'has ratio_type "{self._escape(basket.ratio_type)}"')
        if basket.is_unlimited_if_met is not None:
            attrs.append(f'has is_unlimited_if_met {str(basket.is_unlimited_if_met).lower()}')
        if basket.test_date_type:
            attrs.append(f'has test_date_type "{self._escape(basket.test_date_type)}"')
        if basket.pro_forma_basis is not None:
            attrs.append(f'has pro_forma_basis {str(basket.pro_forma_basis).lower()}')
        if basket.uses_closing_ratio_alternative is not None:
            attrs.append(f'has uses_closing_ratio_alternative {str(basket.uses_closing_ratio_alternative).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa ratio_rdp_basket,
                {attrs_str};
                (provision: $prov, rdp_basket: $basket) isa provision_has_rdp_basket;
        '''
        self._execute_query(query)

    def _store_builder_rdp_basket_v4(self, provision_id: str, basket):
        """Store builder RDP basket."""
        basket_id = f"rdp_builder_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.shares_with_rp_builder is not None:
            attrs.append(f'has shares_with_rp_builder {str(basket.shares_with_rp_builder).lower()}')
        if basket.subject_to_intercreditor is not None:
            attrs.append(f'has subject_to_intercreditor {str(basket.subject_to_intercreditor).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa builder_rdp_basket,
                {attrs_str};
                (provision: $prov, rdp_basket: $basket) isa provision_has_rdp_basket;
        '''
        self._execute_query(query)

    def _store_equity_funded_rdp_basket_v4(self, provision_id: str, basket):
        """Store equity-funded RDP basket."""
        basket_id = f"rdp_eqfund_{provision_id}"

        attrs = [f'has basket_id "{basket_id}"']

        if basket.requires_qualified_stock_only is not None:
            attrs.append(f'has requires_qualified_stock_only {str(basket.requires_qualified_stock_only).lower()}')
        if basket.requires_cash_common_equity is not None:
            attrs.append(f'has requires_cash_common_equity {str(basket.requires_cash_common_equity).lower()}')
        if basket.not_otherwise_applied is not None:
            attrs.append(f'has not_otherwise_applied {str(basket.not_otherwise_applied).lower()}')
        if basket.default_condition:
            attrs.append(f'has default_condition "{self._escape(basket.default_condition)}"')

        # Provenance
        if basket.provenance:
            if basket.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(basket.provenance.section_reference)}"')
            if basket.provenance.source_page is not None:
                attrs.append(f'has source_page {basket.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $basket isa equity_funded_rdp_basket,
                {attrs_str};
                (provision: $prov, rdp_basket: $basket) isa provision_has_rdp_basket;
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # INVESTMENT PATHWAYS - J.Crew chain analysis
    # ═══════════════════════════════════════════════════════════════════════════

    def _store_investment_pathway_v4(self, provision_id: str, pathway, index: int):
        """Store a single investment pathway entity."""
        pathway_id = f"pathway_{provision_id}_{index}"

        attrs = [
            f'has pathway_id "{pathway_id}"',
            f'has pathway_source_type "{self._escape(pathway.pathway_source_type)}"',
            f'has pathway_target_type "{self._escape(pathway.pathway_target_type)}"',
        ]

        if pathway.cap_dollar_usd is not None:
            attrs.append(f'has cap_dollar_usd {pathway.cap_dollar_usd}')
        if pathway.cap_pct_total_assets is not None:
            attrs.append(f'has cap_pct_total_assets {pathway.cap_pct_total_assets}')
        if pathway.cap_uses_greater_of is not None:
            attrs.append(f'has cap_uses_greater_of {str(pathway.cap_uses_greater_of).lower()}')
        if pathway.is_uncapped is not None:
            attrs.append(f'has is_uncapped {str(pathway.is_uncapped).lower()}')
        if pathway.can_stack_with_other_baskets is not None:
            attrs.append(f'has can_stack_with_other_baskets {str(pathway.can_stack_with_other_baskets).lower()}')

        # Provenance
        if pathway.provenance:
            if pathway.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(pathway.provenance.section_reference)}"')
            if pathway.provenance.source_page is not None:
                attrs.append(f'has source_page {pathway.provenance.source_page}')
            if pathway.provenance.verbatim_text:
                attrs.append(f'has source_text "{self._escape(pathway.provenance.verbatim_text[:500])}"')
            if pathway.provenance.confidence:
                attrs.append(f'has confidence "{pathway.provenance.confidence}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $pathway isa investment_pathway,
                {attrs_str};
                (provision: $prov, pathway: $pathway) isa provision_has_pathway;
        '''
        self._execute_query(query)

    def _store_jcrew_blocker_v4(self, provision_id: str, blocker):
        """Store J.Crew blocker with exceptions."""
        blocker_id = f"jcrew_{provision_id}"

        attrs = [f'has blocker_id "{blocker_id}"']

        if blocker.covers_transfer:
            attrs.append('has covers_transfer true')
        if blocker.covers_designation:
            attrs.append('has covers_designation true')
        if blocker.covers_exclusive_licensing is not None:
            attrs.append(f'has covers_exclusive_licensing {str(blocker.covers_exclusive_licensing).lower()}')
        if blocker.covers_nonexclusive_licensing is not None:
            attrs.append(f'has covers_nonexclusive_licensing {str(blocker.covers_nonexclusive_licensing).lower()}')
        if blocker.covers_pledge is not None:
            attrs.append(f'has covers_pledge {str(blocker.covers_pledge).lower()}')
        if blocker.covers_abandonment is not None:
            attrs.append(f'has covers_abandonment {str(blocker.covers_abandonment).lower()}')

        # Provenance
        if blocker.provenance:
            if blocker.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(blocker.provenance.section_reference)}"')
            if blocker.provenance.source_page is not None:
                attrs.append(f'has source_page {blocker.provenance.source_page}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $blocker isa jcrew_blocker,
                {attrs_str};
                (provision: $prov, blocker: $blocker) isa provision_has_blocker;
        '''
        self._execute_query(query)

        # Store exceptions
        for i, exc in enumerate(blocker.exceptions):
            self._store_blocker_exception_v4(blocker_id, exc, i)

        # Link to seeded IP type reference entities
        # concept_id values in seeds match Pydantic Literal values ("patents", "trademarks", etc.)
        for ip_type_id in blocker.covered_ip_types:
            try:
                self._link_blocker_to_ip_type(blocker_id, ip_type_id)
            except Exception as e:
                logger.warning(f"Could not link IP type '{ip_type_id}' to blocker: {e}")

        # bound_parties: captured in Pydantic model for Q&A synthesis.
        # Graph representation uses covered_entity_type via concept_applicability (Channel 2).

    def _store_blocker_exception_v4(self, blocker_id: str, exc, index: int):
        """Store blocker exception."""
        exc_id = f"{blocker_id}_exc_{index}"

        type_map = {
            "nonexclusive_license": "nonexclusive_license_exception",
            "ordinary_course": "ordinary_course_exception",
            "intercompany": "intercompany_exception",
            "fair_value": "fair_value_exception",
            "license_back": "license_back_exception",
            "immaterial_ip": "immaterial_ip_exception",
            "required_by_law": "blocker_exception"  # Fallback
        }
        entity_type = type_map.get(exc.exception_type, "blocker_exception")

        attrs = [
            f'has exception_id "{exc_id}"',
            f'has exception_name "{exc.exception_type}"'
        ]

        if exc.scope_limitation:
            attrs.append(f'has scope_limitation "{self._escape(exc.scope_limitation)}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $blocker isa jcrew_blocker, has blocker_id "{blocker_id}";
            insert
                $exc isa {entity_type},
                {attrs_str};
                (blocker: $blocker, exception: $exc) isa blocker_has_exception;
        '''
        self._execute_query(query)

    def _store_unsub_designation_v4(self, provision_id: str, unsub):
        """Store unrestricted subsidiary designation rules."""
        designation_id = f"unsub_{provision_id}"

        attrs = [f'has designation_id "{designation_id}"']

        if unsub.dollar_cap is not None:
            attrs.append(f'has dollar_cap {unsub.dollar_cap}')
        if unsub.ebitda_percentage is not None:
            attrs.append(f'has ebitda_percentage {unsub.ebitda_percentage}')
        if unsub.uses_greater_of:
            attrs.append('has uses_greater_of true')
        if unsub.requires_no_default:
            attrs.append('has requires_no_default true')
        if unsub.requires_board_approval:
            attrs.append('has requires_board_approval true')
        if unsub.requires_ratio_test:
            attrs.append('has requires_ratio_test true')
        if unsub.ratio_threshold is not None:
            attrs.append(f'has ratio_threshold {unsub.ratio_threshold}')
        if unsub.permits_equity_dividend:
            attrs.append('has permits_equity_dividend true')
        if unsub.permits_asset_dividend:
            attrs.append('has permits_asset_dividend true')

        # Provenance
        if unsub.provenance:
            if unsub.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(unsub.provenance.section_reference)}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $unsub isa unsub_designation,
                {attrs_str};
                (provision: $prov, designation: $unsub) isa provision_has_unsub_designation;
        '''
        self._execute_query(query)

    def _store_sweep_tier_v4(self, provision_id: str, tier):
        """Store sweep tier entity."""
        tier_id = f"sweep_{provision_id}_{tier.leverage_threshold}"

        attrs = [
            f'has tier_id "{tier_id}"',
            f'has leverage_threshold {tier.leverage_threshold}',
            f'has sweep_percentage {tier.sweep_percentage}'
        ]

        if tier.is_highest_tier:
            attrs.append('has is_highest_tier true')

        # Provenance
        if tier.provenance:
            if tier.provenance.section_reference:
                attrs.append(f'has section_reference "{self._escape(tier.provenance.section_reference)}"')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $tier isa sweep_tier,
                {attrs_str};
                (provision: $prov, tier: $tier) isa provision_has_sweep_tier;
        '''
        self._execute_query(query)

    def _store_de_minimis_v4(self, provision_id: str, threshold):
        """Store de minimis threshold entity."""
        threshold_id = f"deminimis_{provision_id}_{threshold.threshold_type}"

        attrs = [
            f'has threshold_id "{threshold_id}"',
            f'has threshold_type "{threshold.threshold_type}"',
            f'has dollar_amount {threshold.dollar_amount}'
        ]

        if threshold.ebitda_percentage is not None:
            attrs.append(f'has ebitda_percentage {threshold.ebitda_percentage}')
        if threshold.uses_greater_of:
            attrs.append('has uses_greater_of true')
        if threshold.permits_carryforward:
            attrs.append('has permits_carryforward true')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $dm isa de_minimis_threshold,
                {attrs_str};
                (provision: $prov, threshold: $dm) isa provision_has_de_minimis;
        '''
        self._execute_query(query)

    def _store_reallocation_v4(self, provision_id: str, realloc):
        """Store basket reallocation relation."""
        # Map basket names to expected IDs
        basket_map = {
            "investment": f"investment_{provision_id}",
            "rdp": f"rdp_{provision_id}",
            "builder": f"builder_{provision_id}",
            "general_rp": f"general_rp_{provision_id}",
            "prepayment": f"prepayment_{provision_id}",
            "intercompany": f"intercompany_{provision_id}"
        }

        source_id = basket_map.get(realloc.source_basket)
        target_id = basket_map.get(realloc.target_basket)

        if not source_id or not target_id:
            logger.warning(f"Unknown basket in reallocation: {realloc.source_basket} -> {realloc.target_basket}")
            return

        rel_attrs = []
        if realloc.reallocation_cap is not None:
            rel_attrs.append(f'has reallocation_cap {realloc.reallocation_cap}')
        if realloc.is_bidirectional:
            rel_attrs.append('has is_bidirectional true')
        if realloc.reduces_source_basket is not None:
            rel_attrs.append(f'has reduces_source_basket {str(realloc.reduces_source_basket).lower()}')
        if realloc.reduction_is_dollar_for_dollar is not None:
            rel_attrs.append(f'has reduction_is_dollar_for_dollar {str(realloc.reduction_is_dollar_for_dollar).lower()}')
        if realloc.reduction_while_outstanding_only is not None:
            rel_attrs.append(f'has reduction_while_outstanding_only {str(realloc.reduction_while_outstanding_only).lower()}')
        if realloc.provenance and realloc.provenance.section_reference:
            rel_attrs.append(f'has reallocation_section "{self._escape(realloc.provenance.section_reference)}"')

        rel_attrs_str = ""
        if rel_attrs:
            rel_attrs_str = ",\n                " + ",\n                ".join(rel_attrs)

        # Note: This may fail if baskets don't exist (e.g., investment basket wasn't extracted)
        # We handle this gracefully with try/except in the caller
        query = f'''
            match
                $src isa basket, has basket_id "{source_id}";
                $tgt isa basket, has basket_id "{target_id}";
            insert
                (source_basket: $src, target_basket: $tgt) isa basket_reallocates_to{rel_attrs_str};
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # MFN ENTITY STORAGE (Channel 3)
    # ═══════════════════════════════════════════════════════════════════════════

    def store_mfn_exclusion(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_exclusion entity + provision_has_exclusion relation."""
        excl_id = f"mfn_excl_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has exclusion_id "{excl_id}"']

        field_map = {
            "exclusion_type": ("string", "exclusion_type"),
            "exclusion_has_cap": ("bool", "exclusion_has_cap"),
            "exclusion_cap_usd": ("double", "exclusion_cap_usd"),
            "exclusion_cap_pct_ebitda": ("double", "exclusion_cap_pct_ebitda"),
            "exclusion_conditions": ("string", "exclusion_conditions"),
            "can_stack_with_other_exclusions": ("bool", "can_stack_with_other_exclusions"),
            "excludes_from_mfn": ("bool", "excludes_from_mfn"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $excl isa mfn_exclusion,
                {attrs_str};
                (provision: $prov, exclusion: $excl) isa provision_has_exclusion;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_exclusion: {excl_id}")
        return excl_id

    def store_mfn_yield_definition(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_yield_definition entity + provision_has_yield_def relation."""
        yield_id = f"mfn_yield_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has yield_def_id "{yield_id}"']

        field_map = {
            "defined_term": ("string", "defined_term"),
            "includes_margin": ("bool", "includes_margin"),
            "includes_floor_benefit": ("bool", "includes_floor_benefit"),
            "includes_oid": ("bool", "includes_oid"),
            "includes_upfront_fees": ("bool", "includes_upfront_fees"),
            "includes_commitment_fees": ("bool", "includes_commitment_fees"),
            "includes_other_fees": ("bool", "includes_other_fees"),
            "oid_amortization_method": ("string", "oid_amortization_method"),
            "comparison_baseline": ("string", "comparison_baseline"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')
        if entity_data.get("oid_amortization_years") is not None:
            attrs.append(f'has oid_amortization_years {int(entity_data["oid_amortization_years"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $ydef isa mfn_yield_definition,
                {attrs_str};
                (provision: $prov, yield_def: $ydef) isa provision_has_yield_def;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_yield_definition: {yield_id}")
        return yield_id

    def store_mfn_sunset_provision(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_sunset_provision entity + provision_has_sunset relation."""
        sunset_id = f"mfn_sunset_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has sunset_id "{sunset_id}"']

        field_map = {
            "sunset_exists": ("bool", "sunset_exists"),
            "sunset_trigger_event": ("string", "sunset_trigger_event"),
            "sunset_resets_on_refi": ("bool", "sunset_resets_on_refi"),
            "sunset_tied_to_maturity": ("bool", "sunset_tied_to_maturity"),
            "sunset_timing_loophole": ("bool", "sunset_timing_loophole"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')
        if entity_data.get("sunset_period_months") is not None:
            attrs.append(f'has sunset_period_months {int(entity_data["sunset_period_months"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $sun isa mfn_sunset_provision,
                {attrs_str};
                (provision: $prov, sunset: $sun) isa provision_has_sunset;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_sunset_provision: {sunset_id}")
        return sunset_id

    def store_mfn_freebie_basket(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_freebie_basket entity + provision_has_freebie relation."""
        freebie_id = f"mfn_freebie_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has freebie_id "{freebie_id}"']

        field_map = {
            "uses_greater_of": ("bool", "uses_greater_of"),
            "stacks_with_general_basket": ("bool", "stacks_with_general_basket"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')

        for dbl_field in ("dollar_amount_usd", "ebitda_pct",
                          "general_basket_amount_usd", "total_mfn_exempt_capacity_usd"):
            if entity_data.get(dbl_field) is not None:
                attrs.append(f'has {dbl_field} {float(entity_data[dbl_field])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $fb isa mfn_freebie_basket,
                {attrs_str};
                (provision: $prov, freebie: $fb) isa provision_has_freebie;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_freebie_basket: {freebie_id}")
        return freebie_id

    def _build_attrs_from_data(self, data: dict, field_map: dict) -> list:
        """Build TypeQL attribute clauses from entity data dict.

        field_map: {json_key: (type, tql_attr_name)}
        type is 'string', 'bool', 'double', or 'int'.
        """
        attrs = []
        for json_key, (ftype, tql_name) in field_map.items():
            val = data.get(json_key)
            if val is None:
                continue
            if ftype == "string":
                attrs.append(f'has {tql_name} "{self._escape(str(val)[:2000])}"')
            elif ftype == "bool":
                attrs.append(f'has {tql_name} {str(val).lower()}')
            elif ftype == "double":
                attrs.append(f'has {tql_name} {float(val)}')
            elif ftype == "int":
                attrs.append(f'has {tql_name} {int(val)}')
        return attrs

    MFN_ENTITY_STORE_MAP = {
        "mfn_exclusion": "store_mfn_exclusion",
        "mfn_yield_definition": "store_mfn_yield_definition",
        "mfn_sunset_provision": "store_mfn_sunset_provision",
        "mfn_freebie_basket": "store_mfn_freebie_basket",
    }

    @classmethod
    def load_mfn_extraction_metadata(cls) -> list:
        """Load extraction metadata for MFN entity types only."""
        driver = typedb_client.driver
        db_name = settings.typedb_database

        if not driver:
            logger.warning("No TypeDB driver for MFN metadata")
            return []

        query = '''
            match
                $em isa extraction_metadata,
                    has metadata_id $id,
                    has target_entity_type $type,
                    has extraction_prompt $prompt;
                $id like "mfn_.*";
            select $id, $type, $prompt, $em;
        '''

        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            tx.close()

            metadata = []
            for row in rows:
                item = {
                    "metadata_id": cls._get_attr(row, "id"),
                    "target_entity_type": cls._get_attr(row, "type"),
                    "extraction_prompt": cls._get_attr(row, "prompt"),
                }
                meta_id = item["metadata_id"]
                item.update(cls._get_optional_metadata_attrs(meta_id))
                metadata.append(item)

            metadata.sort(key=lambda x: x.get("extraction_priority", 99))
            return metadata

        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.error(f"Error loading MFN extraction metadata: {e}")
            return []

    def summarize_extraction(self, extraction: RPExtractionV4) -> str:
        """Create summary string of what was extracted."""
        parts = []

        if extraction.builder_basket and extraction.builder_basket.exists:
            parts.append(f"builder({len(extraction.builder_basket.sources)} sources)")

        if extraction.ratio_basket and extraction.ratio_basket.exists:
            no_worse = "✓no-worse" if extraction.ratio_basket.has_no_worse_test else ""
            parts.append(f"ratio({extraction.ratio_basket.ratio_threshold}x {no_worse})")

        if extraction.jcrew_blocker and extraction.jcrew_blocker.exists:
            designation = "✓desig" if extraction.jcrew_blocker.covers_designation else "✗desig"
            parts.append(f"jcrew({designation}, {len(extraction.jcrew_blocker.exceptions)} exc)")

        if extraction.unsub_designation and extraction.unsub_designation.permitted:
            cap = extraction.unsub_designation.dollar_cap
            cap_str = f"${cap/1e6:.0f}M" if cap else "uncapped"
            parts.append(f"unsub({cap_str})")

        if extraction.sweep_tiers:
            parts.append(f"sweeps({len(extraction.sweep_tiers)} tiers)")

        if extraction.holdco_overhead_basket and extraction.holdco_overhead_basket.exists:
            parts.append("holdco")

        if extraction.equity_award_basket and extraction.equity_award_basket.exists:
            parts.append("equity_award")

        if extraction.reallocations:
            parts.append(f"realloc({len(extraction.reallocations)})")

        if extraction.investment_pathways:
            parts.append(f"pathways({len(extraction.investment_pathways)})")

        # RDP baskets
        rdp_count = sum(1 for b in [
            extraction.refinancing_rdp_basket,
            extraction.general_rdp_basket,
            extraction.ratio_rdp_basket,
            extraction.builder_rdp_basket,
            extraction.equity_funded_rdp_basket,
        ] if b and b.exists)
        if rdp_count:
            parts.append(f"rdp({rdp_count} baskets)")

        return ", ".join(parts) or "empty"

    def store_extraction(self, extraction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Store extracted covenant data as graph entities and relations.

        Args:
            extraction: Nested dict from extraction service with structure:
                {
                    "provision_type": "RP",
                    "section_reference": "6.06",
                    "baskets": [...],
                    "blockers": [...],
                    "sweep_config": {...},
                    ...
                }

        Returns:
            Dict with counts of created entities/relations
        """
        results = {
            "provision_id": None,
            "baskets_created": 0,
            "sources_created": 0,
            "blockers_created": 0,
            "exceptions_created": 0,
            "sweep_tiers_created": 0,
            "relations_created": 0,
            "errors": []
        }

        try:
            # 1. Create the provision entity
            provision_id = self._create_provision(extraction)
            results["provision_id"] = provision_id

            # 2. Link provision to deal
            self._link_provision_to_deal(provision_id)
            results["relations_created"] += 1

            # 3. Create baskets and link to provision
            baskets = extraction.get("baskets", [])
            for basket_data in baskets:
                try:
                    basket_id = self._create_basket(basket_data)
                    self._link_basket_to_provision(provision_id, basket_id)
                    results["baskets_created"] += 1
                    results["relations_created"] += 1

                    # If builder basket, create sources
                    if basket_data.get("type") == "builder":
                        sources = basket_data.get("sources", [])
                        for source_data in sources:
                            source_id = self._create_builder_source(source_data)
                            self._link_source_to_builder(basket_id, source_id)
                            results["sources_created"] += 1
                            results["relations_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Basket error: {str(e)[:100]}")

            # 4. Create blockers and link to provision
            blockers = extraction.get("blockers", [])
            for blocker_data in blockers:
                try:
                    blocker_id = self._create_blocker(blocker_data)
                    self._link_blocker_to_provision(provision_id, blocker_id)
                    results["blockers_created"] += 1
                    results["relations_created"] += 1

                    # Create blocker exceptions
                    exceptions = blocker_data.get("exceptions", [])
                    for exc_data in exceptions:
                        exc_id = self._create_blocker_exception(exc_data)
                        self._link_exception_to_blocker(blocker_id, exc_id)
                        results["exceptions_created"] += 1
                        results["relations_created"] += 1

                    # Link to IP types covered
                    ip_types = blocker_data.get("ip_types_covered", [])
                    for ip_type_id in ip_types:
                        self._link_blocker_to_ip_type(blocker_id, ip_type_id)
                        results["relations_created"] += 1

                except Exception as e:
                    results["errors"].append(f"Blocker error: {str(e)[:100]}")

            # 5. Create sweep configuration
            sweep_config = extraction.get("sweep_config", {})
            if sweep_config:
                try:
                    # Sweep tiers
                    tiers = sweep_config.get("tiers", [])
                    for tier_data in tiers:
                        tier_id = self._create_sweep_tier(tier_data)
                        self._link_tier_to_provision(provision_id, tier_id)
                        results["sweep_tiers_created"] += 1
                        results["relations_created"] += 1

                    # De minimis thresholds
                    thresholds = sweep_config.get("de_minimis", [])
                    for thresh_data in thresholds:
                        thresh_id = self._create_de_minimis(thresh_data)
                        self._link_threshold_to_provision(provision_id, thresh_id)
                        results["relations_created"] += 1

                    # Sweep exemptions
                    exemptions = sweep_config.get("exemptions", [])
                    for exemption_id in exemptions:
                        self._link_exemption_to_provision(provision_id, exemption_id)
                        results["relations_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Sweep config error: {str(e)[:100]}")

            # 6. Create reallocation relations
            reallocations = extraction.get("reallocations", [])
            for realloc_data in reallocations:
                try:
                    self._create_reallocation_relation(realloc_data)
                    results["relations_created"] += 1
                except Exception as e:
                    results["errors"].append(f"Reallocation error: {str(e)[:100]}")

        except Exception as e:
            results["errors"].append(f"Top-level error: {str(e)[:200]}")
            logger.exception(f"Error storing graph extraction for deal {self.deal_id}")

        return results

    def _gen_id(self, prefix: str) -> str:
        """Generate a unique ID with prefix."""
        return f"{prefix}_{self.deal_id}_{uuid.uuid4().hex[:8]}"

    def _execute_query(self, query: str, tx_type: TransactionType = TransactionType.WRITE) -> Any:
        """Execute a TypeQL query."""
        tx = self.driver.transaction(self.db_name, tx_type)
        try:
            result = tx.query(query).resolve()
            if tx_type == TransactionType.WRITE:
                tx.commit()
            else:
                tx.close()
            return result
        except Exception:
            tx.close()
            raise

    # ═══════════════════════════════════════════════════════════════════════════
    # PROVISION
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_provision(self, data: Dict[str, Any]) -> str:
        """Create an RP provision entity."""
        provision_id = self._gen_id("prov")
        prov_type = data.get("provision_type", "RP").lower()
        entity_type = f"{prov_type}_provision"

        section_ref = data.get("section_reference", "")
        source_page = data.get("source_page", 0)

        query = f'''
            insert $p isa {entity_type},
                has provision_id "{provision_id}",
                has section_reference "{section_ref}",
                has source_page {source_page};
        '''
        self._execute_query(query)
        return provision_id

    def _link_provision_to_deal(self, provision_id: str):
        """Link provision to deal."""
        query = f'''
            match
                $deal isa deal, has deal_id "{self.deal_id}";
                $prov isa provision, has provision_id "{provision_id}";
            insert
                (deal: $deal, provision: $prov) isa deal_has_provision;
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # BASKETS
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_basket(self, data: Dict[str, Any]) -> str:
        """Create a basket entity of the appropriate subtype."""
        basket_id = self._gen_id("basket")
        basket_type = data.get("type", "general_rp")

        # Map to entity type
        type_map = {
            "builder": "builder_basket",
            "ratio": "ratio_basket",
            "general_rp": "general_rp_basket",
            "management_equity": "management_equity_basket",
            "tax_distribution": "tax_distribution_basket",
            "rdp": "rdp_basket",
            "investment": "investment_basket",
            "unsub": "unsub_basket",
        }
        entity_type = type_map.get(basket_type, "basket")

        # Build attribute list
        attrs = [
            f'has basket_id "{basket_id}"',
            f'has basket_name "{self._escape(data.get("name", ""))}"',
        ]

        if data.get("section_reference"):
            attrs.append(f'has section_reference "{data["section_reference"]}"')
        if data.get("dollar_cap") is not None:
            attrs.append(f'has dollar_cap {data["dollar_cap"]}')
        if data.get("ebitda_percentage") is not None:
            attrs.append(f'has ebitda_percentage {data["ebitda_percentage"]}')
        if data.get("uses_greater_of") is not None:
            attrs.append(f'has uses_greater_of {str(data["uses_greater_of"]).lower()}')
        if data.get("requires_no_default") is not None:
            attrs.append(f'has requires_no_default {str(data["requires_no_default"]).lower()}')
        if data.get("requires_ratio_test") is not None:
            attrs.append(f'has requires_ratio_test {str(data["requires_ratio_test"]).lower()}')
        if data.get("ratio_threshold") is not None:
            attrs.append(f'has ratio_threshold {data["ratio_threshold"]}')
        if data.get("verbatim_text"):
            attrs.append(f'has verbatim_text "{self._escape(data["verbatim_text"][:2000])}"')

        # Builder-specific
        if basket_type == "builder":
            if data.get("start_date_language"):
                attrs.append(f'has start_date_language "{self._escape(data["start_date_language"])}"')
            if data.get("uses_greatest_of_tests") is not None:
                attrs.append(f'has uses_greatest_of_tests {str(data["uses_greatest_of_tests"]).lower()}')

        # Ratio-specific
        if basket_type == "ratio":
            if data.get("is_unlimited_if_met") is not None:
                attrs.append(f'has is_unlimited_if_met {str(data["is_unlimited_if_met"]).lower()}')
            if data.get("has_no_worse_test") is not None:
                attrs.append(f'has has_no_worse_test {str(data["has_no_worse_test"]).lower()}')
            if data.get("no_worse_threshold") is not None:
                attrs.append(f'has no_worse_threshold {data["no_worse_threshold"]}')

        # Management equity specific
        if basket_type == "management_equity":
            if data.get("annual_cap") is not None:
                attrs.append(f'has annual_cap {data["annual_cap"]}')
            if data.get("permits_carryforward") is not None:
                attrs.append(f'has permits_carryforward {str(data["permits_carryforward"]).lower()}')

        query = f'''
            insert $b isa {entity_type},
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return basket_id

    def _link_basket_to_provision(self, provision_id: str, basket_id: str):
        """Link basket to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $basket isa basket, has basket_id "{basket_id}";
            insert
                (provision: $prov, basket: $basket) isa provision_has_basket;
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # BUILDER SOURCES
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_builder_source(self, data: Dict[str, Any]) -> str:
        """Create a builder source entity."""
        source_id = self._gen_id("src")
        source_type = data.get("type", "builder_source")

        # Map to entity type
        type_map = {
            "cni": "cni_source",
            "ecf": "ecf_source",
            "ebitda_fc": "ebitda_fc_source",
            "equity_proceeds": "equity_proceeds_source",
            "asset_sale_proceeds": "asset_sale_proceeds_source",
            "investment_returns": "investment_returns_source",
            "declined_proceeds": "declined_proceeds_source",
            "starter_amount": "starter_amount_source",
        }
        entity_type = type_map.get(source_type, "builder_source")

        attrs = [
            f'has source_id "{source_id}"',
            f'has source_name "{self._escape(data.get("name", source_type))}"',
        ]

        if data.get("percentage") is not None:
            attrs.append(f'has percentage {data["percentage"]}')
        if data.get("floor_amount") is not None:
            attrs.append(f'has floor_amount {data["floor_amount"]}')
        if data.get("verbatim_text"):
            attrs.append(f'has verbatim_text "{self._escape(data["verbatim_text"][:1000])}"')
        if source_type == "ebitda_fc" and data.get("fc_multiplier") is not None:
            attrs.append(f'has fc_multiplier {data["fc_multiplier"]}')

        query = f'''
            insert $s isa {entity_type},
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return source_id

    def _link_source_to_builder(self, basket_id: str, source_id: str, is_primary: bool = False):
        """Link builder source to builder basket."""
        primary_attr = f', has is_primary_test {str(is_primary).lower()}' if is_primary else ''
        query = f'''
            match
                $builder isa builder_basket, has basket_id "{basket_id}";
                $source isa builder_source, has source_id "{source_id}";
            insert
                (builder: $builder, source: $source) isa builder_has_source{primary_attr};
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # BLOCKERS
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_blocker(self, data: Dict[str, Any]) -> str:
        """Create a blocker entity."""
        blocker_id = self._gen_id("blocker")
        blocker_type = data.get("type", "blocker")

        type_map = {
            "jcrew": "jcrew_blocker",
            "serta": "serta_blocker",
        }
        entity_type = type_map.get(blocker_type, "blocker")

        attrs = [
            f'has blocker_id "{blocker_id}"',
        ]

        if data.get("section_reference"):
            attrs.append(f'has section_reference "{data["section_reference"]}"')
        if data.get("verbatim_text"):
            attrs.append(f'has verbatim_text "{self._escape(data["verbatim_text"][:2000])}"')
        if data.get("source_page") is not None:
            attrs.append(f'has source_page {data["source_page"]}')

        # J.Crew specific
        if blocker_type == "jcrew":
            if data.get("covers_transfer") is not None:
                attrs.append(f'has covers_transfer {str(data["covers_transfer"]).lower()}')
            if data.get("covers_designation") is not None:
                attrs.append(f'has covers_designation {str(data["covers_designation"]).lower()}')

        query = f'''
            insert $b isa {entity_type},
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return blocker_id

    def _link_blocker_to_provision(self, provision_id: str, blocker_id: str):
        """Link blocker to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $blocker isa blocker, has blocker_id "{blocker_id}";
            insert
                (provision: $prov, blocker: $blocker) isa provision_has_blocker;
        '''
        self._execute_query(query)

    def _create_blocker_exception(self, data: Dict[str, Any]) -> str:
        """Create a blocker exception entity."""
        exc_id = self._gen_id("exc")
        exc_type = data.get("type", "blocker_exception")

        type_map = {
            "nonexclusive_license": "nonexclusive_license_exception",
            "ordinary_course": "ordinary_course_exception",
            "intercompany": "intercompany_exception",
            "fair_value": "fair_value_exception",
            "license_back": "license_back_exception",
            "immaterial_ip": "immaterial_ip_exception",
        }
        entity_type = type_map.get(exc_type, "blocker_exception")

        attrs = [
            f'has exception_id "{exc_id}"',
            f'has exception_name "{self._escape(data.get("name", exc_type))}"',
        ]

        if data.get("scope_limitation"):
            attrs.append(f'has scope_limitation "{self._escape(data["scope_limitation"])}"')
        if data.get("verbatim_text"):
            attrs.append(f'has verbatim_text "{self._escape(data["verbatim_text"][:1000])}"')

        query = f'''
            insert $e isa {entity_type},
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return exc_id

    def _link_exception_to_blocker(self, blocker_id: str, exception_id: str):
        """Link exception to blocker."""
        query = f'''
            match
                $blocker isa blocker, has blocker_id "{blocker_id}";
                $exc isa blocker_exception, has exception_id "{exception_id}";
            insert
                (blocker: $blocker, exception: $exc) isa blocker_has_exception;
        '''
        self._execute_query(query)

    def _link_blocker_to_ip_type(self, blocker_id: str, ip_type_concept_id: str, scope: str = "full"):
        """Link blocker to IP type it covers.

        Fixed from legacy version:
        - concept_id (not ip_type_id): ip_type sub concept inherits concept_id @key
        - blocker_covers_ip_type (not blocker_covers): per schema_unified.tql
        - jcrew_blocker (not blocker): V4 path creates jcrew_blocker instances
        """
        query = f'''
            match
                $blocker isa jcrew_blocker, has blocker_id "{blocker_id}";
                $ip isa ip_type, has concept_id "{ip_type_concept_id}";
            insert
                (blocker: $blocker, ip_type: $ip) isa blocker_covers_ip_type,
                    has coverage_scope "{scope}";
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # SWEEP CONFIG
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_sweep_tier(self, data: Dict[str, Any]) -> str:
        """Create a sweep tier entity."""
        tier_id = self._gen_id("tier")

        attrs = [
            f'has tier_id "{tier_id}"',
        ]

        if data.get("leverage_threshold") is not None:
            attrs.append(f'has leverage_threshold {data["leverage_threshold"]}')
        if data.get("sweep_percentage") is not None:
            attrs.append(f'has sweep_percentage {data["sweep_percentage"]}')
        if data.get("is_highest_tier") is not None:
            attrs.append(f'has is_highest_tier {str(data["is_highest_tier"]).lower()}')

        query = f'''
            insert $t isa sweep_tier,
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return tier_id

    def _link_tier_to_provision(self, provision_id: str, tier_id: str):
        """Link sweep tier to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $tier isa sweep_tier, has tier_id "{tier_id}";
            insert
                (provision: $prov, tier: $tier) isa provision_has_sweep_tier;
        '''
        self._execute_query(query)

    def _create_de_minimis(self, data: Dict[str, Any]) -> str:
        """Create a de minimis threshold entity."""
        thresh_id = self._gen_id("thresh")

        attrs = [
            f'has threshold_id "{thresh_id}"',
            f'has threshold_type "{data.get("type", "individual")}"',
        ]

        if data.get("dollar_amount") is not None:
            attrs.append(f'has dollar_cap {data["dollar_amount"]}')
        if data.get("ebitda_percentage") is not None:
            attrs.append(f'has ebitda_percentage {data["ebitda_percentage"]}')
        if data.get("uses_greater_of") is not None:
            attrs.append(f'has uses_greater_of {str(data["uses_greater_of"]).lower()}')
        if data.get("permits_carryforward") is not None:
            attrs.append(f'has permits_carryforward {str(data["permits_carryforward"]).lower()}')

        query = f'''
            insert $th isa de_minimis_threshold,
                {", ".join(attrs)};
        '''
        self._execute_query(query)
        return thresh_id

    def _link_threshold_to_provision(self, provision_id: str, threshold_id: str):
        """Link de minimis threshold to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $thresh isa de_minimis_threshold, has threshold_id "{threshold_id}";
            insert
                (provision: $prov, threshold: $thresh) isa provision_has_de_minimis;
        '''
        self._execute_query(query)

    def _link_exemption_to_provision(self, provision_id: str, exemption_id: str):
        """Link sweep exemption to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $ex isa sweep_exemption, has exemption_id "{exemption_id}";
            insert
                (provision: $prov, exemption: $ex) isa provision_has_sweep_exemption;
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # REALLOCATIONS
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_reallocation_relation(self, data: Dict[str, Any]):
        """Create basket reallocation relation."""
        source_basket = data.get("source_basket_id")
        target_basket = data.get("target_basket_id")

        if not source_basket or not target_basket:
            return

        attrs = []
        if data.get("section_reference"):
            attrs.append(f'has reallocation_section "{data["section_reference"]}"')
        if data.get("cap") is not None:
            attrs.append(f'has reallocation_cap {data["cap"]}')
        if data.get("is_bidirectional") is not None:
            attrs.append(f'has is_bidirectional {str(data["is_bidirectional"]).lower()}')

        attr_str = f', {", ".join(attrs)}' if attrs else ''

        query = f'''
            match
                $src isa basket, has basket_id "{source_basket}";
                $tgt isa basket, has basket_id "{target_basket}";
            insert
                (source_basket: $src, target_basket: $tgt) isa basket_reallocates_to{attr_str};
        '''
        self._execute_query(query)

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════════

    def _escape(self, text: str) -> str:
        """Escape text for TypeQL string."""
        if not text:
            return ""
        return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
