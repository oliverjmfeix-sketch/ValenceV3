"""
Automated Q&A Evaluation Pipeline.

Generates test questions from the raw RP universe text, produces gold-standard
answers (Claude + raw text), runs each through the normal /ask pipeline,
compares/scores each pair, and returns a structured report.

This validates that the extraction-based pipeline produces answers at least
as good as raw document analysis.
"""
import logging
import time
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import anthropic

from app.config import settings
from app.routers.deals import ask_question, AskRequest, UPLOADS_DIR

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Eval"])


# =============================================================================
# MODELS
# =============================================================================

class EvalRequest(BaseModel):
    num_questions: int = 5
    focus_areas: Optional[List[str]] = None


class QuestionResult(BaseModel):
    question: str
    raw_answer: str
    valence_answer: str
    score: float
    max_score: float
    advantages: List[str]
    gaps: List[str]
    reasoning: str


class EvalResult(BaseModel):
    deal_id: str
    num_questions: int
    total_score: float
    max_score: float
    pct_score: float
    questions: List[QuestionResult]
    elapsed_seconds: float


# =============================================================================
# PROMPTS
# =============================================================================

QUESTION_GENERATION_PROMPT = """You are a legal analyst testing a covenant analysis system.
Given the following Restricted Payments universe text extracted from a credit agreement,
generate {num_questions} specific, testable questions that a legal professional would ask.

RULES:
1. Questions must be answerable from the text below
2. Mix question types: yes/no, numeric thresholds, definition checks, cross-reference analysis
3. Include at least one question about basket mechanics and one about definitions
4. Questions should require specific factual answers, not opinions
5. Make questions progressively harder (start with basic, end with analytical)
{focus_instruction}

## RP UNIVERSE TEXT

{rp_text}

## OUTPUT

Return a JSON array of question strings:
["question 1", "question 2", ...]

Return ONLY the JSON array."""

BASELINE_PROMPT = """You are a legal analyst answering a question about a credit agreement
using the raw extracted RP-relevant text below. Answer thoroughly and precisely,
citing specific sections and page numbers where possible.

## QUESTION

{question}

## RP UNIVERSE TEXT

{rp_text}

## INSTRUCTIONS

- Answer based ONLY on the text provided
- Cite specific sections and page numbers
- If information is not found, say so explicitly
- Be precise about thresholds, ratios, and dollar amounts
- Mention qualifications and exceptions where relevant"""

COMPARISON_PROMPT = """You are evaluating the quality of two answers to the same legal question
about a credit agreement's restricted payments covenant.

## QUESTION
{question}

## ANSWER A (Raw Document Analysis — baseline)
{raw_answer}

## ANSWER B (Extraction Pipeline — Valence)
{valence_answer}

## SCORING INSTRUCTIONS

Score Answer B (Valence) against Answer A (Raw) on these dimensions (1-5 each):

1. **Completeness** — Does B cover all key points from A?
2. **Accuracy** — Are B's factual claims correct compared to A?
3. **Citations** — Does B provide section/page references?
4. **Specificity** — Does B include specific thresholds, ratios, amounts?

Also identify:
- **advantages**: Things B does BETTER than A (e.g., better structured, more citations)
- **gaps**: Things A covers that B misses or gets wrong

## OUTPUT

Return JSON:
{{
  "completeness": <1-5>,
  "accuracy": <1-5>,
  "citations": <1-5>,
  "specificity": <1-5>,
  "advantages": ["..."],
  "gaps": ["..."],
  "reasoning": "1-2 sentence summary"
}}

Return ONLY the JSON object."""


# =============================================================================
# HELPERS
# =============================================================================

def _get_rp_universe_text(deal_id: str) -> str:
    """Read cached RP universe text from disk."""
    import os

    universe_path = os.path.join(UPLOADS_DIR, f"{deal_id}_rp_universe.txt")
    if not os.path.exists(universe_path):
        raise HTTPException(
            status_code=404,
            detail="RP universe not found. Re-extract the deal first."
        )

    with open(universe_path, "r", encoding="utf-8") as f:
        text = f.read()

    if not text.strip():
        raise HTTPException(
            status_code=404,
            detail="RP universe file is empty. Re-extract the deal first."
        )

    return text


def _call_sonnet(system: str, user: str, max_tokens: int = 4000) -> str:
    """Call Claude Sonnet for eval tasks (cost-effective)."""
    import time as _time
    from app.services.cost_tracker import extract_usage

    eval_model = "claude-sonnet-4-20250514"
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    start = _time.time()
    response = client.messages.create(
        model=eval_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    duration = _time.time() - start
    extract_usage(response, eval_model, "eval", duration=duration)
    return response.content[0].text


def generate_questions(rp_text: str, num_questions: int, focus_areas: Optional[List[str]] = None) -> List[str]:
    """Generate test questions from RP universe text using Sonnet."""
    import json

    focus_instruction = ""
    if focus_areas:
        focus_instruction = f"6. Focus questions on these areas: {', '.join(focus_areas)}"

    prompt = QUESTION_GENERATION_PROMPT.format(
        num_questions=num_questions,
        focus_instruction=focus_instruction,
        rp_text=rp_text[:100000],  # Cap context to avoid token limits
    )

    response_text = _call_sonnet(
        system="You generate legal analysis questions. Return ONLY a JSON array of strings.",
        user=prompt,
        max_tokens=2000,
    )

    # Parse JSON array
    start = response_text.find("[")
    end = response_text.rfind("]") + 1
    if start == -1 or end == 0:
        raise HTTPException(status_code=500, detail="Failed to parse generated questions")

    questions = json.loads(response_text[start:end])
    if not isinstance(questions, list) or not questions:
        raise HTTPException(status_code=500, detail="No questions generated")

    return questions[:num_questions]


def generate_raw_answer(question: str, rp_text: str) -> str:
    """Generate a gold-standard answer from raw RP universe text using Sonnet."""
    prompt = BASELINE_PROMPT.format(
        question=question,
        rp_text=rp_text[:100000],
    )

    return _call_sonnet(
        system="You are a legal analyst. Answer precisely using only the provided text.",
        user=prompt,
        max_tokens=3000,
    )


async def get_valence_answer(deal_id: str, question: str) -> str:
    """Get answer from the normal /ask pipeline."""
    try:
        result = await ask_question(deal_id, AskRequest(question=question))
        answer = result.get("answer", "")
        # Strip the evidence block if present (not useful for eval comparison)
        if "<!-- EVIDENCE" in answer:
            answer = answer[:answer.index("<!-- EVIDENCE")].rstrip()
        return answer
    except HTTPException as e:
        return f"[Pipeline error: {e.detail}]"
    except Exception as e:
        return f"[Pipeline error: {str(e)}]"


def compare_answers(question: str, raw_answer: str, valence_answer: str) -> Dict[str, Any]:
    """Compare raw vs Valence answers using Sonnet as judge."""
    import json

    prompt = COMPARISON_PROMPT.format(
        question=question,
        raw_answer=raw_answer,
        valence_answer=valence_answer,
    )

    response_text = _call_sonnet(
        system="You are an impartial judge comparing two legal analysis answers. Return ONLY JSON.",
        user=prompt,
        max_tokens=1500,
    )

    # Parse JSON
    start = response_text.find("{")
    end = response_text.rfind("}") + 1
    if start == -1 or end == 0:
        return {
            "completeness": 3, "accuracy": 3, "citations": 3, "specificity": 3,
            "advantages": [], "gaps": ["Could not parse comparison"],
            "reasoning": "Comparison parse failed",
        }

    try:
        return json.loads(response_text[start:end])
    except json.JSONDecodeError:
        return {
            "completeness": 3, "accuracy": 3, "citations": 3, "specificity": 3,
            "advantages": [], "gaps": ["Could not parse comparison"],
            "reasoning": "Comparison JSON parse failed",
        }


# =============================================================================
# ENDPOINT
# =============================================================================

@router.post("/{deal_id}/eval", response_model=EvalResult)
async def evaluate_deal(deal_id: str, request: EvalRequest) -> EvalResult:
    """
    Automated Q&A evaluation for a deal.

    1. Generate test questions from raw RP universe text
    2. For each question: get raw answer (Claude + raw text) and Valence answer (/ask pipeline)
    3. Compare and score each pair
    4. Return structured report

    Typical cost: ~$0.15-0.30 for 5 questions (all Sonnet calls).
    Typical time: ~2-3 minutes for 5 questions.
    """
    start_time = time.time()

    # Step 1: Load RP universe text
    rp_text = _get_rp_universe_text(deal_id)
    logger.info(f"Eval: loaded RP universe ({len(rp_text)} chars) for deal {deal_id}")

    # Step 2: Generate questions
    logger.info(f"Eval: generating {request.num_questions} questions...")
    questions = generate_questions(rp_text, request.num_questions, request.focus_areas)
    logger.info(f"Eval: generated {len(questions)} questions")

    # Step 3: For each question, get raw + Valence answers, then compare
    question_results: List[QuestionResult] = []
    total_score = 0.0
    max_score = 0.0

    for i, question in enumerate(questions):
        logger.info(f"Eval: processing question {i+1}/{len(questions)}: {question[:80]}...")

        # Get raw answer (baseline)
        raw_answer = generate_raw_answer(question, rp_text)

        # Get Valence answer (through extraction pipeline)
        valence_answer = await get_valence_answer(deal_id, question)

        # Compare
        comparison = compare_answers(question, raw_answer, valence_answer)

        # Calculate score (sum of 4 dimensions, max 20)
        dims = ["completeness", "accuracy", "citations", "specificity"]
        score = sum(comparison.get(d, 3) for d in dims)
        q_max = 20.0

        total_score += score
        max_score += q_max

        question_results.append(QuestionResult(
            question=question,
            raw_answer=raw_answer,
            valence_answer=valence_answer,
            score=float(score),
            max_score=q_max,
            advantages=comparison.get("advantages", []),
            gaps=comparison.get("gaps", []),
            reasoning=comparison.get("reasoning", ""),
        ))

    elapsed = round(time.time() - start_time, 1)
    pct = round((total_score / max_score * 100) if max_score > 0 else 0, 1)

    logger.info(f"Eval complete for {deal_id}: {pct}% ({total_score}/{max_score}) in {elapsed}s")

    return EvalResult(
        deal_id=deal_id,
        num_questions=len(questions),
        total_score=total_score,
        max_score=max_score,
        pct_score=pct,
        questions=question_results,
        elapsed_seconds=elapsed,
    )
