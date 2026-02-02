"""
Pydantic models for API request/response schemas.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


# ==============================================================================
# DEAL MODELS
# ==============================================================================

class DealBase(BaseModel):
    """Base deal information."""
    deal_name: str
    borrower: str


class DealCreate(DealBase):
    """Request model for creating a deal (from upload)."""
    pass


class DealSummary(DealBase):
    """Deal summary for list view."""
    deal_id: str
    upload_date: datetime
    has_mfn: Optional[bool] = None
    has_rp: Optional[bool] = None
    has_jcrew_risk: Optional[bool] = None


class Deal(DealBase):
    """Full deal with all primitives."""
    deal_id: str
    upload_date: datetime
    pdf_filename: Optional[str] = None
    mfn_provision: Optional[Dict[str, Any]] = None
    rp_provision: Optional[Dict[str, Any]] = None
    patterns: Optional[Dict[str, bool]] = None


# ==============================================================================
# PROVENANCE MODELS
# ==============================================================================

class Provenance(BaseModel):
    """Source provenance for an extracted primitive."""
    attribute_name: str
    source_text: str
    source_page: int
    source_section: Optional[str] = None
    extraction_confidence: str = "high"
    extracted_at: Optional[datetime] = None


class PrimitiveWithProvenance(BaseModel):
    """A single extracted value with its provenance."""
    attribute_name: str
    value: Any
    provenance: Optional[Provenance] = None


# ==============================================================================
# ONTOLOGY MODELS
# ==============================================================================

class OntologyQuestion(BaseModel):
    """A question from the ontology."""
    question_id: str
    question_text: str
    question_category: str
    category_order: int
    question_order: int
    target_attribute: str
    answer_type: str  # "boolean", "integer", "double", "string"


class QuestionWithAnswer(OntologyQuestion):
    """Question with its answer for a specific deal."""
    answer: Optional[Any] = None
    provenance: Optional[Provenance] = None


class CategoryWithQuestions(BaseModel):
    """A category containing its questions."""
    category_name: str
    category_order: int
    questions: List[QuestionWithAnswer]


# ==============================================================================
# Q&A MODELS
# ==============================================================================

class QARequest(BaseModel):
    """Request for Q&A interface."""
    question: str


class QAResponse(BaseModel):
    """Response from Q&A interface."""
    answer: str
    supporting_primitives: List[PrimitiveWithProvenance] = []
    confidence: str = "high"


class CrossDealQuery(BaseModel):
    """Query across multiple deals."""
    question: str
    deal_ids: Optional[List[str]] = None  # None = all deals


class CrossDealResult(BaseModel):
    """Result of cross-deal query."""
    deal_id: str
    deal_name: str
    matches: bool
    relevant_primitives: List[PrimitiveWithProvenance] = []


class CrossDealResponse(BaseModel):
    """Response from cross-deal query."""
    query: str
    total_deals: int
    matching_deals: int
    results: List[CrossDealResult]


# ==============================================================================
# EXTRACTION MODELS
# ==============================================================================

class ExtractedPrimitive(BaseModel):
    """A primitive extracted by Claude."""
    attribute_name: str
    value: Any
    source_text: str
    source_page: int
    source_section: Optional[str] = None
    confidence: str = "high"


class MultiselectAnswer(BaseModel):
    """Answer to a multiselect question with concept applicabilities."""
    concept_type: str
    included: List[str] = []  # concept_ids that apply
    excluded: List[str] = []  # concept_ids explicitly excluded
    source_text: str = ""
    source_page: int = 0


class ExtractionResult(BaseModel):
    """Result of document extraction."""
    deal_id: str
    mfn_primitives: List[ExtractedPrimitive] = []
    rp_primitives: List[ExtractedPrimitive] = []
    mfn_multiselect: List[MultiselectAnswer] = []
    rp_multiselect: List[MultiselectAnswer] = []
    extraction_time_seconds: float
    token_count: Optional[int] = None


class ExtractionStatus(BaseModel):
    """Status of ongoing extraction."""
    deal_id: str
    status: str  # "pending", "extracting", "storing", "complete", "error"
    progress: int = 0  # 0-100
    current_step: Optional[str] = None
    error: Optional[str] = None


# ==============================================================================
# UPLOAD MODELS
# ==============================================================================

class UploadResponse(BaseModel):
    """Response from PDF upload."""
    deal_id: str
    deal_name: str
    status: str
    message: str


# ==============================================================================
# HEALTH MODELS
# ==============================================================================

class HealthCheck(BaseModel):
    """Health check response."""
    status: str
    version: str = "2.0.0"


class TypeDBHealth(BaseModel):
    """TypeDB health check response."""
    connected: bool
    address: Optional[str] = None
    database: Optional[str] = None
    error: Optional[str] = None
    tls_enabled: Optional[bool] = None
