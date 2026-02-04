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

                    # Link to restricted parties
                    parties = blocker_data.get("bound_parties", [])
                    for party_id in parties:
                        self._link_blocker_to_party(blocker_id, party_id)
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
        verbatim = self._escape(data.get("verbatim_text", "")[:5000])
        source_page = data.get("source_page", 0)

        query = f'''
            insert $p isa {entity_type},
                has provision_id "{provision_id}",
                has section_reference "{section_ref}",
                has verbatim_text "{verbatim}",
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

    def _link_blocker_to_ip_type(self, blocker_id: str, ip_type_id: str, scope: str = "full"):
        """Link blocker to IP type it covers."""
        query = f'''
            match
                $blocker isa blocker, has blocker_id "{blocker_id}";
                $ip isa ip_type, has ip_type_id "{ip_type_id}";
            insert
                (blocker: $blocker, ip_type: $ip) isa blocker_covers,
                    has coverage_scope "{scope}";
        '''
        self._execute_query(query)

    def _link_blocker_to_party(self, blocker_id: str, party_id: str):
        """Link blocker to restricted party it binds."""
        query = f'''
            match
                $blocker isa blocker, has blocker_id "{blocker_id}";
                $party isa restricted_party, has party_id "{party_id}";
            insert
                (blocker: $blocker, party: $party) isa blocker_binds;
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
