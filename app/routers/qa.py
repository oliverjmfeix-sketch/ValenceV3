"""
Q&A endpoints - Placeholder for V3 launch
"""
from typing import Dict, Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/qa", tags=["Q&A"])


@router.post("/ask")
async def ask_question(deal_id: str, question: str) -> Dict[str, Any]:
    """Ask a question about a deal (placeholder)."""
    return {
        "answer": "Q&A functionality coming soon. Data is being extracted from TypeDB.",
        "deal_id": deal_id,
        "question": question,
        "supporting_data": []
    }


@router.get("/status")
async def qa_status() -> Dict[str, Any]:
    """Q&A service status."""
    return {
        "status": "available",
        "version": "3.0.0",
        "features": ["deal_lookup", "cross_deal_query"]
    }
