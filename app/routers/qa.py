"""
Q&A endpoints - Natural language questions answered from TypeDB.

IMPORTANT: The ANSWERS come from TypeDB structured data, not Claude.
Claude was used once at extraction time.
"""
from typing import Optional, List

from fastapi import APIRouter, Depends

from app.services.qa_engine import QAEngine, get_qa_engine
from app.schemas.models import (
    QARequest, QAResponse, CrossDealQuery, CrossDealResponse
)

router = APIRouter(prefix="/api", tags=["Q&A"])


@router.post("/deals/{deal_id}/qa", response_model=QAResponse)
async def ask_question(
    deal_id: str,
    request: QARequest,
    engine: QAEngine = Depends(get_qa_engine)
):
    """
    Ask a natural language question about a specific deal.
    
    The answer comes from TypeDB structured data, not Claude.
    
    Example questions:
    - "Does this deal have J.Crew risk?"
    - "What is the sunset period?"
    - "Is OID included in yield calculation?"
    - "Does it have a builder basket?"
    """
    return await engine.answer_question(deal_id, request.question)


@router.post("/qa/cross-deal", response_model=CrossDealResponse)
async def cross_deal_query(
    request: CrossDealQuery,
    engine: QAEngine = Depends(get_qa_engine)
):
    """
    Query across multiple deals.
    
    Example questions:
    - "Which deals have J.Crew risk?"
    - "Find deals with sunset periods under 12 months"
    - "Which deals exclude OID from yield?"
    
    Optionally filter to specific deal_ids.
    """
    return await engine.cross_deal_query(
        request.question,
        request.deal_ids
    )
