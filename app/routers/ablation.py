"""
TypeDB Structure Ablation Test endpoint.

Compares three evidence formats against gold standard answers:
  A) Structured — current /ask pipeline (TypeDB categories + entities)
  B) Flat — same data, no structure (Postgres-EAV-style dump)
  C) Raw PDF — Claude reads RP universe text directly, no extraction

All three use show_reasoning=true so we can compare which facts Claude
selected and which interactions it found.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import anthropic

from app.config import settings
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Ablation"])


# =============================================================================
# REQUEST / RESPONSE MODELS
# =============================================================================

class AblationQuestion(BaseModel):
    question: str
    gold_answer: str


class AblationRequest(BaseModel):
    questions: List[AblationQuestion]


class AblationQuestionResult(BaseModel):
    question: str
    gold_answer: str
    # Format A
    structured_answer: str
    structured_reasoning: Optional[dict] = None
    structured_scores: dict = {}
    # Format B
    flat_answer: str
    flat_reasoning: Optional[dict] = None
    flat_scores: dict = {}
    # Format C
    raw_answer: str
    raw_reasoning: Optional[dict] = None
    raw_scores: dict = {}
    # Comparison
    structure_advantage: str = ""
    judge_summary: str = ""


class AblationSummary(BaseModel):
    avg_structured: dict = {}
    avg_flat: dict = {}
    avg_raw: dict = {}
    interactions_only_structured: List[str] = []
    interactions_only_with_extraction: List[str] = []
    interactions_all_found: List[str] = []
    total_cost_usd: float = 0.0


class AblationResult(BaseModel):
    deal_id: str
    questions: List[AblationQuestionResult]
    summary: AblationSummary
    elapsed_seconds: float
    report_file: Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================

def _strip_json_fences(text: str) -> str:
    """Strip markdown code fences and find JSON object boundaries."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
        raw = re.sub(r'\n?```\s*$', '', raw)
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1:
        raw = raw[start:end + 1]
    return raw


def _parse_reasoning_response(answer_text: str) -> tuple:
    """Parse a reasoning JSON response into (reasoning_dict, answer_str).

    Returns (None, original_text) on any parse failure.
    """
    from app.prompts.reasoning import ReasoningChain

    try:
        raw = _strip_json_fences(answer_text)
        parsed = json.loads(raw)
        reasoning_obj = ReasoningChain.model_validate(parsed["reasoning"])
        return reasoning_obj.model_dump(), parsed["answer"]
    except Exception as e:
        logger.warning("Ablation: reasoning parse failed: %s — first 200 chars: %.200s", e, answer_text)
        return None, answer_text


def _avg_scores(results: List[AblationQuestionResult], field: str) -> dict:
    """Average scores across all questions for a given format."""
    score_keys = ["completeness", "accuracy", "connections", "absence_detection"]
    totals = {k: 0.0 for k in score_keys}
    count = len(results)
    if count == 0:
        return totals
    for r in results:
        scores = getattr(r, field)
        for k in score_keys:
            totals[k] += scores.get(k, 0)
    return {k: round(v / count, 2) for k, v in totals.items()}


def _write_ablation_report(result: "AblationResult") -> str:
    """Write a human-readable txt report and return the file path."""
    uploads_dir = settings.upload_dir
    os.makedirs(uploads_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"ablation_{result.deal_id}_{ts}.txt"
    filepath = os.path.join(uploads_dir, filename)

    lines = []
    w = lines.append

    w("=" * 78)
    w("TYPEDB STRUCTURE ABLATION TEST RESULTS")
    w("=" * 78)
    w(f"Deal ID:       {result.deal_id}")
    w(f"Timestamp:     {ts}")
    w(f"Questions:     {len(result.questions)}")
    w(f"Elapsed:       {result.elapsed_seconds}s")
    w("")

    # ── Summary scores ────────────────────────────────────────────────
    w("-" * 78)
    w("AVERAGE SCORES (1-5)")
    w("-" * 78)
    w(f"{'Dimension':<22} {'Structured':>12} {'Flat':>12} {'Raw PDF':>12}")
    w("-" * 78)
    s = result.summary
    for dim in ("completeness", "accuracy", "connections", "absence_detection"):
        w(f"{dim:<22} {s.avg_structured.get(dim, 0):>12.2f} "
          f"{s.avg_flat.get(dim, 0):>12.2f} {s.avg_raw.get(dim, 0):>12.2f}")
    w("")

    # ── Interaction buckets ───────────────────────────────────────────
    w("-" * 78)
    w("INTERACTION ANALYSIS — WHERE DOES STRUCTURE ADD VALUE?")
    w("-" * 78)
    w("")
    w(f"Interactions found ONLY by Structured (TypeDB's unique value):")
    if s.interactions_only_structured:
        for item in s.interactions_only_structured:
            w(f"  * {item}")
    else:
        w("  (none)")
    w("")
    w(f"Interactions found by Structured+Flat but NOT Raw (extraction value):")
    if s.interactions_only_with_extraction:
        for item in s.interactions_only_with_extraction:
            w(f"  * {item}")
    else:
        w("  (none)")
    w("")
    w(f"Interactions found by ALL THREE (Claude already knows from training):")
    if s.interactions_all_found:
        for item in s.interactions_all_found:
            w(f"  * {item}")
    else:
        w("  (none)")
    w("")

    # ── Per-question detail ───────────────────────────────────────────
    for i, q in enumerate(result.questions, 1):
        w("=" * 78)
        w(f"QUESTION {i}/{len(result.questions)}")
        w("=" * 78)
        w("")
        w(f"Q: {q.question}")
        w("")
        w(f"GOLD STANDARD:")
        w(f"  {q.gold_answer}")
        w("")

        # ── Scores table for this question ────────────────────────────
        w(f"  {'Dimension':<22} {'Structured':>12} {'Flat':>12} {'Raw PDF':>12}")
        w(f"  {'-'*22} {'-'*12} {'-'*12} {'-'*12}")
        for dim in ("completeness", "accuracy", "connections", "absence_detection"):
            w(f"  {dim:<22} {q.structured_scores.get(dim, 0):>12} "
              f"{q.flat_scores.get(dim, 0):>12} {q.raw_scores.get(dim, 0):>12}")
        w("")
        w(f"  Structure advantage: {q.structure_advantage}")
        w(f"  Judge summary:       {q.judge_summary}")
        w("")

        # ── Format A: Structured ──────────────────────────────────────
        w("-" * 78)
        w("FORMAT A — STRUCTURED (TypeDB)")
        w("-" * 78)
        w("")
        if q.structured_reasoning:
            r = q.structured_reasoning
            w(f"  Issue: {r.get('issue', '')}")
            w(f"  Provisions cited: {len(r.get('provisions', []))}")
            for p in r.get("provisions", []):
                w(f"    - {p.get('question_id')}: {p.get('value')} [p.{p.get('source_page')}]")
                w(f"      {p.get('why_relevant', '')}")
            w(f"  Analysis points: {len(r.get('analysis', []))}")
            for a in r.get("analysis", []):
                w(f"    - {a}")
            interactions = r.get("interactions") or []
            w(f"  Interactions: {len(interactions)}")
            for ix in interactions:
                w(f"    [{ix.get('finding')}]")
                for c in ix.get("chain", []):
                    w(f"      > {c}")
                w(f"      => {ix.get('implication', '')}")
            w(f"  Conclusion: {r.get('conclusion', '')}")
            stats = r.get("evidence_stats") or {}
            w(f"  Evidence: {stats.get('cited_in_answer', '?')}/{stats.get('total_available', '?')} data points used")
        else:
            w("  (reasoning not available)")
        w("")
        w("  ANSWER:")
        for line in q.structured_answer.split("\n"):
            w(f"  {line}")
        w("")

        # ── Format B: Flat ────────────────────────────────────────────
        w("-" * 78)
        w("FORMAT B — FLAT (no structure)")
        w("-" * 78)
        w("")
        if q.flat_reasoning:
            r = q.flat_reasoning
            w(f"  Issue: {r.get('issue', '')}")
            w(f"  Provisions cited: {len(r.get('provisions', []))}")
            for p in r.get("provisions", []):
                w(f"    - {p.get('question_id')}: {p.get('value')} [p.{p.get('source_page')}]")
                w(f"      {p.get('why_relevant', '')}")
            w(f"  Analysis points: {len(r.get('analysis', []))}")
            for a in r.get("analysis", []):
                w(f"    - {a}")
            interactions = r.get("interactions") or []
            w(f"  Interactions: {len(interactions)}")
            for ix in interactions:
                w(f"    [{ix.get('finding')}]")
                for c in ix.get("chain", []):
                    w(f"      > {c}")
                w(f"      => {ix.get('implication', '')}")
            w(f"  Conclusion: {r.get('conclusion', '')}")
            stats = r.get("evidence_stats") or {}
            w(f"  Evidence: {stats.get('cited_in_answer', '?')}/{stats.get('total_available', '?')} data points used")
        else:
            w("  (reasoning not available)")
        w("")
        w("  ANSWER:")
        for line in q.flat_answer.split("\n"):
            w(f"  {line}")
        w("")

        # ── Format C: Raw PDF ─────────────────────────────────────────
        w("-" * 78)
        w("FORMAT C — RAW PDF (no extraction)")
        w("-" * 78)
        w("")
        if q.raw_reasoning:
            r = q.raw_reasoning
            w(f"  Issue: {r.get('issue', '')}")
            w(f"  Provisions cited: {len(r.get('provisions', []))}")
            for p in r.get("provisions", []):
                w(f"    - {p.get('question_id')}: {p.get('value')} [p.{p.get('source_page')}]")
                w(f"      {p.get('why_relevant', '')}")
            w(f"  Analysis points: {len(r.get('analysis', []))}")
            for a in r.get("analysis", []):
                w(f"    - {a}")
            interactions = r.get("interactions") or []
            w(f"  Interactions: {len(interactions)}")
            for ix in interactions:
                w(f"    [{ix.get('finding')}]")
                for c in ix.get("chain", []):
                    w(f"      > {c}")
                w(f"      => {ix.get('implication', '')}")
            w(f"  Conclusion: {r.get('conclusion', '')}")
            stats = r.get("evidence_stats") or {}
            w(f"  Evidence: {stats.get('cited_in_answer', '?')}/{stats.get('total_available', '?')} data points used")
        else:
            w("  (reasoning not available)")
        w("")
        w("  ANSWER:")
        for line in q.raw_answer.split("\n"):
            w(f"  {line}")
        w("")

    w("=" * 78)
    w("END OF ABLATION REPORT")
    w("=" * 78)

    report_text = "\n".join(lines)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)

    logger.info(f"Ablation report written: {filepath} ({len(report_text)} chars)")
    return filepath


# =============================================================================
# JUDGE PROMPT
# =============================================================================

ABLATION_JUDGE_PROMPT = """You are comparing three answers to the same legal question about a credit agreement, judging each against a gold standard answer written by a senior leveraged finance lawyer.

QUESTION: {question}

GOLD STANDARD: {gold_answer}

ANSWER A (Structured TypeDB data): {answer_a}
REASONING A: {reasoning_a_json}

ANSWER B (Flat unstructured data): {answer_b}
REASONING B: {reasoning_b_json}

ANSWER C (Raw PDF text): {answer_c}
REASONING C: {reasoning_c_json}

For each answer, score on these dimensions (1-5):
1. Completeness — does it cover all points in the gold standard?
2. Accuracy — are factual claims correct vs gold standard?
3. Connections — did it identify relationships BETWEEN provisions (e.g. basket stacking, reallocation chains, definition gaps)?
4. Absence detection — did it correctly identify things that are NOT present or NOT covered?

Also identify for each answer:
- interactions_found: list of cross-provision findings (from reasoning)
- interactions_missed: findings in gold standard that this answer missed
- false_interactions: findings claimed but not supported by gold standard

Return ONLY valid JSON with no markdown fencing:
{{
  "structured": {{
    "completeness": 0, "accuracy": 0, "connections": 0,
    "absence_detection": 0,
    "interactions_found": [], "interactions_missed": [],
    "false_interactions": []
  }},
  "flat": {{
    "completeness": 0, "accuracy": 0, "connections": 0,
    "absence_detection": 0,
    "interactions_found": [], "interactions_missed": [],
    "false_interactions": []
  }},
  "raw": {{
    "completeness": 0, "accuracy": 0, "connections": 0,
    "absence_detection": 0,
    "interactions_found": [], "interactions_missed": [],
    "false_interactions": []
  }},
  "structure_advantage": "1-2 sentences on what structured format enabled that flat/raw missed, or 'none' if no advantage observed",
  "summary": "1-2 sentence overall finding"
}}"""


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/{deal_id}/ablation")
async def run_ablation_test(deal_id: str, request: AblationRequest) -> AblationResult:
    """Run the full ablation test comparing structured, flat, and raw PDF formats."""
    overall_start = time.time()

    # Import here to avoid circular imports
    from app.routers.deals import (
        AskRequest,
        ask_question,
        ask_question_flat,
        get_rp_provision,
        get_mfn_provision,
    )
    from app.prompts.reasoning import (
        REASONING_SYSTEM_PROMPT,
        REASONING_FORMAT_INSTRUCTIONS,
    )

    # Load RP universe text for Format C
    uploads_dir = settings.upload_dir
    rp_path = Path(uploads_dir) / f"{deal_id}_rp_universe.txt"
    rp_text = ""
    if rp_path.exists():
        rp_text = rp_path.read_text(encoding="utf-8")
    else:
        logger.warning(f"Ablation: RP universe text not found for {deal_id}")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model_used = settings.synthesis_model

    question_results = []

    for aq in request.questions:
        logger.info(f"Ablation: processing question: {aq.question[:80]}...")

        # ── Format A: Structured (existing /ask) ──────────────────────
        try:
            a_request = AskRequest(question=aq.question, show_reasoning=True)
            a_response = await ask_question(deal_id, a_request)
            a_answer = a_response.get("answer", "")
            a_reasoning = a_response.get("reasoning")
        except Exception as e:
            logger.error(f"Ablation Format A failed: {e}")
            a_answer = f"[ERROR: {e}]"
            a_reasoning = None

        # ── Format B: Flat (/ask-flat) ────────────────────────────────
        try:
            b_request = AskRequest(question=aq.question, show_reasoning=True)
            b_response = await ask_question_flat(deal_id, b_request)
            b_answer = b_response.get("answer", "")
            b_reasoning = b_response.get("reasoning")
        except Exception as e:
            logger.error(f"Ablation Format B failed: {e}")
            b_answer = f"[ERROR: {e}]"
            b_reasoning = None

        # ── Format C: Raw PDF ─────────────────────────────────────────
        c_answer = ""
        c_reasoning = None
        if rp_text:
            try:
                raw_user_prompt = f"""You are a legal analyst answering a question about a credit agreement using the raw text below. Answer thoroughly and precisely, citing specific sections and page numbers.

QUESTION: {aq.question}

RP UNIVERSE TEXT:
{rp_text[:100000]}

{REASONING_FORMAT_INSTRUCTIONS}"""

                raw_response = client.messages.create(
                    model=model_used,
                    max_tokens=6000,
                    system=REASONING_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": raw_user_prompt}]
                )
                raw_text = raw_response.content[0].text
                c_reasoning, c_answer = _parse_reasoning_response(raw_text)
            except Exception as e:
                logger.error(f"Ablation Format C failed: {e}")
                c_answer = f"[ERROR: {e}]"
                c_reasoning = None
        else:
            c_answer = "[RP universe text not available for this deal]"

        # ── Judge all three against gold standard ─────────────────────
        structured_scores = {}
        flat_scores = {}
        raw_scores = {}
        structure_advantage = ""
        judge_summary = ""

        try:
            judge_prompt = ABLATION_JUDGE_PROMPT.format(
                question=aq.question,
                gold_answer=aq.gold_answer,
                answer_a=a_answer,
                reasoning_a_json=json.dumps(a_reasoning) if a_reasoning else "null",
                answer_b=b_answer,
                reasoning_b_json=json.dumps(b_reasoning) if b_reasoning else "null",
                answer_c=c_answer,
                reasoning_c_json=json.dumps(c_reasoning) if c_reasoning else "null",
            )

            judge_response = client.messages.create(
                model=model_used,
                max_tokens=4000,
                messages=[{"role": "user", "content": judge_prompt}]
            )
            judge_text = judge_response.content[0].text
            judge_raw = _strip_json_fences(judge_text)
            judge_parsed = json.loads(judge_raw)

            structured_scores = judge_parsed.get("structured", {})
            flat_scores = judge_parsed.get("flat", {})
            raw_scores = judge_parsed.get("raw", {})
            structure_advantage = judge_parsed.get("structure_advantage", "")
            judge_summary = judge_parsed.get("summary", "")
        except Exception as e:
            logger.error(f"Ablation judge failed: {e}")
            judge_summary = f"[Judge error: {e}]"

        question_results.append(AblationQuestionResult(
            question=aq.question,
            gold_answer=aq.gold_answer,
            structured_answer=a_answer,
            structured_reasoning=a_reasoning,
            structured_scores=structured_scores,
            flat_answer=b_answer,
            flat_reasoning=b_reasoning,
            flat_scores=flat_scores,
            raw_answer=c_answer,
            raw_reasoning=c_reasoning,
            raw_scores=raw_scores,
            structure_advantage=structure_advantage,
            judge_summary=judge_summary,
        ))

    # ── Build summary ─────────────────────────────────────────────────
    # Collect interactions across formats
    all_structured_interactions = set()
    all_flat_interactions = set()
    all_raw_interactions = set()

    for r in question_results:
        for label_list, target_set in [
            (r.structured_scores.get("interactions_found", []), all_structured_interactions),
            (r.flat_scores.get("interactions_found", []), all_flat_interactions),
            (r.raw_scores.get("interactions_found", []), all_raw_interactions),
        ]:
            for item in label_list:
                target_set.add(str(item))

    interactions_only_structured = sorted(
        all_structured_interactions - all_flat_interactions - all_raw_interactions
    )
    interactions_only_with_extraction = sorted(
        (all_structured_interactions | all_flat_interactions) - all_raw_interactions
    )
    interactions_all_found = sorted(
        all_structured_interactions & all_flat_interactions & all_raw_interactions
    )

    elapsed = time.time() - overall_start

    summary = AblationSummary(
        avg_structured=_avg_scores(question_results, "structured_scores"),
        avg_flat=_avg_scores(question_results, "flat_scores"),
        avg_raw=_avg_scores(question_results, "raw_scores"),
        interactions_only_structured=interactions_only_structured,
        interactions_only_with_extraction=interactions_only_with_extraction,
        interactions_all_found=interactions_all_found,
        total_cost_usd=0.0,  # TODO: aggregate from cost_tracker
    )

    result = AblationResult(
        deal_id=deal_id,
        questions=question_results,
        summary=summary,
        elapsed_seconds=round(elapsed, 1),
    )

    # Write human-readable report to disk
    try:
        result.report_file = _write_ablation_report(result)
    except Exception as e:
        logger.error(f"Failed to write ablation report: {e}")

    return result


@router.post("/{deal_id}/ablation/duck-creek")
async def run_duck_creek_ablation(deal_id: str) -> AblationResult:
    """Run the full Duck Creek ablation test with preset gold standard."""
    from app.eval.duck_creek_ablation import DUCK_CREEK_ABLATION_QUESTIONS

    request = AblationRequest(
        questions=[AblationQuestion(**q) for q in DUCK_CREEK_ABLATION_QUESTIONS]
    )
    return await run_ablation_test(deal_id, request)
