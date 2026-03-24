"""
Deal endpoints - Simplified for V3 launch (TypeDB 3.x API)
"""
import hashlib
import json
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
from app.services.topic_router import get_topic_router
from app.services.graph_traversal import get_rp_entities, get_provision_entities
from app.services.graph_reader import _get_annotation_map, _get_question_texts, safe_val, run_query
from app.services.graph_storage import GraphStorage
from app.schemas.models import UploadResponse, ExtractionStatus
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Deals"])

# Concurrency guard: prevent duplicate extractions for the same deal
_extraction_locks: Dict[str, bool] = {}  # deal_id -> is_running


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
    show_reasoning: bool = False


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: List[Dict[str, Any]]

# In-memory extraction status tracking (for MVP; use Redis in production)
extraction_status: Dict[str, ExtractionStatus] = {}


def _detect_covenant_type(question: str) -> str:
    """Detect whether a question is about MFN, RP, or both.

    SSoT-compliant: delegates to TopicRouter which derives keyword
    mappings from TypeDB category metadata at runtime.

    Returns: "mfn", "rp", or "both"
    """
    try:
        router = get_topic_router()
        return router.detect_covenant_type(question)
    except Exception as e:
        logger.warning("TopicRouter unavailable, defaulting to 'both': %s", e)
        return "both"


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
                    try { $d has created_at $ca; };
                select $id, $name, $ca;
            """
            result = tx.query(query).resolve()

            deals = []
            for row in result.as_concept_rows():
                deal_id = _safe_get_value(row, "id")
                deal_name = _safe_get_value(row, "name")
                created_at = _safe_get_value(row, "ca")
                if deal_id:  # Only add if we have a valid ID
                    deal_obj = {
                        "deal_id": deal_id,
                        "deal_name": deal_name or "Unknown",
                    }
                    if created_at:
                        deal_obj["created_at"] = str(created_at)
                    deals.append(deal_obj)
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

    try:
        # Read file and compute SHA-256 hash for deduplication
        contents = await file.read()
        pdf_hash = hashlib.sha256(contents).hexdigest()

        # Check for duplicate: query TypeDB for existing document with same hash
        existing_deal_id = None
        try:
            tx_read = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
            try:
                dup_query = f"""
                    match
                        $doc isa document, has document_hash "{pdf_hash}";
                        (deal: $deal, document: $doc) isa deal_has_document;
                        $deal has deal_id $did;
                    select $did;
                """
                result = tx_read.query(dup_query).resolve()
                for row in result.as_concept_rows():
                    existing_deal_id = _safe_get_value(row, "did")
                    break
            finally:
                tx_read.close()
        except Exception as e:
            logger.warning(f"Dedup check failed (proceeding with upload): {e}")

        if existing_deal_id:
            logger.info(
                f"Duplicate PDF detected (hash={pdf_hash[:12]}...). "
                f"Returning existing deal_id={existing_deal_id}"
            )
            return UploadResponse(
                deal_id=existing_deal_id,
                deal_name=deal_name,
                status="duplicate",
                message=f"This PDF was already uploaded as deal {existing_deal_id}. No re-extraction needed."
            )

        # Not a duplicate — proceed with new deal
        deal_id = str(uuid.uuid4())[:8]
        pdf_filename = f"{deal_id}.pdf"
        pdf_path = os.path.join(UPLOADS_DIR, pdf_filename)

        # Save PDF to disk
        with open(pdf_path, "wb") as f:
            f.write(contents)

        logger.info(f"Saved PDF: {pdf_path} ({len(contents)} bytes, hash={pdf_hash[:12]}...)")

        # Create deal + document in TypeDB, link via deal_has_document
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            # Escape quotes in strings
            safe_name = deal_name.replace('"', '\\"')
            safe_borrower = borrower.replace('"', '\\"')

            from datetime import datetime, timezone
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

            doc_url = f"/uploads/{pdf_filename}"

            query = f"""
                insert
                    $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{safe_name}",
                    has borrower_name "{safe_borrower}",
                    has created_at {now_iso};
                    $doc isa document,
                    has document_url "{doc_url}",
                    has document_hash "{pdf_hash}",
                    has created_at {now_iso};
                    (deal: $d, document: $doc) isa deal_has_document;
            """
            tx.query(query).resolve()
            tx.commit()
        except Exception as e:
            tx.close()
            raise e

        logger.info(f"Created deal in TypeDB: {deal_id} (with document hash)")

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

            # Check which provisions exist for this deal
            rp_answers = _load_provision_answers(tx, f"{deal_id}_rp")
            mfn_answers = _load_provision_answers(tx, f"{deal_id}_mfn")

            return {
                "deal_id": deal_id,
                "deal_name": _safe_get_value(rows[0], "name", "Unknown"),
                "answers": rp_answers,
                "applicabilities": {},
                "mfn_provision": {
                    "answers": mfn_answers,
                    "extracted": len(mfn_answers) > 0,
                }
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
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            query = f"""
                insert
                    $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{deal_name}",
                    has borrower_name "{borrower}",
                    has created_at {now_iso};
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
    Delete a deal and all related data.
    Order: answers → applicabilities → provisions → deal link → deal → files
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            # 1. Delete provision_has_answer for RP provision
            try:
                tx.query(f"""
                    match
                        $p isa rp_provision, has provision_id "{deal_id}_rp";
                        $rel isa provision_has_answer(provision: $p, question: $q);
                    delete $rel;
                """).resolve()
            except Exception:
                pass

            # 2. Delete concept_applicability for RP provision
            try:
                tx.query(f"""
                    match
                        $p isa rp_provision, has provision_id "{deal_id}_rp";
                        $rel isa concept_applicability(provision: $p, concept: $c);
                    delete $rel;
                """).resolve()
            except Exception:
                pass

            # 3. Delete provision_has_answer for MFN provision
            try:
                tx.query(f"""
                    match
                        $p isa mfn_provision, has provision_id "{deal_id}_mfn";
                        $rel isa provision_has_answer(provision: $p, question: $q);
                    delete $rel;
                """).resolve()
            except Exception:
                pass

            # 4. Delete concept_applicability for MFN provision
            try:
                tx.query(f"""
                    match
                        $p isa mfn_provision, has provision_id "{deal_id}_mfn";
                        $rel isa concept_applicability(provision: $p, concept: $c);
                    delete $rel;
                """).resolve()
            except Exception:
                pass

            # 5. Delete deal_has_provision relations
            try:
                tx.query(f"""
                    match
                        $d isa deal, has deal_id "{deal_id}";
                        $rel isa deal_has_provision(deal: $d, provision: $p);
                    delete $rel;
                """).resolve()
            except Exception:
                pass

            # 6. Delete rp_provision entity
            try:
                tx.query(f"""
                    match $p isa rp_provision, has provision_id "{deal_id}_rp";
                    delete $p;
                """).resolve()
            except Exception:
                pass

            # 7. Delete mfn_provision entity
            try:
                tx.query(f"""
                    match $p isa mfn_provision, has provision_id "{deal_id}_mfn";
                    delete $p;
                """).resolve()
            except Exception:
                pass

            # 8. Delete deal entity
            tx.query(f"""
                match $d isa deal, has deal_id "{deal_id}";
                delete $d;
            """).resolve()

            tx.commit()
            logger.info(f"Deleted deal {deal_id} from TypeDB")

        except Exception as e:
            tx.close()
            raise e

        # 9. Delete PDF file
        pdf_path = Path(UPLOADS_DIR) / f"{deal_id}.pdf"
        if pdf_path.exists():
            pdf_path.unlink()
            logger.info(f"Deleted PDF: {pdf_path}")

        # 10. Delete RP universe file
        rp_path = Path(UPLOADS_DIR) / f"{deal_id}_rp_universe.txt"
        if rp_path.exists():
            rp_path.unlink()

        # 11. Delete MFN universe file
        mfn_path = Path(UPLOADS_DIR) / f"{deal_id}_mfn_universe.txt"
        if mfn_path.exists():
            mfn_path.unlink()

        # 12. Clear extraction status
        if deal_id in extraction_status:
            del extraction_status[deal_id]

        return {"status": "deleted", "deal_id": deal_id}

    except Exception as e:
        logger.error(f"Error deleting deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def run_extraction(deal_id: str, pdf_path: str):
    """
    Background task: extract RP provision from PDF and store in TypeDB.

    V4 Unified pipeline:
    1. Parse PDF → text with page markers
    2. Segment document → extract RP universe
    3. Single Claude call → entities (baskets, blockers, etc.) + flat answers (~200 Q)
    4. Store entities + answers to TypeDB in one pass
    5. Run J.Crew Tiers 2-3 (separate context needed)
    6. Run MFN extraction (separate provision type)
    """
    # Concurrency guard — skip if extraction already running for this deal
    if _extraction_locks.get(deal_id):
        logger.warning(f"Background extraction SKIPPED for {deal_id} — extraction already in progress")
        return

    extraction_svc = get_extraction_service()
    _extraction_locks[deal_id] = True

    try:
        # Update status: parsing PDF
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="extracting",
            progress=10,
            current_step="Parsing PDF..."
        )

        # Step 1: Parse PDF
        document_text = extraction_svc.parse_document(pdf_path)

        # Step 2: Extract RP Universe
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="extracting",
            progress=20,
            current_step="Extracting RP-relevant content..."
        )
        rp_universe = extraction_svc.extract_rp_universe(document_text)
        segment_map = getattr(extraction_svc, '_last_segment_map', None)
        universe_chars = len(rp_universe.raw_text)
        universe_kb = universe_chars // 1024

        # Cache RP universe text to disk
        try:
            universe_path = os.path.join(settings.upload_dir, f"{deal_id}_rp_universe.txt")
            os.makedirs(settings.upload_dir, exist_ok=True)
            with open(universe_path, "w", encoding="utf-8") as f:
                f.write(rp_universe.raw_text)
        except Exception as e:
            logger.warning(f"Could not cache RP universe text: {e}")

        # Steps 3-5: V4 Unified extraction (entities + answers + JC tiers 2-3)
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="extracting",
            progress=40,
            current_step="Running V4 unified extraction (entities + answers)..."
        )

        v4_result = await extraction_svc.extract_rp_v4_unified(
            deal_id=deal_id,
            document_text=document_text,
            rp_universe=rp_universe,
            segment_map=segment_map,
        )

        answers_stored = v4_result.storage_result.get("answers_stored", 0)
        entities_stored = v4_result.storage_result.get("entities_created", 0)

        logger.info(
            f"V4 unified extraction for {deal_id}: "
            f"{answers_stored} answers, {entities_stored} entities "
            f"in {v4_result.extraction_time_seconds:.1f}s"
        )

        # ── MFN Extraction (non-blocking) ─────────────────────────────
        # Separate provision type — not affected by RP unification
        if document_text:
            try:
                extraction_status[deal_id] = ExtractionStatus(
                    deal_id=deal_id,
                    status="extracting",
                    progress=85,
                    current_step="Extracting MFN provision..."
                )
                # Reuse segmentation from RP extraction
                if not segment_map:
                    segment_map = extraction_svc.segment_document(document_text)

                mfn_universe_text = extraction_svc._build_mfn_universe_from_segments(
                    document_text, segment_map
                )

                # Fallback: if segmenter yields too little, use Claude-based extraction
                if not mfn_universe_text or len(mfn_universe_text) < 1000:
                    logger.warning("Segmenter MFN universe too small, falling back to Claude")
                    mfn_universe_text = extraction_svc.extract_mfn_universe(
                        document_text
                    )
                if mfn_universe_text:
                    # Persist MFN universe text for eval pipeline
                    mfn_universe_path = os.path.join(
                        settings.upload_dir, f"{deal_id}_mfn_universe.txt"
                    )
                    os.makedirs(settings.upload_dir, exist_ok=True)
                    with open(mfn_universe_path, "w", encoding="utf-8") as f:
                        f.write(mfn_universe_text)
                    logger.info(f"MFN universe saved: {len(mfn_universe_text)} chars")

                    # Consolidated MFN extraction (2 calls instead of 6)
                    # Stores answers internally via _store_mfn_answers
                    mfn_result = await extraction_svc.run_mfn_extraction_consolidated(
                        deal_id, mfn_universe_text, document_text
                    )

                    if mfn_result["answers"]:
                        logger.info(
                            f"MFN extraction complete: "
                            f"{mfn_result['answered']}/{mfn_result['total_questions']} answers"
                        )

                        # MFN entity extraction (Channel 3)
                        extraction_status[deal_id] = ExtractionStatus(
                            deal_id=deal_id,
                            status="extracting",
                            progress=95,
                            current_step="Extracting MFN entities..."
                        )
                        mfn_entity_result = await extraction_svc.run_mfn_entity_extraction(
                            deal_id, mfn_universe_text
                        )
                        logger.info(
                            f"MFN entity extraction: "
                            f"{mfn_entity_result['entities_stored']} entities stored"
                        )
                    else:
                        logger.warning(
                            f"MFN extraction returned no answers: "
                            f"{mfn_result.get('errors')}"
                        )
                else:
                    logger.warning(
                        "MFN universe extraction returned empty — "
                        "no MFN provision found or extraction failed"
                    )
            except Exception as mfn_err:
                logger.error(
                    f"MFN extraction failed for {deal_id} (non-blocking): {mfn_err}",
                    exc_info=True
                )

        # Compute MFN pattern flags from TypeDB functions
        try:
            extraction_svc._compute_mfn_pattern_flags(deal_id, f"{deal_id}_mfn")
        except Exception as flag_err:
            logger.warning(f"MFN pattern flags failed (non-blocking): {flag_err}")

        # Cross-reference MFN ↔ RP provisions if both exist
        try:
            extraction_svc._create_cross_references(deal_id)
        except Exception as xref_err:
            logger.warning(f"Cross-reference creation failed (non-blocking): {xref_err}")

        # Update status: complete
        extraction_status[deal_id] = ExtractionStatus(
            deal_id=deal_id,
            status="complete",
            progress=100,
            current_step=(
                f"V4 unified: {answers_stored} answers, {entities_stored} baskets, "
                f"{universe_kb}KB universe in {v4_result.extraction_time_seconds:.1f}s"
            )
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
    finally:
        _extraction_locks.pop(deal_id, None)


@router.post("/{deal_id}/upload-pdf")
async def upload_pdf_for_deal(
    deal_id: str,
    file: UploadFile = File(...)
) -> Dict[str, Any]:
    """
    Upload a PDF for an existing deal.

    Use this to attach a PDF to a deal that was created without one.
    Does NOT trigger extraction.
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
            "message": "PDF uploaded successfully."
        }
    except Exception as e:
        logger.error(f"Failed to save PDF: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{deal_id}/re-extract")
async def re_extract_deal(deal_id: str) -> Dict[str, Any]:
    """
    Re-run V4 entity extraction from cached RP universe text.

    Skips PDF parsing and universe extraction (~$0.50).
    Re-runs the Claude entity+answers call (~$0.10) and re-stores to TypeDB.
    Useful when Channel 3 entities are missing but Channel 1 scalars exist.
    """
    logger.info(f"Re-extract requested for deal {deal_id}")

    # Concurrency guard — reject if extraction already running for this deal
    if _extraction_locks.get(deal_id):
        logger.warning(f"Re-extract REJECTED for {deal_id} — extraction already in progress")
        raise HTTPException(
            status_code=409,
            detail=f"Extraction already in progress for deal {deal_id}. Wait for it to complete."
        )

    from app.services.extraction import RPUniverse

    # Check cached RP universe text exists
    universe_path = os.path.join(UPLOADS_DIR, f"{deal_id}_rp_universe.txt")
    if not os.path.exists(universe_path):
        raise HTTPException(
            status_code=404,
            detail=f"No cached RP universe text at {universe_path}. Full re-extraction from PDF required."
        )

    with open(universe_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    if not raw_text or len(raw_text) < 100:
        raise HTTPException(status_code=400, detail="Cached RP universe text is empty or too short")

    rp_universe = RPUniverse(raw_text=raw_text)

    # Ensure deal entity exists in TypeDB (may have been wiped by init_schema)
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            rows = list(tx.query(f'match $d isa deal, has deal_id "{deal_id}"; select $d;').resolve().as_concept_rows())
            deal_exists = len(rows) > 0
        finally:
            tx.close()

        if not deal_exists:
            logger.info(f"Deal {deal_id} not in TypeDB — creating stub entity")
            tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
            try:
                tx.query(f'insert $d isa deal, has deal_id "{deal_id}", has deal_name "re-extracted";').resolve()
                tx.commit()
            except Exception:
                if tx.is_open():
                    tx.close()
                raise
    except Exception as e:
        logger.warning(f"Could not ensure deal entity: {e}")

    extraction_svc = get_extraction_service()
    _extraction_locks[deal_id] = True
    try:
        v4_result = await extraction_svc.extract_rp_v4_unified(
            deal_id=deal_id,
            document_text="",  # No full PDF text — JC tiers 2-3 will be skipped
            rp_universe=rp_universe,
            segment_map=None,
            model="claude-sonnet-4-6",  # Use Sonnet for re-extraction (~$0.10 vs $4.60)
        )

        return {
            "status": "success",
            "deal_id": deal_id,
            "universe_chars": len(raw_text),
            "storage_result": v4_result.storage_result,
            "extraction_time_seconds": v4_result.extraction_time_seconds,
            "model_used": v4_result.model_used,
            "total_cost_usd": v4_result.total_cost_usd,
        }
    except Exception as e:
        logger.error(f"Re-extraction failed for {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _extraction_locks.pop(deal_id, None)



@router.post("/{deal_id}/re-extract-mfn")
async def re_extract_mfn(deal_id: str) -> Dict[str, Any]:
    """
    Re-run MFN extraction from cached MFN universe text (or rebuild from PDF).

    Steps:
    1. Load cached MFN universe text, or rebuild from PDF via segmenter
    2. Clean up existing MFN answers/entities
    3. Run consolidated MFN scalar extraction (MFN1-MFN6)
    4. Run MFN entity extraction (exclusions, yield def, sunset, freebie)
    5. Compute MFN pattern flags via TypeDB functions
    """
    lock_key = f"{deal_id}_mfn"
    logger.info(f"MFN re-extract requested for deal {deal_id}")

    # Concurrency guard
    if _extraction_locks.get(lock_key):
        logger.warning(f"MFN re-extract REJECTED for {deal_id} — already in progress")
        raise HTTPException(
            status_code=409,
            detail=f"MFN extraction already in progress for deal {deal_id}."
        )

    # 1. Find MFN universe text
    mfn_universe_path = os.path.join(UPLOADS_DIR, f"{deal_id}_mfn_universe.txt")
    mfn_universe_text = None

    if os.path.exists(mfn_universe_path):
        with open(mfn_universe_path, "r", encoding="utf-8") as f:
            mfn_universe_text = f.read()
        if mfn_universe_text and len(mfn_universe_text) >= 500:
            logger.info(f"Loaded cached MFN universe: {len(mfn_universe_text)} chars")
        else:
            mfn_universe_text = None

    # Fallback: rebuild from PDF via segmenter
    if not mfn_universe_text:
        pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
        if not os.path.exists(pdf_path):
            raise HTTPException(
                status_code=404,
                detail=f"No cached MFN universe and no PDF at {pdf_path}."
            )

        logger.info(f"Rebuilding MFN universe from PDF: {pdf_path}")
        extraction_svc = get_extraction_service()
        document_text = extraction_svc.parse_document(pdf_path)
        segment_map = extraction_svc.segment_document(document_text)

        mfn_universe_text = extraction_svc._build_mfn_universe_from_segments(
            document_text, segment_map
        )
        if not mfn_universe_text or len(mfn_universe_text) < 1000:
            logger.warning("Segmenter MFN universe too small, falling back to Claude")
            mfn_universe_text = extraction_svc.extract_mfn_universe(document_text)

        if not mfn_universe_text:
            raise HTTPException(
                status_code=404,
                detail="Could not extract MFN universe from PDF."
            )

        # Cache for next time
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        with open(mfn_universe_path, "w", encoding="utf-8") as f:
            f.write(mfn_universe_text)
        logger.info(f"MFN universe rebuilt and cached: {len(mfn_universe_text)} chars")

    # Ensure deal entity exists
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            rows = list(tx.query(
                f'match $d isa deal, has deal_id "{deal_id}"; select $d;'
            ).resolve().as_concept_rows())
            deal_exists = len(rows) > 0
        finally:
            tx.close()

        if not deal_exists:
            logger.info(f"Deal {deal_id} not in TypeDB — creating stub entity")
            tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
            try:
                tx.query(
                    f'insert $d isa deal, has deal_id "{deal_id}", has deal_name "re-extracted";'
                ).resolve()
                tx.commit()
            except Exception:
                if tx.is_open():
                    tx.close()
                raise
    except Exception as e:
        logger.warning(f"Could not ensure deal entity: {e}")

    # 2. Clean up existing MFN data
    provision_id = f"{deal_id}_mfn"
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            rows = list(tx.query(
                f'match $p isa mfn_provision, has provision_id "{provision_id}"; select $p;'
            ).resolve().as_concept_rows())
            mfn_exists = len(rows) > 0
        finally:
            tx.close()

        if mfn_exists:
            logger.info(f"MFN provision {provision_id} exists — cleaning up old data")
            cleanup_queries = [
                # Delete provision_has_answer relations
                f'''match
                    $p isa mfn_provision, has provision_id "{provision_id}";
                    $rel isa provision_has_answer, links (provision: $p);
                delete $rel;''',
                # Delete concept_applicability relations
                f'''match
                    $p isa mfn_provision, has provision_id "{provision_id}";
                    $rel isa concept_applicability, links (provision: $p);
                delete $rel;''',
                # Delete MFN entities via provision_has_extracted_entity (polymorphic)
                f'''match
                    $p isa mfn_provision, has provision_id "{provision_id}";
                    $rel isa provision_has_extracted_entity, links (provision: $p, extracted: $e);
                delete $e;''',
            ]
            for q in cleanup_queries:
                try:
                    tx = typedb_client.driver.transaction(
                        settings.typedb_database, TransactionType.WRITE
                    )
                    try:
                        tx.query(q).resolve()
                        tx.commit()
                    except Exception:
                        if tx.is_open():
                            tx.close()
                except Exception as cleanup_err:
                    logger.warning(f"MFN cleanup query failed (non-blocking): {cleanup_err}")
    except Exception as e:
        logger.warning(f"MFN cleanup check failed: {e}")

    # 3-6. Run MFN extraction
    extraction_svc = get_extraction_service()
    _extraction_locks[lock_key] = True
    try:
        # Use full document text for consolidated extraction (needs it for Batch B context)
        # If we rebuilt from PDF, we have document_text; otherwise pass empty string
        doc_text_for_context = ""
        pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
        if os.path.exists(pdf_path):
            doc_text_for_context = extraction_svc.parse_document(pdf_path)

        # Step 3: Consolidated MFN scalar extraction (MFN1-MFN6)
        mfn_result = await extraction_svc.run_mfn_extraction_consolidated(
            deal_id, mfn_universe_text, doc_text_for_context
        )
        logger.info(
            f"MFN scalar extraction: {mfn_result['answered']}/{mfn_result['total_questions']} answers"
        )

        # Step 4: MFN entity extraction (Channel 3)
        entity_result = {"entities_stored": 0}
        if mfn_result["answers"]:
            entity_result = await extraction_svc.run_mfn_entity_extraction(
                deal_id, mfn_universe_text
            )
            logger.info(f"MFN entity extraction: {entity_result['entities_stored']} entities")

        # Step 5: Compute MFN pattern flags
        try:
            extraction_svc._compute_mfn_pattern_flags(deal_id, provision_id)
            logger.info("MFN pattern flags computed")
        except Exception as flag_err:
            logger.warning(f"MFN pattern flags failed (non-blocking): {flag_err}")

        # Step 6: Cross-reference MFN ↔ RP if both exist
        try:
            extraction_svc._create_cross_references(deal_id)
        except Exception as xref_err:
            logger.warning(f"Cross-reference failed (non-blocking): {xref_err}")

        return {
            "status": "success",
            "deal_id": deal_id,
            "provision_id": provision_id,
            "mfn_universe_chars": len(mfn_universe_text),
            "scalar_answers": mfn_result.get("answered", 0),
            "total_questions": mfn_result.get("total_questions", 0),
            "entities_stored": entity_result.get("entities_stored", 0),
        }
    except Exception as e:
        logger.error(f"MFN re-extraction failed for {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _extraction_locks.pop(lock_key, None)


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

            # LEGACY: concept_applicability will be removed entirely once all covenant types
            # route multiselect answers through entity booleans.
            # RP already has full routing. MFN needs target_entity_type/target_entity_attribute
            # seed data on its concept instances before this can be removed.
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

        finally:
            tx.close()

        # Entity booleans: all annotated attributes from typed entities (SSoT)
        entity_booleans = _load_entity_booleans(provision_id) if extraction_complete else {}

        return {
            "deal_id": deal_id,
            "extraction_complete": extraction_complete,
            "answer_count": answer_count,
            "total_questions": len(questions),
            "answers": answers,
            "entity_booleans": entity_booleans,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting answers for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _load_entity_booleans(provision_id: str) -> dict:
    """Load all annotated entity attributes for a provision.

    Returns dict[entity_type] → dict[attr_name] → {value, question_id, question_text}
    For multi-instance entities, returns a list of dicts instead.

    Both data sources are TypeDB SSoT:
    - Annotation map: question_annotates_attribute relations
    - Entity relation map: schema introspection of plays declarations
    No hardcoded attribute lists or relation mappings.
    """
    annotation_map = _get_annotation_map()
    question_texts = _get_question_texts()
    entity_relation_map = GraphStorage._load_entity_relation_map()
    result = {}

    for entity_type, attrs in annotation_map.items():
        # Skip _exists annotations (entity-level markers, not attributes)
        real_attrs = {k: v for k, v in attrs.items() if k != "_exists"}
        if not real_attrs:
            continue

        # Look up relation from schema introspection cache
        relation_info = entity_relation_map.get(entity_type)
        if not relation_info:
            continue

        relation_type, provision_role, entity_role = relation_info

        # Build dynamic TQL from annotation map keys
        try_clauses = []
        var_map = {}
        select_vars = ["$e"]

        for i, attr_name in enumerate(real_attrs.keys()):
            var_name = f"a{i}"
            try_clauses.append(f'try {{ $e has {attr_name} ${var_name}; }};')
            var_map[var_name] = attr_name
            select_vars.append(f"${var_name}")

        indent = " " * 16
        query = f'''
            match
                $p isa rp_provision, has provision_id "{provision_id}";
                ({provision_role}: $p, {entity_role}: $e) isa {relation_type};
                $e isa {entity_type};
{chr(10).join(indent + tc for tc in try_clauses)}
            select {", ".join(select_vars)};
        '''

        try:
            rows = run_query(query)
            instances = []
            for row in rows:
                entity_data = {}
                for var_name, attr_name in var_map.items():
                    val = safe_val(row, var_name)
                    if val is not None:
                        qid = real_attrs.get(attr_name)
                        qt = question_texts.get(qid) if qid else None
                        entity_data[attr_name] = {
                            "value": val,
                            "question_id": qid,
                            "question_text": qt,
                        }
                if entity_data:
                    instances.append(entity_data)

            if instances:
                # Single-instance types → dict, multi-instance → list
                result[entity_type] = instances[0] if len(instances) == 1 else instances

        except Exception as e:
            logger.warning(f"Failed to load entity booleans for {entity_type}: {e}")

    return result


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

            # LEGACY: concept_applicability will be removed entirely once all covenant types
            # route multiselect answers through entity booleans.
            # RP already has full routing. MFN needs target_entity_type/target_entity_attribute
            # seed data on its concept instances before this can be removed.
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

        finally:
            tx.close()

        # Entity booleans: all annotated attributes from typed entities (SSoT)
        entity_booleans = _load_entity_booleans(provision_id)

        return {
            "deal_id": deal_id,
            "provision_id": provision_id,
            "provision_type": "rp_provision",
            "scalar_answers": scalar_answers,
            "pattern_flags": pattern_flags,
            "multiselect_answers": multiselect_answers,  # LEGACY — remove once frontend reads entity_booleans
            "entity_booleans": entity_booleans,
            "scalar_count": len(scalar_answers),
            "pattern_flag_count": len(pattern_flags),
            "multiselect_count": sum(len(v) for v in multiselect_answers.values()),
            "entity_boolean_types": len(entity_booleans),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting RP provision for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/mfn-provision")
async def get_mfn_provision(deal_id: str) -> Dict[str, Any]:
    """
    Get the MFN provision for a deal via provision_has_answer + concept_applicability.

    Returns:
        - provision_id
        - scalar_answers: keyed by question_id with typed values + provenance
        - pattern_flags: flat attributes (yield_exclusion_pattern_detected)
        - multiselect_answers: concept_applicability relations grouped by concept type
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    provision_id = f"{deal_id}_mfn"

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # Check if provision exists
            check_query = f"""
                match $p isa mfn_provision, has provision_id "{provision_id}";
                select $p;
            """
            check_result = tx.query(check_query).resolve()
            if not list(check_result.as_concept_rows()):
                raise HTTPException(status_code=404, detail="MFN provision not found for this deal")

            # Get scalar answers via provision_has_answer (SSoT)
            scalar_answers = _load_provision_answers(tx, provision_id)

            # Get pattern flags (flat attributes on mfn_provision)
            pattern_flags = {}
            for flag_name in (
                "yield_exclusion_pattern_detected",
                "reclassification_loophole_detected",
                "mfn_amendment_vulnerable",
                "mfn_exclusion_stacking_detected",
                "sunset_timing_loophole_detected",
                "bridge_to_term_loophole_detected",
                "currency_arbitrage_detected",
                "freebie_oversized_detected",
                "mfn_margin_only_weakness_detected",
                "mfn_comprehensive_protection_detected",
            ):
                try:
                    flag_query = f"""
                        match
                            $p isa mfn_provision, has provision_id "{provision_id}",
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
                    $p isa mfn_provision, has provision_id "{provision_id}";
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
                "provision_type": "mfn_provision",
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
        logger.error(f"Error getting MFN provision for deal {deal_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{deal_id}/mfn")
async def get_deal_mfn(deal_id: str) -> Dict[str, Any]:
    """Get MFN provision data for a deal."""
    try:
        result = await get_mfn_provision(deal_id)
        return {
            "deal_id": deal_id,
            "extracted": result.get("scalar_count", 0) > 0,
            "answer_count": result.get("scalar_count", 0),
            "answers": result.get("scalar_answers", {}),
            "applicabilities": result.get("multiselect_answers", {}),
        }
    except HTTPException as e:
        if e.status_code == 404:
            return {
                "deal_id": deal_id,
                "extracted": False,
                "answer_count": 0,
                "answers": {},
                "applicabilities": {},
            }
        raise


@router.get("/{deal_id}/rp-universe")
async def get_rp_universe_text(deal_id: str):
    """Serve the cached RP universe text. No regeneration."""
    rp_path = Path(UPLOADS_DIR) / f"{deal_id}_rp_universe.txt"
    if not rp_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"RP universe text not cached for deal {deal_id}. "
                   f"Re-upload the PDF to generate it."
        )
    text = rp_path.read_text(encoding="utf-8")
    return {"deal_id": deal_id, "text": text, "chars": len(text)}


@router.get("/{deal_id}/mfn-universe")
async def get_mfn_universe_text(deal_id: str):
    """Serve the cached MFN universe text for a deal (eval pipeline).

    If the cached file doesn't exist but the PDF does, regenerates it
    from the PDF using the segmenter-based extraction.
    """
    mfn_path = Path(UPLOADS_DIR) / f"{deal_id}_mfn_universe.txt"

    if not mfn_path.exists():
        # Try to regenerate from PDF
        pdf_path = Path(UPLOADS_DIR) / f"{deal_id}.pdf"
        if not pdf_path.exists():
            raise HTTPException(
                status_code=404,
                detail="MFN universe text not found and PDF not available for regeneration"
            )
        logger.info(f"Regenerating MFN universe for {deal_id} from PDF...")
        from app.services.pdf_parser import PDFParser
        svc = get_extraction_service()
        parser = PDFParser()
        pages = parser.extract_pages(str(pdf_path))
        document_text = parser.get_full_text(pages)

        segment_map = svc.segment_document(document_text)
        mfn_text = svc._build_mfn_universe_from_segments(
            document_text, segment_map
        )
        if not mfn_text or len(mfn_text) < 1000:
            logger.warning("Segmenter MFN universe too small, falling back to Claude")
            mfn_text = svc.extract_mfn_universe(document_text)

        if mfn_text:
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            mfn_path.write_text(mfn_text, encoding="utf-8")
            logger.info(f"MFN universe regenerated and saved: {len(mfn_text)} chars")
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to regenerate MFN universe from PDF"
            )

    text = mfn_path.read_text(encoding="utf-8")
    return {"deal_id": deal_id, "text": text, "chars": len(text)}


@router.post("/{deal_id}/qa")
async def deal_qa(deal_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Q&A endpoint — DEPRECATED.  Redirects all questions to the Claude-powered
    /ask endpoint which uses TopicRouter for SSoT-compliant routing.

    Kept for backward compatibility; new clients should call POST /{deal_id}/ask.
    """
    question = request.get("question", "")
    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    return await ask_question(deal_id, AskRequest(question=question))


@router.post("/{deal_id}/ask")
async def ask_question(deal_id: str, request: AskRequest) -> Dict[str, Any]:
    """
    Answer a natural language question about a deal using extracted data.

    Auto-detects whether the question is about MFN, RP, or both covenants
    and loads the appropriate data and synthesis rules.

    Flow:
    1. Detect covenant type from question
    2. Fetch answers for the deal (MFN, RP, or both)
    3. Format as structured context
    4. Call Claude with appropriate synthesis rules
    5. Return synthesized answer with citations
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Step 1: Route question via TopicRouter (SSoT-compliant)
    try:
        topic_router = get_topic_router()
        route_result = topic_router.route(request.question)
        covenant_type = route_result.covenant_type
    except Exception as e:
        logger.warning("TopicRouter unavailable, defaulting to 'both': %s", e)
        covenant_type = "both"
        route_result = None

    # Step 2: Load provision data based on detected type
    rp_response = None
    mfn_response = None

    if covenant_type in ("rp", "both"):
        try:
            rp_response = await get_rp_provision(deal_id)
        except HTTPException as e:
            if e.status_code != 404:
                raise

    if covenant_type in ("mfn", "both"):
        try:
            mfn_response = await get_mfn_provision(deal_id)
        except HTTPException as e:
            if e.status_code != 404:
                raise

    # If MFN-only question but no MFN data, fall back to RP
    if covenant_type == "mfn" and not mfn_response:
        try:
            rp_response = await get_rp_provision(deal_id)
            covenant_type = "rp"
        except HTTPException:
            pass

    # Check we have some data
    total_scalar = 0
    total_multiselect = 0
    if rp_response:
        total_scalar += rp_response.get("scalar_count", 0)
        total_multiselect += rp_response.get("multiselect_count", 0)
    if mfn_response:
        total_scalar += mfn_response.get("scalar_count", 0)
        total_multiselect += mfn_response.get("multiselect_count", 0)

    if total_scalar == 0 and total_multiselect == 0:
        raise HTTPException(
            status_code=400,
            detail="No extracted data found. Please upload and extract a document first."
        )

    # Step 3: Load question metadata for the relevant covenant types
    question_meta = {}  # {question_id: {question_text, category_id, category_name}}
    concept_type_labels = {}  # {concept_type: question_text} for multiselect labels

    covenant_types_to_load = []
    if rp_response:
        covenant_types_to_load.append("RP")
    if mfn_response:
        covenant_types_to_load.append("MFN")

    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for ct in covenant_types_to_load:
                meta_query = f"""
                    match
                        $q isa ontology_question,
                            has covenant_type "{ct}",
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

    # Step 4: Format answers as structured context for Claude
    context_parts = []
    if rp_response:
        rp_context = _format_rp_provision_as_context(
            rp_response, question_meta, concept_type_labels
        )
        if mfn_response:
            context_parts.append("# RESTRICTED PAYMENTS DATA\n\n" + rp_context)
        else:
            context_parts.append(rp_context)
    if mfn_response:
        mfn_context = _format_rp_provision_as_context(
            mfn_response, question_meta, concept_type_labels
        )
        if rp_response:
            context_parts.append("# MFN (MOST FAVORED NATION) DATA\n\n" + mfn_context)
        else:
            context_parts.append(mfn_context)

        # MFN entity context now flows through polymorphic fetch (Prompt 3)

    context = "\n\n".join(context_parts)

    # Step 5: Build system rules based on covenant type
    # Load category-specific synthesis guidance from TypeDB (SSoT)
    if route_result and topic_router:
        category_guidance = topic_router.get_synthesis_guidance(route_result.matched_categories)
        if not category_guidance:
            # Broad question — load ALL categories for this covenant type
            all_cats = topic_router.get_all_categories()
            relevant = [c for c in all_cats.values()
                        if c.covenant_type.upper() == covenant_type.upper()
                        or covenant_type == "both"]
            category_guidance = topic_router.get_synthesis_guidance(relevant)
    else:
        category_guidance = ""

    if covenant_type == "mfn":
        covenant_subject = "MFN (Most Favored Nation) provision"
    elif covenant_type == "both":
        covenant_subject = "credit agreement covenants (Restricted Payments and MFN)"
    else:
        covenant_subject = "restricted payments covenant"

    system_rules = f"""You are a legal analyst answering questions about a credit agreement's {covenant_subject} using pre-extracted structured data.

## STRICT RULES

1. **CITATION REQUIRED**: Every factual claim must include a clause and page citation where available, formatted as [Section X.XX(y), p.XX]. Use the section references from the extracted data. If only a page number is available, use [p.XX]. Never cite just a page number if a section reference is also available.
2. **ONLY USE PROVIDED DATA**: Never invent facts not present in EXTRACTED DATA below
3. **QUALIFICATIONS REQUIRED**: If a qualification, condition, or exception exists in the data, you MUST mention it
4. **MISSING DATA**: If the requested information is not found, say "Not found in extracted data"
5. **OBJECTIVE ONLY**: Report what the document states. Do NOT characterize provisions as borrower-friendly, lender-friendly, aggressive, conservative, or any other subjective assessment. Do NOT assign risk scores or favorability ratings. Users are legal professionals who will form their own judgments.
6. **VERIFY BEFORE ANSWERING**: Before providing your final answer, cross-check every factual claim against the entity data provided. For each claim, identify which entity attribute supports it and confirm the value matches. If you cannot find supporting data for a claim, do not make it. Extracted boolean attributes that are true are findings, not possibilities — do not hedge with "may" or "potentially". State your verification briefly at the end: "Verified against: [entity types checked]"

## CATEGORY-SPECIFIC ANALYSIS GUIDANCE

{category_guidance}
## FORMATTING

- Use **bold** for key terms and defined terms
- Use bullet points for lists
- Keep response concise but complete
- State facts with citations. Do not editorialize.

## EVIDENCE TRACING

After your answer, on a new line, output an evidence block in this exact format:

<!-- EVIDENCE: ["rp_g5", "rp_f14", "mfn_01"] -->

List the question_ids of every extracted data point you relied on to form
your answer. Include ALL data points that influenced your response — both
those you cited explicitly and those you used for background context.
Order them by importance (most critical first). Include 5-20 question_ids.
This block MUST appear at the very end of your response."""

    user_prompt = f"""## USER QUESTION

{request.question}

## EXTRACTED DATA FOR THIS DEAL

{context}"""

    # Step 5b: If show_reasoning, swap prompts for structured reasoning mode
    if request.show_reasoning:
        from app.prompts.reasoning import (
            REASONING_SYSTEM_PROMPT,
            REASONING_FORMAT_INSTRUCTIONS,
        )
        active_system = REASONING_SYSTEM_PROMPT
        active_user = user_prompt + "\n\n" + REASONING_FORMAT_INSTRUCTIONS
        active_max_tokens = 6000
    else:
        active_system = system_rules
        active_user = user_prompt
        active_max_tokens = 4000

    # Step 6: Call Claude with system message + user message
    try:
        import time as _time
        from app.services.cost_tracker import extract_usage

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        model_used = settings.synthesis_model

        _qa_start = _time.time()
        response = client.messages.create(
            model=model_used,
            max_tokens=active_max_tokens,
            system=active_system,
            messages=[{"role": "user", "content": active_user}]
        )
        _qa_duration = _time.time() - _qa_start
        # QA cost is log-only (not aggregated into ExtractionCostSummary).
        # Acceptable: QA is low-cost (~$0.02-0.05/question), logged to Railway.
        # TODO: Persist QA cost to TypeDB or local storage if needed for billing.
        extract_usage(response, model_used, "qa", deal_id=deal_id, duration=_qa_duration)

        answer_text = response.content[0].text

        # Step 6b: If reasoning mode, parse JSON response
        reasoning_dict = None
        if request.show_reasoning:
            try:
                import json as _json
                from app.prompts.reasoning import ReasoningChain

                raw = answer_text.strip()
                # Strip markdown code fences (```json ... ``` or ``` ... ```)
                if raw.startswith("```"):
                    raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
                    raw = re.sub(r'\n?```\s*$', '', raw)
                # Find JSON object boundaries
                start = raw.find('{')
                end = raw.rfind('}')
                if start != -1 and end != -1:
                    raw = raw[start:end + 1]

                parsed = _json.loads(raw)
                reasoning_obj = ReasoningChain.model_validate(parsed["reasoning"])
                reasoning_dict = reasoning_obj.model_dump()
                answer_text = parsed["answer"]
            except Exception as e:
                logger.warning(
                    "Failed to parse reasoning JSON, using raw response: %s — first 200 chars: %.200s",
                    e, answer_text
                )
                reasoning_dict = None

        # Step 7: Parse evidence block and extract citations
        # Merge scalar answers from all loaded provisions for evidence lookup
        combined_response = {"scalar_answers": {}}
        if rp_response:
            combined_response["scalar_answers"].update(
                rp_response.get("scalar_answers", {})
            )
        if mfn_response:
            combined_response["scalar_answers"].update(
                mfn_response.get("scalar_answers", {})
            )

        clean_answer, evidence = _parse_evidence_block(
            answer_text, combined_response, question_meta
        )
        citations = _extract_citations_from_answer(clean_answer)

        response_data = {
            "question": request.question,
            "answer": clean_answer,
            "citations": citations,
            "evidence": evidence,
            "reasoning": reasoning_dict,
            "covenant_type": covenant_type,
            "model": model_used,
            "data_source": {
                "deal_id": deal_id,
                "scalar_answers": total_scalar,
                "multiselect_answers": total_multiselect,
            },
        }
        # Include routing metadata when available (helps debugging)
        if route_result is not None:
            response_data["routed_categories"] = [
                c.category_id for c in route_result.matched_categories
            ]
        return response_data

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error in Q&A: {e}")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in Q&A: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{deal_id}/ask-flat")
async def ask_question_flat(deal_id: str, request: AskRequest) -> Dict[str, Any]:
    """Answer a question using FLAT (unstructured) evidence format.

    Identical to /ask except:
    - Uses format_evidence_flat() instead of the structured formatter
    - Always uses show_reasoning=true
    - No covenant-specific synthesis rules in the system prompt
    - Adds "format": "flat" to the response

    Used for ablation testing to measure what TypeDB's structure adds.
    """
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    # Step 1: Route question (same as /ask)
    try:
        topic_router = get_topic_router()
        route_result = topic_router.route(request.question)
        covenant_type = route_result.covenant_type
    except Exception as e:
        logger.warning("TopicRouter unavailable, defaulting to 'both': %s", e)
        covenant_type = "both"
        route_result = None

    # Step 2: Load provision data (same as /ask)
    rp_response = None
    mfn_response = None

    if covenant_type in ("rp", "both"):
        try:
            rp_response = await get_rp_provision(deal_id)
        except HTTPException as e:
            if e.status_code != 404:
                raise

    if covenant_type in ("mfn", "both"):
        try:
            mfn_response = await get_mfn_provision(deal_id)
        except HTTPException as e:
            if e.status_code != 404:
                raise

    if covenant_type == "mfn" and not mfn_response:
        try:
            rp_response = await get_rp_provision(deal_id)
            covenant_type = "rp"
        except HTTPException:
            pass

    total_scalar = 0
    total_multiselect = 0
    if rp_response:
        total_scalar += rp_response.get("scalar_count", 0)
        total_multiselect += rp_response.get("multiselect_count", 0)
    if mfn_response:
        total_scalar += mfn_response.get("scalar_count", 0)
        total_multiselect += mfn_response.get("multiselect_count", 0)

    if total_scalar == 0 and total_multiselect == 0:
        raise HTTPException(
            status_code=400,
            detail="No extracted data found. Please upload and extract a document first."
        )

    # Step 3: Format as FLAT context (no categories, no entity hierarchies)
    context = _format_evidence_flat(rp_response, mfn_response)

    # Step 4: Minimal system prompt — no covenant-specific synthesis rules
    from app.prompts.reasoning import (
        REASONING_SYSTEM_PROMPT,
        REASONING_FORMAT_INSTRUCTIONS,
    )

    user_prompt = f"""## USER QUESTION

{request.question}

## EXTRACTED DATA FOR THIS DEAL

{context}

{REASONING_FORMAT_INSTRUCTIONS}"""

    # Step 5: Call Claude
    try:
        import time as _time
        from app.services.cost_tracker import extract_usage

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        model_used = settings.synthesis_model

        _qa_start = _time.time()
        response = client.messages.create(
            model=model_used,
            max_tokens=6000,
            system=REASONING_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        _qa_duration = _time.time() - _qa_start
        extract_usage(response, model_used, "qa", deal_id=deal_id, duration=_qa_duration)

        answer_text = response.content[0].text

        # Parse reasoning JSON (same code-fence stripping as /ask)
        reasoning_dict = None
        try:
            import json as _json
            from app.prompts.reasoning import ReasoningChain

            raw = answer_text.strip()
            if raw.startswith("```"):
                raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
                raw = re.sub(r'\n?```\s*$', '', raw)
            start = raw.find('{')
            end = raw.rfind('}')
            if start != -1 and end != -1:
                raw = raw[start:end + 1]

            parsed = _json.loads(raw)
            reasoning_obj = ReasoningChain.model_validate(parsed["reasoning"])
            reasoning_dict = reasoning_obj.model_dump()
            answer_text = parsed["answer"]
        except Exception as e:
            logger.warning(
                "Failed to parse flat reasoning JSON: %s — first 200 chars: %.200s",
                e, answer_text
            )
            reasoning_dict = None

        # Parse evidence and citations (same as /ask)
        combined_response = {"scalar_answers": {}}
        if rp_response:
            combined_response["scalar_answers"].update(
                rp_response.get("scalar_answers", {})
            )
        if mfn_response:
            combined_response["scalar_answers"].update(
                mfn_response.get("scalar_answers", {})
            )

        # Load question metadata for evidence resolution
        question_meta = {}
        try:
            tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
            try:
                covenant_types_to_load = []
                if rp_response:
                    covenant_types_to_load.append("RP")
                if mfn_response:
                    covenant_types_to_load.append("MFN")
                for ct in covenant_types_to_load:
                    meta_query = f"""
                        match
                            $q isa ontology_question,
                                has covenant_type "{ct}",
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
                        if qid and qtext:
                            question_meta[qid] = {
                                "question_text": qtext,
                                "category_id": _safe_get_value(row, "cid") or "ZZ",
                                "category_name": _safe_get_value(row, "cname") or "Other",
                            }
            finally:
                tx.close()
        except Exception:
            pass

        clean_answer, evidence = _parse_evidence_block(
            answer_text, combined_response, question_meta
        )
        citations = _extract_citations_from_answer(clean_answer)

        response_data = {
            "question": request.question,
            "answer": clean_answer,
            "citations": citations,
            "evidence": evidence,
            "reasoning": reasoning_dict,
            "format": "flat",
            "covenant_type": covenant_type,
            "model": model_used,
            "data_source": {
                "deal_id": deal_id,
                "scalar_answers": total_scalar,
                "multiselect_answers": total_multiselect,
            },
        }
        if route_result is not None:
            response_data["routed_categories"] = [
                c.category_id for c in route_result.matched_categories
            ]
        return response_data

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error in flat Q&A: {e}")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in flat Q&A: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{deal_id}/ask-graph")
async def ask_question_graph(deal_id: str, request: AskRequest, trace: bool = False) -> Dict[str, Any]:
    """
    Answer a question using Channel 3 graph entities instead of Channel 1 scalars.

    Same synthesis prompt as /ask, but data source is typed entities from TypeDB
    (baskets, sources, blocker, pathways, etc.) instead of flat key-value answers.

    Pass ?trace=true to include the full pipeline trace in the response.
    """
    import time as _time
    from app.services.trace_collector import TraceCollector

    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")

    collector = TraceCollector() if trace else None

    if collector:
        collector.question = request.question
        collector.deal_id = deal_id

    # Step 1: Covenant type routing
    start = _time.time()
    covenant_type = "rp"
    route_result = None
    try:
        topic_router = get_topic_router()
        route_result = topic_router.route(request.question)
        covenant_type = route_result.covenant_type
        if collector:
            collector.covenant_type = covenant_type
            collector.matched_categories = [
                {"id": cat.category_id, "name": cat.name, "covenant_type": cat.covenant_type}
                for cat in route_result.matched_categories
            ]
    except Exception as e:
        logger.warning("TopicRouter unavailable, defaulting to 'rp': %s", e)
        if collector:
            collector.covenant_type = "rp"
            collector.routing_fallback = "TopicRouter unavailable"
    if collector:
        collector.routing_duration_ms = (_time.time() - start) * 1000

    # Step 2+3+4: Fetch entity context (provision-type-aware)
    start = _time.time()
    if covenant_type == "mfn":
        all_docs, entity_context = get_provision_entities(deal_id, "mfn_provision", trace=collector)
    elif covenant_type == "both":
        rp_docs, rp_ctx = get_provision_entities(deal_id, "rp_provision", trace=collector)
        mfn_docs, mfn_ctx = get_provision_entities(deal_id, "mfn_provision", trace=collector)
        all_docs = rp_docs + mfn_docs
        entity_context = rp_ctx + "\n\n" + mfn_ctx if rp_ctx and mfn_ctx else rp_ctx or mfn_ctx or ""
    else:
        all_docs, entity_context = get_provision_entities(deal_id, "rp_provision", trace=collector)

    if collector:
        collector.provision_lookup_ms = (_time.time() - start) * 1000
    if not all_docs:
        raise HTTPException(status_code=400, detail=entity_context or "No entities found")

    # Metadata pre-filter: narrow entities by category → question → entity_type
    try:
        if route_result is None:
            raise ValueError("No route result — skip filtering")
        topic_router = get_topic_router()
        relevant_types = topic_router.get_relevant_entity_types(route_result.matched_categories)
        if relevant_types:
            filtered_docs = [d for d in all_docs if d.get("type_name") in relevant_types]
            if filtered_docs:  # only apply filter if it doesn't empty the set
                all_docs = filtered_docs
                entity_json = json.dumps(all_docs, indent=2, default=str)
                entity_context = f"## ENTITY DATA\n\n{entity_json}"
            if collector:
                collector.metadata_filter = {
                    "relevant_types": sorted(relevant_types),
                }
    except Exception as e:
        logger.warning(f"Metadata entity filter failed (using all entities): {e}")

    # Build synthesis prompt — load category-specific guidance from TypeDB (SSoT)
    if route_result and topic_router:
        category_guidance = topic_router.get_synthesis_guidance(route_result.matched_categories)
        if not category_guidance:
            # Broad question — load ALL categories for this covenant type
            all_cats = topic_router.get_all_categories()
            relevant = [c for c in all_cats.values()
                        if c.covenant_type.upper() == covenant_type.upper()
                        or covenant_type == "both"]
            category_guidance = topic_router.get_synthesis_guidance(relevant)
    else:
        category_guidance = ""

    if covenant_type == "mfn":
        covenant_subject = "MFN (Most Favored Nation) provision"
    elif covenant_type == "both":
        covenant_subject = "credit agreement covenants (Restricted Payments and MFN)"
    else:
        covenant_subject = "restricted payments covenant"

    system_rules = f"""You are a legal analyst answering questions about a credit agreement's {covenant_subject} using pre-extracted ENTITY DATA from a knowledge graph.

## DATA FORMAT

The data below is a JSON array of all extracted entities for this provision. Each entity has:
- `relation`: how this entity connects to the provision (e.g., "provision_has_basket", "provision_has_blocker")
- `type_name`: the specific entity type (e.g., "builder_basket", "jcrew_blocker", "investment_pathway")
- `attributes`: all attribute values as key-value pairs
- `annotations`: human-readable questions that explain what each attribute means — use these to understand attribute semantics
- `children`: nested sub-entities (e.g., builder basket sources, blocker exceptions), each with their own attributes and annotations
- `links`: connections to other entities in the graph (e.g., reallocation edges between baskets). Each link has:
  - `link_relation`: the relationship type (e.g., "basket_reallocates_to")
  - `my_role`: what role this entity plays (e.g., "target_basket")
  - `their_role`: what role the linked entity plays (e.g., "source_basket")
  - `linked_type`: the type of the linked entity
  - `linked_attributes`: all attributes of the linked entity
  - `relation_attributes`: attributes on the relationship itself (e.g., reallocation amounts)

When answering:
- Scan the entity array to find entities relevant to the question (use `type_name` and `relation` to navigate)
- Read `annotations` to understand what boolean/numeric attributes mean before interpreting their values
- Check `children` for supporting detail (e.g., builder basket `starter_amount_source` for the starter dollar amount)
- For capacity/aggregation questions, follow `links` to find cross-basket relationships — reallocation links show which baskets flow capacity to which other baskets, with dollar amounts and direction
- For capacity/aggregation questions, identify ALL relevant baskets and reallocation paths — check `basket_reallocation` entities for cross-covenant capacity flows
- Use `source_text` attributes for verbatim agreement language when available

## STRICT RULES

1. **CITATION REQUIRED**: Every factual claim must include a clause and page citation where available, formatted as [Section X.XX(y), p.XX].
2. **ONLY USE PROVIDED DATA**: Answer ONLY using the data provided below. If information is not present in the extracted data, say "Not found in extracted data." Do not use general knowledge about credit agreements.
3. **QUALIFICATIONS REQUIRED**: If a qualification, condition, or exception exists in the data, you MUST mention it.
4. **MISSING DATA**: If the requested information is not found, say "Not found in extracted data".
5. **OBJECTIVE ONLY**: Report what the document states. Do NOT characterize provisions as borrower-friendly, lender-friendly, aggressive, conservative, or any other subjective assessment.
6. **VERIFY BEFORE ANSWERING**: Before providing your final answer, cross-check every factual claim against the entity data provided. For each claim, identify which entity attribute supports it and confirm the value matches. If you cannot find supporting data for a claim, do not make it. Extracted boolean attributes that are true are findings, not possibilities — do not hedge with "may" or "potentially". State your verification briefly at the end: "Verified against: [entity types checked]"

## CATEGORY-SPECIFIC ANALYSIS GUIDANCE

{category_guidance}

## FORMATTING

- Use **bold** for key terms and defined terms
- Use bullet points for lists
- Keep response concise but complete
- State facts with citations. Do not editorialize.

## EVIDENCE TRACING

After your answer, on a new line, output an evidence block in this exact format:

<!-- EVIDENCE: ["entity_type_1", "entity_type_2"] -->

List the entity types you relied on (e.g., "builder_basket", "ratio_basket", "jcrew_blocker").
This block MUST appear at the very end of your response."""

    user_prompt = f"""## USER QUESTION

{request.question}

## EXTRACTED ENTITY DATA FOR THIS DEAL

{entity_context}"""

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        model_used = settings.claude_model

        # ── Stage 1: Entity Filter (Sonnet) ──────────────────────────
        # Parse entity context into header + JSON array
        parts = entity_context.split("\n\n", 1)
        header = parts[0] if len(parts) > 1 else ""
        entities_json_str = parts[1] if len(parts) > 1 else parts[0]
        try:
            all_entities = json.loads(entities_json_str)
        except json.JSONDecodeError:
            all_entities = []
            logger.warning("Could not parse entity context as JSON for filtering")

        filter_prompt = """You are a legal data analyst. Given a question about a credit agreement and a set of extracted entities, classify each entity into one of three categories:

PRIMARY — Core entities needed to directly answer the question. These form the basis of the analysis. Be precise: for a capacity question, PRIMARY includes all baskets and reallocation edges. For a ratio test question, PRIMARY includes the ratio basket. For a specific covenant question, PRIMARY includes the directly governing entity.

SUPPLEMENTARY — Entities that might add detail, qualifications, edge cases, or corrections to the answer. Include entities that provide related context even if not directly cited — for example, sweep tiers and de minimis thresholds for asset sale questions, or the J.Crew blocker for unrestricted subsidiary questions.

EXCLUDE — Entities that are clearly irrelevant to this specific question. Only exclude entities you are certain have no bearing on the answer.

When in doubt between PRIMARY and SUPPLEMENTARY, choose PRIMARY.
When in doubt between SUPPLEMENTARY and EXCLUDE, choose SUPPLEMENTARY.

## OUTPUT FORMAT

Return ONLY a JSON object with two arrays:

{"primary": ["type_name_1", "type_name_2"], "supplementary": ["type_name_3", "type_name_4"]}

Use the entity's type_name field. For linked entities that appear only in another entity's links array, include the linked type_name.

Return ONLY the JSON object. No explanation."""

        filter_start = _time.time()
        filter_response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            system=filter_prompt,
            messages=[{"role": "user", "content": f"## QUESTION\n\n{request.question}\n\n## ENTITIES\n\n{entities_json_str}"}]
        )
        filter_duration_ms = (_time.time() - filter_start) * 1000

        # Parse two-tier filter response (robust fence + boundary extraction)
        filter_text = filter_response.content[0].text.strip()
        if filter_text.startswith("```"):
            filter_text = re.sub(r'^```(?:json)?\s*\n?', '', filter_text)
            filter_text = re.sub(r'\n?```\s*$', '', filter_text)
        start_idx = filter_text.find('{')
        end_idx = filter_text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            filter_text = filter_text[start_idx:end_idx + 1]
        try:
            tiers = json.loads(filter_text)
            primary_types = tiers.get("primary", [])
            supplementary_types = tiers.get("supplementary", [])
        except json.JSONDecodeError:
            logger.warning(f"Filter response not valid JSON, using all as primary: {filter_text[:200]}")
            primary_types = [e.get("type_name", "") for e in all_entities]
            supplementary_types = []

        primary_set = set(primary_types)
        supplementary_set = set(supplementary_types)

        # Split entities into tiers
        primary_entities = []
        supplementary_entities = []
        for entity in all_entities:
            type_name = entity.get("type_name", "")
            links = entity.get("links", [])
            linked_types = {lnk.get("linked_type") for lnk in links}

            if type_name in primary_set:
                primary_entities.append(entity)
            elif type_name in supplementary_set:
                supplementary_entities.append(entity)
            elif linked_types & primary_set:
                primary_entities.append(entity)
            elif linked_types & supplementary_set:
                supplementary_entities.append(entity)

        # Build tiered context
        tiered_context = header + "\n\n"
        tiered_context += "## PRIMARY ENTITIES\nBase your core analysis on these entities.\n\n"
        tiered_context += json.dumps(primary_entities, indent=2)
        tiered_context += "\n\n## SUPPLEMENTARY ENTITIES\nCheck these for additional detail, qualifications, or corrections during self-verification.\n\n"
        tiered_context += json.dumps(supplementary_entities, indent=2)

        # Trace filter stage
        excluded_count = len(all_entities) - len(primary_entities) - len(supplementary_entities)
        if collector:
            collector.filter_model = "claude-opus-4-6"
            collector.filter_input_tokens = filter_response.usage.input_tokens
            collector.filter_output_tokens = filter_response.usage.output_tokens
            collector.filter_cost_usd = (
                filter_response.usage.input_tokens * 15.0 / 1_000_000
                + filter_response.usage.output_tokens * 75.0 / 1_000_000
            )
            collector.filter_duration_ms = filter_duration_ms
            collector.filter_primary_types = primary_types
            collector.filter_supplementary_types = supplementary_types
            collector.filter_total_entities = len(all_entities)
            collector.filter_primary_count = len(primary_entities)
            collector.filter_supplementary_count = len(supplementary_entities)
            collector.filter_excluded_count = excluded_count

        logger.info(f"Entity filter: {len(all_entities)} total -> {len(primary_entities)} primary + {len(supplementary_entities)} supplementary ({excluded_count} excluded)")

        # ── Stage 2: Synthesis (Opus) with tiered entities ──────────
        filtered_user_prompt = f"""## USER QUESTION

{request.question}

## EXTRACTED ENTITY DATA FOR THIS DEAL

{tiered_context}"""

        claude_start = _time.time()
        response = client.messages.create(
            model=model_used,
            max_tokens=6000,
            system=system_rules,
            messages=[{"role": "user", "content": filtered_user_prompt}]
        )
        claude_duration_ms = (_time.time() - claude_start) * 1000

        answer_text = response.content[0].text

        # Parse evidence block
        evidence_match = re.search(
            r'<!--\s*EVIDENCE:\s*\[([^\]]*)\]\s*-->',
            answer_text,
        )
        clean_answer = answer_text
        evidence_entities = []
        if evidence_match:
            clean_answer = answer_text[:evidence_match.start()].rstrip()
            raw_ids = evidence_match.group(1)
            evidence_entities = [
                eid.strip().strip('"').strip("'")
                for eid in raw_ids.split(",")
                if eid.strip()
            ]

        citations = _extract_citations_from_answer(clean_answer)

        # Capture trace data for Claude synthesis
        if collector:
            collector.claude_system_prompt = system_rules
            collector.claude_user_prompt = filtered_user_prompt
            collector.claude_model = model_used
            collector.claude_input_tokens = response.usage.input_tokens
            collector.claude_output_tokens = response.usage.output_tokens
            # Cost estimate: Opus input=$15/MTok, output=$75/MTok
            collector.claude_cost_usd = (
                response.usage.input_tokens * 15.0 / 1_000_000
                + response.usage.output_tokens * 75.0 / 1_000_000
            )
            collector.claude_duration_ms = claude_duration_ms
            collector.claude_answer = answer_text

        response_data = {
            "question": request.question,
            "answer": clean_answer,
            "citations": citations,
            "evidence_entities": evidence_entities,
            "data_source": "graph_entities",
            "entity_context": entity_context,
            "entity_context_chars": len(entity_context),
            "filtered_entity_count": len(primary_entities) + len(supplementary_entities),
            "total_entity_count": len(all_entities),
            "model": model_used,
            "deal_id": deal_id,
        }

        if collector:
            response_data["trace"] = collector.to_dict()

        return response_data

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error in graph Q&A: {e}")
        raise HTTPException(status_code=502, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in graph Q&A: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# _format_mfn_entities_as_context deleted in Prompt 3.
# MFN entities now flow through polymorphic fetch (get_provision_entities).


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
                lines.append(f"  Source: \"{source_text[:500]}\"")
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


def _format_evidence_flat(
    rp_response: Dict = None,
    mfn_response: Dict = None,
) -> str:
    """Format extracted data as a flat unstructured list.

    No category headers, no entity hierarchies, no SILENT applicability
    states, no synthesis rules. Used for ablation testing to measure
    what TypeDB's structure adds to analysis quality.
    """
    lines = []

    for response in (rp_response, mfn_response):
        if not response:
            continue

        # 1. Scalar answers — flat list, no category grouping
        for qid, data in sorted(response.get("scalar_answers", {}).items()):
            value = data.get("value")
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            elif isinstance(value, float) and value == int(value):
                value_str = str(int(value))
            else:
                value_str = str(value)

            page = data.get("source_page")
            source = data.get("source_text", "")
            parts = [f"{qid}: {value_str}"]
            cite_parts = []
            if page:
                cite_parts.append(f"p.{page}")
            if source:
                cite_parts.append(f'"{source[:200]}"')
            if cite_parts:
                parts.append(f" ({', '.join(cite_parts)})")
            lines.append("".join(parts))

        # 2. Multiselect — convert to simple booleans, omit SILENT
        for concept_type, concepts in sorted(
            response.get("multiselect_answers", {}).items()
        ):
            for concept in concepts:
                cid = concept.get("concept_id", "")
                lines.append(f"{concept_type}/{cid}: true")

        # 3. Pattern flags — flat booleans
        for flag, value in sorted(response.get("pattern_flags", {}).items()):
            if isinstance(value, bool):
                lines.append(f"{flag}: {'true' if value else 'false'}")

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
