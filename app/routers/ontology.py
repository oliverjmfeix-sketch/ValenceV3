"""
Ontology endpoints - Questions from TypeDB (SSoT)
"""
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.typedb_client import typedb_client
from typedb.driver import TransactionType

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
    """Get ontology questions filtered by covenant type (RP, MFN, etc.)."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Category names derived from question_id prefix (e.g., rp_a1 -> A)
    category_names = {
        "A": "Dividend Restrictions - General Structure",
        "B": "Intercompany Dividends",
        "C": "Management Equity Basket",
        "D": "Tax Distribution Basket",
        "E": "Equity Awards",
        "F": "Builder Basket / Cumulative Amount",
        "G": "Ratio-Based Dividend Basket",
        "H": "Holding Company Overhead",
        "I": "Basket Reallocation",
        "J": "Unrestricted Subsidiaries",
        "K": "J.Crew Blocker",
        "S": "Restricted Debt Payments - General",
        "T": "RDP Baskets",
        "Z": "Pattern Detection",
        # MFN categories
        "M": "MFN General",
        "Q": "Legacy Questions",
    }

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Query questions by covenant_type
            query = f"""
                match
                    $q isa ontology_question,
                    has covenant_type "{covenant_type.upper()}",
                    has question_id $id,
                    has question_text $text,
                    has answer_type $type;
                select $id, $text, $type;
            """
            result = tx.query(query).resolve()

            questions = []
            for row in result.as_concept_rows():
                qid = row.get("id").as_attribute().get_value()
                # Extract category from question_id: "rp_a1" -> "A", "mfn_q1" -> "Q"
                parts = qid.split("_")
                if len(parts) >= 2 and len(parts[1]) >= 1:
                    cat_letter = parts[1][0].upper()
                else:
                    cat_letter = "Z"

                questions.append({
                    "question_id": qid,
                    "question_text": row.get("text").as_attribute().get_value(),
                    "answer_type": row.get("type").as_attribute().get_value(),
                    "category_id": cat_letter,
                    "category_name": category_names.get(cat_letter, f"Category {cat_letter}")
                })

            # Sort by category_id then question_id for consistent ordering
            questions.sort(key=lambda q: (q["category_id"], q["question_id"]))

            return {
                "covenant_type": covenant_type.upper(),
                "count": len(questions),
                "questions": questions
            }
        finally:
            tx.close()
    except Exception as e:
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
