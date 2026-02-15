"""
MFN Expert Q&A Evaluation — Re-extract + Test.

Flow:
1. Parse PDF and build MFN universe text (once)
2. Re-run MFN extraction to populate TypeDB (once)
3. For each expert question:
   a. Raw: Claude + MFN universe text
   b. TypeDB: /ask pipeline (cached extraction → synthesis)
4. Return structured JSON results
"""
import logging
import os
import time
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import anthropic

from app.config import settings
from app.routers.deals import ask_question, AskRequest, UPLOADS_DIR
from app.services.extraction import get_extraction_service
from app.services.typedb_client import get_typedb_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mfn-eval", tags=["MFN Eval"])


# =============================================================================
# EXPERT QUESTIONS — VERBATIM from domain expert Q&A on ACP Tara
# =============================================================================

EXPERT_QA = [
    {
        "id": "EQ1",
        "short": "Ratio prong MFN trigger",
        "question": 'Am I correct in saying that if you do a non-fungible, co-terminus 1L term loan through the ratio prong (not freebie), it won\'t trigger MFN?',
        "expert_answer": 'No, assuming it is incurred within the agreement under the Incremental Facility and assuming no other carve-out applies, MFN will be triggered to the extent it exceeds the $147m/100% EBITDA carveout. But it depends on what you mean by "non-fungible". If non-fungible means separate class but with the same terms then MFN will be triggerred as per above. But as per the question/answer above, a 1L pari TL can be incurred under Incremental Equivalent Debt without triggering the MFN.',
        "key_signals": ["MFN will be triggered", "$147m/100% EBITDA", "IED avoids MFN"],
    },
    {
        "id": "EQ2",
        "short": "Pari bond / sidecar avoidance",
        "question": "Can the $150m Incremental be issued in the form of pari secured bond or a separate pari Term Loan (under sidecar arrangement) such that MFN protection is not triggered?",
        "expert_answer": "Yes. Either under Incremental Equivalent Debt or Ratio Debt. And such amount is not only the $150m freebie, but also includes the general debt basket and debt under the 5.1x First Lien Net Leverage Ratio.\n\nAlso, the freebie basket under the Incremental Facility can also be used to incur incremental debt under this agreement without triggering MFN protection since the freebie is a carve-out.",
        "key_signals": ["IED", "Ratio Debt", "general debt basket", "5.1x", "freebie"],
    },
    {
        "id": "EQ3",
        "short": "$200M div recap with maturity escape",
        "question": "Can the borrower incur debt of $Xm without triggering MFN? The debt is: syndicated, matures 6 months after initial loan's maturity, is in USD, has a value of $200m, is incurred 2 months after the initial loan, has a margin of 200bps above the initial loan, is for the purpose of dividend recapitalization.",
        "expert_answer": "Yes, since it matures after the initial TL",
        "key_signals": ["Yes", "matures after"],
    },
    {
        "id": "EQ4",
        "short": "All avoidance methods",
        "question": "How could the borrower avoid triggering MFN?",
        "expert_answer": "(1) Ratio Debt\n(2) Incremental Equivalent Debt\n(3) Incremental Debt incurred 6 months after the Effective Date or later than 6 months\n(4) if incurred within 6 months then under the freebie, plus the general debt basket plus the $147m/100% EBITDA carveout\n(5) If incurred within 6 months debt that does not meet any one of the following requirements (change any of these and MFN falls away):\n - broadly syndicated\n - floating rate term loan\n - matures on or prior to the Term Maturity Date\n - rank pari in right of payment and security with initial\n - USD denominated\n - incurred under the 5.1x First Lien Net Leverage Ratio\n - incurred 6 months after Closing\n - incurred to finance a Permitted Acquisition or other Investment\n - using baskets referenced in (4) above",
        "key_signals": ["Ratio Debt", "IED", "6 months", "freebie", "general debt basket", "conjunctive conditions"],
    },
    {
        "id": "EQ5",
        "short": "Revolver at higher rate",
        "question": "If the borrower incurs a revolver, can they do so at a higher rate without triggering MFN?",
        "expert_answer": "Yes, MFN only applies to floating rate pari term loans.",
        "key_signals": ["Yes", "floating rate pari term loans"],
    },
    {
        "id": "EQ6",
        "short": "Acquisition + maturity >2y",
        "question": "If the borrower incurs additional debt to fund an acquisition and with a maturity >2 years than the existing term loan, is there no MFN?",
        "expert_answer": "Correct, no MFN",
        "key_signals": ["Correct", "no MFN"],
    },
    {
        "id": "EQ7",
        "short": "Sponsor loopholes",
        "question": "If you were a sponsor and wanted to raise debt without incurring the MFN, excluding any methods clearly intended to be legitimate by the lenders, how would you do this? I.e. find loopholes in the MFN provision",
        "expert_answer": "There are so many ways to raise debt without triggering the MFN and most Lenders do not understand all the carve-outs, even if clearly legitimate under the plain language of the document.\n(1) Incur under Ratio Debt or Incremental Equivalent Debt,\n(2) Incur under Incremental Facility but use carve-out for freebie, plus general debt basket plus $147m/100% EBITDA = $382.2m,\n(3) structure new debt to mature after Term Maturity Date,\n(4) or use any of the other carve-outs - fixed rate, non-USD, not broadly syndicated (private credit), or alter priority so technically new debt does not rank equal in payment priority (but still gets pari passu lien on collateral).",
        "key_signals": ["Ratio Debt", "IED", "$382.2m", "maturity", "fixed rate", "non-USD", "private credit", "alter priority"],
    },
    {
        "id": "EQ8",
        "short": "Effective Yield definition",
        "question": "Is MFN triggered (i) by the all-in yield differential of the new floating/fixed rate security, or (ii) by the actual spread (over the forward curve) differential?",
        "expert_answer": "Triggered by the Effective Yield on the Incremental Facility being 75bps greater than the Initial TL. But the MFN only applies to floating rate syndicated term loans.\n\n\"Effective Yield\" takes into account the margins, any floors, OID and any fees shared by lenders.",
        "key_signals": ["Effective Yield", "75bps", "floating rate syndicated", "margins", "floors", "OID", "fees shared by lenders"],
    },
    {
        "id": "EQ9",
        "short": "Separate agreement pari debt",
        "question": "Is pari-passu debt under a separate credit agreement protected by MFN?",
        "expert_answer": "No, pari passu Incremental Equivalent Debt and Ratio Debt has no MFN protection",
        "key_signals": ["No", "no MFN protection"],
    },
    {
        "id": "EQ10",
        "short": "Acquisition carveout language",
        "question": "Please provide the language where the agreement states there is a carveout for debt incurred in connection with acquisitions.",
        "expert_answer": "provided that, for any First Lien Incremental Term Facility incurred prior to the date that is six (6) months after the Effective Date (other than First Lien Incremental Term Facilities together with First Lien Incremental Equivalent Debt in an aggregate principal amount not to exceed the greater of (i) $147,000,000 and (ii) 100% of Consolidated EBITDA...), that (u) is not incurred in connection with a Permitted Acquisition or other Investment not prohibited by this Agreement...",
        "key_signals": ["verbatim provision text", "condition (u)", "Permitted Acquisition"],
    },
    {
        "id": "EQ11",
        "short": "SOFR vs market price",
        "question": "Yield at Effective Date (date when initial term loan was incurred) was 6%. The term loan is currently yielding 11%. If the borrower wanted to issue incremental at/above current yield, how exactly would the existing debt be repriced? Would the 6% or 11% be used in the yield differential calculation?",
        "expert_answer": 'Need to understand what is meant by "currently yielding". If the yield increase is due to an uptick in SOFR, that is taken into account. But if the yield increase is due to a decline in the market price, that is not included.',
        "key_signals": ["SOFR uptick taken into account", "market price not included"],
    },
]


# =============================================================================
# HELPERS
# =============================================================================

def _call_claude(system: str, user: str, max_tokens: int = 4000) -> str:
    """Call Claude Sonnet."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text


def _get_raw_answer(question: str, mfn_universe_text: str) -> str:
    """Raw one-shot: Claude reads MFN universe text and answers directly."""
    prompt = f"""You are a legal analyst specialising in leveraged finance credit agreements.
Answer the following question using ONLY the MFN-related text provided below.
Be precise and cite specific sections where possible.

## QUESTION

{question}

## MFN UNIVERSE TEXT

{mfn_universe_text[:200000]}"""

    return _call_claude(
        system="You are a legal analyst specialising in leveraged finance. Answer precisely using only the provided text.",
        user=prompt,
        max_tokens=4000,
    )


async def _get_typedb_answer(deal_id: str, question: str) -> str:
    """TypeDB pipeline: reads cached extraction from TypeDB, synthesizes answer."""
    try:
        result = await ask_question(deal_id, AskRequest(question=question))
        answer = result.get("answer", "")
        if "<!-- EVIDENCE" in answer:
            answer = answer[:answer.index("<!-- EVIDENCE")].rstrip()
        return answer
    except HTTPException as e:
        return f"[Pipeline error: {e.detail}]"
    except Exception as e:
        return f"[Pipeline error: {str(e)}]"


def _cleanup_existing_mfn_answers(deal_id: str):
    """Delete existing MFN provision_has_answer relations before re-extraction."""
    client = get_typedb_client()
    provision_id = f"{deal_id}_mfn"
    try:
        with client.write_transaction() as tx:
            tx.query(f"""
                match
                    $prov isa mfn_provision, has provision_id "{provision_id}";
                    $ans (provision: $prov, question: $q) isa provision_has_answer;
                delete $ans;
            """).resolve()
        logger.info(f"Cleaned up existing MFN answers for {deal_id}")
    except Exception as e:
        logger.warning(f"Cleanup skipped (may not have existing answers): {e}")


# =============================================================================
# MODELS
# =============================================================================

class MFNEvalResult(BaseModel):
    deal_id: str
    num_questions: int
    extraction_time_seconds: float
    mfn_universe_chars: int
    extraction_answers_count: int
    results: List[Dict[str, Any]]
    total_eval_seconds: float


# =============================================================================
# ENDPOINT
# =============================================================================

@router.post("/{deal_id}")
async def run_mfn_eval(
    deal_id: str,
    force_rebuild_universe: bool = False,
    skip_extraction: bool = False,
) -> MFNEvalResult:
    """
    MFN Expert Evaluation with re-extraction.

    Phase 1: Build MFN universe (skipped if cached file exists)
    Phase 2: Re-extract to TypeDB (skipped if skip_extraction=true)
    Phase 3: Evaluate (11 expert questions x 2 methods)

    Query params:
      - force_rebuild_universe: Re-parse PDF + re-segment even if cache exists
      - skip_extraction: Skip Phase 2 (use existing TypeDB data)
    """
    total_start = time.time()
    svc = get_extraction_service()
    document_text = None  # Set in Phase 1 if PDF is parsed

    # --- Phase 1: Build MFN universe (or load from cache) ----------------
    mfn_cache_path = os.path.join(UPLOADS_DIR, f"{deal_id}_mfn_universe.txt")

    if os.path.exists(mfn_cache_path) and not force_rebuild_universe:
        with open(mfn_cache_path, "r", encoding="utf-8") as f:
            mfn_universe_text = f.read()
        logger.info(
            f"MFN eval Phase 1: Using cached universe "
            f"({len(mfn_universe_text)} chars)"
        )
    else:
        logger.info(f"MFN eval Phase 1: Building MFN universe for {deal_id}")
        pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
        if not os.path.exists(pdf_path):
            raise HTTPException(status_code=404, detail=f"PDF not found: {pdf_path}")

        document_text = svc.parse_document(pdf_path)
        logger.info(f"Parsed PDF: {len(document_text)} chars")

        segment_map = svc.segment_document(document_text)
        found = sum(
            1 for s in segment_map.get("segments", []) if s.get("found", True)
        )
        logger.info(f"Segmentation: {found} sections found")

        mfn_universe_text = svc._build_mfn_universe_from_segments(
            document_text, segment_map
        )
        if not mfn_universe_text or len(mfn_universe_text) < 1000:
            logger.warning("Segmenter MFN universe too small, falling back to Claude")
            mfn_universe_text = svc.extract_mfn_universe(document_text)

        if not mfn_universe_text:
            raise HTTPException(
                status_code=500, detail="Failed to build MFN universe text"
            )

        os.makedirs(UPLOADS_DIR, exist_ok=True)
        with open(mfn_cache_path, "w", encoding="utf-8") as f:
            f.write(mfn_universe_text)
        logger.info(f"MFN universe cached: {len(mfn_universe_text)} chars")

    # --- Phase 2: Re-extract to TypeDB ------------------------------------
    extraction_time = 0.0
    answers_count = 0

    if skip_extraction:
        logger.info("MFN eval Phase 2: SKIPPED (skip_extraction=true)")
    else:
        logger.info("MFN eval Phase 2: Re-extracting 42 ontology questions")

        _cleanup_existing_mfn_answers(deal_id)

        extract_start = time.time()

        if document_text is None:
            pdf_path = os.path.join(UPLOADS_DIR, f"{deal_id}.pdf")
            document_text = svc.parse_document(pdf_path)

        extraction_result = await svc.run_mfn_extraction(
            deal_id=deal_id,
            mfn_universe_text=mfn_universe_text,
            document_text=document_text,
        )

        extraction_time = round(time.time() - extract_start, 1)
        answers_count = extraction_result.get("answered", 0)
        total_qs = extraction_result.get("total_questions", 0)
        logger.info(
            f"MFN extraction complete: {answers_count}/{total_qs} answers "
            f"in {extraction_time}s"
        )

    # --- Phase 3: Evaluate expert questions --------------------------------
    logger.info(f"MFN eval Phase 3: Testing {len(EXPERT_QA)} expert questions")
    results = []

    for eq in EXPERT_QA:
        logger.info(f"  {eq['id']}: {eq['question'][:60]}...")

        raw_start = time.time()
        raw_answer = _get_raw_answer(eq["question"], mfn_universe_text)
        raw_time = round(time.time() - raw_start, 2)

        tdb_start = time.time()
        tdb_answer = await _get_typedb_answer(deal_id, eq["question"])
        tdb_time = round(time.time() - tdb_start, 2)

        results.append({
            "id": eq["id"],
            "short": eq["short"],
            "question": eq["question"],
            "expert_answer": eq["expert_answer"],
            "key_signals": eq["key_signals"],
            "raw_answer": raw_answer,
            "raw_time_seconds": raw_time,
            "typedb_answer": tdb_answer,
            "typedb_time_seconds": tdb_time,
        })

        logger.info(f"  {eq['id']}: raw={raw_time}s, typedb={tdb_time}s")

    total_time = round(time.time() - total_start, 1)
    logger.info(f"MFN eval complete: {len(results)} questions in {total_time}s")

    return MFNEvalResult(
        deal_id=deal_id,
        num_questions=len(results),
        extraction_time_seconds=extraction_time,
        mfn_universe_chars=len(mfn_universe_text),
        extraction_answers_count=answers_count,
        results=results,
        total_eval_seconds=total_time,
    )
