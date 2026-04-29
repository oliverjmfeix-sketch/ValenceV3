"""
Phase G commit 1 — synthesis authority diagnostic.

Three-phase methodology to disambiguate the Q4 / Q5 Stage 2 override
phenomenon between mechanisms:
  (a) LLM stylistic choice (residual; inferred when neither (b) nor (c) moves behavior)
  (b) Prompt-iteration distance (synthesis_guidance ignored or under-weighted)
  (c) Data-presentation issue (payload depth / positioning suppresses signal)

Probes:
  Phase 1 — baselines: run Q4 + Q5 with current configuration. Captures
            full Stage 1 + Stage 2 raw responses + cite sets.
  Phase 2 — controlled variations:
            V1: Q4 with category L synthesis_guidance temporarily emptied
            V2: Q5 with category N synthesis_guidance temporarily emptied
            V3: Q4 with provision_level_entities reordered (sweep_tier and
                asset_sale_sweep moved to front of `by_type` dict)
            V4: Q5 with norms reordered (RDP norms moved to front of
                primary_norms list before Stage 2 sees them)
  Phase 3 — synthesize: per question, classify the operative mechanism
            from comparison of Phase 1 baseline vs Phase 2 variations.

No DB mutation: variations are achieved via monkey-patches on the
TopicRouter and fetch helpers for the duration of a single run, then
restored. Idempotent. Re-runnable.

Output: docs/v4_synthesis_diagnostic_runs/<timestamp>/<probe_id>.json
plus a summary at docs/v4_synthesis_diagnostic_summary.json.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_g_synthesis_diagnostic
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Use override=True for ANTHROPIC_API_KEY but preserve CLI-supplied
# TYPEDB_DATABASE (matches the Phase E commit 0 CLI pattern).
_cli_typedb_database = os.environ.get("TYPEDB_DATABASE")
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
if _cli_typedb_database:
    os.environ["TYPEDB_DATABASE"] = _cli_typedb_database

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("phase_g_diagnostic")


# ─────────────────────────────────────────────────────────────────────────
# Question definitions
# ─────────────────────────────────────────────────────────────────────────

Q4_TEXT = "Can any asset sale proceeds be used to make dividends?"
Q5_TEXT = "Determine the total amount of quantifiable dividend capacity."


def synthesize(question: str, deal_id: str = "6e76ed06") -> dict:
    """Run a single synthesize_one_question and return a dict with the
    fields most useful for diagnostic comparison."""
    from app.services import synthesis_v4
    result = synthesis_v4.synthesize_one_question(question, deal_id, "valence_v4")
    s1 = result.stage1
    s2 = result.stage2
    return {
        "question": question,
        "stage1_primary_count": len(s1.primary_norm_ids),
        "stage1_supplementary_count": len(s1.supplementary_norm_ids),
        "stage1_skip_count": len(s1.skip_norm_ids),
        "stage1_classifications": s1.classifications,
        "stage2_citations": s2.citations,
        "stage2_citation_norm_ids": [c.get("norm_id") for c in s2.citations],
        "stage2_answer": s2.answer,
        "stage2_reasoning_conclusion": s2.reasoning.get("conclusion", "") if s2.reasoning else "",
        "stage2_reasoning_analysis": s2.reasoning.get("analysis", "") if s2.reasoning else "",
        "total_cost_usd": result.total_cost_usd,
    }


# ─────────────────────────────────────────────────────────────────────────
# Variation: empty synthesis_guidance for a specific category
# ─────────────────────────────────────────────────────────────────────────

def with_emptied_guidance(category_id: str):
    """Context-manager-style decorator that monkey-patches TopicRouter to
    return empty synthesis_guidance for a target category, restores after."""
    from app.services import topic_router as tr_module

    class _Patcher:
        def __enter__(self):
            self._original = tr_module.TopicRouter.get_synthesis_guidance

            def _patched(self_router, matched_categories):
                # Filter out the target category from guidance assembly,
                # but leave others. This isolates the variable.
                filtered = [c for c in matched_categories
                            if c.category_id != category_id]
                return self._original(self_router, filtered)

            tr_module.TopicRouter.get_synthesis_guidance = _patched
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            tr_module.TopicRouter.get_synthesis_guidance = self._original

    return _Patcher()


# ─────────────────────────────────────────────────────────────────────────
# Variation: reorder fetch payload
# ─────────────────────────────────────────────────────────────────────────

def with_reordered_provision_entities(priority_types: list):
    """Monkey-patches fetch_norm_context to push the listed entity types
    to the front of provision_level_entities.by_type. Tests whether
    payload position affects Stage 2 attention."""
    from app.services import synthesis_v4_fetch as fetch_module

    class _Patcher:
        def __enter__(self):
            self._original = fetch_module.fetch_norm_context

            def _patched(driver, db, deal_id):
                ctx = self._original(driver, db, deal_id)
                ple = ctx.get("provision_level_entities", {})
                by_type = ple.get("by_type", {})
                if by_type:
                    reordered = {}
                    # Priority types first
                    for t in priority_types:
                        if t in by_type:
                            reordered[t] = by_type[t]
                    # Then the rest
                    for t, v in by_type.items():
                        if t not in reordered:
                            reordered[t] = v
                    ple["by_type"] = reordered
                    ctx["provision_level_entities"] = ple
                return ctx

            fetch_module.fetch_norm_context = _patched
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            fetch_module.fetch_norm_context = self._original

    return _Patcher()


def with_reordered_norms(priority_substring: str):
    """Monkey-patches fetch_norm_context to push norms whose norm_id
    contains the substring to the front of context.norms. Tests whether
    norm position affects Stage 2 weighting."""
    from app.services import synthesis_v4_fetch as fetch_module

    class _Patcher:
        def __enter__(self):
            self._original = fetch_module.fetch_norm_context

            def _patched(driver, db, deal_id):
                ctx = self._original(driver, db, deal_id)
                norms = ctx.get("norms", [])
                if norms:
                    priority = [n for n in norms if priority_substring in n.get("norm_id", "")]
                    rest = [n for n in norms if priority_substring not in n.get("norm_id", "")]
                    ctx["norms"] = priority + rest
                return ctx

            fetch_module.fetch_norm_context = _patched
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            fetch_module.fetch_norm_context = self._original

    return _Patcher()


# ─────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                    errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", default="6e76ed06")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory; default: docs/v4_synthesis_diagnostic_runs/<timestamp>/")
    parser.add_argument("--phase", choices=["1", "2", "all"], default="all",
                        help="Run only Phase 1 baselines, only Phase 2 variations, or all")
    args = parser.parse_args()

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = (Path(args.out_dir) if args.out_dir
                else REPO_ROOT / "docs" / "v4_synthesis_diagnostic_runs" / timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output dir: %s", out_dir)

    runs = {}

    if args.phase in ("1", "all"):
        # Phase 1 — baselines
        logger.info("Phase 1 baseline: Q4")
        runs["q4_baseline"] = synthesize(Q4_TEXT, args.deal)
        logger.info("Phase 1 baseline: Q5")
        runs["q5_baseline"] = synthesize(Q5_TEXT, args.deal)

    if args.phase in ("2", "all"):
        # Phase 2 — controlled variations
        logger.info("Phase 2 V1: Q4 with category L synthesis_guidance emptied")
        with with_emptied_guidance("L"):
            runs["q4_v1_no_l_guidance"] = synthesize(Q4_TEXT, args.deal)

        logger.info("Phase 2 V2: Q5 with category N synthesis_guidance emptied")
        with with_emptied_guidance("N"):
            runs["q5_v2_no_n_guidance"] = synthesize(Q5_TEXT, args.deal)

        logger.info("Phase 2 V3: Q4 with sweep_tier+asset_sale_sweep prioritized in payload")
        with with_reordered_provision_entities(["sweep_tier", "asset_sale_sweep"]):
            runs["q4_v3_reordered_payload"] = synthesize(Q4_TEXT, args.deal)

        logger.info("Phase 2 V4: Q5 with rdp norms prioritized in payload")
        with with_reordered_norms("rdp_basket"):
            runs["q5_v4_reordered_norms"] = synthesize(Q5_TEXT, args.deal)

    # Save individual runs
    for probe_id, run_data in runs.items():
        (out_dir / f"{probe_id}.json").write_text(
            json.dumps(run_data, indent=2, default=str), encoding="utf-8"
        )

    # Summary
    summary_lines = [f"# Phase G commit 1 diagnostic — {timestamp}", ""]
    total_cost = 0.0
    for probe_id, run in runs.items():
        cost = run.get("total_cost_usd", 0)
        total_cost += cost
        cite_ids = run.get("stage2_citation_norm_ids", [])
        rdp_in_cites = any("rdp" in (c or "").lower() for c in cite_ids)
        summary_lines.append(f"## {probe_id}")
        summary_lines.append(f"- cost: ${cost:.4f}")
        summary_lines.append(f"- stage1: {run.get('stage1_primary_count')}P / "
                              f"{run.get('stage1_supplementary_count')}S / "
                              f"{run.get('stage1_skip_count')}K")
        summary_lines.append(f"- stage2 citations: {len(cite_ids)}")
        summary_lines.append(f"- stage2 cite norm_ids: {cite_ids}")
        summary_lines.append(f"- rdp basket in cites: {rdp_in_cites}")
        # Brief answer snippet
        ans = run.get("stage2_answer", "")
        summary_lines.append(f"- answer first 200 chars: {ans[:200]}")
        summary_lines.append("")

    summary_lines.append(f"**Total cost: ${total_cost:.4f}**")
    summary = "\n".join(summary_lines)
    (out_dir / "SUMMARY.md").write_text(summary, encoding="utf-8")
    print(summary)
    print()
    print(f"Wrote {len(runs)} runs to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
