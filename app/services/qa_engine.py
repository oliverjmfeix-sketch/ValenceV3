"""
Q&A Engine - Answer questions using TypeDB structured data.

IMPORTANT: This queries TypeDB for answers, NOT Claude.
Claude was used once at extraction time. Q&A uses the pre-extracted primitives.
"""
import re
import logging
from typing import Optional, List, Dict, Any

from anthropic import Anthropic

from app.config import settings
from app.services.typedb_client import TypeDBClient, get_typedb_client
from app.repositories.ontology_repository import OntologyRepository, get_ontology_repository
from app.schemas.models import (
    QAResponse, PrimitiveWithProvenance, Provenance,
    CrossDealResponse, CrossDealResult
)

logger = logging.getLogger(__name__)


class QAEngine:
    """
    Answer questions using TypeDB data.
    
    The Q&A engine:
    1. Interprets the natural language question (uses Claude)
    2. Builds a TypeQL query to get the answer from structured data
    3. Returns the answer with supporting primitives and provenance
    
    The ANSWER comes from TypeDB, not Claude.
    """
    
    def __init__(
        self,
        client: Optional[TypeDBClient] = None,
        ontology_repo: Optional[OntologyRepository] = None
    ):
        self.client = client or get_typedb_client()
        self.ontology_repo = ontology_repo or get_ontology_repository()
        self.anthropic = Anthropic(api_key=settings.anthropic_api_key)
        
        # Cache attribute mappings
        self._attribute_map: Optional[Dict[str, str]] = None
    
    @property
    def attribute_map(self) -> Dict[str, str]:
        """Get attribute -> question text mapping."""
        if self._attribute_map is None:
            self._attribute_map = self.ontology_repo.get_attribute_mapping()
        return self._attribute_map
    
    async def answer_question(
        self,
        deal_id: str,
        question: str
    ) -> QAResponse:
        """
        Answer a natural language question about a deal.
        
        The process:
        1. Identify which attributes are relevant to the question
        2. Query TypeDB for those attribute values
        3. Build a natural language answer with provenance
        """
        # Step 1: Identify relevant attributes
        relevant_attrs = self._identify_relevant_attributes(question)
        logger.info(f"Relevant attributes for '{question}': {relevant_attrs}")
        
        if not relevant_attrs:
            return QAResponse(
                answer="I couldn't identify specific data points to answer this question. Try asking about MFN provisions, restricted payments, or J.Crew risk factors.",
                supporting_primitives=[],
                confidence="low"
            )
        
        # Step 2: Query TypeDB for values
        primitives = self._get_primitives_for_attributes(deal_id, relevant_attrs)
        
        if not primitives:
            return QAResponse(
                answer="No data found for this deal. The document may not have been extracted yet.",
                supporting_primitives=[],
                confidence="low"
            )
        
        # Step 3: Build natural language answer
        answer = self._build_answer(question, primitives)
        
        return QAResponse(
            answer=answer,
            supporting_primitives=primitives,
            confidence="high" if len(primitives) > 0 else "low"
        )
    
    async def cross_deal_query(
        self,
        question: str,
        deal_ids: Optional[List[str]] = None
    ) -> CrossDealResponse:
        """
        Query across multiple deals.
        
        Examples:
        - "Which deals have J.Crew risk?"
        - "Find deals with sunset periods under 12 months"
        - "Which deals exclude OID from yield?"
        """
        # Identify the attribute and condition
        query_info = self._parse_cross_deal_query(question)
        
        if not query_info:
            return CrossDealResponse(
                query=question,
                total_deals=0,
                matching_deals=0,
                results=[]
            )
        
        attribute = query_info["attribute"]
        condition = query_info.get("condition", "exists")
        value = query_info.get("value")
        
        # Build TypeQL query
        results = self._execute_cross_deal_query(
            attribute, condition, value, deal_ids
        )
        
        return CrossDealResponse(
            query=question,
            total_deals=results["total"],
            matching_deals=results["matching"],
            results=results["deals"]
        )
    
    def _identify_relevant_attributes(self, question: str) -> List[str]:
        """
        Identify which TypeDB attributes are relevant to the question.
        
        Uses pattern matching and keyword detection.
        """
        question_lower = question.lower()
        relevant = []
        
        # J.Crew related
        if any(kw in question_lower for kw in ["j.crew", "jcrew", "j crew", "trapdoor"]):
            relevant.extend([
                "jcrew_pattern",
                "jcrew_blocker_present",
                "jcrew_blocker_covers_ip",
                "unrestricted_sub_designation_permitted",
                "ip_transfers_to_subs_permitted",
                "ip_definition_includes_trade_secrets",
                "ip_definition_includes_know_how"
            ])
        
        # Sunset related
        if any(kw in question_lower for kw in ["sunset", "expir", "terminat"]):
            relevant.extend([
                "sunset_exists",
                "sunset_period_months",
                "sunset_tied_to_maturity"
            ])
        
        # Threshold related
        if any(kw in question_lower for kw in ["threshold", "basis point", "bps"]):
            relevant.extend([
                "threshold_bps",
                "threshold_applies_to_margin_only"
            ])
        
        # Yield related
        if any(kw in question_lower for kw in ["yield", "oid", "floor", "fee"]):
            relevant.extend([
                "oid_included_in_yield",
                "floor_included_in_yield",
                "upfront_fees_included_in_yield",
                "yield_exclusion_pattern"
            ])
        
        # MFN existence
        if any(kw in question_lower for kw in ["mfn", "most favored", "favoured"]):
            relevant.extend([
                "mfn_exists",
                "mfn_section_reference"
            ])
        
        # Builder basket
        if any(kw in question_lower for kw in ["builder", "cumulative", "basket"]):
            relevant.extend([
                "builder_basket_exists",
                "builder_starter_amount_usd",
                "builder_includes_retained_ecf"
            ])
        
        # Unrestricted subsidiaries
        if any(kw in question_lower for kw in ["unrestricted", "subsidiary", "designation"]):
            relevant.extend([
                "unrestricted_sub_designation_permitted",
                "unrestricted_sub_requires_no_default",
                "unrestricted_sub_has_ebitda_cap"
            ])
        
        # General RP
        if any(kw in question_lower for kw in ["dividend", "restricted payment", "rp "]):
            relevant.extend([
                "rp_exists",
                "dividend_applies_to_holdings",
                "general_dividend_basket_usd"
            ])
        
        # Ratio basket
        if any(kw in question_lower for kw in ["ratio", "leverage", "unlimited"]):
            relevant.extend([
                "ratio_dividend_basket_exists",
                "ratio_dividend_leverage_threshold",
                "ratio_dividend_is_unlimited"
            ])
        
        # If nothing matched, try to be helpful
        if not relevant:
            # Check if asking about patterns
            if "pattern" in question_lower or "risk" in question_lower:
                relevant.extend([
                    "jcrew_pattern",
                    "yield_exclusion_pattern",
                    "weak_mfn_pattern"
                ])
        
        return list(set(relevant))  # Remove duplicates
    
    def _get_primitives_for_attributes(
        self,
        deal_id: str,
        attributes: List[str]
    ) -> List[PrimitiveWithProvenance]:
        """Query TypeDB for specific attribute values."""
        primitives = []
        
        # Get all primitives for this deal
        all_primitives = self.ontology_repo._get_deal_primitives(deal_id)
        provenance_map = self.ontology_repo._get_deal_provenance(deal_id)
        
        for attr in attributes:
            if attr in all_primitives:
                value = all_primitives[attr]
                prov = provenance_map.get(attr)
                
                primitives.append(PrimitiveWithProvenance(
                    attribute_name=attr,
                    value=value,
                    provenance=prov
                ))
        
        return primitives
    
    def _build_answer(
        self,
        question: str,
        primitives: List[PrimitiveWithProvenance]
    ) -> str:
        """Build a natural language answer from primitives."""
        question_lower = question.lower()
        
        # Build fact statements
        facts = []
        for p in primitives:
            # Get human-readable question text
            q_text = self.attribute_map.get(p.attribute_name, p.attribute_name)
            
            if isinstance(p.value, bool):
                value_str = "Yes" if p.value else "No"
            else:
                value_str = str(p.value)
            
            # Add page reference if available
            if p.provenance and p.provenance.source_page:
                facts.append(f"• {q_text}: **{value_str}** (page {p.provenance.source_page})")
            else:
                facts.append(f"• {q_text}: **{value_str}**")
        
        # Build answer based on question type
        if "j.crew" in question_lower or "jcrew" in question_lower:
            jcrew_pattern = next(
                (p for p in primitives if p.attribute_name == "jcrew_pattern"),
                None
            )
            
            if jcrew_pattern:
                if jcrew_pattern.value:
                    answer = "**Yes, this deal has J.Crew pattern risk.**\n\n"
                    answer += "This is based on the following factors:\n\n"
                else:
                    answer = "**No, this deal does not have J.Crew pattern risk.**\n\n"
                    answer += "The relevant factors are:\n\n"
            else:
                answer = "Here's what I found about J.Crew risk factors:\n\n"
            
            answer += "\n".join(facts)
            return answer
        
        if "sunset" in question_lower:
            sunset_exists = next(
                (p for p in primitives if p.attribute_name == "sunset_exists"),
                None
            )
            sunset_months = next(
                (p for p in primitives if p.attribute_name == "sunset_period_months"),
                None
            )
            
            if sunset_exists and sunset_exists.value:
                if sunset_months:
                    return f"**Yes, there is a sunset provision of {sunset_months.value} months.**"
                return "**Yes, there is a sunset provision.**"
            elif sunset_exists:
                return "**No, there is no sunset provision.** MFN protection continues throughout the facility term."
        
        # Default: list all facts
        if len(facts) == 1:
            return facts[0].replace("• ", "")
        
        answer = "Based on the extracted data:\n\n" + "\n".join(facts)
        return answer
    
    def _parse_cross_deal_query(self, question: str) -> Optional[Dict[str, Any]]:
        """Parse a cross-deal query to identify attribute and condition."""
        question_lower = question.lower()
        
        # J.Crew risk
        if "j.crew" in question_lower or "jcrew" in question_lower:
            return {
                "attribute": "jcrew_pattern",
                "condition": "equals",
                "value": True
            }
        
        # Sunset under X months
        match = re.search(r'sunset.*(?:under|less than|<)\s*(\d+)', question_lower)
        if match:
            return {
                "attribute": "sunset_period_months",
                "condition": "less_than",
                "value": int(match.group(1))
            }
        
        # Yield exclusion
        if "yield exclusion" in question_lower or "oid excluded" in question_lower:
            return {
                "attribute": "yield_exclusion_pattern",
                "condition": "equals",
                "value": True
            }
        
        # No MFN
        if "no mfn" in question_lower or "without mfn" in question_lower:
            return {
                "attribute": "mfn_exists",
                "condition": "equals",
                "value": False
            }
        
        # Has MFN
        if "has mfn" in question_lower or "with mfn" in question_lower:
            return {
                "attribute": "mfn_exists",
                "condition": "equals",
                "value": True
            }
        
        return None
    
    def _execute_cross_deal_query(
        self,
        attribute: str,
        condition: str,
        value: Any,
        deal_ids: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Execute a cross-deal TypeQL query."""
        # Build query based on condition
        if condition == "equals":
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            else:
                value_str = str(value)
            
            # Determine which provision type has this attribute
            provision_type = self._get_provision_type(attribute)
            
            query = f"""
                match
                    $d isa deal,
                        has deal_id $id,
                        has deal_name $name;
                    ($d, $p) isa deal_has_provision;
                    $p isa {provision_type},
                        has {attribute} {value_str};
                fetch {{
                    "deal_id": $id,
                    "deal_name": $name
                }};
            """
        
        elif condition == "less_than":
            provision_type = self._get_provision_type(attribute)
            
            query = f"""
                match
                    $d isa deal,
                        has deal_id $id,
                        has deal_name $name;
                    ($d, $p) isa deal_has_provision;
                    $p isa {provision_type},
                        has {attribute} $val;
                    $val < {value};
                fetch {{
                    "deal_id": $id,
                    "deal_name": $name
                }};
            """
        
        else:
            return {"total": 0, "matching": 0, "deals": []}
        
        try:
            with self.client.read_transaction() as tx:
                results = list(tx.query(query).resolve())
                
                # Get total deal count
                total_query = "match $d isa deal; select count($d);"
                total_result = list(tx.query(total_query).resolve())
                total = total_result[0].get("count") if total_result else 0
                
                deals = []
                for row in results:
                    data = row.get("fetch", {})
                    deal_id = data.get("deal_id", "")
                    
                    # Filter by deal_ids if specified
                    if deal_ids and deal_id not in deal_ids:
                        continue
                    
                    deals.append(CrossDealResult(
                        deal_id=deal_id,
                        deal_name=data.get("deal_name", ""),
                        matches=True,
                        relevant_primitives=[]
                    ))
                
                return {
                    "total": total,
                    "matching": len(deals),
                    "deals": deals
                }
                
        except Exception as e:
            logger.error(f"Cross-deal query error: {e}")
            return {"total": 0, "matching": 0, "deals": []}
    
    def _get_provision_type(self, attribute: str) -> str:
        """Determine which provision type owns an attribute."""
        mfn_attributes = {
            "mfn_exists", "mfn_section_reference",
            "sunset_exists", "sunset_period_months", "sunset_tied_to_maturity",
            "threshold_bps", "threshold_applies_to_margin_only",
            "oid_included_in_yield", "floor_included_in_yield",
            "upfront_fees_included_in_yield",
            "covers_term_loan_a", "covers_term_loan_b",
            "covers_incremental_facilities", "covers_ratio_debt",
            "excludes_acquisition_debt", "excludes_bridge_loans",
            "yield_exclusion_pattern", "weak_mfn_pattern"
        }
        
        if attribute in mfn_attributes:
            return "mfn_provision"
        else:
            return "rp_provision"


# Global Q&A engine instance
qa_engine = QAEngine()


def get_qa_engine() -> QAEngine:
    """Dependency injection for Q&A engine."""
    return qa_engine
