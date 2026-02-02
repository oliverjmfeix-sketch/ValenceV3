"""
Ontology endpoints - Questions from TypeDB (SSoT).

IMPORTANT: Questions are fetched from TypeDB, NOT hardcoded.
This is the single source of truth for the ontology.
"""
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from app.repositories.ontology_repository import OntologyRepository, get_ontology_repository
from app.schemas.models import OntologyQuestion, CategoryWithQuestions

router = APIRouter(prefix="/api/ontology", tags=["Ontology"])


@router.get("/questions", response_model=List[OntologyQuestion])
async def get_all_questions(
    repo: OntologyRepository = Depends(get_ontology_repository)
):
    """
    Get all ontology questions.
    
    These come from TypeDB (SSoT), not hardcoded lists.
    """
    return repo.get_all_questions()


@router.get("/questions/by-category", response_model=Dict[str, List[OntologyQuestion]])
async def get_questions_by_category(
    repo: OntologyRepository = Depends(get_ontology_repository)
):
    """Get questions grouped by category."""
    return repo.get_questions_by_category()


@router.get("/categories", response_model=List[Dict[str, Any]])
async def get_categories(
    repo: OntologyRepository = Depends(get_ontology_repository)
):
    """Get list of categories with question counts."""
    return repo.get_categories()


@router.get("/deals/{deal_id}/answers", response_model=List[CategoryWithQuestions])
async def get_deal_answers(
    deal_id: str,
    repo: OntologyRepository = Depends(get_ontology_repository)
):
    """
    Get all questions with answers for a specific deal.
    
    This powers the Ontology Browser in the UI:
    - Shows all questions organized by category
    - Shows extracted answers (or "Not found")
    - Includes provenance for each answer
    """
    categories = repo.get_questions_with_answers(deal_id)
    
    if not categories:
        raise HTTPException(
            status_code=404,
            detail="Deal not found or no data extracted"
        )
    
    return categories
