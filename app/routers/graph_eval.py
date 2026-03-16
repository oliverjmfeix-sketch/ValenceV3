"""
Graph Eval — gold standard evaluation with full pipeline tracing.

Endpoints:
  POST /api/graph-eval/{deal_id}         — Run eval using stored gold standard Q&A
  POST /api/deals/{deal_id}/graph-trace  — Run graph pipeline WITHOUT Claude (zero cost)
  GET  /api/eval-results/{deal_id}       — List saved eval result files
  GET  /api/eval-results/{deal_id}/{fn}  — Retrieve a saved eval result
  GET  /api/gold-standard                — List all gold standard sets
  GET  /api/gold-standard/{deal_id}      — Get gold standard Q&A for a deal
  PUT  /api/gold-standard/{deal_id}      — Save/update gold standard Q&A
"""
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.typedb_client import typedb_client
from app.services.trace_collector import TraceCollector
from app.services.graph_traversal import get_rp_entities
from app.services.topic_router import get_topic_router
from app.routers.deals import ask_question, ask_question_graph, AskRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Graph Eval"])

# ── Persistent storage dirs (Railway Volume at /app/uploads) ─────────────
EVAL_RESULTS_DIR = Path("/app/uploads/eval_results")
GOLD_STANDARD_DIR = Path("/app/uploads/gold_standard")
EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
GOLD_STANDARD_DIR.mkdir(parents=True, exist_ok=True)

# ── Seed gold standard files from bundled data if not already present ────
_SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "gold_standard"
if _SEED_DIR.is_dir():
    for seed_file in _SEED_DIR.glob("*.json"):
        dest = GOLD_STANDARD_DIR / seed_file.name
        if not dest.exists():
            import shutil
            shutil.copy2(seed_file, dest)
            logger.info(f"Seeded gold standard: {dest}")


# ═══════════════════════════════════════════════════════════════════════════
# GOLD STANDARD MODELS + ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class GoldStandardQuestion(BaseModel):
    """A single gold standard Q&A pair from lawyer analysis."""
    question_id: str = Field(..., description="e.g. 'duck_creek_q1'")
    question: str
    gold_answer: str
    source: str = Field("xtract_lawyer_report", description="Where this Q&A came from")
    category: Optional[str] = Field(None, description="e.g. 'builder_basket', 'reallocation'")
    requires_entities: List[str] = Field(default_factory=list, description="Entity types needed")
    added_date: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))


class DealGoldStandard(BaseModel):
    """Full gold standard set for a deal."""
    deal_id: str
    deal_name: str
    version: str = Field("1.0", description="Increment when questions change")
    questions: List[GoldStandardQuestion]
    last_updated: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


@router.get("/gold-standard")
async def list_gold_standards():
    """List all deals that have gold standard Q&A sets."""
    files = sorted(GOLD_STANDARD_DIR.glob("*.json"))
    results = []
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
            results.append({
                "deal_id": data["deal_id"],
                "deal_name": data.get("deal_name", ""),
                "version": data.get("version", ""),
                "question_count": len(data.get("questions", [])),
                "last_updated": data.get("last_updated", ""),
            })
    return {"deals": results}


@router.get("/gold-standard/{deal_id}")
async def get_gold_standard(deal_id: str):
    """Return the gold standard Q&A set for a deal."""
    filepath = GOLD_STANDARD_DIR / f"{deal_id}.json"
    if not filepath.exists():
        raise HTTPException(404, f"No gold standard found for deal {deal_id}")
    with open(filepath) as f:
        return json.load(f)


@router.put("/gold-standard/{deal_id}")
async def save_gold_standard(deal_id: str, data: DealGoldStandard):
    """Save or update the gold standard Q&A set for a deal."""
    filepath = GOLD_STANDARD_DIR / f"{deal_id}.json"
    with open(filepath, "w") as f:
        json.dump(data.dict(), f, indent=2)
    return {"saved": str(filepath), "question_count": len(data.questions)}


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH TRACE ENDPOINT (zero Claude cost)
# ═══════════════════════════════════════════════════════════════════════════

class TraceRequest(BaseModel):
    question: str = ""


@router.post("/deals/{deal_id}/graph-trace")
async def graph_trace(deal_id: str, request: TraceRequest = None):
    """
    Run the graph pipeline WITHOUT calling Claude.
    Returns the full trace: routing, provision lookup, queries, entity context, capacity breakdown.
    Zero API cost.
    """
    question = (request.question if request else "") or ""

    trace = TraceCollector()
    trace.question = question
    trace.deal_id = deal_id

    # Step 1: Covenant type routing
    start = time.time()
    try:
        topic_router = get_topic_router()
        route_result = topic_router.route(question) if question else None
        if route_result:
            trace.covenant_type = route_result.covenant_type
            trace.matched_categories = [
                {"id": cat.category_id, "name": cat.name, "covenant_type": cat.covenant_type}
                for cat in route_result.matched_categories
            ]
        else:
            trace.covenant_type = "rp"
    except Exception as e:
        trace.covenant_type = "rp"
        trace.routing_fallback = f"TopicRouter error: {str(e)[:100]}"
    trace.routing_duration_ms = (time.time() - start) * 1000

    # MFN graph entities not yet available
    if trace.covenant_type == "mfn":
        trace.routing_fallback = "mfn_graph_not_available — falling back to RP"
        trace.covenant_type = "rp"

    # Step 2: Provision lookup (embedded in get_rp_entities)
    start = time.time()
    if not typedb_client.driver:
        return {"error": "TypeDB not connected", "trace": trace.to_dict()}

    # Step 3+4: Run graph reader with tracing
    entity_context = get_rp_entities(deal_id, trace=trace)
    trace.provision_lookup_ms = (time.time() - start) * 1000

    if entity_context.startswith("("):
        return {"error": entity_context, "trace": trace.to_dict()}

    # Steps 5-7: NOT executed (no Claude call)

    return {
        "deal_id": deal_id,
        "provision_id": trace.provision_id,
        "question": question,
        "note": "Graph pipeline executed without Claude. Steps 5-7 (prompt assembly, Claude call, answer) not included. Use ?trace=true on /ask-graph to see the full pipeline.",
        "trace": trace.to_dict(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EVAL RESULTS PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════

def _save_eval_results(deal_id: str, results: dict) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"eval_{deal_id}_{timestamp}.json"
    filepath = EVAL_RESULTS_DIR / filename
    with open(filepath, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Eval results saved: {filepath}")
    return str(filepath)


@router.get("/eval-results/{deal_id}")
async def list_eval_results(deal_id: str):
    """List saved eval result files for a deal."""
    results = sorted(EVAL_RESULTS_DIR.glob(f"eval_{deal_id}_*.json"), reverse=True)
    return {
        "deal_id": deal_id,
        "results": [
            {
                "filename": r.name,
                "size_bytes": r.stat().st_size,
                "created": datetime.fromtimestamp(r.stat().st_mtime).isoformat(),
            }
            for r in results[:20]
        ]
    }


@router.get("/eval-results/{deal_id}/{filename}")
async def get_eval_result(deal_id: str, filename: str):
    """Return a saved eval result file with full traces."""
    filepath = EVAL_RESULTS_DIR / filename
    if not filepath.exists() or not filename.startswith(f"eval_{deal_id}_"):
        raise HTTPException(404, "Eval result not found")
    with open(filepath) as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════
# GRAPH EVAL — FULL PIPELINE WITH TRACING
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/graph-eval/{deal_id}")
async def run_graph_eval(deal_id: str):
    """
    Run graph eval using the stored gold standard Q&A set.
    Each question gets a full pipeline trace. Results are persisted.
    """
    filepath = GOLD_STANDARD_DIR / f"{deal_id}.json"
    if not filepath.exists():
        raise HTTPException(
            404,
            f"No gold standard found for deal {deal_id}. PUT to /api/gold-standard/{deal_id} first."
        )

    with open(filepath) as f:
        gold = json.load(f)

    questions = gold["questions"]
    eval_start = time.time()
    comparisons = []

    for question_data in questions:
        q = question_data["question"]
        req = AskRequest(question=q)

        # Run graph pipeline with trace=true
        graph_result = {
            "answer": "(error)", "citations": [], "entity_context_chars": 0,
            "evidence_entities": [], "entity_context": "", "trace": {}
        }
        try:
            graph_result = await ask_question_graph(deal_id, req, trace=True)
        except HTTPException as e:
            graph_result["answer"] = f"(HTTP {e.status_code}: {e.detail})"
        except Exception as e:
            graph_result["answer"] = f"(error: {e})"

        # Run scalar pipeline for comparison
        scalar_answer = ""
        try:
            scalar_result = await ask_question(deal_id, req)
            scalar_answer = scalar_result.get("answer", "")
        except Exception as e:
            scalar_answer = f"(error: {e})"

        # Patch scalar answer into the trace if present
        trace_dict = graph_result.get("trace", {})
        if trace_dict and trace_dict.get("step_7_answer"):
            trace_dict["step_7_answer"]["scalar_answer"] = scalar_answer

        comparison = {
            "question_id": question_data.get("question_id", ""),
            "question": q,
            "gold_answer": question_data.get("gold_answer", ""),
            "category": question_data.get("category", ""),
            "requires_entities": question_data.get("requires_entities", []),
            "graph_answer": graph_result.get("answer", ""),
            "scalar_answer": scalar_answer,
            "trace": trace_dict,
        }
        comparisons.append(comparison)

    total_elapsed = time.time() - eval_start

    # Compute total Claude cost from traces
    total_cost = 0.0
    for c in comparisons:
        synth = c.get("trace", {}).get("step_5_6_claude_synthesis")
        if synth and isinstance(synth, dict):
            total_cost += synth.get("cost_usd", 0.0)

    full_results = {
        "deal_id": deal_id,
        "deal_name": gold.get("deal_name", ""),
        "gold_standard_version": gold.get("version", ""),
        "timestamp": datetime.utcnow().isoformat(),
        "num_questions": len(comparisons),
        "comparisons": comparisons,
        "elapsed_seconds": round(total_elapsed, 1),
        "total_claude_cost_usd": round(total_cost, 4),
    }

    # Persist results
    results_path = _save_eval_results(deal_id, full_results)
    full_results["results_file"] = results_path

    return full_results
