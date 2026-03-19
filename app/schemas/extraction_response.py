"""
Unified Extraction Response Models (Phase 2d-ii)

Generic Answer + ExtractionResponse models that replace the dual-format pipeline.
ALL answers (scalar, multiselect, entity_list) flow through one response format.
"""
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class Answer(BaseModel):
    """A single answer — scalar, multiselect, or entity_list."""
    question_id: str
    value: Any  # bool, float, str, list[str], list[dict]
    answer_type: str  # "boolean", "number", "string", "multiselect", "entity_list"
    source_text: str = ""
    source_page: Optional[int] = None
    section_reference: Optional[str] = None
    reasoning: Optional[str] = None


class ExtractionResponse(BaseModel):
    """Unified extraction response — all answers in one list."""
    answers: List[Answer] = Field(default_factory=list)
