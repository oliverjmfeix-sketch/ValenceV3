"""
Deal endpoints - Simplified for V3 launch (TypeDB 3.x API)
"""
import os
import asyncio
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks

from app.config import settings
from app.services.typedb_client import typedb_client
from app.services.extraction import get_extraction_service
from app.schemas.models import UploadResponse, ExtractionStatus
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Deals"])

# In-memory extraction status tracking (for MVP; use Redis in production)
extraction_status: Dict[str, ExtractionStatus] = {}

# Ensure uploads directory exists
UPLOADS_DIR = "/app/uploads"
os.makedirs(UPLOADS_DIR, exist_ok=True)


@router.get("")
async def list_deals() -> List[Dict[str, Any]]:
    """List all deals."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = """
                match 
                    $d isa deal, 
                    has deal_id $id, 
                    has deal_name $name;
                select $id, $name;
            """
            result = tx.query(query).resolve()
            
            deals = []
            for row in result.as_concept_rows():
                deals.append({
                    "deal_id": row.get("id").as_attribute().get_value(),
                    "deal_name": row.get("name").as_attribute().get_value()
                })
            return deals
        finally:
            tx.close()
    except Exception as e:
        logger.error(f"Error listing deals: {e}")
        return []


@router.get("/{deal_id}")
async def get_deal(deal_id: str) -> Dict[str, Any]:
    """Get a single deal."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"""
                match 
                    $d isa deal, 
                    has deal_id "{deal_id}", 
                    has deal_name $name;
                select $name;
            """
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            
            if not rows:
                raise HTTPException(status_code=404, detail="Deal not found")
            
            return {
                "deal_id": deal_id,
                "deal_name": rows[0].get("name").as_attribute().get_value(),
                "answers": {},
                "applicabilities": {}
            }
        finally:
            tx.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting deal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_deal(
    deal_name: str = Form(...),
    borrower: str = Form(...)
) -> Dict[str, Any]:
    """Create a new deal (without PDF for now)."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    deal_id = str(uuid.uuid4())[:8]
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            query = f"""
                insert 
                    $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{deal_name}",
                    has borrower_name "{borrower}";
            """
            tx.query(query).resolve()
            tx.commit()
            
            return {
                "deal_id": deal_id,
                "deal_name": deal_name,
                "borrower": borrower,
                "status": "created"
            }
        except Exception as e:
            tx.close()
            raise e
    except Exception as e:
        logger.error(f"Error creating deal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{deal_id}")
async def delete_deal(deal_id: str) -> Dict[str, Any]:
    """
    Delete a deal and all related data:
    1. Delete concept_applicability relations
    2. Delete rp_provision entity
    3. Delete mfn_provision entity
    4. Delete deal_has_provision relations
    5. Delete deal entity
    6. Delete PDF file from disk
    7. Clear extraction status
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            # 1. Delete concept_applicability relations for RP provision
            try:
                tx.query(f"""
                    match
                        $p isa rp_provision, has provision_id "{deal_id}_rp";
                        $rel (provision: $p, concept: $c) isa concept_applicability;
                    delete $rel;
                """).resolve()
            except Exception:
                pass  # May not exist

            # 2. Delete rp_provision
            try:
                tx.query(f"""
                    match $p isa rp_provision, has provision_id "{deal_id}_rp";
                    delete $p;
                """).resolve()
            except Exception:
                pass  # May not exist

            # 3. Delete mfn_provision (if exists)
            try:
                tx.query(f"""
                    match $p isa mfn_provision, has provision_id "{deal_id}_mfn";
                    delete $p;
                """).resolve()
            except Exception:
                pass  # May not exist

            # 4. Delete deal_has_provision relations
            try:
                tx.query(f"""
                    match
                        $d isa deal, has deal_id "{deal_id}";
                        $rel (deal: $d, provision: $p) isa deal_has_provision;
                    delete $rel;
                """).resolve()
            except Exception:
                pass  # May not exist

            # 5. Delete deal entity
            tx.query(f"""
                match $d isa deal, has deal_id "{deal_id}";
                delete $d;
            """).resolve()

            tx.commit()
            logger.info(f"Deleted deal {deal_id} from TypeDB")

        except Exception as e:
            tx.close()
            raise e

        # 6. Delete PDF file
        pdf_path = Path(UPLOADS_DIR) / f"{deal_id}.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
            logger.info(f"Deleted PDF: {pdf_path}")

        # 7. Clear extraction status
        if deal_id in extraction_status:
            del extraction_status[deal_id]

        return {"status": "deleted", "deal_id": deal_id}

    except Exception as e:
        logger.error(f"Error deleting deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def run_extraction(deal_id: str, pdf_path: str):
    """
    Background task: extract RP provision from PDF and store in TypeDB.

    Uses the simplified 5-step pipeline:
    1. Parse PDF
    2. Extract RP content (ONE Claude call)
    3. Load questions from TypeDB
    4. Answer questions by category
    5. Store results to TypeDB (automatic)
    """
    extraction_svc = get_extraction_service()

    try:
        # Update status: extracting content
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="extracting",
            progress=10,
            current_step="Parsing PDF and extracting RP content..."
        )

        # Run the simplified extraction pipeline
        # This handles: parse → extract content → load questions → answer → store
        result = await extraction_svc.extract_rp_provision(
            pdf_path=pdf_path,
            deal_id=deal_id,
            store_results=True  # Automatically stores to TypeDB
        )

        # Count answers for status message
        total_answers = sum(
            len(cat.answers) for cat in result.category_answers
        )
        baskets_found = len(result.extracted_content.permitted_baskets)
        definitions_found = len(result.extracted_content.definitions)

        # Update status: complete
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="complete",
            progress=100,
            current_step=f"Extracted {total_answers} answers, {baskets_found} baskets, {definitions_found} definitions in {result.extraction_time_seconds:.1f}s"
        )

        logger.info(
            f"Extraction complete for deal {deal_id}: "
            f"{total_answers} answers in {result.extraction_time_seconds:.1f}s"
        )

    except Exception as e:
        logger.error(f"Extraction failed for deal {deal_id}: {e}")
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="error",
            progress=0,
            current_step=None,
            error=str(e)
        )


@router.post("/upload", response_model=UploadResponse)
async def upload_deal(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    deal_name: str = Form(...),
    borrower: str = Form(...)
) -> UploadResponse:
    """
    Upload a credit agreement PDF and trigger extraction.

    Returns immediately with deal_id; extraction runs in background.
    Use GET /api/deals/{deal_id}/status to check extraction progress.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Generate deal ID
    deal_id = str(uuid.uuid4())[:8]
    pdf_filename = f"{deal_id}.pdf"
    pdf_path = os.path.join(UPLOADS_DIR, pdf_filename)

    try:
        # Save PDF to disk
        contents = await file.read()
        with open(pdf_path, "wb") as f:
            f.write(contents)

        logger.info(f"Saved PDF: {pdf_path} ({len(contents)} bytes)")

        # Create deal in TypeDB (using same pattern as create_deal endpoint)
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            # Escape quotes in strings
            safe_name = deal_name.replace('"', '\\"')
            safe_borrower = borrower.replace('"', '\\"')

            query = f"""
                insert
                    $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{safe_name}",
                    has borrower_name "{safe_borrower}";
            """
            tx.query(query).resolve()
            tx.commit()
        except Exception as e:
            tx.close()
            raise e

        logger.info(f"Created deal in TypeDB: {deal_id}")

        # Initialize extraction status
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="pending",
            progress=0,
            current_step="Queued for extraction"
        )

        # Kick off background extraction
        background_tasks.add_task(run_extraction, deal_id, pdf_path)

        return UploadResponse(
            deal_id=deal_id,
            deal_name=deal_name,
            status="processing",
            message="PDF uploaded. Extraction started in background."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        # Clean up on failure
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/status", response_model=ExtractionStatus)
async def get_extraction_status(deal_id: str) -> ExtractionStatus:
    """Get the extraction status for a deal."""
    if deal_id in extraction_status:
        return extraction_status[deal_id]

    # Check if deal exists but extraction never started
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                select $d;
            """
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())

            if rows:
                return ExtractionStatus(
                    deal_id=deal_id,
                    status="complete",
                    progress=100,
                    current_step="Extraction completed (status not tracked)"
                )
        finally:
            tx.close()
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="Deal not found")


@router.get("/{deal_id}/answers")
async def get_deal_answers(deal_id: str) -> Dict[str, Any]:
    """Get all extracted answers (provision attributes) for a deal."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Check deal exists
            deal_query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                select $d;
            """
            deal_result = tx.query(deal_query).resolve()
            if not list(deal_result.as_concept_rows()):
                raise HTTPException(status_code=404, detail="Deal not found")

            answers = {}

            # Get RP provision attributes via direct query (TypeDB 3.x compatible)
            rp_attrs_query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    (deal: $d, provision: $p) isa deal_has_provision;
                    $p isa rp_provision, has $attr;
                select $attr;
            """
            rp_result = tx.query(rp_attrs_query).resolve()
            for row in rp_result.as_concept_rows():
                attr = row.get("attr").as_attribute()
                attr_type = attr.get_type().get_label()
                if attr_type != "provision_id":
                    answers[attr_type] = attr.get_value()

            # Get MFN provision attributes
            mfn_attrs_query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    (deal: $d, provision: $p) isa deal_has_provision;
                    $p isa mfn_provision, has $attr;
                select $attr;
            """
            mfn_result = tx.query(mfn_attrs_query).resolve()
            for row in mfn_result.as_concept_rows():
                attr = row.get("attr").as_attribute()
                attr_type = attr.get_type().get_label()
                if attr_type != "provision_id":
                    answers[attr_type] = attr.get_value()

            return {
                "deal_id": deal_id,
                "answers": answers,
                "count": len(answers)
            }

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting answers for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/rp-provision")
async def get_rp_provision(deal_id: str) -> Dict[str, Any]:
    """
    Get the RP provision for a deal with all scalar attributes and concept applicabilities.

    Returns:
        - provision_id
        - scalar_answers: all boolean/string/number attributes
        - multiselect_answers: concept_applicability relations grouped by concept type
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    provision_id = f"{deal_id}_rp"

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Check if provision exists
            check_query = f"""
                match $p isa rp_provision, has provision_id "{provision_id}";
                select $p;
            """
            check_result = tx.query(check_query).resolve()
            if not list(check_result.as_concept_rows()):
                raise HTTPException(status_code=404, detail="RP provision not found for this deal")

            # Get all scalar attributes
            scalar_answers = {}
            attrs_query = f"""
                match
                    $p isa rp_provision, has provision_id "{provision_id}";
                    $p has $attr;
                select $attr;
            """
            attrs_result = tx.query(attrs_query).resolve()
            for row in attrs_result.as_concept_rows():
                attr = row.get("attr").as_attribute()
                attr_type = attr.get_type().get_label()
                if attr_type != "provision_id":
                    scalar_answers[attr_type] = attr.get_value()

            # Get all concept applicabilities (multiselect answers)
            multiselect_answers = {}
            applicability_query = f"""
                match
                    $p isa rp_provision, has provision_id "{provision_id}";
                    (provision: $p, concept: $c) isa concept_applicability;
                    $c has concept_id $cid, has name $cname;
                select $c, $cid, $cname;
            """
            applicability_result = tx.query(applicability_query).resolve()
            for row in applicability_result.as_concept_rows():
                concept = row.get("c").as_entity()
                concept_type = concept.get_type().get_label()
                concept_id = row.get("cid").as_attribute().get_value()
                concept_name = row.get("cname").as_attribute().get_value()

                if concept_type not in multiselect_answers:
                    multiselect_answers[concept_type] = []
                multiselect_answers[concept_type].append({
                    "concept_id": concept_id,
                    "name": concept_name
                })

            return {
                "deal_id": deal_id,
                "provision_id": provision_id,
                "provision_type": "rp_provision",
                "scalar_answers": scalar_answers,
                "multiselect_answers": multiselect_answers,
                "scalar_count": len(scalar_answers),
                "multiselect_count": sum(len(v) for v in multiselect_answers.values())
            }

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting RP provision for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{deal_id}/qa")
async def deal_qa(deal_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Q&A endpoint for asking questions about a deal.
    For MVP, returns extracted answers that match the question.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    question = request.get("question", "")
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    try:
        # Get all answers for context
        answers_response = await get_deal_answers(deal_id)
        answers = answers_response.get("answers", {})

        # For MVP: return relevant answers based on keyword matching
        # In production, this would use Claude for semantic Q&A
        relevant = {}
        question_lower = question.lower()

        # Simple keyword matching for common queries
        keyword_map = {
            "mfn": ["mfn_exists", "mfn_applies_to", "threshold_bps", "sunset"],
            "dividend": ["general_dividend_prohibition_exists", "ratio_dividend_basket"],
            "builder": ["builder_basket_exists", "builder_starter", "builder_uses_greater_of"],
            "jcrew": ["jcrew_blocker_exists", "blocker_covers", "unsub_designation"],
            "blocker": ["jcrew_blocker_exists", "blocker_covers", "blocker_binds", "blocker_is_sacred_right"],
            "tax": ["tax_distribution_basket_exists", "tax_standalone_taxpayer_limit"],
            "ip": ["blocker_covers_ip", "ip_transfers", "ip_licensing_restricted"],
            "ratio": ["ratio_dividend_basket", "ratio_leverage_threshold", "ratio_interest_coverage"],
            "management": ["mgmt_equity_basket_exists", "mgmt_equity_annual_cap"],
        }

        for keyword, fields in keyword_map.items():
            if keyword in question_lower:
                for field in fields:
                    for answer_key, answer_val in answers.items():
                        if field in answer_key:
                            relevant[answer_key] = answer_val

        # If no keyword match, return all answers
        if not relevant:
            relevant = answers

        return {
            "deal_id": deal_id,
            "question": question,
            "answer": f"Based on the extracted data, here are the relevant findings:",
            "relevant_fields": relevant,
            "total_extracted": len(answers)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Q&A error for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
