"""
Graph Queries Service - V4 Graph-Native Schema

Query builders for reading covenant data from graph model.
"""
import logging
from typing import Dict, Any, List, Optional
from typedb.driver import TransactionType

from app.services.typedb_client import typedb_client
from app.config import settings

logger = logging.getLogger(__name__)


class GraphQueries:
    """Query covenant graph data."""

    def __init__(self):
        self.driver = typedb_client.driver
        self.db_name = settings.typedb_database

    def _execute_read(self, query: str) -> List[Dict[str, Any]]:
        """Execute a read query and return results as list of dicts."""
        tx = self.driver.transaction(self.db_name, TransactionType.READ)
        try:
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            tx.close()
            return rows
        except Exception:
            tx.close()
            raise

    def _get_attr(self, row, key: str, default=None):
        """Safely get attribute value from row."""
        try:
            concept = row.get(key)
            if concept is None:
                return default
            return concept.as_attribute().get_value()
        except Exception:
            return default

    # ═══════════════════════════════════════════════════════════════════════════
    # DEAL QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    def get_deal_with_provisions(self, deal_id: str) -> Dict[str, Any]:
        """Get deal with all its provisions and nested data."""
        deal_data = {"deal_id": deal_id, "provisions": []}

        # Get deal basics
        query = f'''
            match $d isa deal, has deal_id "{deal_id}",
                has deal_name $name, has borrower $borrower, has status $status;
            select $name, $borrower, $status;
        '''
        rows = self._execute_read(query)
        if rows:
            deal_data["deal_name"] = self._get_attr(rows[0], "name")
            deal_data["borrower"] = self._get_attr(rows[0], "borrower")
            deal_data["status"] = self._get_attr(rows[0], "status")

        # Get provisions
        prov_query = f'''
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                $p has provision_id $pid, has section_reference $sec;
            select $pid, $sec;
        '''
        prov_rows = self._execute_read(prov_query)
        for row in prov_rows:
            prov_id = self._get_attr(row, "pid")
            provision = {
                "provision_id": prov_id,
                "section_reference": self._get_attr(row, "sec"),
                "baskets": self.get_provision_baskets(prov_id),
                "blockers": self.get_provision_blockers(prov_id),
                "sweep_config": self.get_provision_sweep_config(prov_id),
            }
            deal_data["provisions"].append(provision)

        return deal_data

    # ═══════════════════════════════════════════════════════════════════════════
    # BASKET QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    def get_provision_baskets(self, provision_id: str) -> List[Dict[str, Any]]:
        """Get all baskets for a provision."""
        query = f'''
            match
                $p isa provision, has provision_id "{provision_id}";
                ($p, $b) isa provision_has_basket;
                $b has basket_id $bid, has basket_name $name;
            select $bid, $name, $b;
        '''
        rows = self._execute_read(query)
        baskets = []

        for row in rows:
            basket_id = self._get_attr(row, "bid")
            basket = {
                "basket_id": basket_id,
                "name": self._get_attr(row, "name"),
            }

            # Get additional basket attributes
            basket.update(self._get_basket_details(basket_id))

            # If builder basket, get sources
            if basket.get("type") == "builder":
                basket["sources"] = self.get_builder_sources(basket_id)

            baskets.append(basket)

        return baskets

    def _get_basket_details(self, basket_id: str) -> Dict[str, Any]:
        """Get detailed attributes for a basket."""
        query = f'''
            match $b isa basket, has basket_id "{basket_id}";
            select $b;
        '''
        # This is simplified - in practice you'd fetch all optional attributes
        return {}

    def get_builder_sources(self, basket_id: str) -> List[Dict[str, Any]]:
        """Get all sources for a builder basket."""
        query = f'''
            match
                $bb isa builder_basket, has basket_id "{basket_id}";
                ($bb, $s) isa builder_has_source;
                $s has source_id $sid, has source_name $name;
            select $sid, $name;
        '''
        rows = self._execute_read(query)
        return [
            {
                "source_id": self._get_attr(row, "sid"),
                "name": self._get_attr(row, "name"),
            }
            for row in rows
        ]

    # ═══════════════════════════════════════════════════════════════════════════
    # BLOCKER QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    def get_provision_blockers(self, provision_id: str) -> List[Dict[str, Any]]:
        """Get all blockers for a provision."""
        query = f'''
            match
                $p isa provision, has provision_id "{provision_id}";
                ($p, $b) isa provision_has_blocker;
                $b has blocker_id $bid;
            select $bid, $b;
        '''
        rows = self._execute_read(query)
        blockers = []

        for row in rows:
            blocker_id = self._get_attr(row, "bid")
            blocker = {
                "blocker_id": blocker_id,
                "exceptions": self.get_blocker_exceptions(blocker_id),
                "ip_types_covered": self.get_blocker_ip_types(blocker_id),
                "bound_parties": self.get_blocker_parties(blocker_id),
            }

            # Get J.Crew specific attributes
            jcrew_query = f'''
                match $b isa jcrew_blocker, has blocker_id "{blocker_id}",
                    has covers_transfer $ct, has covers_designation $cd;
                select $ct, $cd;
            '''
            try:
                jcrew_rows = self._execute_read(jcrew_query)
                if jcrew_rows:
                    blocker["type"] = "jcrew"
                    blocker["covers_transfer"] = self._get_attr(jcrew_rows[0], "ct")
                    blocker["covers_designation"] = self._get_attr(jcrew_rows[0], "cd")
            except Exception:
                pass

            blockers.append(blocker)

        return blockers

    def get_blocker_exceptions(self, blocker_id: str) -> List[Dict[str, Any]]:
        """Get all exceptions for a blocker."""
        query = f'''
            match
                $b isa blocker, has blocker_id "{blocker_id}";
                ($b, $e) isa blocker_has_exception;
                $e has exception_id $eid, has exception_name $name;
            select $eid, $name;
        '''
        rows = self._execute_read(query)
        return [
            {
                "exception_id": self._get_attr(row, "eid"),
                "name": self._get_attr(row, "name"),
            }
            for row in rows
        ]

    def get_blocker_ip_types(self, blocker_id: str) -> List[str]:
        """Get IP types covered by a blocker."""
        query = f'''
            match
                $b isa blocker, has blocker_id "{blocker_id}";
                ($b, $ip) isa blocker_covers;
                $ip has ip_type_id $ipid;
            select $ipid;
        '''
        rows = self._execute_read(query)
        return [self._get_attr(row, "ipid") for row in rows]

    def get_blocker_parties(self, blocker_id: str) -> List[str]:
        """Get parties bound by a blocker."""
        query = f'''
            match
                $b isa blocker, has blocker_id "{blocker_id}";
                ($b, $party) isa blocker_binds;
                $party has party_id $pid;
            select $pid;
        '''
        rows = self._execute_read(query)
        return [self._get_attr(row, "pid") for row in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # SWEEP CONFIG QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    def get_provision_sweep_config(self, provision_id: str) -> Dict[str, Any]:
        """Get sweep configuration for a provision."""
        config = {
            "tiers": [],
            "de_minimis": [],
            "exemptions": [],
        }

        # Get sweep tiers
        tier_query = f'''
            match
                $p isa provision, has provision_id "{provision_id}";
                ($p, $t) isa provision_has_sweep_tier;
                $t has tier_id $tid, has leverage_threshold $lev, has sweep_percentage $pct;
            select $tid, $lev, $pct;
        '''
        try:
            tier_rows = self._execute_read(tier_query)
            for row in tier_rows:
                config["tiers"].append({
                    "tier_id": self._get_attr(row, "tid"),
                    "leverage_threshold": self._get_attr(row, "lev"),
                    "sweep_percentage": self._get_attr(row, "pct"),
                })
        except Exception:
            pass

        # Get de minimis thresholds
        dm_query = f'''
            match
                $p isa provision, has provision_id "{provision_id}";
                ($p, $th) isa provision_has_de_minimis;
                $th has threshold_id $thid, has threshold_type $type, has dollar_cap $cap;
            select $thid, $type, $cap;
        '''
        try:
            dm_rows = self._execute_read(dm_query)
            for row in dm_rows:
                config["de_minimis"].append({
                    "threshold_id": self._get_attr(row, "thid"),
                    "type": self._get_attr(row, "type"),
                    "dollar_amount": self._get_attr(row, "cap"),
                })
        except Exception:
            pass

        # Get exemptions
        ex_query = f'''
            match
                $p isa provision, has provision_id "{provision_id}";
                ($p, $ex) isa provision_has_sweep_exemption;
                $ex has exemption_id $exid, has exemption_name $name;
            select $exid, $name;
        '''
        try:
            ex_rows = self._execute_read(ex_query)
            for row in ex_rows:
                config["exemptions"].append({
                    "exemption_id": self._get_attr(row, "exid"),
                    "name": self._get_attr(row, "name"),
                })
        except Exception:
            pass

        return config

    # ═══════════════════════════════════════════════════════════════════════════
    # CROSS-DEAL ANALYTICS QUERIES
    # ═══════════════════════════════════════════════════════════════════════════

    def find_deals_with_jcrew_risk(self) -> List[Dict[str, Any]]:
        """Find all deals with J.Crew blocker and their risk assessment."""
        query = '''
            match
                $deal isa deal, has deal_id $did, has deal_name $name;
                ($deal, $prov) isa deal_has_provision;
                ($prov, $b) isa provision_has_blocker;
                $b isa jcrew_blocker;
            select $did, $name;
        '''
        rows = self._execute_read(query)
        return [
            {
                "deal_id": self._get_attr(row, "did"),
                "deal_name": self._get_attr(row, "name"),
            }
            for row in rows
        ]

    def find_deals_with_builder_basket(self, min_sources: int = 3) -> List[Dict[str, Any]]:
        """Find deals with builder baskets having multiple sources."""
        # This would use aggregation in a more complete implementation
        query = '''
            match
                $deal isa deal, has deal_id $did, has deal_name $name;
                ($deal, $prov) isa deal_has_provision;
                ($prov, $bb) isa provision_has_basket;
                $bb isa builder_basket, has basket_id $bid;
            select $did, $name, $bid;
        '''
        rows = self._execute_read(query)
        return [
            {
                "deal_id": self._get_attr(row, "did"),
                "deal_name": self._get_attr(row, "name"),
                "basket_id": self._get_attr(row, "bid"),
            }
            for row in rows
        ]

    def compare_ratio_baskets(self) -> List[Dict[str, Any]]:
        """Compare ratio baskets across deals."""
        query = '''
            match
                $deal isa deal, has deal_id $did, has deal_name $name;
                ($deal, $prov) isa deal_has_provision;
                ($prov, $rb) isa provision_has_basket;
                $rb isa ratio_basket,
                    has ratio_threshold $thresh,
                    has has_no_worse_test $nw;
            select $did, $name, $thresh, $nw;
        '''
        rows = self._execute_read(query)
        return [
            {
                "deal_id": self._get_attr(row, "did"),
                "deal_name": self._get_attr(row, "name"),
                "ratio_threshold": self._get_attr(row, "thresh"),
                "has_no_worse_test": self._get_attr(row, "nw"),
            }
            for row in rows
        ]
