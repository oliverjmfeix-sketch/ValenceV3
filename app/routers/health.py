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

    # Check if we can insert a question with extraction_prompt
    # This tests if the attribute exists in the schema
    try:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        test_query = """
            insert $q isa ontology_question,
                has question_id "test_extraction_prompt_check",
                has question_text "Test",
                has answer_type "boolean",
                has covenant_type "TEST",
                has display_order 999,
                has extraction_prompt "test prompt";
        """
        tx.query(test_query).resolve()
        # If we get here, extraction_prompt exists - roll back
        tx.close()
        results["extraction_prompt_exists"] = True

        # Delete the test question
        tx = driver.transaction(db_name, TransactionType.WRITE)
        delete_query = """
            match $q isa ontology_question, has question_id "test_extraction_prompt_check";
            delete $q;
        """
        tx.query(delete_query).resolve()
        tx.commit()
    except Exception as e:
        try:
            tx.close()
        except:
            pass
        error_lower = str(e).lower()
        if "extraction_prompt" in error_lower and ("unknown" in error_lower or "does not" in error_lower or "cannot" in error_lower):
            results["extraction_prompt_exists"] = False
            results["extraction_prompt_error"] = "Attribute does not exist in schema"
        else:
            results["extraction_prompt_exists"] = "unknown"
            results["extraction_prompt_error"] = str(e)[:200]

    # Check for new concept types by trying to query instances
    for concept_type in ["reallocatable_basket", "exempt_sale_type"]:
        try:
            tx = driver.transaction(db_name, TransactionType.READ)
            query = f"""
                match $c isa {concept_type};
                select $c;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            results[f"{concept_type}_count"] = len(result)
            tx.close()
        except Exception as e:
            error_lower = str(e).lower()
            if "unknown" in error_lower or "does not exist" in error_lower:
                results[f"{concept_type}_exists"] = False
            else:
                results[f"{concept_type}_error"] = str(e)[:100]

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
