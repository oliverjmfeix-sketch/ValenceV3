"""
Graph vs Scalar A/B Evaluation.

Runs the same gold-standard questions through both /ask (Channel 1 scalars)
and /ask-graph (Channel 3 entities), returning side-by-side results for
manual comparison.
"""
import logging
import time
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.deals import ask_question, ask_question_graph, AskRequest
from app.eval.duck_creek_ablation import DUCK_CREEK_ABLATION_QUESTIONS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Graph Eval"])


class QuestionComparison(BaseModel):
    question: str
    gold_answer: str
    scalar_answer: str
    scalar_citations: List[Dict[str, Any]]
    scalar_context_type: str
    graph_answer: str
    graph_citations: List[Dict[str, Any]]
    graph_context_chars: int
    graph_evidence_entities: List[str]
    graph_entity_context: str


class GraphEvalResult(BaseModel):
    deal_id: str
    num_questions: int
    comparisons: List[QuestionComparison]
    elapsed_seconds: float


@router.post("/graph-eval/{deal_id}")
async def run_graph_eval(deal_id: str) -> GraphEvalResult:
    """
    Run A/B comparison: scalar /ask vs graph /ask-graph on gold standard questions.

    Uses DUCK_CREEK_ABLATION_QUESTIONS from app/eval/duck_creek_ablation.py.
    Returns both answers for each question alongside the gold standard for manual review.
    """
    start = time.time()
    comparisons = []

    for item in DUCK_CREEK_ABLATION_QUESTIONS:
        q = item["question"]
        req = AskRequest(question=q)

        # Run scalar pipeline
        scalar_result = {"answer": "(error)", "citations": [], "data_source": {}}
        try:
            scalar_result = await ask_question(deal_id, req)
        except HTTPException as e:
            scalar_result["answer"] = f"(HTTP {e.status_code}: {e.detail})"
        except Exception as e:
            scalar_result["answer"] = f"(error: {e})"

        # Run graph pipeline
        graph_result = {"answer": "(error)", "citations": [], "entity_context_chars": 0, "evidence_entities": [], "entity_context": ""}
        try:
            graph_result = await ask_question_graph(deal_id, req)
        except HTTPException as e:
            graph_result["answer"] = f"(HTTP {e.status_code}: {e.detail})"
        except Exception as e:
            graph_result["answer"] = f"(error: {e})"

        comparisons.append(QuestionComparison(
            question=q,
            gold_answer=item["gold_answer"],
            scalar_answer=scalar_result.get("answer", ""),
            scalar_citations=scalar_result.get("citations", []),
            scalar_context_type="channel_1_scalars",
            graph_answer=graph_result.get("answer", ""),
            graph_citations=graph_result.get("citations", []),
            graph_context_chars=graph_result.get("entity_context_chars", 0),
            graph_evidence_entities=graph_result.get("evidence_entities", []),
            graph_entity_context=graph_result.get("entity_context", ""),
        ))

    elapsed = time.time() - start

    return GraphEvalResult(
        deal_id=deal_id,
        num_questions=len(comparisons),
        comparisons=comparisons,
        elapsed_seconds=round(elapsed, 1),
    )
