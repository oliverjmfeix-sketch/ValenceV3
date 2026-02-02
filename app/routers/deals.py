"""
Deal endpoints - Upload, CRUD, PDF serving.
"""
import os
import uuid
import logging
import shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse

from app.config import settings
from app.repositories.deal_repository import DealRepository, get_deal_repository
from app.services.extraction import ExtractionService, get_extraction_service
from app.schemas.models import (
    DealSummary, Deal, UploadResponse, ExtractionStatus, Provenance
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Deals"])

# In-memory extraction status (use Redis in production)
extraction_status: dict = {}


@router.get("", response_model=List[DealSummary])
async def list_deals(
    repo: DealRepository = Depends(get_deal_repository)
):
    """List all deals with summary info."""
    return repo.list_deals()


@router.get("/{deal_id}", response_model=Deal)
async def get_deal(
    deal_id: str,
    repo: DealRepository = Depends(get_deal_repository)
):
    """Get a deal with all typed primitives."""
    deal = repo.get_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.delete("/{deal_id}")
async def delete_deal(
    deal_id: str,
    repo: DealRepository = Depends(get_deal_repository)
):
    """Delete a deal and all associated data."""
    success = repo.delete_deal(deal_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete deal")
    
    # Also delete PDF file
    pdf_path = os.path.join(settings.uploads_dir, f"{deal_id}.pdf")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    
    return {"status": "deleted", "deal_id": deal_id}


@router.post("/upload", response_model=UploadResponse)
async def upload_deal(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    deal_name: str = Form(...),
    borrower: str = Form(...),
    repo: DealRepository = Depends(get_deal_repository),
    extractor: ExtractionService = Depends(get_extraction_service)
):
    """
    Upload a PDF and start extraction.
    
    This endpoint:
    1. Saves the PDF to storage
    2. Creates the deal entity in TypeDB
    3. Starts background extraction
    4. Returns immediately with deal_id
    
    Poll /api/deals/{deal_id}/extraction/status for progress.
    """
    # Validate file
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    # Generate deal ID
    deal_id = str(uuid.uuid4())[:8]
    
    # Ensure uploads directory exists
    os.makedirs(settings.uploads_dir, exist_ok=True)
    
    # Save PDF
    pdf_path = os.path.join(settings.uploads_dir, f"{deal_id}.pdf")
    try:
        with open(pdf_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        logger.info(f"Saved PDF: {pdf_path}")
    except Exception as e:
        logger.error(f"Failed to save PDF: {e}")
        raise HTTPException(status_code=500, detail="Failed to save PDF")
    
    # Create deal in TypeDB
    success = repo.create_deal(
        deal_id=deal_id,
        deal_name=deal_name,
        borrower=borrower,
        pdf_filename=file.filename
    )
    
    if not success:
        os.remove(pdf_path)
        raise HTTPException(status_code=500, detail="Failed to create deal")
    
    # Initialize extraction status
    extraction_status[deal_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "Queued for extraction"
    }
    
    # Start background extraction
    background_tasks.add_task(
        run_extraction,
        deal_id=deal_id,
        pdf_path=pdf_path,
        extractor=extractor,
        repo=repo
    )
    
    return UploadResponse(
        deal_id=deal_id,
        deal_name=deal_name,
        status="processing",
        message="PDF uploaded. Extraction started in background."
    )


async def run_extraction(
    deal_id: str,
    pdf_path: str,
    extractor: ExtractionService,
    repo: DealRepository
):
    """Background task to extract and store primitives."""
    try:
        # Update status
        extraction_status[deal_id] = {
            "status": "extracting",
            "progress": 10,
            "current_step": "Parsing PDF..."
        }
        
        # Run extraction
        extraction_status[deal_id]["progress"] = 20
        extraction_status[deal_id]["current_step"] = "Extracting MFN provisions..."
        
        result = await extractor.extract_document(pdf_path, deal_id)
        
        # Store MFN primitives
        extraction_status[deal_id]["progress"] = 60
        extraction_status[deal_id]["current_step"] = "Storing MFN primitives..."
        
        if result.mfn_primitives:
            repo.store_mfn_primitives(deal_id, result.mfn_primitives)
        
        # Store RP primitives
        extraction_status[deal_id]["progress"] = 80
        extraction_status[deal_id]["current_step"] = "Storing RP primitives..."
        
        if result.rp_primitives:
            repo.store_rp_primitives(deal_id, result.rp_primitives)
        
        # Complete
        extraction_status[deal_id] = {
            "status": "complete",
            "progress": 100,
            "current_step": "Extraction complete",
            "mfn_count": len(result.mfn_primitives),
            "rp_count": len(result.rp_primitives),
            "extraction_time": result.extraction_time_seconds
        }
        
        logger.info(f"Extraction complete for {deal_id}")
        
    except Exception as e:
        logger.error(f"Extraction failed for {deal_id}: {e}")
        extraction_status[deal_id] = {
            "status": "error",
            "progress": 0,
            "current_step": "Extraction failed",
            "error": str(e)
        }


@router.get("/{deal_id}/extraction/status", response_model=ExtractionStatus)
async def get_extraction_status(deal_id: str):
    """Get the status of an ongoing extraction."""
    if deal_id not in extraction_status:
        raise HTTPException(status_code=404, detail="No extraction found for this deal")
    
    status = extraction_status[deal_id]
    return ExtractionStatus(
        deal_id=deal_id,
        status=status.get("status", "unknown"),
        progress=status.get("progress", 0),
        current_step=status.get("current_step"),
        error=status.get("error")
    )


@router.get("/{deal_id}/pdf")
async def get_deal_pdf(deal_id: str):
    """
    Serve the stored PDF for a deal.
    
    IMPORTANT: Frontend must call this with full backend URL:
    `${BACKEND_URL}/api/deals/${dealId}/pdf`
    
    NOT a relative path, which would hit Lovable's server.
    """
    pdf_path = os.path.join(settings.uploads_dir, f"{deal_id}.pdf")
    
    if not os.path.exists(pdf_path):
        raise HTTPException(
            status_code=404,
            detail=f"PDF file not found for deal {deal_id}"
        )
    
    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename=f"{deal_id}.pdf",
        headers={
            "Content-Disposition": f'inline; filename="{deal_id}.pdf"',
            "Cache-Control": "public, max-age=3600"
        }
    )


@router.get("/{deal_id}/provenance/{attribute_name}", response_model=Provenance)
async def get_provenance(
    deal_id: str,
    attribute_name: str,
    repo: DealRepository = Depends(get_deal_repository)
):
    """Get source provenance for a specific primitive."""
    prov = repo.get_provenance(deal_id, attribute_name)
    if not prov:
        raise HTTPException(
            status_code=404,
            detail=f"No provenance found for attribute '{attribute_name}'"
        )
    return prov
