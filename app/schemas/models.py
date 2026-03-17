"""
Pydantic models for API request/response schemas.

Only models actively used by routers/services are kept here.
Unused models (from deleted repositories/) have been removed.
"""
from typing import Optional
from pydantic import BaseModel


class ExtractionStatus(BaseModel):
    """Status of ongoing extraction."""
    deal_id: str
    status: str  # "pending", "extracting", "storing", "complete", "error"
    progress: int = 0  # 0-100
    current_step: Optional[str] = None
    error: Optional[str] = None


class UploadResponse(BaseModel):
    """Response from PDF upload."""
    deal_id: str
    deal_name: str
    status: str
    message: str
