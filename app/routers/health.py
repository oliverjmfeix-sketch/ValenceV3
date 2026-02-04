"""
Health check endpoints - Simplified
"""
from typing import Dict, Any
from fastapi import APIRouter
from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Basic health check."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }


@router.get("/api/health")
async def api_health_check() -> Dict[str, Any]:
    """API health check (with /api prefix)."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }


@router.get("/api/debug/schema-check")
async def debug_schema_check() -> Dict[str, Any]:
    """Check if expanded schema types exist."""
    driver = typedb_client.driver
    db_name = settings.typedb_database
    results = {}

    if not driver:
        return {"error": "No TypeDB driver"}

    # Check for extraction_prompt attribute
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = """
            match $t type extraction_prompt;
            select $t;
        """
        result = list(tx.query(query).resolve().as_concept_rows())
        results["extraction_prompt_exists"] = len(result) > 0
        tx.close()
    except Exception as e:
        results["extraction_prompt_exists"] = False
        results["extraction_prompt_error"] = str(e)

    # Check for new concept types
    for concept_type in ["reallocatable_basket", "exempt_sale_type", "unsub_distribution_condition"]:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $t type {concept_type};
                select $t;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"{concept_type}_exists"] = len(result) > 0
            tx.close()
        except Exception as e:
            results[f"{concept_type}_exists"] = False
            results[f"{concept_type}_error"] = str(e)

    # Count questions
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        query = """
            match $q isa ontology_question, has covenant_type "RP";
            select $q;
        """
        result = list(tx.query(query).resolve().as_concept_rows())
        results["rp_question_count"] = len(result)
        tx.close()
    except Exception as e:
        results["rp_question_count_error"] = str(e)

    # Check for new questions
    new_question_ids = ["rp_f9", "rp_l1", "rp_m1", "rp_n1", "rp_i7"]
    for qid in new_question_ids:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $q isa ontology_question, has question_id "{qid}";
                select $q;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"question_{qid}_exists"] = len(result) > 0
            tx.close()
        except Exception as e:
            results[f"question_{qid}_error"] = str(e)

    return results
