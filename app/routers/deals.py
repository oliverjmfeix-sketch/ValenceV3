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
from pydantic import BaseModel
import anthropic
import re

from app.config import settings
from app.services.typedb_client import typedb_client
from app.services.extraction import get_extraction_service
from app.schemas.models import UploadResponse, ExtractionStatus
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Deals"])


def _safe_get_value(row, key: str, default=None):
    """Safely get attribute value from a TypeDB row with null check."""
    try:
        concept = row.get(key)
        if concept is None:
            return default
        return concept.as_attribute().get_value()
    except Exception:
        return default


def _safe_get_entity(row, key: str):
    """Safely get entity from a TypeDB row with null check."""
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_entity()
    except Exception:
        return None


def _query_relation_attr(tx, provision_id: str, attr_name: str) -> Dict[str, Any]:
    """Query a single attribute from provision_has_answer using anonymous relation pattern.

    TypeDB 3.x can't mix relation variable + attribute access (Object vs ThingType conflict).
    Anonymous relation with inline `has` avoids the conflict.
    """
    query = f"""
        match
            $p isa provision, has provision_id "{provision_id}";
            $q has question_id $qid;
            (provision: $p, question: $q) isa provision_has_answer,
                has {attr_name} $val;
        select $qid, $val;
    """
    result = tx.query(query).resolve()
    answers = {}
    for row in result.as_concept_rows():
        qid = _safe_get_value(row, "qid")
        val = _safe_get_value(row, "val")
        if qid is not None and val is not None:
            answers[qid] = val
    return answers


def _load_provision_answers(tx, provision_id: str) -> Dict[str, Dict]:
    """Load all scalar answers for a provision, returning {qid: {value, source_text, source_page, confidence}}."""
    stored = {}

    # Get values — each answer has exactly one type
    for attr in ("answer_boolean", "answer_string", "answer_integer", "answer_double", "answer_date"):
        for qid, val in _query_relation_attr(tx, provision_id, attr).items():
            stored.setdefault(qid, {})["value"] = val

    # Get provenance fields
    for attr, key in [("source_text", "source_text"), ("source_page", "source_page"), ("source_section", "source_section"), ("confidence", "confidence")]:
        for qid, val in _query_relation_attr(tx, provision_id, attr).items():
            if qid in stored:
                stored[qid][key] = val

    return stored


# Request/Response models for Q&A
class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: List[Dict[str, Any]]

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
                deal_id = _safe_get_value(row, "id")
                deal_name = _safe_get_value(row, "name")
                if deal_id:  # Only add if we have a valid ID
                    deals.append({
                        "deal_id": deal_id,
                        "deal_name": deal_name or "Unknown"
                    })
            return deals
        finally:
            tx.close()
    except Exception as e:
        logger.error(f"Error listing deals: {e}")
        return []


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


@router.post("/test-v4-extraction")
async def test_v4_extraction(store: bool = False, deal_id: str = "test_v4") -> Dict[str, Any]:
    """
    Test V4 extraction pipeline with sample RP covenant text.

    Skips PDF parsing and RP Universe extraction.
    Tests: metadata loading, prompt building, Claude call, Pydantic parsing.

    Args:
        store: If True, store to TypeDB and verify graph structure
        deal_id: Deal ID to use for storage (default: test_v4)
    """
    import time
    start = time.time()

    # Sample Duck Creek RP covenant (representative excerpt)
    sample_rp_text = '''
=== DEFINITIONS ===

"Available Amount" means, at any date of determination, an amount equal to (without duplication):
(a) the greater of (x) $130,000,000 and (y) 100% of EBITDA for the Test Period then most recently ended; plus
(b) 50% of Consolidated Net Income for the period from the first day of the fiscal quarter in which the Closing Date occurs to the end of the most recently ended fiscal quarter (or 100% of any deficit); plus
(c) the Retained ECF Amount for the most recently ended fiscal year; plus
(d) the greater of (x) EBITDA minus 1.40 times Fixed Charges for the most recently ended Test Period; plus
(e) 100% of the Net Cash Proceeds from any Equity Issuance after the Closing Date; plus
(f) returns on Investments made using the Available Amount.

"Consolidated Net Income" means, for any period, the net income (or loss) of Holdings and its Restricted Subsidiaries...

"First Lien Leverage Ratio" means, as of any date, the ratio of (a) Consolidated First Lien Debt as of such date to (b) EBITDA for the Test Period most recently ended.

=== DIVIDEND/RESTRICTED PAYMENT COVENANT ===

Section 6.06 Restricted Payments.

(a) The Borrower will not, and will not permit any Restricted Subsidiary to, declare or make any Restricted Payment except:

(f) [Builder Basket] the Borrower may make Restricted Payments in an aggregate amount not to exceed the Available Amount at the time of such payment, so long as (i) no Default exists or would result therefrom and (ii) after giving pro forma effect thereto, the Total Leverage Ratio would not exceed 6.50 to 1.00;

(j) [General RP Basket] the Borrower may make Restricted Payments in an aggregate amount not to exceed the greater of (x) $130,000,000 and (y) 100% of EBITDA;

(n) [Ratio Basket] the Borrower may make Restricted Payments without limit if, after giving pro forma effect thereto, the First Lien Leverage Ratio would not exceed 5.75 to 1.00;

(o) [No Worse Test] the Borrower may make Restricted Payments if, after giving pro forma effect thereto, the First Lien Leverage Ratio would not be greater than the First Lien Leverage Ratio immediately prior to giving effect to such Restricted Payment (the "No Worse Test");

(p) [Management Equity] the Borrower may repurchase Equity Interests held by directors, officers, employees or consultants in an aggregate amount not to exceed $25,000,000 in any fiscal year, with unused amounts carrying forward to the next fiscal year;

(q) [Tax Distributions] the Borrower may make distributions to Holdings to pay taxes attributable to the income of Holdings and its Subsidiaries;

(k) [J.Crew Blocker] No Loan Party shall transfer any Material Intellectual Property to any Unrestricted Subsidiary or designate any Subsidiary holding Material Intellectual Property as an Unrestricted Subsidiary, except:
(i) non-exclusive licenses granted in the ordinary course of business;
(ii) transfers between Loan Parties;
(iii) transfers for fair market value.

"Material Intellectual Property" means patents, trademarks, copyrights and trade secrets that are material to the business.

=== UNRESTRICTED SUBSIDIARY MECHANICS ===

Section 5.15 Designation of Subsidiaries.

The Borrower may designate any Subsidiary as an Unrestricted Subsidiary if:
(a) no Default exists or would result therefrom;
(b) the aggregate Fair Market Value of all Unrestricted Subsidiaries does not exceed the greater of $40,000,000 and 30% of EBITDA;
(c) such designation is treated as an Investment.

The Borrower may distribute the Equity Interests of any Unrestricted Subsidiary to its shareholders.

=== SWEEP TIERS ===

Mandatory Prepayment from Excess Cash Flow:
- If First Lien Leverage Ratio > 5.75x: 50% of ECF
- If First Lien Leverage Ratio > 5.50x but <= 5.75x: 25% of ECF
- If First Lien Leverage Ratio <= 5.50x: 0% of ECF

De Minimis: No prepayment required if ECF is less than the greater of $20,000,000 and 15% of EBITDA.
Annual threshold: $40,000,000 with carryforward of unused amounts.
'''

    try:
        from app.services.graph_storage import GraphStorage
        from app.services.extraction import get_extraction_service

        extraction_svc = get_extraction_service()

        # Step 1: Load extraction metadata from TypeDB (SSoT)
        logger.info("Test V4: Loading extraction metadata...")
        metadata = GraphStorage.load_extraction_metadata()
        logger.info(f"Test V4: Loaded {len(metadata)} extraction instructions")

        # Step 2: Build Claude prompt
        logger.info("Test V4: Building Claude prompt...")
        prompt = GraphStorage.build_claude_prompt(metadata, sample_rp_text)
        logger.info(f"Test V4: Prompt built ({len(prompt)} chars)")

        # Step 3: Call Claude (use Sonnet for speed)
        logger.info("Test V4: Calling Claude (claude-sonnet-4-20250514)...")
        response_text = extraction_svc._call_claude_v4(prompt, model="claude-sonnet-4-20250514")
        logger.info(f"Test V4: Response received ({len(response_text)} chars)")

        # Step 4: Parse into Pydantic
        logger.info("Test V4: Parsing response...")
        extraction = GraphStorage.parse_claude_response(response_text)

        # Create storage instance and summarize
        storage = GraphStorage(deal_id)
        summary = storage.summarize_extraction(extraction)
        logger.info(f"Test V4: Parsed extraction - {summary}")

        result = {
            "status": "success",
            "deal_id": deal_id,
            "time_seconds": round(time.time() - start, 2),
            "sample_text_chars": len(sample_rp_text),
            "prompt_chars": len(prompt),
            "response_chars": len(response_text),
            "metadata_count": len(metadata),
            "summary": summary,
            "extraction": extraction.model_dump()
        }

        # Optionally store to TypeDB and verify
        if store:
            logger.info(f"Test V4: Storing to TypeDB for deal {deal_id}...")
            try:
                storage_result = storage.store_rp_extraction_v4(extraction)
                result["storage"] = storage_result
                logger.info(f"Test V4: Storage complete - {storage_result}")

                # Query back to verify
                provision_id = storage_result.get("provision_id")
                if provision_id:
                    verification = _verify_v4_storage(provision_id)
                    result["verification"] = verification
                    logger.info(f"Test V4: Verification - {verification}")

            except Exception as e:
                logger.exception(f"Test V4: Storage failed - {e}")
                result["storage_error"] = str(e)

        result["time_seconds"] = round(time.time() - start, 2)
        return result

    except Exception as e:
        logger.exception(f"Test V4 extraction failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "time_seconds": round(time.time() - start, 2)
        }


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
                "deal_name": _safe_get_value(rows[0], "name", "Unknown"),
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
            # 0. Delete provision_has_answer relations for RP provision
            try:
                tx.query(f"""
                    match
                        $p isa rp_provision, has provision_id "{deal_id}_rp";
                        $rel (provision: $p, question: $q) isa provision_has_answer;
                    delete $rel;
                """).resolve()
            except Exception:
                pass  # May not exist

            # 1. Delete concept_applicability relations for RP provision
            try:
                tx.query(f"""
                    match
                        $p isa rp_provision, has provision_id "{deal_id}_rp";
                        $rel isa concept_applicability, links (provision: $p, concept: $c);
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
                        $rel isa deal_has_provision, links (deal: $d, provision: $p);
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
        high_conf = result.high_confidence_answers
        universe_kb = result.rp_universe_chars // 1024

        logger.info(
            f"Extraction complete for deal {deal_id}: "
            f"{total_answers} answers in {result.extraction_time_seconds:.1f}s"
        )

        # ── J.Crew Deep Analysis (non-blocking) ─────────────────────────
        # Runs 3-tier analysis if JC1/JC2/JC3 questions are seeded in TypeDB.
        # Failure here does NOT block the main extraction from succeeding.
        if result.rp_universe and result.document_text:
            try:
                extraction_status[deal_id] = ExtractionStatus(
                    deal_id=deal_id,
                    status="extracting",
                    progress=85,
                    current_step="Running J.Crew deep analysis (3-tier)..."
                )
                jcrew_result = await extraction_svc.run_jcrew_deep_analysis(
                    deal_id=deal_id,
                    rp_universe=result.rp_universe,
                    document_text=result.document_text,
                )
                if not jcrew_result.get("skipped"):
                    jc_answers = jcrew_result.get("total_answers", 0)
                    jc_high = jcrew_result.get("high_confidence", 0)
                    logger.info(
                        f"J.Crew deep analysis for {deal_id}: "
                        f"{jc_answers} answers ({jc_high} high confidence) "
                        f"in {jcrew_result.get('elapsed_seconds', 0)}s"
                    )
            except Exception as jc_err:
                logger.warning(
                    f"J.Crew deep analysis failed for {deal_id} (non-blocking): {jc_err}"
                )

        # Update status: complete (after both standard + J.Crew)
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="complete",
            progress=100,
            current_step=f"Extracted {total_answers} answers ({high_conf} high confidence), {universe_kb}KB RP universe in {result.extraction_time_seconds:.1f}s"
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


@router.post("/{deal_id}/upload-pdf")
async def upload_pdf_for_deal(
    deal_id: str,
    file: UploadFile = File(...)
) -> Dict[str, Any]:
    """
    Upload a PDF for an existing deal.

    Use this to attach a PDF to a deal that was created without one.
    Does NOT trigger extraction - use POST /{deal_id}/extract-v4 after upload.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Check deal exists
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                select $d;
            """
            result = tx.query(query).resolve()
            if not list(result.as_concept_rows()):
                raise HTTPException(status_code=404, detail="Deal not found")
        finally:
            tx.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Save PDF
    pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
    try:
        contents = await file.read()
        with open(pdf_path, "wb") as f:
            f.write(contents)

        logger.info(f"Saved PDF for deal {deal_id}: {pdf_path} ({len(contents)} bytes)")

        return {
            "status": "success",
            "deal_id": deal_id,
            "pdf_size": len(contents),
            "message": "PDF uploaded. Use POST /api/deals/{deal_id}/extract-v4 to run extraction."
        }
    except Exception as e:
        logger.error(f"Failed to save PDF: {e}")
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
    """
    Get all answers for a deal in SSoT format.

    Joins:
    1. Question metadata from ontology (question_id, question_text, answer_type, category)
    2. Stored values from provision attributes
    3. Concept applicabilities for multiselect questions

    Returns array with question metadata + value for frontend display.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    provision_id = f"{deal_id}_rp"

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

            # Check if extraction is complete (provision exists)
            provision_query = f"""
                match $p isa rp_provision, has provision_id "{provision_id}";
                select $p;
            """
            provision_result = tx.query(provision_query).resolve()
            extraction_complete = len(list(provision_result.as_concept_rows())) > 0

            # 1. Load all RP questions with category via category_has_question (SSoT)
            questions_query = """
                match
                    $q isa ontology_question,
                        has covenant_type "RP",
                        has question_id $qid,
                        has question_text $qtext,
                        has answer_type $atype,
                        has display_order $order;
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid, has name $cname;
                select $qid, $qtext, $atype, $order, $cid, $cname;
            """
            questions_result = tx.query(questions_query).resolve()

            questions = []
            for row in questions_result.as_concept_rows():
                qid = _safe_get_value(row, "qid")
                if not qid:
                    continue

                questions.append({
                    "question_id": qid,
                    "question_text": _safe_get_value(row, "qtext", ""),
                    "answer_type": _safe_get_value(row, "atype", "string"),
                    "display_order": _safe_get_value(row, "order", 0),
                    "category_id": _safe_get_value(row, "cid", ""),
                    "category_name": _safe_get_value(row, "cname", ""),
                })

            # 2. Load stored scalar values via provision_has_answer (SSoT)
            stored_values = {}
            if extraction_complete:
                stored_values = _load_provision_answers(tx, provision_id)

            # 3. Load concept applicabilities for multiselect questions
            multiselect_values = {}
            if extraction_complete:
                applicability_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        (provision: $p, concept: $c) isa concept_applicability;
                        $c has concept_id $cid, has name $cname;
                    select $c, $cid, $cname;
                """
                applicability_result = tx.query(applicability_query).resolve()
                for row in applicability_result.as_concept_rows():
                    concept_entity = _safe_get_entity(row, "c")
                    concept_id = _safe_get_value(row, "cid")
                    concept_name = _safe_get_value(row, "cname")

                    if concept_entity and concept_id:
                        concept_type = concept_entity.get_type().get_label()
                        if concept_type not in multiselect_values:
                            multiselect_values[concept_type] = []
                        multiselect_values[concept_type].append({
                            "concept_id": concept_id,
                            "name": concept_name or ""
                        })

            # 4. Load multiselect concept type mapping from ontology (SSoT)
            multiselect_map = {}  # {question_id: concept_type_name}
            if extraction_complete:
                concept_type_query = """
                    match
                        $q isa ontology_question,
                            has covenant_type "RP",
                            has answer_type "multiselect",
                            has question_id $qid;
                        (question: $q) isa question_targets_concept,
                            has target_concept_type $tct;
                    select $qid, $tct;
                """
                concept_type_result = tx.query(concept_type_query).resolve()
                for row in concept_type_result.as_concept_rows():
                    qid = _safe_get_value(row, "qid")
                    tct = _safe_get_value(row, "tct")
                    if qid and tct:
                        multiselect_map[qid] = tct

            # 5. Build answer array
            answers = []
            answer_count = 0

            for q in sorted(questions, key=lambda x: (x["category_id"], x["display_order"])):
                qid = q["question_id"]
                answer_type = q["answer_type"]

                value = None
                answer_data = None
                if answer_type == "multiselect":
                    concept_type = multiselect_map.get(qid)
                    if concept_type and concept_type in multiselect_values:
                        value = multiselect_values[concept_type]
                else:
                    answer_data = stored_values.get(qid)
                    if answer_data:
                        value = answer_data.get("value")

                if value is not None:
                    answer_count += 1

                answers.append({
                    "question_id": qid,
                    "question_text": q["question_text"],
                    "answer_type": answer_type,
                    "category_id": q["category_id"],
                    "category_name": q["category_name"],
                    "value": value,
                    "source_text": answer_data.get("source_text") if answer_data else None,
                    "source_page": answer_data.get("source_page") if answer_data else None,
                    "confidence": answer_data.get("confidence") if answer_data else None,
                })

            return {
                "deal_id": deal_id,
                "extraction_complete": extraction_complete,
                "answer_count": answer_count,
                "total_questions": len(questions),
                "answers": answers
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
    Get the RP provision for a deal via provision_has_answer + concept_applicability.

    Returns:
        - provision_id
        - scalar_answers: keyed by question_id with typed values + provenance
        - pattern_flags: flat attributes (jcrew/serta/collateral_leakage_pattern_detected)
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

            # Get scalar answers via provision_has_answer (SSoT)
            scalar_answers = _load_provision_answers(tx, provision_id)

            # Get pattern flags (still flat attributes on rp_provision)
            pattern_flags = {}
            for flag_name in ("jcrew_pattern_detected", "serta_pattern_detected", "collateral_leakage_pattern_detected"):
                try:
                    flag_query = f"""
                        match
                            $p isa rp_provision, has provision_id "{provision_id}",
                                has {flag_name} $val;
                        select $val;
                    """
                    flag_result = tx.query(flag_query).resolve()
                    for row in flag_result.as_concept_rows():
                        val = _safe_get_value(row, "val")
                        if val is not None:
                            pattern_flags[flag_name] = val
                except Exception:
                    pass  # Flag not set on this provision

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
                concept_entity = _safe_get_entity(row, "c")
                concept_id = _safe_get_value(row, "cid")
                concept_name = _safe_get_value(row, "cname")

                if concept_entity and concept_id:
                    concept_type = concept_entity.get_type().get_label()
                    if concept_type not in multiselect_answers:
                        multiselect_answers[concept_type] = []
                    multiselect_answers[concept_type].append({
                        "concept_id": concept_id,
                        "name": concept_name or "Unknown"
                    })

            return {
                "deal_id": deal_id,
                "provision_id": provision_id,
                "provision_type": "rp_provision",
                "scalar_answers": scalar_answers,
                "pattern_flags": pattern_flags,
                "multiselect_answers": multiselect_answers,
                "scalar_count": len(scalar_answers),
                "pattern_flag_count": len(pattern_flags),
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


@router.post("/{deal_id}/ask")
async def ask_question(deal_id: str, request: AskRequest) -> Dict[str, Any]:
    """
    Answer a natural language question about a deal using extracted data.

    Model Selection:
    - Simple questions: Sonnet (fast, cost-effective)
    - Complex questions: Opus (deeper reasoning)
    - Uncertain Sonnet responses: Automatic Opus fallback

    Flow:
    1. Fetch all answers for the deal (scalar + multiselect)
    2. Format as structured context
    3. Detect question complexity
    4. Call appropriate model (with fallback)
    5. Return synthesized answer with citations
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Step 1: Get RP provision data (scalar + multiselect)
    try:
        rp_response = await get_rp_provision(deal_id)
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(
                status_code=400,
                detail="Extraction not complete. Please wait for extraction to finish."
            )
        raise

    scalar_count = rp_response.get("scalar_count", 0)
    multiselect_count = rp_response.get("multiselect_count", 0)

    if scalar_count == 0 and multiselect_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No extracted data found. Please upload and extract a document first."
        )

    # Step 2: Load question metadata with category info for grouped display
    question_meta = {}  # {question_id: {question_text, category_id, category_name}}
    concept_type_labels = {}  # {concept_type: question_text} for multiselect labels
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Load question_id → question_text + category info
            meta_query = """
                match
                    $q isa ontology_question,
                        has covenant_type "RP",
                        has question_id $qid,
                        has question_text $qtext;
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid, has name $cname;
                select $qid, $qtext, $cid, $cname;
            """
            meta_result = tx.query(meta_query).resolve()
            for row in meta_result.as_concept_rows():
                qid = _safe_get_value(row, "qid")
                qtext = _safe_get_value(row, "qtext")
                cid = _safe_get_value(row, "cid")
                cname = _safe_get_value(row, "cname")
                if qid and qtext:
                    question_meta[qid] = {
                        "question_text": qtext,
                        "category_id": cid or "ZZ",
                        "category_name": cname or "Other",
                    }

            # Load multiselect concept type → question_text labels from TypeDB
            label_query = """
                match
                    $q isa ontology_question,
                        has question_text $qt,
                        has answer_type "multiselect";
                    (question: $q) isa question_targets_concept,
                        has target_concept_type $tct;
                select $qt, $tct;
            """
            label_result = tx.query(label_query).resolve()
            for row in label_result.as_concept_rows():
                qt = _safe_get_value(row, "qt")
                tct = _safe_get_value(row, "tct")
                if qt and tct:
                    concept_type_labels[tct] = qt
        finally:
            tx.close()
    except Exception:
        pass  # Proceed with empty metadata — context will still work

    # Step 3: Format answers as structured context for Claude
    context = _format_rp_provision_as_context(rp_response, question_meta, concept_type_labels)

    # Step 4: Build system rules and user prompt (separated for priority)
    system_rules = """You are a legal analyst answering questions about a credit agreement's restricted payments covenant using pre-extracted structured data.

## STRICT RULES

1. **CITATION REQUIRED**: Every factual claim must include a clause and page citation where available, formatted as [Section X.XX(y), p.XX]. Use the section references from the extracted data. If only a page number is available, use [p.XX]. Never cite just a page number if a section reference is also available.
2. **ONLY USE PROVIDED DATA**: Never invent facts not present in EXTRACTED DATA below
3. **QUALIFICATIONS REQUIRED**: If a qualification, condition, or exception exists in the data, you MUST mention it
4. **MISSING DATA**: If the requested information is not found, say "Not found in extracted data"
5. **OBJECTIVE ONLY**: Report what the document states. Do NOT characterize provisions as borrower-friendly, lender-friendly, aggressive, conservative, or any other subjective assessment. Do NOT assign risk scores or favorability ratings. Users are legal professionals who will form their own judgments.
6. **JCREW BLOCKER ANALYSIS RULES**:
   When answering about J.Crew blockers, IP protection, unrestricted subsidiary risk, or covenant loopholes, structure the answer as follows:

   **BLOCKER PROVISION** — State whether the blocker exists, quote it verbatim, and cite the page. This is the anchor — everything else is analysis of this provision.

   **WHAT IT COVERS** — State scope ONCE (do not repeat scope facts later). What actions are prohibited (ownership, licensing, etc.), who is bound (Loan Parties only vs all Restricted Subsidiaries), what assets are protected, when it applies (designation-only vs ongoing). IMPORTANT: A J.Crew blocker covering only Intellectual Property (not broader "material assets") is STANDARD MARKET PRACTICE — do not frame IP-only coverage as a limitation or gap.

   **DEFINITION QUALITY** — For each key definition (Intellectual Property, Material, Transfer), state: is it defined inline, by cross-reference to another document, or not defined at all? If inline, what does it include/exclude? If cross-reference, state which document and note full analysis requires it. If not defined, flag as vulnerability. Frame by practical impact: "Material is determined by the Borrower Agent in good faith with no objective threshold — the borrower controls what is considered material" is better than just "Material is subjective."

   **INVESTMENT PATHWAYS** — ALWAYS include if jc_t1 data is available. Show: direct LP-to-Unsub investment cap (dollar and percentage), LP-to-Non-Guarantor RS cap (first hop), RS-to-Unsub cap (second hop), whether baskets can stack or rebuild, which baskets fund unsub investments. If blocker binds ALL Restricted Subs, note this CLOSES the chain pathway. If only Loan Parties, flag chain pathway as open and explain the Pluralsight pattern.

   **AMENDMENT VULNERABILITY** — State the SPECIFIC amendment threshold (Required Lenders/simple majority, supermajority with percentage, or all-lender consent). "Not a sacred right" alone is insufficient.

   **LIEN RELEASE INTERACTION** — CONNECT lien release to blocker: explain WHY automatic lien release matters (IP collateral liens releasing without consent means collateral protection evaporates if blocker has gaps). Do not state the fact without the connection.

   **SYNTHESIS** — End with 2-3 sentences connecting findings with cause-and-effect relationships. Do NOT include subjective risk ratings. State objective facts and their connections. Legal professionals make the judgment calls.

   FORMATTING RULES FOR JCREW ANSWERS: Do NOT use labels "Tier 1", "Tier 2", "Tier 3". Do NOT repeat the same fact in multiple sections. Do NOT frame IP-only coverage as a gap. Do NOT list findings without explaining why they matter. ALWAYS include investment pathway data if available. ALWAYS state specific amendment thresholds. ALWAYS connect lien release to blocker analysis. ALWAYS end with connective synthesis.

7. **RATIO BASKET AND DIVIDEND CAPACITY RULES**:
   When answering questions about whether a specific dividend, distribution,
   or restricted payment is permitted at a given leverage level:

   (a) **CHECK ALL BASKETS** — Never answer based on one basket alone. The borrower
       can use ANY available basket. Check in this order:
       - Ratio-based unlimited basket — what is the absolute threshold?
       - "No worse" test — does it exist? If yes, the borrower can make the
         payment at ANY leverage level as long as the pro forma ratio is no
         worse than immediately before the transaction.
       - Builder basket / Cumulative Amount — what capacity has accumulated?
       - General RP basket — fixed dollar + grower amounts
       - Specific-purpose baskets — management equity, tax distributions, etc.
       - Basket stacking — can multiple baskets be combined?

   (b) **THE "NO WORSE" TEST IS CRITICAL** — If the extracted data shows a "no worse"
       ratio test exists (look for answers about "no worse" in the Ratio Basket
       category), ALWAYS analyze whether the specific transaction would pass it.
       Key insight: Disposing of a negative-EBITDA asset IMPROVES the leverage
       ratio (consolidated EBITDA increases), so the "no worse" test may be
       satisfied even at leverage levels above the absolute threshold.

   (c) **PRO FORMA ANALYSIS** — When a question specifies a transaction (e.g.,
       "dividend a business division with $X EBITDA"), analyze the pro forma
       impact on the leverage ratio. Removing EBITDA changes the denominator.
       Removing debt changes the numerator. State the directional impact even
       if you cannot compute exact numbers.

   (d) **CITE SPECIFIC CLAUSES** — Reference the specific subsection for each basket
       (e.g., "Section 6.06(n) permits unlimited dividends at <=5.75x" and
       "Section 6.06(o) permits dividends under the No Worse Test"). When
       source data includes section references, always include them.

   (e) **CAPACITY SUMMARY** — For complex questions, end with a table showing each
       potentially available basket, its capacity or test, and whether it is
       available for the specific scenario asked about.

## FORMATTING

- Use **bold** for key terms and defined terms
- Use bullet points for lists
- Keep response concise but complete
- State facts with citations. Do not editorialize.

## EVIDENCE TRACING

After your answer, on a new line, output an evidence block in this exact format:

<!-- EVIDENCE: ["rp_g5", "rp_f14", "jc_t2_01"] -->

List the question_ids of every extracted data point you relied on to form
your answer. Include ALL data points that influenced your response — both
those you cited explicitly and those you used for background context.
Order them by importance (most critical first). Include 5-20 question_ids.
This block MUST appear at the very end of your response."""

    user_prompt = f"""## USER QUESTION

{request.question}

## EXTRACTED DATA FOR THIS DEAL

{context}"""

    # Step 5: Call Claude with system message + user message
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        model_used = settings.claude_model

        response = client.messages.create(
            model=model_used,
            max_tokens=4000,
            system=system_rules,
            messages=[{"role": "user", "content": user_prompt}]
        )

        answer_text = response.content[0].text

        # Step 7: Parse evidence block and extract citations
        clean_answer, evidence = _parse_evidence_block(
            answer_text, rp_response, question_meta
        )
        citations = _extract_citations_from_answer(clean_answer)

        return {
            "question": request.question,
            "answer": clean_answer,
            "citations": citations,
            "evidence": evidence,
            "model": model_used,
            "data_source": {
                "deal_id": deal_id,
                "scalar_answers": scalar_count,
                "multiselect_answers": multiselect_count
            }
        }

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error in Q&A: {e}")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in Q&A: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _format_rp_provision_as_context(
    rp_response: Dict,
    question_meta: Dict[str, Dict] = None,
    concept_type_labels: Dict[str, str] = None,
) -> str:
    """Format RP provision data as structured context for Claude.

    Groups scalar answers by category for easier navigation.
    Uses question_text labels from TypeDB (SSoT).
    Derives multiselect concept labels from TypeDB question_text.
    """
    if question_meta is None:
        question_meta = {}
    if concept_type_labels is None:
        concept_type_labels = {}

    lines = []

    # ── Scalar answers grouped by category ────────────────────────────
    scalar_answers = rp_response.get("scalar_answers", {})

    # Group answers by (category_id, category_name)
    by_category: Dict[tuple, list] = {}
    for qid, data in scalar_answers.items():
        meta = question_meta.get(qid)
        if meta:
            cat_key = (meta["category_id"], meta["category_name"])
        else:
            cat_key = ("ZZ", "Other")
        by_category.setdefault(cat_key, []).append((qid, data))

    lines.append("## EXTRACTED ANSWERS")
    lines.append("")

    for (cat_id, cat_name), answers in sorted(by_category.items()):
        lines.append(f"### {cat_name} ({cat_id})")
        for qid, data in sorted(answers, key=lambda x: x[0]):
            meta = question_meta.get(qid)
            q_text = meta["question_text"] if meta else qid

            value = data.get("value")
            if isinstance(value, bool):
                value_str = "Yes" if value else "No"
            elif isinstance(value, float) and value == int(value):
                value_str = str(int(value))
            else:
                value_str = str(value)

            page = data.get("source_page")
            section = data.get("source_section", "")
            # Build citation: prefer "Section X [p.Y]" over just "[p.Y]"
            if section and page:
                cite = f" [{section}, p.{page}]"
            elif section:
                cite = f" [{section}]"
            elif page:
                cite = f" [p.{page}]"
            else:
                cite = ""
            lines.append(f"- {q_text}: {value_str}{cite}")

            source_text = data.get("source_text")
            if source_text:
                lines.append(f"  Source: \"{source_text[:200]}\"")
        lines.append("")

    # ── Pattern flags ─────────────────────────────────────────────────
    pattern_flags = rp_response.get("pattern_flags", {})
    if pattern_flags:
        flag_labels = {
            "jcrew_pattern_detected": "J.Crew blocker pattern detected",
            "serta_pattern_detected": "Serta pattern detected",
            "collateral_leakage_pattern_detected": "Collateral leakage pattern detected",
        }
        lines.append("## PATTERN FLAGS")
        lines.append("")
        for flag, value in sorted(pattern_flags.items()):
            label = flag_labels.get(flag, flag)
            lines.append(f"- {label}: {'Yes' if value else 'No'}")
        lines.append("")

    # ── Multiselect answers (labels from TypeDB) ─────────────────────
    multiselect_answers = rp_response.get("multiselect_answers", {})
    if multiselect_answers:
        lines.append("## APPLICABLE CONCEPTS")
        lines.append("")
        for concept_type, concepts in sorted(multiselect_answers.items()):
            concept_names = [c["name"] for c in concepts]
            label = concept_type_labels.get(
                concept_type,
                concept_type.replace("_", " ").title(),
            )
            lines.append(f"- {label}: {', '.join(concept_names)}")
        lines.append("")

    return "\n".join(lines)


def _parse_evidence_block(
    answer_text: str,
    rp_response: Dict,
    question_meta: Dict[str, Dict],
) -> tuple:
    """Extract evidence block from answer and resolve to full data points.

    Returns: (clean_answer, evidence_list)
    """
    evidence_match = re.search(
        r'<!--\s*EVIDENCE:\s*\[([^\]]*)\]\s*-->',
        answer_text,
    )

    if not evidence_match:
        return answer_text, []

    # Clean the answer (remove evidence block)
    clean_answer = answer_text[:evidence_match.start()].rstrip()

    # Parse question_ids
    raw_ids = evidence_match.group(1)
    question_ids = [
        qid.strip().strip('"').strip("'")
        for qid in raw_ids.split(",")
        if qid.strip()
    ]

    # Look up each question_id in the extracted data
    scalar_answers = rp_response.get("scalar_answers", {})
    evidence = []

    for qid in question_ids:
        if qid in scalar_answers:
            data = scalar_answers[qid]
            meta = question_meta.get(qid)
            evidence.append({
                "question_id": qid,
                "question_text": meta["question_text"] if meta else qid,
                "value": data.get("value"),
                "source_text": data.get("source_text", ""),
                "source_page": data.get("source_page"),
                "source_section": data.get("source_section", ""),
                "confidence": data.get("confidence", ""),
            })

    return clean_answer, evidence


def _extract_citations_from_answer(answer_text: str) -> List[Dict[str, Any]]:
    """Extract page and section citations from the answer."""

    # Find all [Section X, p.Y] patterns
    section_page_refs = re.findall(r'\[([^,\]]+),\s*p\.(\d+)\]', answer_text)
    # Find all standalone [p.XX] patterns
    page_only_refs = re.findall(r'\[p\.(\d+)\]', answer_text)

    citations = []
    seen_pages = set()

    # Section + page citations
    for section, page in section_page_refs:
        page_int = int(page)
        seen_pages.add(page_int)
        citations.append({
            "page": page_int,
            "section": section.strip(),
            "text": None
        })

    # Page-only citations (not already captured)
    for page in page_only_refs:
        page_int = int(page)
        if page_int not in seen_pages:
            seen_pages.add(page_int)
            citations.append({
                "page": page_int,
                "section": None,
                "text": None
            })

    citations.sort(key=lambda c: c["page"])
    return citations


@router.get("/{deal_id}/debug-multiselect")
async def debug_multiselect(deal_id: str) -> Dict[str, Any]:
    """
    Debug endpoint to check what concept_applicabilities are stored in TypeDB.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    provision_id = f"{deal_id}_rp"

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # 1. Get all concept types with applicabilities
            types_query = f"""
                match
                    $p isa rp_provision, has provision_id "{provision_id}";
                    (provision: $p, concept: $c) isa concept_applicability;
                select $c;
            """
            types_result = tx.query(types_query).resolve()
            concept_types = set()
            for row in types_result.as_concept_rows():
                c = row.get("c")
                if c:
                    try:
                        concept_types.add(c.as_entity().get_type().get_label())
                    except Exception:
                        pass

            # 2. Get actual applicability records
            records_query = f"""
                match
                    $p isa rp_provision, has provision_id "{provision_id}";
                    (provision: $p, concept: $c) isa concept_applicability;
                    $c has concept_id $cid, has name $cname;
                select $c, $cid, $cname;
            """
            records_result = tx.query(records_query).resolve()
            records = []
            for row in records_result.as_concept_rows():
                c = row.get("c")
                cid = _safe_get_value(row, "cid")
                cname = _safe_get_value(row, "cname")
                ctype = None
                if c:
                    try:
                        ctype = c.as_entity().get_type().get_label()
                    except Exception:
                        pass
                if cid:
                    records.append({
                        "concept_type": ctype,
                        "concept_id": cid,
                        "name": cname
                    })

            # 3. Check what multiselect questions expect
            questions_query = """
                match
                    $q isa ontology_question,
                        has question_id $qid,
                        has answer_type "multiselect";
                select $qid;
            """
            questions_result = tx.query(questions_query).resolve()
            multiselect_questions = []
            for row in questions_result.as_concept_rows():
                qid = _safe_get_value(row, "qid")
                if qid:
                    multiselect_questions.append(qid)

            # Load expected mapping from ontology (SSoT)
            expected_mapping = {}
            mapping_query = """
                match
                    $q isa ontology_question,
                        has covenant_type "RP",
                        has answer_type "multiselect",
                        has question_id $qid;
                    (question: $q) isa question_targets_concept,
                        has target_concept_type $tct;
                select $qid, $tct;
            """
            mapping_result = tx.query(mapping_query).resolve()
            for row in mapping_result.as_concept_rows():
                qid = _safe_get_value(row, "qid")
                tct = _safe_get_value(row, "tct")
                if qid and tct:
                    expected_mapping[qid] = tct

            return {
                "provision_id": provision_id,
                "stored_concept_types": sorted(list(concept_types)),
                "total_applicabilities": len(records),
                "sample_records": records[:20],
                "multiselect_questions": sorted(multiselect_questions),
                "expected_mapping": expected_mapping,
            }

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Debug error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# V4 GRAPH-NATIVE EXTRACTION ENDPOINTS
# =============================================================================

@router.post("/{deal_id}/extract-v4")
async def extract_rp_v4(
    deal_id: str,
    background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """
    Trigger V4 graph-native RP extraction.

    This uses the new V4 pipeline:
    1. Extract RP universe from document
    2. Load extraction metadata from TypeDB (SSoT)
    3. Build structured prompt with JSON schema
    4. Parse Claude response into typed Pydantic model
    5. Store as graph entities and relations

    Creates in TypeDB:
    - Basket entities (builder, ratio, general, mgmt, tax)
    - Blocker entities with exceptions
    - Unsub designation
    - Sweep tiers and de minimis thresholds
    - Reallocation relations
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Check PDF exists
    pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail=f"PDF not found for deal {deal_id}")

    # Check deal exists
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                select $d;
            """
            result = tx.query(query).resolve()
            if not list(result.as_concept_rows()):
                raise HTTPException(status_code=404, detail="Deal not found")
        finally:
            tx.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Update status
    extraction_status[deal_id] = ExtractionStatus(
        deal_id=deal_id,
        status="extracting",
        progress=10,
        current_step="Starting V4 graph extraction..."
    )

    # Run extraction in background
    background_tasks.add_task(run_extraction_v4, deal_id, pdf_path)

    return {
        "status": "processing",
        "deal_id": deal_id,
        "message": "V4 extraction started. Use GET /api/deals/{deal_id}/status to check progress."
    }


async def run_extraction_v4(deal_id: str, pdf_path: str):
    """Background task for V4 extraction."""
    extraction_svc = get_extraction_service()

    try:
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="extracting",
            progress=20,
            current_step="Parsing PDF and extracting RP universe..."
        )

        result = await extraction_svc.extract_rp_v4(
            pdf_path=pdf_path,
            deal_id=deal_id
        )

        # Build summary
        storage = result.storage_result
        summary_parts = [
            f"{storage.get('baskets_created', 0)} baskets",
            f"{storage.get('sources_created', 0)} sources",
            f"{storage.get('blockers_created', 0)} blockers",
            f"{storage.get('sweep_tiers_created', 0)} sweep tiers"
        ]

        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="complete",
            progress=100,
            current_step=f"V4 extraction complete: {', '.join(summary_parts)} in {result.extraction_time_seconds:.1f}s"
        )

        logger.info(f"V4 extraction complete for deal {deal_id}: {storage}")

    except Exception as e:
        logger.error(f"V4 extraction failed for deal {deal_id}: {e}")
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="error",
            progress=0,
            current_step=None,
            error=str(e)
        )


def _verify_v4_storage(provision_id: str) -> Dict[str, Any]:
    """Query TypeDB to verify V4 entities were stored correctly."""
    verification = {
        "provision_id": provision_id,
        "provision_found": False,
        "baskets": [],
        "sources": [],
        "blockers": [],
        "exceptions": [],
        "sweep_tiers": 0,
        "de_minimis": 0
    }

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Check provision exists
            prov_query = f'''
                match $p isa rp_provision, has provision_id "{provision_id}";
                select $p;
            '''
            result = tx.query(prov_query).resolve()
            if list(result.as_concept_rows()):
                verification["provision_found"] = True

            # Count baskets by type
            basket_query = f'''
                match
                    $prov isa rp_provision, has provision_id "{provision_id}";
                    ($prov, $basket) isa provision_has_basket;
                    $basket isa $basket_type;
                select $basket_type;
            '''
            result = tx.query(basket_query).resolve()
            basket_types = []
            for row in result.as_concept_rows():
                btype = row.get("basket_type")
                if btype:
                    basket_types.append(btype.get_label().name)
            verification["baskets"] = list(set(basket_types))

            # Count builder sources
            source_query = f'''
                match
                    $prov isa rp_provision, has provision_id "{provision_id}";
                    ($prov, $bb) isa provision_has_basket;
                    $bb isa builder_basket;
                    ($bb, $src) isa basket_has_source;
                select $src;
            '''
            result = tx.query(source_query).resolve()
            verification["sources"] = len(list(result.as_concept_rows()))

            # Count blockers
            blocker_query = f'''
                match
                    $prov isa rp_provision, has provision_id "{provision_id}";
                    ($prov, $blocker) isa provision_has_blocker;
                select $blocker;
            '''
            result = tx.query(blocker_query).resolve()
            verification["blockers"] = len(list(result.as_concept_rows()))

            # Count sweep tiers
            sweep_query = f'''
                match
                    $prov isa rp_provision, has provision_id "{provision_id}";
                    ($prov, $tier) isa provision_has_sweep_tier;
                select $tier;
            '''
            result = tx.query(sweep_query).resolve()
            verification["sweep_tiers"] = len(list(result.as_concept_rows()))

        finally:
            tx.close()

    except Exception as e:
        verification["error"] = str(e)[:200]

    return verification


@router.get("/{deal_id}/rp-graph")
async def get_rp_graph(deal_id: str) -> Dict[str, Any]:
    """
    Get RP extraction as graph structure.

    Returns all V4 entities and relations for visualization/analysis:
    - Baskets (builder, ratio, general_rp, etc.)
    - Builder sources
    - Blockers and exceptions
    - Sweep tiers
    - De minimis thresholds
    - Reallocations
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        from app.services.graph_queries import GraphQueries
        queries = GraphQueries()

        # Find the provision for this deal
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Get provision ID
            prov_query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    ($d, $p) isa deal_has_provision;
                    $p isa rp_provision, has provision_id $pid;
                select $pid;
            """
            result = tx.query(prov_query).resolve()
            rows = list(result.as_concept_rows())

            if not rows:
                raise HTTPException(status_code=404, detail="No RP provision found for this deal")

            provision_id = _safe_get_value(rows[0], "pid")

            # Get baskets
            baskets = queries.get_provision_baskets(provision_id)

            # Get blockers
            blockers = queries.get_provision_blockers(provision_id)

            # Get sweep config
            sweep_config = queries.get_provision_sweep_config(provision_id)

            return {
                "deal_id": deal_id,
                "provision_id": provision_id,
                "baskets": baskets,
                "blockers": blockers,
                "sweep_config": sweep_config
            }

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting RP graph for {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/v4-summary")
async def get_v4_extraction_summary(deal_id: str) -> Dict[str, Any]:
    """
    Get a summary of V4 extraction results.

    Returns key metrics extracted from the credit agreement:
    - Builder basket configuration
    - Ratio basket thresholds
    - J.Crew blocker coverage
    - Unsub designation rules
    - Sweep tiers
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            summary = {
                "deal_id": deal_id,
                "builder_basket": None,
                "ratio_basket": None,
                "jcrew_blocker": None,
                "unsub_designation": None,
                "sweep_tiers": [],
                "de_minimis": []
            }

            # Find provision
            prov_query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    ($d, $p) isa deal_has_provision;
                    $p isa rp_provision, has provision_id $pid;
                select $pid;
            """
            result = tx.query(prov_query).resolve()
            rows = list(result.as_concept_rows())

            if not rows:
                return {"deal_id": deal_id, "error": "No V4 extraction found"}

            provision_id = _safe_get_value(rows[0], "pid")
            summary["provision_id"] = provision_id

            # Get builder basket
            try:
                builder_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        ($p, $b) isa provision_has_basket;
                        $b isa builder_basket, has basket_id $bid;
                    select $b, $bid;
                """
                builder_result = tx.query(builder_query).resolve()
                builder_rows = list(builder_result.as_concept_rows())
                if builder_rows:
                    summary["builder_basket"] = {
                        "exists": True,
                        "basket_id": _safe_get_value(builder_rows[0], "bid")
                    }

                    # Count sources
                    sources_query = f"""
                        match
                            $bb isa builder_basket, has basket_id "{summary['builder_basket']['basket_id']}";
                            ($bb, $s) isa builder_has_source;
                        select $s;
                    """
                    sources_result = tx.query(sources_query).resolve()
                    summary["builder_basket"]["source_count"] = len(list(sources_result.as_concept_rows()))
            except Exception:
                pass

            # Get ratio basket
            try:
                ratio_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        ($p, $b) isa provision_has_basket;
                        $b isa ratio_basket;
                    select $b;
                """
                ratio_result = tx.query(ratio_query).resolve()
                for row in ratio_result.as_concept_rows():
                    basket = row.get("b")
                    if basket:
                        # Get attributes
                        summary["ratio_basket"] = {"exists": True}
                        # Note: Would need additional queries to get specific attributes
                        break
            except Exception:
                pass

            # Get J.Crew blocker
            try:
                jcrew_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        ($p, $b) isa provision_has_blocker;
                        $b isa jcrew_blocker, has blocker_id $bid;
                    select $b, $bid;
                """
                jcrew_result = tx.query(jcrew_query).resolve()
                jcrew_rows = list(jcrew_result.as_concept_rows())
                if jcrew_rows:
                    summary["jcrew_blocker"] = {
                        "exists": True,
                        "blocker_id": _safe_get_value(jcrew_rows[0], "bid")
                    }
            except Exception:
                pass

            # Get sweep tiers
            try:
                sweep_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        ($p, $t) isa provision_has_sweep_tier;
                        $t has tier_id $tid, has leverage_threshold $lev, has sweep_percentage $pct;
                    select $tid, $lev, $pct;
                """
                sweep_result = tx.query(sweep_query).resolve()
                for row in sweep_result.as_concept_rows():
                    summary["sweep_tiers"].append({
                        "tier_id": _safe_get_value(row, "tid"),
                        "leverage_threshold": _safe_get_value(row, "lev"),
                        "sweep_percentage": _safe_get_value(row, "pct")
                    })
            except Exception:
                pass

            return summary

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting V4 summary for {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
