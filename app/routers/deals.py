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

        # Update status: complete
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="complete",
            progress=100,
            current_step=f"Extracted {total_answers} answers ({high_conf} high confidence), {universe_kb}KB RP universe in {result.extraction_time_seconds:.1f}s"
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

            # 1. Load all RP questions from ontology with category info
            questions_query = """
                match
                    $q isa ontology_question,
                        has covenant_type "RP",
                        has question_id $qid,
                        has question_text $qtext,
                        has answer_type $atype,
                        has display_order $order;
                select $qid, $qtext, $atype, $order;
            """
            questions_result = tx.query(questions_query).resolve()

            # Build questions list with category derived from question_id
            questions = []
            for row in questions_result.as_concept_rows():
                qid = _safe_get_value(row, "qid")
                if not qid:
                    continue

                # Derive category from question_id (e.g., rp_a1 -> A)
                parts = qid.split("_")
                cat_letter = parts[1][0].upper() if len(parts) >= 2 and len(parts[1]) >= 1 else "Z"

                # Category name mapping
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
                }

                questions.append({
                    "question_id": qid,
                    "question_text": _safe_get_value(row, "qtext", ""),
                    "answer_type": _safe_get_value(row, "atype", "string"),
                    "display_order": _safe_get_value(row, "order", 0),
                    "category_id": cat_letter,
                    "category_name": category_names.get(cat_letter, f"Category {cat_letter}"),
                })

            # 2. Load stored scalar values from provision
            stored_values = {}
            if extraction_complete:
                attrs_query = f"""
                    match
                        $p isa rp_provision, has provision_id "{provision_id}";
                        $p has $attr;
                    select $attr;
                """
                attrs_result = tx.query(attrs_query).resolve()
                for row in attrs_result.as_concept_rows():
                    attr_concept = row.get("attr")
                    if attr_concept:
                        try:
                            attr = attr_concept.as_attribute()
                            attr_type = attr.get_type().get_label()
                            if attr_type != "provision_id":
                                stored_values[attr_type] = attr.get_value()
                        except Exception:
                            pass

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

            # 4. Join questions with stored values
            # Build field name mapping from question_id
            field_name_map = {
                # Category A - General
                "rp_a1": "general_dividend_prohibition_exists",
                # Category C - Management Equity
                "rp_c1": "mgmt_equity_basket_exists",
                "rp_c4": "mgmt_equity_annual_cap_usd",
                "rp_c5": "mgmt_equity_annual_cap_pct_ebitda",
                "rp_c6": "mgmt_equity_uses_greater_of",
                "rp_c7": "mgmt_equity_carryforward_permitted",
                # Category D - Tax
                "rp_d1": "tax_distribution_basket_exists",
                "rp_d3": "tax_standalone_taxpayer_limit",
                # Category F - Builder Basket
                "rp_f1": "builder_basket_exists",
                "rp_f2": "builder_starter_amount_usd",
                "rp_f3": "builder_starter_pct_ebitda",
                "rp_f4": "builder_uses_greater_of",
                "rp_f5": "builder_cni_addition_pct",
                "rp_f6": "builder_equity_addition_pct",
                # Category G - Ratio Basket
                "rp_g1": "ratio_dividend_basket_exists",
                "rp_g2": "ratio_leverage_threshold",
                "rp_g3": "ratio_interest_coverage_threshold",
                "rp_g4": "ratio_is_unlimited_if_met",
                # Category J - Unrestricted Subs
                "rp_j1": "unsub_designation_permitted",
                "rp_j2": "unsub_requires_no_default",
                "rp_j3": "unsub_requires_board_approval",
                "rp_j4": "unsub_dollar_cap_usd",
                "rp_j5": "unsub_pct_total_assets_cap",
                # Category K - J.Crew Blocker
                "rp_k1": "jcrew_blocker_exists",
                "rp_k2": "blocker_covers_ip",
                "rp_k3": "blocker_covers_material_assets",
                "rp_k8": "jcrew_blocker_is_sacred_right",
                # Category Z - Patterns
                "rp_z1": "jcrew_pattern_detected",
                "rp_z2": "serta_pattern_detected",
                "rp_z3": "collateral_leakage_pattern_detected",
            }

            # Multiselect field mapping (question_id -> concept_type)
            # NOTE: concept_type must match what's stored in TypeDB (from extraction)
            multiselect_map = {
                "rp_a2": "dividend_applies_to_entity",
                "rp_a3": "dividend_action",
                "rp_b1": "intercompany_recipient",
                "rp_c2": "covered_person",  # Actual TypeDB type
                "rp_c3": "repurchase_trigger",  # Actual TypeDB type
                "rp_d2": "tax_group_type",
                "rp_e1": "equity_award_type",
                "rp_f7": "builder_source",
                "rp_f8": "builder_use",
                "rp_h1": "holdco_overhead_cost",
                "rp_h2": "holdco_transaction_cost",
                "rp_i1": "reallocation_source_basket",
                "rp_i2": "reallocation_target_basket",
                "rp_k4": "blocker_binding_entity",  # Actual TypeDB type
                "rp_k5": "jcrew_trigger_condition",
                "rp_k6": "jcrew_ip_type",
                "rp_k7": "transfer_type",  # Actual TypeDB type
                "rp_s1": "rdp_payment_type",
                "rp_t1": "rdp_basket",
            }

            # Build answer array
            answers = []
            answer_count = 0

            for q in sorted(questions, key=lambda x: (x["category_id"], x["display_order"])):
                qid = q["question_id"]
                answer_type = q["answer_type"]

                # Get value based on answer type
                value = None
                if answer_type == "multiselect":
                    concept_type = multiselect_map.get(qid)
                    if concept_type and concept_type in multiselect_values:
                        value = multiselect_values[concept_type]
                else:
                    field_name = field_name_map.get(qid)
                    if field_name and field_name in stored_values:
                        value = stored_values[field_name]

                if value is not None:
                    answer_count += 1

                answers.append({
                    "question_id": qid,
                    "question_text": q["question_text"],
                    "answer_type": answer_type,
                    "category_id": q["category_id"],
                    "category_name": q["category_name"],
                    "value": value,
                    "source_text": None,  # TODO: Add provenance when stored
                    "source_page": None,
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
                attr_concept = row.get("attr")
                if attr_concept:
                    attr = attr_concept.as_attribute()
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


@router.post("/{deal_id}/ask")
async def ask_question(deal_id: str, request: AskRequest) -> Dict[str, Any]:
    """
    Answer a natural language question about a deal using extracted data.

    Flow:
    1. Fetch all answers for the deal (scalar + multiselect)
    2. Format as structured context
    3. Send to Claude with strict citation rules
    4. Return synthesized answer with citations
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

    # Step 2: Format answers as structured context for Claude
    context = _format_rp_provision_as_context(rp_response)

    # Step 3: Build prompt with strict rules
    prompt = f"""You are a legal analyst answering questions about a credit agreement.

## STRICT RULES (YOU MUST FOLLOW)

1. **CITATION REQUIRED**: Every factual claim must include [p.XX] citation if page numbers are available
2. **ONLY USE PROVIDED DATA**: Never invent facts not in EXTRACTED DATA below
3. **QUALIFICATIONS REQUIRED**: If a qualification/exception exists, you MUST mention it
4. **MISSING DATA**: If information is not found, say "Not found in extracted data"
5. **INTERPRETATION MARKING**:
   - "The document states X" [p.XX] → factual, cite page
   - "This suggests Y" → interpretation, mark clearly
   - "Consider Z" → recommendation, mark clearly

## FORMATTING

- Use **bold** for key terms and risk levels
- Use bullet points for lists
- Use ✓ for protections/positives
- Use ⚠ for risks/concerns/gaps
- Keep response concise but complete

## USER QUESTION

{request.question}

## EXTRACTED DATA FOR THIS DEAL

{context}

## YOUR RESPONSE

Answer the user's question following all rules above. Be specific and cite sources where available."""

    # Step 4: Call Claude
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )

        answer_text = response.content[0].text

        # Step 5: Extract citations from the answer
        citations = _extract_citations_from_answer(answer_text)

        return {
            "question": request.question,
            "answer": answer_text,
            "citations": citations,
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


def _format_rp_provision_as_context(rp_response: Dict) -> str:
    """Format RP provision data as structured context for Claude."""

    lines = []
    lines.append(f"Deal ID: {rp_response['deal_id']}")
    lines.append(f"Provision: {rp_response['provision_type']}")
    lines.append(f"Scalar answers: {rp_response['scalar_count']}")
    lines.append(f"Multiselect answers: {rp_response['multiselect_count']}")
    lines.append("")

    # Scalar answers grouped by category (inferred from attribute name)
    lines.append("### SCALAR ANSWERS (Boolean/Numeric/Text)")
    lines.append("")

    scalar_answers = rp_response.get("scalar_answers", {})

    # Group by prefix
    groups = {}
    for attr_name, value in scalar_answers.items():
        # Extract category from attribute name (e.g., builder_basket_exists -> builder)
        parts = attr_name.split("_")
        prefix = parts[0] if parts else "other"
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append((attr_name, value))

    for prefix, attrs in sorted(groups.items()):
        lines.append(f"**{prefix.upper()}**")
        for attr_name, value in attrs:
            # Format value
            if isinstance(value, bool):
                value_str = "Yes" if value else "No"
            elif isinstance(value, float) and value == int(value):
                value_str = str(int(value))
            else:
                value_str = str(value)
            lines.append(f"  - {attr_name}: {value_str}")
        lines.append("")

    # Multiselect answers
    lines.append("### MULTISELECT ANSWERS (Concept Applicabilities)")
    lines.append("")

    multiselect_answers = rp_response.get("multiselect_answers", {})

    for concept_type, concepts in sorted(multiselect_answers.items()):
        concept_names = [c["name"] for c in concepts]
        lines.append(f"**{concept_type}**: {', '.join(concept_names)}")
    lines.append("")

    return "\n".join(lines)


def _extract_citations_from_answer(answer_text: str) -> List[Dict[str, Any]]:
    """Extract page citations from the answer."""

    # Find all [p.XX] patterns
    page_refs = re.findall(r'\[p\.(\d+)\]', answer_text)
    pages = list(set(int(p) for p in page_refs))
    pages.sort()

    # Build citation list
    citations = []
    for page in pages:
        citations.append({
            "page": page,
            "text": None  # Would need source_text from provenance to populate
        })

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

            return {
                "provision_id": provision_id,
                "stored_concept_types": sorted(list(concept_types)),
                "total_applicabilities": len(records),
                "sample_records": records[:20],
                "multiselect_questions": sorted(multiselect_questions),
                "expected_mapping": {
                    "rp_a2": "dividend_applies_to_entity",
                    "rp_a3": "dividend_action",
                    "rp_b1": "intercompany_recipient",
                    "rp_c2": "mgmt_equity_covered_person",
                    "rp_c3": "mgmt_equity_trigger_event",
                    "rp_d2": "tax_group_type",
                    "rp_e1": "equity_award_type",
                    "rp_f7": "builder_source",
                    "rp_f8": "builder_use",
                    "rp_h1": "holdco_overhead_cost",
                    "rp_h2": "holdco_transaction_cost",
                    "rp_i1": "reallocation_source_basket",
                    "rp_i2": "reallocation_target_basket",
                    "rp_k4": "jcrew_bound_entity",
                    "rp_k5": "jcrew_trigger_condition",
                    "rp_k6": "jcrew_ip_type",
                    "rp_k7": "jcrew_transfer_type",
                    "rp_s1": "rdp_payment_type",
                    "rp_t1": "rdp_basket",
                }
            }

        finally:
            tx.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Debug error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
