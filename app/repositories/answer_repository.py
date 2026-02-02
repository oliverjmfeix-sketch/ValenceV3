"""
Answer Repository - V3 with Provenance

Stores extracted answers as provision_has_answer relations.
Each answer includes:
- Typed value (boolean, integer, double, string)
- Source text (verbatim quote from document)
- Source page number
- Source section reference
- Extraction confidence
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


class AnswerRepository:
    """Repository for extracted answers with provenance."""
    
    def save_answer(
        self,
        provision_id: str,
        question_id: str,
        value: Any,
        answer_type: str,
        source_text: str = "",
        source_page: int = 0,
        source_section: str = "",
        confidence: str = "high"
    ) -> Optional[str]:
        """
        Save an extracted answer with provenance.
        
        Args:
            provision_id: ID of the provision (mfn or rp)
            question_id: ID of the ontology question
            value: The extracted value
            answer_type: Type of answer (boolean, integer, double, string)
            source_text: Verbatim quote from document
            source_page: Page number in PDF
            source_section: Section reference (e.g., "Section 6.06(a)")
            confidence: Extraction confidence (high/medium/low)
            
        Returns:
            answer_id if successful, None otherwise
        """
        answer_id = f"{provision_id}_{question_id}"
        
        # Build typed answer attribute
        if answer_type == "boolean":
            if isinstance(value, str):
                value = value.lower() in ("true", "yes", "1")
            answer_attr = f"has answer_boolean {str(bool(value)).lower()}"
        elif answer_type == "integer":
            answer_attr = f"has answer_integer {int(value)}"
        elif answer_type in ("double", "currency", "percentage"):
            answer_attr = f"has answer_double {float(value)}"
        else:
            escaped = str(value).replace('"', '\\"').replace('\n', ' ')
            answer_attr = f'has answer_string "{escaped}"'
        
        # Escape source text
        escaped_source = source_text.replace('"', '\\"').replace('\n', ' ')[:500]
        escaped_section = source_section.replace('"', '\\"')
        
        query = f"""
            match
                $p isa provision, has provision_id "{provision_id}";
                $q isa ontology_question, has question_id "{question_id}";
            insert
                (provision: $p, question: $q) isa provision_has_answer,
                    has answer_id "{answer_id}",
                    {answer_attr},
                    has source_text "{escaped_source}",
                    has source_page {source_page},
                    has source_section "{escaped_section}",
                    has confidence "{confidence}",
                    has extracted_at {datetime.utcnow().isoformat()};
        """
        
        try:
            typedb_client.query_write(query)
            logger.debug(f"Saved answer: {answer_id}")
            return answer_id
        except Exception as e:
            logger.error(f"Failed to save answer {answer_id}: {e}")
            return None
    
    def save_answers_batch(
        self,
        provision_id: str,
        answers: List[Dict[str, Any]]
    ) -> int:
        """
        Save multiple answers from extraction.
        
        Args:
            provision_id: ID of the provision
            answers: List of answer dicts with keys:
                - question_id
                - value
                - answer_type (optional, inferred if missing)
                - source_text
                - source_page
                - source_section
                - confidence
                
        Returns:
            Number of answers saved successfully
        """
        saved = 0
        for answer in answers:
            try:
                # Infer answer_type if not provided
                answer_type = answer.get("answer_type")
                if not answer_type:
                    value = answer.get("value")
                    if isinstance(value, bool):
                        answer_type = "boolean"
                    elif isinstance(value, int):
                        answer_type = "integer"
                    elif isinstance(value, float):
                        answer_type = "double"
                    else:
                        answer_type = "string"
                
                result = self.save_answer(
                    provision_id=provision_id,
                    question_id=answer["question_id"],
                    value=answer.get("value"),
                    answer_type=answer_type,
                    source_text=answer.get("source_text", ""),
                    source_page=answer.get("source_page", 0),
                    source_section=answer.get("source_section", ""),
                    confidence=answer.get("confidence", "medium")
                )
                if result:
                    saved += 1
            except Exception as e:
                logger.error(f"Error saving answer {answer.get('question_id')}: {e}")
        
        logger.info(f"Saved {saved}/{len(answers)} answers for {provision_id}")
        return saved
    
    def get_answers_for_provision(self, provision_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all answers for a provision, keyed by question_id.
        
        Returns dict like:
        {
            "q1": {
                "value": True,
                "source_text": "...",
                "source_page": 42,
                "confidence": "high"
            },
            ...
        }
        """
        # Get answers with provenance
        query = f"""
            match
                $p isa provision, has provision_id "{provision_id}";
                (provision: $p, question: $q) isa provision_has_answer,
                    has answer_id $aid,
                    has source_text $src,
                    has source_page $page,
                    has source_section $section,
                    has confidence $conf;
                $q has question_id $qid;
            select $qid, $aid, $src, $page, $section, $conf;
        """
        
        try:
            results = typedb_client.query_read(query)
            
            answers = {}
            for r in results:
                qid = r["qid"]
                aid = r["aid"]
                
                # Get the actual value
                value = self._get_answer_value(aid)
                
                answers[qid] = {
                    "answer_id": aid,
                    "value": value,
                    "source_text": r["src"],
                    "source_page": r["page"],
                    "source_section": r["section"],
                    "confidence": r["conf"]
                }
            
            return answers
            
        except Exception as e:
            logger.error(f"Error getting answers for {provision_id}: {e}")
            return {}
    
    def get_answers_for_deal(self, deal_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all answers for a deal (across all provisions).
        """
        query = f"""
            match
                $deal isa deal, has deal_id "{deal_id}";
                (deal: $deal, provision: $p) isa deal_has_provision;
                (provision: $p, question: $q) isa provision_has_answer,
                    has answer_id $aid,
                    has source_text $src,
                    has source_page $page,
                    has confidence $conf;
                $q has question_id $qid;
            select $qid, $aid, $src, $page, $conf;
        """
        
        try:
            results = typedb_client.query_read(query)
            
            answers = {}
            for r in results:
                qid = r["qid"]
                aid = r["aid"]
                value = self._get_answer_value(aid)
                
                answers[qid] = {
                    "value": value,
                    "source_text": r["src"],
                    "source_page": r["page"],
                    "confidence": r["conf"]
                }
            
            return answers
            
        except Exception as e:
            logger.error(f"Error getting answers for deal {deal_id}: {e}")
            return {}
    
    def _get_answer_value(self, answer_id: str) -> Any:
        """Get the typed value from an answer relation."""
        # Try each value type
        for attr, converter in [
            ("answer_boolean", bool),
            ("answer_integer", int),
            ("answer_double", float),
            ("answer_string", str)
        ]:
            query = f"""
                match
                    $a isa provision_has_answer, has answer_id "{answer_id}";
                    $a has {attr} $val;
                select $val;
            """
            try:
                results = typedb_client.query_read(query)
                if results:
                    return results[0]["val"]
            except Exception:
                continue
        
        return None
    
    def delete_answers_for_provision(self, provision_id: str) -> int:
        """Delete all answers for a provision (for re-extraction)."""
        query = f"""
            match
                $p isa provision, has provision_id "{provision_id}";
                (provision: $p) isa $answer;
                $answer isa provision_has_answer;
            delete $answer;
        """
        try:
            typedb_client.query_write(query)
            logger.info(f"Deleted answers for {provision_id}")
            return 1
        except Exception as e:
            logger.error(f"Error deleting answers: {e}")
            return 0


# Dependency injection
def get_answer_repository() -> AnswerRepository:
    """Get answer repository instance."""
    return AnswerRepository()
