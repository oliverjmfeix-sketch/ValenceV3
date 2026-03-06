"""
Graph vs Scalar A/B Evaluation.

Runs the same gold-standard questions through both /ask (Channel 1 scalars)
and /ask-graph (Channel 3 entities), returning side-by-side results for
manual comparison.
"""
import logging
import time
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.deals import ask_question, ask_question_graph, AskRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Graph Eval"])


# Duck Creek gold standard — 6 questions covering key RP entity types
GOLD_STANDARD_QUESTIONS = [
    {
        "question": "What is the builder basket structure? What are the sources that feed into it, and what are the dollar amounts and percentages?",
        "gold_answer": "Builder basket uses greatest-of test. Starter amount $130M or 100% EBITDA. CNI at 50% is primary test. ECF retained excess. Equity proceeds at 100% excluding cure contributions and disqualified stock.",
        "target_entities": ["builder_basket", "starter_amount_source", "cni_source", "ecf_source", "equity_proceeds_source"],
    },
    {
        "question": "What is the ratio basket threshold for unlimited restricted payments, and does a no-worse test exist?",
        "gold_answer": "Ratio basket permits unlimited RPs at 5.75x or below. No-worse test exists with 99.0% threshold — borrower can make RP at any leverage if pro forma ratio is no worse than immediately prior.",
        "target_entities": ["ratio_basket"],
    },
    {
        "question": "Describe the J.Crew blocker. What does it cover, who does it bind, and what exceptions exist?",
        "gold_answer": "Blocker covers transfer, designation, and IP. Binds Loan Parties. Not a sacred right. Exceptions include non-exclusive license, intercompany, and immaterial IP.",
        "target_entities": ["jcrew_blocker", "blocker_exception"],
    },
    {
        "question": "What basket reallocation paths exist? Can RDP capacity be reallocated to RP, and is it bidirectional?",
        "gold_answer": "General RDP basket and general investment basket can reallocate to RP covenant. Both paths are bidirectional with dollar-for-dollar reduction.",
        "target_entities": ["basket_reallocation"],
    },
    {
        "question": "What are the investment pathways from Loan Parties to unrestricted subsidiaries? Is there a chain pathway through non-guarantor restricted subsidiaries?",
        "gold_answer": "Direct LP-to-Unsub pathway with dollar cap. LP-to-Non-Guarantor RS pathway (first hop). Non-Guarantor RS to Unsub pathway (second hop). Chain pathway analysis depends on blocker scope.",
        "target_entities": ["investment_pathway"],
    },
    {
        "question": "What is the general RP basket amount, and what RDP baskets exist with their key terms?",
        "gold_answer": "General RP basket $130M with 100% grower. RDP baskets include: general RDP ($130M, 100% grower), ratio RDP (unlimited at threshold), refinancing RDP, builder RDP (shares with RP builder), equity-funded RDP.",
        "target_entities": ["general_rp_basket", "rdp_basket"],
    },
]


class QuestionComparison(BaseModel):
    question: str
    gold_answer: str
    target_entities: List[str]
    scalar_answer: str
    scalar_citations: List[Dict[str, Any]]
    scalar_context_type: str
    graph_answer: str
    graph_citations: List[Dict[str, Any]]
    graph_context_chars: int
    graph_evidence_entities: List[str]


class GraphEvalResult(BaseModel):
    deal_id: str
    num_questions: int
    comparisons: List[QuestionComparison]
    elapsed_seconds: float


@router.post("/graph-eval/{deal_id}")
async def run_graph_eval(deal_id: str) -> GraphEvalResult:
    """
    Run A/B comparison: scalar /ask vs graph /ask-graph on gold standard questions.

    Returns both answers for each question alongside the gold standard for manual review.
    """
    start = time.time()
    comparisons = []

    for item in GOLD_STANDARD_QUESTIONS:
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
        graph_result = {"answer": "(error)", "citations": [], "entity_context_chars": 0, "evidence_entities": []}
        try:
            graph_result = await ask_question_graph(deal_id, req)
        except HTTPException as e:
            graph_result["answer"] = f"(HTTP {e.status_code}: {e.detail})"
        except Exception as e:
            graph_result["answer"] = f"(error: {e})"

        comparisons.append(QuestionComparison(
            question=q,
            gold_answer=item["gold_answer"],
            target_entities=item["target_entities"],
            scalar_answer=scalar_result.get("answer", ""),
            scalar_citations=scalar_result.get("citations", []),
            scalar_context_type="channel_1_scalars",
            graph_answer=graph_result.get("answer", ""),
            graph_citations=graph_result.get("citations", []),
            graph_context_chars=graph_result.get("entity_context_chars", 0),
            graph_evidence_entities=graph_result.get("evidence_entities", []),
        ))

    elapsed = time.time() - start

    return GraphEvalResult(
        deal_id=deal_id,
        num_questions=len(comparisons),
        comparisons=comparisons,
        elapsed_seconds=round(elapsed, 1),
    )
