"""
Ontology endpoints - Questions from TypeDB (SSoT)
"""
import logging
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.typedb_client import typedb_client
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ontology", tags=["Ontology"])


@router.get("/categories")
async def get_categories() -> List[Dict[str, Any]]:
    """Get all ontology categories."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = """
                match
                    $c isa ontology_category,
                    has category_id $id,
                    has name $name;
                select $id, $name;
            """
            result = tx.query(query).resolve()

            categories = []
            for row in result.as_concept_rows():
                categories.append({
                    "category_id": row.get("id").as_attribute().get_value(),
                    "name": row.get("name").as_attribute().get_value()
                })
            return categories
        finally:
            tx.close()
    except Exception as e:
        return []


@router.get("/questions")
async def get_questions() -> List[Dict[str, Any]]:
    """Get all ontology questions."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = """
                match
                    $q isa ontology_question,
                    has question_id $id,
                    has question_text $text,
                    has answer_type $type;
                select $id, $text, $type;
            """
            result = tx.query(query).resolve()

            questions = []
            for row in result.as_concept_rows():
                questions.append({
                    "question_id": row.get("id").as_attribute().get_value(),
                    "question_text": row.get("text").as_attribute().get_value(),
                    "answer_type": row.get("type").as_attribute().get_value()
                })
            return questions
        finally:
            tx.close()
    except Exception as e:
        return []


@router.get("/questions/{covenant_type}")
async def get_questions_by_type(covenant_type: str) -> Dict[str, Any]:
    """
    Get ontology questions filtered by covenant type (RP, MFN, etc.).

    SSoT: Category names come from TypeDB via category_has_question relations.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Query questions WITH their categories via category_has_question relation
            query = f"""
                match
                    $q isa ontology_question,
                        has covenant_type "{covenant_type.upper()}",
                        has question_id $qid,
                        has question_text $qtext,
                        has answer_type $atype;
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid, has name $cname;
                select $qid, $qtext, $atype, $cid, $cname;
            """
            result = tx.query(query).resolve()

            questions = []
            for row in result.as_concept_rows():
                questions.append({
                    "question_id": row.get("qid").as_attribute().get_value(),
                    "question_text": row.get("qtext").as_attribute().get_value(),
                    "answer_type": row.get("atype").as_attribute().get_value(),
                    "category_id": row.get("cid").as_attribute().get_value(),
                    "category_name": row.get("cname").as_attribute().get_value()
                })

            # If no results from join, fall back to questions without categories
            if not questions:
                logger.warning(f"No category_has_question relations found for {covenant_type}, falling back")
                fallback_query = f"""
                    match
                        $q isa ontology_question,
                            has covenant_type "{covenant_type.upper()}",
                            has question_id $qid,
                            has question_text $qtext,
                            has answer_type $atype;
                    select $qid, $qtext, $atype;
                """
                result = tx.query(fallback_query).resolve()
                for row in result.as_concept_rows():
                    qid = row.get("qid").as_attribute().get_value()
                    # Derive category from question_id as last resort
                    parts = qid.split("_")
                    cat_letter = parts[1][0].upper() if len(parts) >= 2 and len(parts[1]) >= 1 else "Z"
                    questions.append({
                        "question_id": qid,
                        "question_text": row.get("qtext").as_attribute().get_value(),
                        "answer_type": row.get("atype").as_attribute().get_value(),
                        "category_id": cat_letter,
                        "category_name": f"Category {cat_letter}"  # Fallback name
                    })

            # Sort by category_id then question_id
            questions.sort(key=lambda q: (q["category_id"], q["question_id"]))

            return {
                "covenant_type": covenant_type.upper(),
                "count": len(questions),
                "questions": questions
            }
        finally:
            tx.close()
    except Exception as e:
        logger.error(f"Error fetching questions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/concepts")
async def get_concepts() -> Dict[str, List[Dict[str, Any]]]:
    """Get all concepts grouped by type."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = """
                match
                    $c isa concept,
                    has concept_id $id,
                    has name $name;
                select $c, $id, $name;
            """
            result = tx.query(query).resolve()

            concepts = {}
            for row in result.as_concept_rows():
                concept_type = row.get("c").as_entity().get_type().get_label()
                if concept_type not in concepts:
                    concepts[concept_type] = []
                concepts[concept_type].append({
                    "concept_id": row.get("id").as_attribute().get_value(),
                    "name": row.get("name").as_attribute().get_value()
                })
            return concepts
        finally:
            tx.close()
    except Exception as e:
        return {}
