"""
TypeDB Structure Ablation Test endpoint.

Compares three evidence formats against gold standard answers:
  A) Structured — current /ask pipeline (TypeDB categories + entities)
  B) Flat — same data, no structure (Postgres-EAV-style dump)
  C) Raw PDF — Claude reads RP universe text directly, no extraction

All three use show_reasoning=true so we can compare which facts Claude
selected and which interactions it found.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
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
    max_questions: Optional[int] = None  # limit for timeout safety


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
# PER-QUESTION HELPERS (concurrent A/B/C)
# =============================================================================

UPLOADS_DIR = settings.upload_dir


async def _run_raw_baseline(question: str, rp_text: str) -> dict:
    """Run Format C: send raw RP universe text to Claude directly."""
    from app.prompts.reasoning import (
        REASONING_SYSTEM_PROMPT,
        REASONING_FORMAT_INSTRUCTIONS,
    )

    raw_user_prompt = (
        "You are a legal analyst answering a question about a credit "
        "agreement using the raw text below. Answer thoroughly and "
        "precisely, citing specific sections and page numbers.\n\n"
        f"QUESTION: {question}\n\n"
        f"RP UNIVERSE TEXT:\n{rp_text[:100000]}\n\n"
        f"{REASONING_FORMAT_INSTRUCTIONS}"
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = await asyncio.to_thread(
        client.messages.create,
        model=settings.synthesis_model,
        max_tokens=6000,
        system=REASONING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": raw_user_prompt}],
    )

    raw_text = response.content[0].text
    reasoning, answer = _parse_reasoning_response(raw_text)
    return {"answer": answer, "reasoning": reasoning}


async def _run_judge(
    question: str,
    gold_answer: str,
    a_answer: str,
    a_reasoning,
    b_answer: str,
    b_reasoning,
    c_answer: str,
    c_reasoning,
) -> dict:
    """Run the judge prompt comparing all three format answers."""
    judge_prompt = ABLATION_JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        answer_a=a_answer,
        reasoning_a_json=json.dumps(a_reasoning) if a_reasoning else "null",
        answer_b=b_answer,
        reasoning_b_json=json.dumps(b_reasoning) if b_reasoning else "null",
        answer_c=c_answer,
        reasoning_c_json=json.dumps(c_reasoning) if c_reasoning else "null",
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = await asyncio.to_thread(
        client.messages.create,
        model=settings.synthesis_model,
        max_tokens=4000,
        messages=[{"role": "user", "content": judge_prompt}],
    )

    judge_text = response.content[0].text
    judge_raw = _strip_json_fences(judge_text)
    return json.loads(judge_raw)


async def _run_single_question(
    deal_id: str,
    question: str,
    gold_answer: str,
    rp_text: str,
) -> AblationQuestionResult:
    """Process one ablation question with A/B/C run concurrently."""
    from app.routers.deals import AskRequest, ask_question, ask_question_flat

    logger.info(f"Ablation Q: {question[:80]}...")

    # Run all three formats concurrently
    async def run_format_a():
        req = AskRequest(question=question, show_reasoning=True)
        return await ask_question(deal_id, req)

    async def run_format_b():
        req = AskRequest(question=question, show_reasoning=True)
        return await ask_question_flat(deal_id, req)

    async def run_format_c():
        return await _run_raw_baseline(question, rp_text)

    results = await asyncio.gather(
        run_format_a(),
        run_format_b(),
        run_format_c(),
        return_exceptions=True,
    )

    # Handle any exceptions from individual formats
    structured_result = results[0] if not isinstance(results[0], Exception) else None
    flat_result = results[1] if not isinstance(results[1], Exception) else None
    raw_result = results[2] if not isinstance(results[2], Exception) else None

    if isinstance(results[0], Exception):
        logger.error(f"Format A failed: {results[0]}")
    if isinstance(results[1], Exception):
        logger.error(f"Format B failed: {results[1]}")
    if isinstance(results[2], Exception):
        logger.error(f"Format C failed: {results[2]}")

    # Extract answers and reasoning
    a_answer = structured_result.get("answer", "") if structured_result else "[Format A failed]"
    a_reasoning = structured_result.get("reasoning") if structured_result else None
    b_answer = flat_result.get("answer", "") if flat_result else "[Format B failed]"
    b_reasoning = flat_result.get("reasoning") if flat_result else None
    c_answer = raw_result.get("answer", "") if raw_result else "[Format C failed]"
    c_reasoning = raw_result.get("reasoning") if raw_result else None

    # Run judge (serial — needs all three answers)
    structured_scores = {}
    flat_scores = {}
    raw_scores = {}
    structure_advantage = ""
    judge_summary = ""

    try:
        judge_parsed = await _run_judge(
            question, gold_answer,
            a_answer, a_reasoning,
            b_answer, b_reasoning,
            c_answer, c_reasoning,
        )
        structured_scores = judge_parsed.get("structured", {})
        flat_scores = judge_parsed.get("flat", {})
        raw_scores = judge_parsed.get("raw", {})
        structure_advantage = judge_parsed.get("structure_advantage", "")
        judge_summary = judge_parsed.get("summary", "")
    except Exception as e:
        logger.error(f"Ablation judge failed: {e}")
        judge_summary = f"[Judge error: {e}]"

    return AblationQuestionResult(
        question=question,
        gold_answer=gold_answer,
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
    )


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.post("/{deal_id}/ablation")
async def run_ablation_test(deal_id: str, request: AblationRequest) -> AblationResult:
    """Run the full ablation test comparing structured, flat, and raw PDF formats.

    Expected duration: ~45s per question (3 concurrent formats + 1 judge).
    With 6 questions: ~270s total.
    """
    overall_start = time.time()

    # === PRE-FLIGHT: Verify RP universe text exists ===
    uploads_dir = settings.upload_dir
    rp_universe_path = os.path.join(uploads_dir, f"{deal_id}_rp_universe.txt")
    if not os.path.exists(rp_universe_path):
        raise HTTPException(
            status_code=404,
            detail=(
                f"RP universe text not found at {rp_universe_path}. "
                f"Cannot run ablation without Format C baseline. "
                f"Either re-extract this deal or use a deal_id that "
                f"has a cached RP universe file."
            ),
        )
    with open(rp_universe_path, "r", encoding="utf-8") as f:
        rp_text = f.read()
    if len(rp_text) < 1000:
        raise HTTPException(
            status_code=422,
            detail=(
                f"RP universe text at {rp_universe_path} is only "
                f"{len(rp_text)} chars — too short for meaningful "
                f"analysis. Re-extract this deal."
            ),
        )
    logger.info(
        f"Ablation pre-flight PASS: RP universe loaded "
        f"({len(rp_text)} chars) from {rp_universe_path}"
    )

    # Apply max_questions limit if set
    questions_to_run = request.questions
    if request.max_questions:
        questions_to_run = questions_to_run[: request.max_questions]

    question_results = []
    for i, aq in enumerate(questions_to_run):
        logger.info(f"Ablation question {i + 1}/{len(questions_to_run)}")
        result = await _run_single_question(deal_id, aq.question, aq.gold_answer, rp_text)
        question_results.append(result)

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
    """Run the full Duck Creek ablation test with preset gold standard.

    If the provided deal_id doesn't have a cached RP universe, searches
    /app/uploads/ for any *_rp_universe.txt file and uses the first match.
    """
    from app.eval.duck_creek_ablation import DUCK_CREEK_ABLATION_QUESTIONS

    rp_path = os.path.join(UPLOADS_DIR, f"{deal_id}_rp_universe.txt")
    if not os.path.exists(rp_path):
        logger.warning(
            f"No RP universe for {deal_id}. "
            f"Scanning {UPLOADS_DIR} for alternatives..."
        )
        try:
            candidates = [
                f.replace("_rp_universe.txt", "")
                for f in os.listdir(UPLOADS_DIR)
                if f.endswith("_rp_universe.txt")
            ]
        except FileNotFoundError:
            candidates = []

        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No RP universe files found in {UPLOADS_DIR}. "
                    f"Upload and extract a Duck Creek PDF first."
                ),
            )
        logger.info(f"Found {len(candidates)} deals with RP universe: {candidates}")
        deal_id = candidates[0]
        logger.info(f"Using deal_id={deal_id} (has cached RP universe)")

    request = AblationRequest(
        questions=[AblationQuestion(**q) for q in DUCK_CREEK_ABLATION_QUESTIONS]
    )
    return await run_ablation_test(deal_id, request)
