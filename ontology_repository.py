"""
Ontology Repository - TypeDB operations for ontology questions.

IMPORTANT: Questions come from TypeDB (SSoT), NOT hardcoded lists.
This is the single source of truth for what questions exist.
"""
import logging
from typing import List, Dict, Optional, Any
from collections import defaultdict

from app.services.typedb_client import TypeDBClient, get_typedb_client
from app.schemas.models import (
    OntologyQuestion, QuestionWithAnswer, CategoryWithQuestions, Provenance
)

logger = logging.getLogger(__name__)


class OntologyRepository:
    """Repository for ontology questions in TypeDB."""
    
    def __init__(self, client: Optional[TypeDBClient] = None):
        self.client = client or get_typedb_client()
    
    def get_all_questions(self) -> List[OntologyQuestion]:
        """
        Get all ontology questions from TypeDB.
        
        This is the SSoT - questions are NOT hardcoded in Python.
        """
        query = """
            match
                $q isa ontology_question,
                    has question_id $id,
                    has question_text $text,
                    has question_category $cat,
                    has category_order $cat_order,
                    has question_order $q_order,
                    has target_attribute $attr,
                    has answer_type $type;
            fetch {
                "question_id": $id,
                "question_text": $text,
                "question_category": $cat,
                "category_order": $cat_order,
                "question_order": $q_order,
                "target_attribute": $attr,
                "answer_type": $type
            };
        """
        
        try:
            with self.client.read_transaction() as tx:
                results = list(tx.query(query).resolve())
                
                questions = []
                for row in results:
                    data = row.get("fetch", {})
                    questions.append(OntologyQuestion(
                        question_id=data.get("question_id", ""),
                        question_text=data.get("question_text", ""),
                        question_category=data.get("question_category", ""),
                        category_order=data.get("category_order", 0),
                        question_order=data.get("question_order", 0),
                        target_attribute=data.get("target_attribute", ""),
                        answer_type=data.get("answer_type", "string")
                    ))
                
                # Sort by category order, then question order
                questions.sort(key=lambda q: (q.category_order, q.question_order))
                
                return questions
                
        except Exception as e:
            logger.error(f"Error getting ontology questions: {e}")
            return []
    
    def get_questions_by_category(self) -> Dict[str, List[OntologyQuestion]]:
        """Get questions grouped by category."""
        questions = self.get_all_questions()
        
        by_category = defaultdict(list)
        for q in questions:
            by_category[q.question_category].append(q)
        
        return dict(by_category)
    
    def get_categories(self) -> List[Dict[str, Any]]:
        """Get list of categories with metadata."""
        questions = self.get_all_questions()
        
        categories = {}
        for q in questions:
            if q.question_category not in categories:
                categories[q.question_category] = {
                    "name": q.question_category,
                    "order": q.category_order,
                    "count": 0
                }
            categories[q.question_category]["count"] += 1
        
        # Sort by order
        return sorted(categories.values(), key=lambda c: c["order"])
    
    def get_questions_with_answers(
        self, 
        deal_id: str
    ) -> List[CategoryWithQuestions]:
        """
        Get all questions with answers for a specific deal.
        
        This powers the ontology browser in the UI.
        """
        questions = self.get_all_questions()
        
        # Get deal primitives
        primitives = self._get_deal_primitives(deal_id)
        
        # Get provenance for deal
        provenance_map = self._get_deal_provenance(deal_id)
        
        # Group by category
        categories_map: Dict[str, CategoryWithQuestions] = {}
        
        for q in questions:
            if q.question_category not in categories_map:
                categories_map[q.question_category] = CategoryWithQuestions(
                    category_name=q.question_category,
                    category_order=q.category_order,
                    questions=[]
                )
            
            # Get answer from primitives
            answer = primitives.get(q.target_attribute)
            
            # Get provenance
            prov = provenance_map.get(q.target_attribute)
            
            categories_map[q.question_category].questions.append(
                QuestionWithAnswer(
                    question_id=q.question_id,
                    question_text=q.question_text,
                    question_category=q.question_category,
                    category_order=q.category_order,
                    question_order=q.question_order,
                    target_attribute=q.target_attribute,
                    answer_type=q.answer_type,
                    answer=answer,
                    provenance=prov
                )
            )
        
        # Sort categories by order
        result = list(categories_map.values())
        result.sort(key=lambda c: c.category_order)
        
        return result
    
    def _get_deal_primitives(self, deal_id: str) -> Dict[str, Any]:
        """Get all primitives for a deal."""
        primitives = {}
        
        # Get MFN primitives
        mfn_query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                $p isa mfn_provision;
            select $p;
        """
        
        try:
            with self.client.read_transaction() as tx:
                result = list(tx.query(mfn_query).resolve())
                
                if result:
                    provision = result[0].get("p")
                    if provision:
                        for attr in provision.get_has():
                            attr_type = attr.get_type().get_label().name
                            primitives[attr_type] = attr.get_value()
                
                # Get RP primitives
                rp_query = f"""
                    match
                        $d isa deal, has deal_id "{deal_id}";
                        ($d, $p) isa deal_has_provision;
                        $p isa rp_provision;
                    select $p;
                """
                
                result = list(tx.query(rp_query).resolve())
                
                if result:
                    provision = result[0].get("p")
                    if provision:
                        for attr in provision.get_has():
                            attr_type = attr.get_type().get_label().name
                            primitives[attr_type] = attr.get_value()
                
                return primitives
                
        except Exception as e:
            logger.error(f"Error getting deal primitives: {e}")
            return {}
    
    def _get_deal_provenance(self, deal_id: str) -> Dict[str, Provenance]:
        """Get all provenance for a deal."""
        query = f"""
            match
                $d isa deal, has deal_id "{deal_id}";
                ($d, $p) isa deal_has_provision;
                ($p, $prov) isa has_provenance;
                $prov has attribute_name $attr,
                    has source_text $text,
                    has source_page $page;
            fetch {{
                "attribute_name": $attr,
                "source_text": $text,
                "source_page": $page
            }};
        """
        
        provenance_map = {}
        
        try:
            with self.client.read_transaction() as tx:
                results = list(tx.query(query).resolve())
                
                for row in results:
                    data = row.get("fetch", {})
                    attr_name = data.get("attribute_name", "")
                    
                    provenance_map[attr_name] = Provenance(
                        attribute_name=attr_name,
                        source_text=data.get("source_text", ""),
                        source_page=data.get("source_page", 0),
                        extraction_confidence="high"
                    )
                
                return provenance_map
                
        except Exception as e:
            logger.error(f"Error getting deal provenance: {e}")
            return {}
    
    def get_attribute_mapping(self) -> Dict[str, str]:
        """
        Get mapping from target_attribute to question_text.
        
        Useful for Q&A engine to explain answers.
        """
        questions = self.get_all_questions()
        return {q.target_attribute: q.question_text for q in questions}


# Global repository instance
ontology_repository = OntologyRepository()


def get_ontology_repository() -> OntologyRepository:
    """Dependency injection for ontology repository."""
    return ontology_repository
