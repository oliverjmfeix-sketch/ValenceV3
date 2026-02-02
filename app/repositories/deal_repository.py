"""
Deal Repository - TypeDB operations for deals.

Handles:
- Deal CRUD operations
- Typed primitive storage (NOT JSON blobs)
- Provenance linking
"""
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from typedb.driver import TransactionType

from app.services.typedb_client import TypeDBClient, get_typedb_client
from app.schemas.models import (
    DealSummary, Deal, ExtractedPrimitive, Provenance, MultiselectAnswer
)

logger = logging.getLogger(__name__)


def sanitize_for_typeql(value: str) -> str:
    """Escape special characters for TypeQL string literals."""
    if value is None:
        return ""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


class DealRepository:
    """Repository for deal operations in TypeDB."""
    
    def __init__(self, client: Optional[TypeDBClient] = None):
        self.client = client or get_typedb_client()
    
    def list_deals(self) -> List[DealSummary]:
        """Get all deals with summary info."""
        query = """
            match
                $d isa deal,
                    has deal_id $id,
                    has deal_name $name,
                    has borrower $borrower,
                    has upload_date $date;
            fetch {
                "deal_id": $id,
                "deal_name": $name,
                "borrower": $borrower,
                "upload_date": $date
            };
        """
        
        try:
            with self.client.read_transaction() as tx:
                results = list(tx.query(query).resolve())
                
                deals = []
                for row in results:
                    data = row.get("fetch", {})
                    deals.append(DealSummary(
                        deal_id=data.get("deal_id", ""),
                        deal_name=data.get("deal_name", ""),
                        borrower=data.get("borrower", ""),
                        upload_date=data.get("upload_date", datetime.now()),
                        has_mfn=None,  # Could fetch from provision
                        has_rp=None,
                        has_jcrew_risk=None
                    ))
                
                return deals
        except Exception as e:
            logger.error(f"Error listing deals: {e}")
            return []
    
    def get_deal(self, deal_id: str) -> Optional[Deal]:
        """Get a deal with all its typed primitives."""
        # First get deal basic info
        deal_query = f"""
            match
                $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name $name,
                    has borrower $borrower,
                    has upload_date $date;
            fetch {{
                "deal_name": $name,
                "borrower": $borrower,
                "upload_date": $date
            }};
        """
        
        try:
            with self.client.read_transaction() as tx:
                result = list(tx.query(deal_query).resolve())
                
                if not result:
                    return None
                
                data = result[0].get("fetch", {})
                
                # Get MFN provision primitives
                mfn_provision = self._get_mfn_primitives(tx, deal_id)
                
                # Get RP provision primitives
                rp_provision = self._get_rp_primitives(tx, deal_id)
                
                # Detect patterns
                patterns = self._detect_patterns(mfn_provision, rp_provision)
                
                return Deal(
                    deal_id=deal_id,
                    deal_name=data.get("deal_name", ""),
                    borrower=data.get("borrower", ""),
                    upload_date=data.get("upload_date", datetime.now()),
                    mfn_provision=mfn_provision,
                    rp_provision=rp_provision,
                    patterns=patterns
                )
        except Exception as e:
            logger.error(f"Error getting deal {deal_id}: {e}")
            return None
    
    def _get_mfn_primitives(self, tx, deal_id: str) -> Optional[Dict[str, Any]]:
        """Get MFN provision typed primitives for a deal."""
        # Query for MFN provision linked to this deal
        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                $p isa mfn_provision;
            select $p;
        """
        
        try:
            result = list(tx.query(query).resolve())
            if not result:
                return None
            
            # Get all attributes from the provision
            provision = result[0].get("p")
            if not provision:
                return None
            
            primitives = {}
            for attr in provision.get_has():
                attr_type = attr.get_type().get_label().name
                primitives[attr_type] = attr.get_value()
            
            return primitives
        except Exception as e:
            logger.error(f"Error getting MFN primitives: {e}")
            return None
    
    def _get_rp_primitives(self, tx, deal_id: str) -> Optional[Dict[str, Any]]:
        """Get RP provision typed primitives for a deal."""
        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                $p isa rp_provision;
            select $p;
        """
        
        try:
            result = list(tx.query(query).resolve())
            if not result:
                return None
            
            provision = result[0].get("p")
            if not provision:
                return None
            
            primitives = {}
            for attr in provision.get_has():
                attr_type = attr.get_type().get_label().name
                primitives[attr_type] = attr.get_value()
            
            return primitives
        except Exception as e:
            logger.error(f"Error getting RP primitives: {e}")
            return None
    
    def _detect_patterns(
        self, 
        mfn: Optional[Dict[str, Any]], 
        rp: Optional[Dict[str, Any]]
    ) -> Dict[str, bool]:
        """Detect patterns from primitives."""
        patterns = {
            "yield_exclusion_pattern": False,
            "weak_mfn_pattern": False,
            "jcrew_pattern": False
        }
        
        # Yield exclusion: OID and floor both excluded
        if mfn:
            oid_excluded = mfn.get("oid_included_in_yield") == False
            floor_excluded = mfn.get("floor_included_in_yield") == False
            patterns["yield_exclusion_pattern"] = oid_excluded and floor_excluded
            
            # Weak MFN: short sunset, high threshold, or major exclusions
            sunset_months = mfn.get("sunset_period_months")
            threshold = mfn.get("threshold_bps")
            if sunset_months and sunset_months < 12:
                patterns["weak_mfn_pattern"] = True
            if threshold and threshold > 50:
                patterns["weak_mfn_pattern"] = True
        
        # J.Crew pattern: unrestricted sub + IP transfers + weak blocker
        if rp:
            has_unsub = rp.get("unrestricted_sub_designation_permitted") == True
            has_ip_transfer = rp.get("ip_transfers_to_subs_permitted") == True
            no_blocker = rp.get("jcrew_blocker_present") == False
            weak_blocker = rp.get("jcrew_blocker_covers_ip") == False
            weak_ip_def = rp.get("ip_definition_includes_trade_secrets") == False
            
            if has_unsub and has_ip_transfer:
                if no_blocker or weak_blocker or weak_ip_def:
                    patterns["jcrew_pattern"] = True
        
        return patterns
    
    def create_deal(
        self,
        deal_id: str,
        deal_name: str,
        borrower: str,
        pdf_filename: str
    ) -> bool:
        """Create a new deal entity."""
        query = f"""
            insert
                $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{sanitize_for_typeql(deal_name)}",
                    has borrower "{sanitize_for_typeql(borrower)}",
                    has upload_date {datetime.now().isoformat()},
                    has pdf_filename "{sanitize_for_typeql(pdf_filename)}";
        """
        
        try:
            with self.client.write_transaction() as tx:
                tx.query(query).resolve()
            logger.info(f"Created deal: {deal_id}")
            return True
        except Exception as e:
            logger.error(f"Error creating deal: {e}")
            return False
    
    def store_mfn_primitives(
        self,
        deal_id: str,
        primitives: List[ExtractedPrimitive]
    ) -> bool:
        """Store MFN provision with typed primitives (NOT JSON)."""
        if not primitives:
            return True
        
        # Build attribute clauses from primitives
        attr_clauses = []
        for p in primitives:
            value = p.value
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            elif isinstance(value, (int, float)):
                value_str = str(value)
            elif isinstance(value, str):
                value_str = f'"{sanitize_for_typeql(value)}"'
            else:
                continue  # Skip unsupported types
            
            attr_clauses.append(f'has {p.attribute_name} {value_str}')
        
        if not attr_clauses:
            return True

        attrs_str = ",\n                    ".join(attr_clauses)

        # provision_id is required (@key in schema)
        provision_id = f"{deal_id}_mfn"

        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
            insert
                $p isa mfn_provision,
                    has provision_id "{provision_id}",
                    {attrs_str};
                ($d, $p) isa deal_has_provision;
        """
        
        try:
            with self.client.write_transaction() as tx:
                tx.query(query).resolve()
            logger.info(f"Stored MFN primitives for deal {deal_id}")
            
            # Store provenance for each primitive
            self._store_provenance(deal_id, "mfn_provision", primitives)
            
            return True
        except Exception as e:
            logger.error(f"Error storing MFN primitives: {e}")
            return False
    
    def store_rp_primitives(
        self,
        deal_id: str,
        primitives: List[ExtractedPrimitive]
    ) -> bool:
        """Store RP provision with typed primitives (NOT JSON)."""
        if not primitives:
            return True
        
        # Build attribute clauses from primitives
        attr_clauses = []
        for p in primitives:
            value = p.value
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            elif isinstance(value, (int, float)):
                value_str = str(value)
            elif isinstance(value, str):
                value_str = f'"{sanitize_for_typeql(value)}"'
            else:
                continue
            
            attr_clauses.append(f'has {p.attribute_name} {value_str}')
        
        if not attr_clauses:
            return True

        attrs_str = ",\n                    ".join(attr_clauses)

        # provision_id is required (@key in schema)
        provision_id = f"{deal_id}_rp"

        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
            insert
                $p isa rp_provision,
                    has provision_id "{provision_id}",
                    {attrs_str};
                ($d, $p) isa deal_has_provision;
        """

        try:
            with self.client.write_transaction() as tx:
                tx.query(query).resolve()
            logger.info(f"Stored RP primitives for deal {deal_id}")
            
            # Store provenance for each primitive
            self._store_provenance(deal_id, "rp_provision", primitives)
            
            return True
        except Exception as e:
            logger.error(f"Error storing RP primitives: {e}")
            return False
    
    def store_concept_applicabilities(
        self,
        deal_id: str,
        provision_type: str,
        multiselect_answers: List[MultiselectAnswer]
    ) -> bool:
        """Store multiselect answers as concept_applicability relations."""
        if not multiselect_answers:
            return True

        provision_id = f"{deal_id}_{'mfn' if provision_type == 'mfn_provision' else 'rp'}"

        for answer in multiselect_answers:
            for concept_id in answer.included:
                # Escape source text
                safe_source = sanitize_for_typeql((answer.source_text or "")[:500])

                query = f"""
                    match
                        $p isa {provision_type}, has provision_id "{provision_id}";
                        $c isa {answer.concept_type}, has concept_id "{concept_id}";
                    insert
                        (provision: $p, concept: $c) isa concept_applicability,
                            has applicability_status "INCLUDED",
                            has source_text "{safe_source}",
                            has source_page {answer.source_page};
                """

                try:
                    with self.client.write_transaction() as tx:
                        tx.query(query).resolve()
                    logger.debug(f"Stored applicability: {concept_id} for {provision_id}")
                except Exception as e:
                    logger.warning(f"Error storing applicability for {concept_id}: {e}")

        logger.info(f"Stored concept applicabilities for {provision_type} on deal {deal_id}")
        return True

    def _store_provenance(
        self,
        deal_id: str,
        provision_type: str,
        primitives: List[ExtractedPrimitive]
    ):
        """Store provenance for each primitive."""
        for p in primitives:
            if not p.source_text:
                continue
            
            query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    ($d, $prov) isa deal_has_provision;
                    $prov isa {provision_type};
                insert
                    $provenance isa attribute_provenance,
                        has attributed_field_name "{p.attribute_name}",
                        has source_text "{sanitize_for_typeql(p.source_text[:1000])}",
                        has source_page {p.source_page},
                        has source_section "{sanitize_for_typeql(p.source_section or '')}",
                        has confidence "{p.confidence}",
                        has extracted_at {datetime.now().isoformat()};
                    ($prov, $provenance) isa has_provenance;
            """
            
            try:
                with self.client.write_transaction() as tx:
                    tx.query(query).resolve()
            except Exception as e:
                logger.warning(f"Error storing provenance for {p.attribute_name}: {e}")
    
    def delete_deal(self, deal_id: str) -> bool:
        """Delete a deal and all associated data."""
        # Delete provenance first
        prov_query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                ($p, $prov) isa has_provenance;
            delete
                $prov isa attribute_provenance;
        """
        
        # Delete provisions
        provision_query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
            delete
                $p isa thing;
        """
        
        # Delete deal
        deal_query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
            delete
                $d isa deal;
        """
        
        try:
            with self.client.write_transaction() as tx:
                tx.query(prov_query).resolve()
                tx.query(provision_query).resolve()
                tx.query(deal_query).resolve()
            
            logger.info(f"Deleted deal: {deal_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting deal: {e}")
            return False
    
    def get_provenance(
        self, 
        deal_id: str, 
        attribute_name: str
    ) -> Optional[Provenance]:
        """Get provenance for a specific attribute."""
        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                ($p, $prov) isa has_provenance;
                $prov has attribute_name "{attribute_name}",
                    has source_text $text,
                    has source_page $page;
            fetch {{
                "source_text": $text,
                "source_page": $page
            }};
        """
        
        try:
            with self.client.read_transaction() as tx:
                result = list(tx.query(query).resolve())
                
                if not result:
                    return None
                
                data = result[0].get("fetch", {})
                return Provenance(
                    attribute_name=attribute_name,
                    source_text=data.get("source_text", ""),
                    source_page=data.get("source_page", 0),
                    extraction_confidence="high"
                )
        except Exception as e:
            logger.error(f"Error getting provenance: {e}")
            return None


# Global repository instance
deal_repository = DealRepository()


def get_deal_repository() -> DealRepository:
    """Dependency injection for deal repository."""
    return deal_repository
