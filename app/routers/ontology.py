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
