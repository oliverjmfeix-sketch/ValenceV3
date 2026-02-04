"""
Graph Storage Service - V4 Graph-Native Schema

Handles inserting extracted covenant data as entities and relations
instead of flat attributes.
"""
import logging
import uuid
from typing import Dict, Any, List, Optional
from typedb.driver import TransactionType

from app.services.typedb_client import typedb_client
from app.config import settings

logger = logging.getLogger(__name__)


class GraphStorage:
    """Insert extracted covenant data as graph entities and relations."""

    def __init__(self, deal_id: str):
        self.deal_id = deal_id
        self.driver = typedb_client.driver
        self.db_name = settings.typedb_database

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
