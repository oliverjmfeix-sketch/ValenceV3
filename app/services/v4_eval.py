"""
Valence v4 — pilot eval runner

Runs a gold-standard eval set end-to-end through the v4 pipeline:

  question -> intent_parser.answer_question -> renderer.render_response
           -> side-by-side comparison artifact (gold vs Valence)

Produces three output artifacts per run, modeled on v3's
app/routers/graph_eval.py format:

  *_full.json      — complete trace data for debugging / analysis
  *_summary.txt    — per-question status + timing
  *_verbatim.txt   — side-by-side gold vs Valence output (primary
                     deliverable — human judgment on this)

No scoring. No pass/fail rubric. Human reads the verbatim artifact.

CLI:

  py -3.12 -m app.services.v4_eval run --eval-set lawyer_dc_rp
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(_REPO_ROOT / ".env", override=False)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("v4_eval")


# ─── Eval set catalog ─────────────────────────────────────────────────────────

EVAL_SETS: dict[str, dict[str, Any]] = {
    "lawyer_dc_rp": {
        "id": "lawyer_dc_rp",
        "name": "Lawyer Q&A — Duck Creek RP (v4 pilot acceptance)",
        "questions_count": 6,
        "covenant": "RP",
        "deal": "Duck Creek",
        "resolve_deal_id": "6e76ed06",
        "gold_file": "app/data/gold_standard/lawyer_dc_rp.json",
    },
}


# ─── Default pilot world state (for evaluated questions) ──────────────────────

# Representative Duck Creek baseline. Back-solved from xtract Q12 gold's
# "$520M or 409.9% of EBITDA": 4 x $130M floor = $520M implies EBITDA ~
# $127M (4 x 130 / 4.099). Per the gold-question posture audit.
#
# Used automatically when the parser routes an evaluated operation and no
# --world-state flag was supplied to the CLI. Documented in
# docs/v4_gold_question_posture_audit.md.
DEFAULT_PILOT_WORLD_STATE: dict[str, Any] = {
    "predicate_values": {
        "first_lien_net_leverage_ratio": 4.50,
        "senior_secured_leverage_ratio": 5.00,
        "total_leverage_ratio": 5.50,
        "consolidated_ebitda_ltm": 127_000_000,
        "cumulative_consolidated_net_income": 180_000_000,
        "available_retained_ecf_amount": 42_000_000,
        "cumulative_ebitda_since_closing": 254_000_000,
        "cumulative_fixed_charges_since_closing": 145_000_000,
        "cumulative_deferred_revenues": 0,
        "ipo_proceeds_usd": 0,
        "market_cap_usd": 0,
        "no_event_of_default_exists": True,
        "pro_forma_compliance_financial_covenants": True,
        "qualified_ipo_has_occurred": False,
        "unsub_would_own_or_license_material_ip_at_designation": False,
        "is_product_line_or_line_of_business_sale": False,
        "individual_proceeds_amount_usd": 0,
        "annual_aggregate_proceeds_amount_usd": 0,
        "prior_year_capacity_was_unused": False,
        "base_capacity_will_be_unused_in_subsequent_year": False,
        "incurrence_test_satisfied": True,
        "officer_certificate_delivered": True,
        "board_approval_obtained": True,
    },
    "proposed_action": {
        "action_class": None,
        "amount_usd": None,
        "is_pro_forma_no_worse": True,  # Q6 hypothetical default
        "target_party_role": None,
    },
}


OUTPUT_DIR = _REPO_ROOT / "app" / "data" / "v4_eval_results"


# ─── Per-question runner ──────────────────────────────────────────────────────


def _run_single_question(question: dict, deal_id: str,
                          world_state: dict | None = None) -> dict:
    """Run one question through parser -> renderer. Never raises."""
    # Local imports so a module-load failure in renderer doesn't abort the
    # whole runner.
    from app.services import intent_parser, renderer

    question_text = question["question"]
    gold_answer = question["gold_answer"]
    question_id = question["question_id"]

    start = time.perf_counter()
    parser_response: dict = {}
    valence_answer = "(not rendered)"
    execution_error: str | None = None
    world_state_used = None

    try:
        # First parse-only to see if the intent will need world state.
        # answer_question handles the full flow, including auto-supplying
        # DEFAULT_PILOT_WORLD_STATE for evaluated ops when no state passed.
        parser_response = intent_parser.answer_question(
            question=question_text,
            deal_id=deal_id,
            world_state=world_state,
        )
        # If the parser classified as an evaluated operation_call but we
        # got a "world state required" error, re-run with the default
        # pilot world state.
        op_resp = parser_response.get("operation_response") or {}
        needs_ws = (
            parser_response.get("intent_classification") == "operation_call"
            and isinstance(op_resp, dict)
            and "evaluated operation requires supplied_world_state" in str(op_resp.get("error", ""))
        )
        if needs_ws and world_state is None:
            parser_response = intent_parser.answer_question(
                question=question_text,
                deal_id=deal_id,
                world_state=DEFAULT_PILOT_WORLD_STATE,
            )
            world_state_used = "default_pilot_world_state"
        elif parser_response.get("intent_classification") == "operation_call":
            world_state_used = parser_response.get("world_state_source")

        # Render
        valence_answer = renderer.render_response(parser_response)
    except Exception as exc:  # noqa: BLE001
        execution_error = f"{type(exc).__name__}: {exc}"
        logger.warning("question %s raised: %s", question_id, execution_error)
        logger.debug("traceback:\n%s", traceback.format_exc())

    elapsed = time.perf_counter() - start

    return {
        "question_id": question_id,
        "question": question_text,
        "gold_answer": gold_answer,
        "valence_answer": valence_answer,
        "parser_intent_classification": parser_response.get("intent_classification"),
        "parser_parsed_as": parser_response.get("parsed_as"),
        "parser_operation": parser_response.get("operation"),
        "parser_parameters": parser_response.get("parameters"),
        "parser_confidence": parser_response.get("intent_confidence"),
        "raw_parser_response_hash": parser_response.get("raw_claude_response_hash"),
        "parser_latency_ms": parser_response.get("parser_latency_ms"),
        "world_state_used": world_state_used,
        "execution_error": execution_error,
        "elapsed_seconds": round(elapsed, 2),
        "full_parser_response": parser_response,
    }


# ─── Artifact writers ─────────────────────────────────────────────────────────


def _write_full_json(out_path: Path, run: dict) -> None:
    out_path.write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")


def _write_summary(out_path: Path, run: dict) -> None:
    eval_set = run["eval_set"]
    lines = [
        f"V4 EVAL SUMMARY: {eval_set['deal']}",
        "=" * 60,
        f"Deal ID:        {run['deal_id']}",
        f"Gold standard:  {eval_set['id']} ({eval_set.get('questions_count','?')} questions)",
        f"Covenant:       {eval_set['covenant']}",
        f"Timestamp:      {run['timestamp']}",
        f"Total time:     {run['total_seconds']:.1f}s",
        f"Parser model:   {run.get('parser_model', 'n/a')}",
        "",
        "Per-question status:",
    ]
    for q in run["questions"]:
        status = "OK" if q.get("execution_error") is None else "ERR"
        short = q["question"]
        if len(short) > 60:
            short = short[:57] + "..."
        lines.append(
            f"  [{status}] {q['question_id']}: {short} "
            f"({q['elapsed_seconds']}s, op={q.get('parser_operation','-')}, "
            f"class={q.get('parser_intent_classification','-')})"
        )
    lines.append("")
    lines.append("Artifacts:")
    for k in ("full_json_path", "verbatim_path", "summary_path"):
        if k in run:
            lines.append(f"  {k}: {run[k]}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_verbatim(out_path: Path, run: dict) -> None:
    eval_set = run["eval_set"]
    lines = [
        f"V4 EVAL VERBATIM: {eval_set['deal']}",
        "=" * 60,
        f"Deal ID:        {run['deal_id']}",
        f"Gold standard:  {eval_set['id']} ({eval_set.get('questions_count','?')} questions)",
        f"Timestamp:      {run['timestamp']}",
        "",
    ]
    for i, q in enumerate(run["questions"], start=1):
        lines.append("─" * 60)
        lines.append(f"Q{i} [{q['question_id']}]: {q['question']}")
        lines.append("─" * 60)
        lines.append("")
        lines.append("GOLD ANSWER:")
        lines.append(q["gold_answer"])
        lines.append("")
        lines.append("VALENCE ANSWER:")
        lines.append(q["valence_answer"].rstrip())
        lines.append("")
        lines.append(f"Parser interpretation: {q.get('parser_parsed_as','n/a')}")
        if q.get("parser_operation"):
            lines.append(f"Parser operation: {q['parser_operation']}")
        if q.get("parser_intent_classification") != "operation_call":
            lines.append(f"Classification: {q.get('parser_intent_classification')}")
        if q.get("world_state_used"):
            lines.append(f"World state source: {q['world_state_used']}")
        if q.get("execution_error"):
            lines.append(f"EXECUTION ERROR: {q['execution_error']}")
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── Main runner ──────────────────────────────────────────────────────────────


def run_eval(eval_set_id: str, deal_id: str | None = None,
             world_state: dict | None = None) -> dict:
    eval_set = EVAL_SETS.get(eval_set_id)
    if eval_set is None:
        raise ValueError(f"Unknown eval set: {eval_set_id!r}. "
                         f"Known: {sorted(EVAL_SETS.keys())}")

    gold_path = _REPO_ROOT / eval_set["gold_file"]
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    deal_id = deal_id or gold.get("resolve_deal_id") or eval_set.get("resolve_deal_id")
    if not deal_id:
        raise ValueError("deal_id not provided and not in gold file")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    timestamp_iso = _dt.datetime.now().isoformat()
    logger.info("eval set %s -> deal %s (%d questions)",
                eval_set_id, deal_id, len(gold["questions"]))

    start_total = time.perf_counter()
    results: list[dict] = []
    for i, q in enumerate(gold["questions"], start=1):
        logger.info("  [%d/%d] %s", i, len(gold["questions"]), q["question_id"])
        r = _run_single_question(q, deal_id, world_state=world_state)
        results.append(r)
        logger.info("     -> %s (%.1fs)",
                    r.get("parser_operation") or r.get("parser_intent_classification"),
                    r["elapsed_seconds"])
    total_elapsed = time.perf_counter() - start_total

    # Artifact paths
    base = OUTPUT_DIR / f"v4_eval_{deal_id}_{ts}"
    full_json = Path(str(base) + "_full.json")
    summary = Path(str(base) + "_summary.txt")
    verbatim = Path(str(base) + "_verbatim.txt")

    parser_model = None
    for r in results:
        pr = r.get("full_parser_response") or {}
        if pr.get("claude_model_used"):
            parser_model = pr["claude_model_used"]
            break

    run = {
        "timestamp": timestamp_iso,
        "eval_set": eval_set,
        "deal_id": deal_id,
        "total_seconds": total_elapsed,
        "parser_model": parser_model,
        "questions": results,
        "full_json_path": str(full_json),
        "summary_path": str(summary),
        "verbatim_path": str(verbatim),
    }

    _write_full_json(full_json, run)
    _write_verbatim(verbatim, run)
    _write_summary(summary, run)

    logger.info("done in %.1fs", total_elapsed)
    logger.info("  full JSON : %s", full_json)
    logger.info("  summary   : %s", summary)
    logger.info("  verbatim  : %s", verbatim)

    return run


def main() -> int:
    parser = argparse.ArgumentParser(description="Valence v4 pilot eval runner")
    sub = parser.add_subparsers(dest="op", required=True)

    p_run = sub.add_parser("run", help="Run an eval set end-to-end.")
    p_run.add_argument("--eval-set", required=True,
                       choices=sorted(EVAL_SETS.keys()))
    p_run.add_argument("--deal-id", default=None,
                       help="Override deal_id (default: from gold file)")
    p_run.add_argument("--world-state", default=None,
                       help="Path to JSON with supplied_world_state. Default: "
                            "auto-supplied DEFAULT_PILOT_WORLD_STATE when an "
                            "evaluated operation needs it.")

    args = parser.parse_args()
    if args.op == "run":
        ws = None
        if args.world_state:
            ws = json.loads(Path(args.world_state).read_text(encoding="utf-8"))
        run_eval(args.eval_set, deal_id=args.deal_id, world_state=ws)
        return 0

    parser.error(f"unknown op: {args.op}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
