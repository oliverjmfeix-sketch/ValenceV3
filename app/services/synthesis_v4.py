"""
Phase D Commit 2 — v4 synthesis service.

Adapts v3's `/ask-graph` two-stage filter+synthesize pipeline (in
app/routers/deals.py) to read v4 norms from `valence_v4` instead of
v3 provisions from `valence`. Reuses the topic_router (same schema in
both DBs) and the migrated synthesis_guidance (D1 commit). Calls
Anthropic in two stages:

  Stage 1 — entity filter:
    Classifies each fetched norm as PRIMARY / SUPPLEMENTARY / SKIP for
    the question. Returns structured JSON only (no prose synthesis at
    Stage 1; v3's pattern). Output includes per-norm verdict + ≤15-word
    rationale so D3's diagnostic can audit "did Stage 1 pick the right
    norms?" separately from "did Stage 2 reason correctly over them?"

  Stage 2 — synthesis:
    Generates the structured legal answer from the filtered context.
    Output is JSON: {reasoning, answer, citations}. Reasoning has
    issue/primary_norms_considered/analysis/interactions/conclusion
    structure (mirrors v3's category K guidance).

CLI:
    # Single question, ad-hoc
    python -m app.services.synthesis_v4 \\
        --deal 6e76ed06 --question "What is the builder basket?"

    # Batch on a gold-standard eval set
    python -m app.services.synthesis_v4 \\
        --deal 6e76ed06 --eval-set lawyer_dc_rp

Output JSON shape per question:
    {
      "question": "...",
      "deal_id": "...",
      "model": "claude-sonnet-4-6",
      "route_result": {covenant_type, matched_categories, is_specific},
      "fetched_norm_count": int,
      "fetched_defeater_count": int,
      "stage1": {
        "classifications": [{"norm_id", "verdict", "rationale"}, ...],
        "primary_count", "supplementary_count", "skip_count",
        "input_tokens", "output_tokens", "latency_ms", "cost_usd"
      },
      "stage2": {
        "reasoning": {issue, primary_norms_considered, analysis,
                      interactions, conclusion},
        "answer": "...",
        "citations": [{"norm_id", "section", "page", "quote"}, ...],
        "input_tokens", "output_tokens", "latency_ms", "cost_usd"
      },
      "total_latency_ms", "total_cost_usd"
    }

Cost (Sonnet 4.6 default, Duck Creek scale ~23 norms):
  Stage 1 ≈ $0.04 ($3/M input × ~5k + $15/M output × ~700)
  Stage 2 ≈ $0.05 ($3/M input × ~8k + $15/M output × ~1.5k)
  Per question ≈ $0.09. Full lawyer eval (6 q) ≈ $0.55.

Phase C constraint "no Claude SDK calls" lifted; pre-flight verified
in Phase D Commit 0 (smoke_test_anthropic.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=False)

import anthropic  # noqa: E402

from app.services.synthesis_v4_fetch import (  # noqa: E402
    connect_typedb,
    fetch_norm_context,
)
from app.services.topic_router import (  # noqa: E402
    TopicRouter,
    TopicRouteResult,
    CategoryMetadata,
)
from app.services.typedb_client import typedb_client  # noqa: E402

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Pricing (USD per 1M tokens) — keep in sync with Anthropic console
# ═══════════════════════════════════════════════════════════════════════════════

_PRICING_PER_1M = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING_PER_1M.get(model)
    if not pricing:
        return 0.0
    return (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1 — entity filter
# ═══════════════════════════════════════════════════════════════════════════════

_STAGE1_SYSTEM_TEMPLATE = """You are a legal data classifier. Given a question about a credit agreement and a list of extracted v4 norms (with their structured deontic information — modality, scope edges, conditions, defeats, contributes_to relations — plus the v3 entity each was extracted from), classify each norm into exactly one of three buckets:

PRIMARY — Core norms needed to directly answer the question. The norms whose attributes/conditions/scope a lawyer would cite verbatim. For a capacity-aggregation question (where the user asks for a total, sum, or aggregate of capacity across multiple norms), PRIMARY includes every basket-permission norm with action_scope: 'reallocable' OR with cap_usd / cap_grower_pct set, regardless of which covenant the norm nominally serves. The action_scope='reallocable' schema marker means the basket's unused capacity flows to other action types — for example, an RDP basket's unused capacity reallocates to dividends under cross-reference clauses — so RDP and investment baskets contribute to aggregate dividend capacity even when the question is phrased about dividends specifically. DO NOT skip a reallocable basket on the basis that its norm_kind contains 'rdp' or 'investment' or 'debt'; the schema's action_scope marker is authoritative for inclusion in capacity aggregation. For a single-norm-applicability question (where the user asks whether a specific action is permitted under a specific test or covenant), PRIMARY is the directly-applicable norm only — do NOT bring in unrelated reallocable baskets. For a ratio test question, PRIMARY is the ratio_rp_basket_permission norm. For a defeater/exception question, PRIMARY includes the defeater AND the norm it defeats.

SUPPLEMENTARY — Norms that might add qualifying detail, edge cases, sub-source breakdowns, or context. Include builder sub-source norms (cni, ecf, ebitda_fc, starter, etc.) when the question is about builder-basket capacity — they feed into the parent norm via norm_contributes_to_capacity. Include norms that share covenant context even if not the direct answer.

SKIP — Norms clearly irrelevant to this specific question. Only choose SKIP when you are certain the norm has no bearing.

Bias toward inclusion: when in doubt between PRIMARY and SUPPLEMENTARY, pick PRIMARY. When in doubt between SUPPLEMENTARY and SKIP, pick SUPPLEMENTARY.
{picker_guidance_block}
## OUTPUT FORMAT — STRICT

Return ONLY a JSON object with this shape, no surrounding prose:

{{"classifications": [
  {{"norm_id": "<exact norm_id from input>", "verdict": "PRIMARY|SUPPLEMENTARY|SKIP", "rationale": "<≤15 words>"}},
  ...
]}}

Constraints:
- One entry per norm in the input. No additions, no omissions.
- `verdict` is exactly one of PRIMARY, SUPPLEMENTARY, SKIP.
- `rationale` is ≤15 words; the most decisive reason for the verdict. NOT a synthesis of the answer — just the classification reason.
- Do NOT pre-answer the question. Do NOT explain how the data combines. That happens at Stage 2.
"""


def _format_stage1_system(picker_guidance: str) -> str:
    """Inject category-specific picker guidance into the Stage 1 system prompt.

    When picker_guidance is empty, the {picker_guidance_block} placeholder
    collapses to a blank line — the prompt is byte-equivalent to the pre-D2
    version. When non-empty, the matched category's picker guidance lands
    between the inclusion-bias paragraph and the OUTPUT FORMAT section.
    """
    if picker_guidance:
        block = (
            "\n## CATEGORY-SPECIFIC PICKER GUIDANCE\n\n"
            f"{picker_guidance}\n"
        )
    else:
        block = ""
    return _STAGE1_SYSTEM_TEMPLATE.format(picker_guidance_block=block)


def _build_stage1_norm_summary(norm: dict) -> dict:
    """Compact per-norm view for the Stage 1 classifier.
    Strips noise (provenance, full source_text) while preserving classification
    signal (norm_kind, modality, scope edges, contributes_to relations, the
    v3 entity type and its capacity_category if any).
    """
    s = norm.get("scalars", {})
    summary: dict[str, Any] = {
        "norm_id": norm["norm_id"],
        "norm_kind": s.get("norm_kind"),
        "modality": s.get("modality"),
        "action_scope": s.get("action_scope"),
        "capacity_composition": s.get("capacity_composition"),
    }
    # Truncate source_text to first 200 chars for brevity
    if s.get("source_text"):
        summary["source_text_excerpt"] = s["source_text"][:200] + (
            "…" if len(s["source_text"]) > 200 else ""
        )
    if s.get("source_section"):
        summary["section"] = s["source_section"]
    if s.get("cap_usd") is not None:
        summary["cap_usd"] = s["cap_usd"]
    if s.get("cap_grower_pct") is not None:
        summary["cap_grower_pct"] = s["cap_grower_pct"]
    # Scope summary
    scope_targets = {
        "subject": [], "action": [], "object": [], "instrument": [],
    }
    for edge in norm.get("scope_edges", []):
        if edge["relation"] == "norm_binds_subject":
            scope_targets["subject"].append(edge["target_type"])
        elif edge["relation"] == "norm_scopes_action":
            scope_targets["action"].append(edge["target_type"])
        elif edge["relation"] == "norm_scopes_object":
            scope_targets["object"].append(edge["target_type"])
        elif edge["relation"] == "norm_scopes_instrument":
            scope_targets["instrument"].append(edge["target_type"])
    summary["scope"] = {k: v for k, v in scope_targets.items() if v}
    if norm.get("contributes_to"):
        summary["contributes_to"] = [
            {"target": e["target_norm_id"], "agg": e["aggregation_function"]}
            for e in norm["contributes_to"]
        ]
    if norm.get("defeated_by"):
        summary["defeated_by"] = norm["defeated_by"]
    if "condition_tree" in norm:
        summary["has_condition_tree"] = True
    # v3 source surface
    ef = norm.get("extracted_from")
    if ef:
        summary["extracted_from"] = {
            "type": ef["v3_entity_type"],
            "capacity_category": ef["v3_attrs"].get("capacity_category"),
        }
    return summary


def _build_stage1_defeater_summary(d: dict) -> dict:
    s = d.get("scalars", {})
    return {
        "defeater_id": d["defeater_id"],
        "defeater_type": s.get("defeater_type"),
        "defeater_name": s.get("defeater_name"),
        "section": s.get("source_section"),
        "source_text_excerpt": (
            (s.get("source_text") or "")[:200]
            + ("…" if len(s.get("source_text") or "") > 200 else "")
        ),
    }


@dataclass
class Stage1Result:
    classifications: list[dict] = field(default_factory=list)
    primary_norm_ids: set[str] = field(default_factory=set)
    supplementary_norm_ids: set[str] = field(default_factory=set)
    skip_norm_ids: set[str] = field(default_factory=set)
    raw_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0


def run_stage1(client: anthropic.Anthropic, model: str, question: str,
                context: dict, picker_guidance: str = "") -> Stage1Result:
    """Stage 1 — strict-JSON classification of every norm + defeater.

    `picker_guidance` is category-specific PRIMARY/SUPPLEMENTARY bias
    instructions sourced from the matched ontology_category's
    `stage1_picker_guidance` attribute (via TopicRouter). When empty,
    the system prompt is byte-equivalent to the pre-D2 baseline.
    """
    norm_summaries = [_build_stage1_norm_summary(n) for n in context["norms"]]
    defeater_summaries = [
        _build_stage1_defeater_summary(d) for d in context["defeaters"]
    ]
    payload = {
        "question": question,
        "norms": norm_summaries,
        "defeaters": defeater_summaries,
    }
    user_message = (
        f"Classify every norm and defeater below for this question.\n\n"
        f"Note: defeaters override specific norms — classify a defeater as "
        f"PRIMARY if the question asks about exceptions to the norm it overrides, "
        f"otherwise SUPPLEMENTARY/SKIP.\n\n"
        f"## INPUT\n\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )

    system_prompt = _format_stage1_system(picker_guidance)
    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = (time.perf_counter() - start) * 1000

    raw = response.content[0].text.strip()
    parsed = _extract_json_object(raw)

    result = Stage1Result(
        raw_response=raw,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
    )
    result.cost_usd = _estimate_cost(model, result.input_tokens, result.output_tokens)

    if not parsed or "classifications" not in parsed:
        logger.warning("Stage 1 did not return valid classifications JSON; "
                       "falling back to PRIMARY for all norms")
        result.classifications = [
            {
                "norm_id": n["norm_id"],
                "verdict": "PRIMARY",
                "rationale": "(fallback: Stage 1 parse failed)",
            }
            for n in context["norms"]
        ]
    else:
        result.classifications = parsed["classifications"]

    for entry in result.classifications:
        nid = entry.get("norm_id") or entry.get("defeater_id") or ""
        verdict = (entry.get("verdict") or "").upper()
        if verdict == "PRIMARY":
            result.primary_norm_ids.add(nid)
        elif verdict == "SUPPLEMENTARY":
            result.supplementary_norm_ids.add(nid)
        elif verdict == "SKIP":
            result.skip_norm_ids.add(nid)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2 — synthesis
# ═══════════════════════════════════════════════════════════════════════════════

_STAGE2_SYSTEM_TEMPLATE = """You are a legal analyst answering questions about a credit agreement's restricted payments covenant. The data below is a structured view of a v4 graph: each NORM has explicit modality, capacity attributes, scope edges (subject/action/object/instrument), conditions tree, and the v3 source entity it was extracted from. Your job is to produce a structured JSON answer.

## DATA FORMAT

Each norm has:
- `norm_id`: unique identifier (cite this for traceability)
- `scalars`: norm_kind, modality, cap_usd, cap_grower_pct, action_scope, source_text, source_section, source_page, etc.
- `scope_edges`: subject/action/object/instrument bindings
- `condition_tree` (optional): explicit deontic conditions with predicates
- `contributes_to` (optional): edges to parent norms with child_index + aggregation_function
- `defeated_by` (optional): defeater_ids that override this norm
- `extracted_from` (optional): the v3 entity (rp_basket, builder_basket, etc.) the norm was projected from, with full v3 attributes — use these for capacity_category, v3-flag attributes, etc.

Defeaters are exceptions/carve-outs. Each defeater overrides specific norms via `defeats` edges (encoded in the norm's `defeated_by` list).

Proceeds-flow edges (event_provides_proceeds_to_norm) connect agreement-level events (asset_sale_event) to norms that receive proceeds.

`provision_level_entities` is a top-level block containing entities attached to the rp_provision (sweep_tier, asset_sale_sweep, investment_pathway, unsub_designation, etc.) with their full v3 attributes. These entities live one hop further out from norms — they are properties of the provision rather than a single norm's v3 source. Surface them alongside norm citations when relevant. Shape: `{{"by_type": {{"sweep_tier": [{{"entity_iid": "...", "attrs": {{...}}}}, ...], ...}}}}`. Notable entity types and the question classes that consume them:
- `sweep_tier`: leverage threshold + sweep percentage tiers for asset-sale sweeps (Q4-class). Iterate the list, sort by `leverage_threshold` desc, cite each tier's `section_reference`.
- `asset_sale_sweep`: per-deal sweep mechanics including de minimis carveouts. Look for `individual_de_minimis_usd`, `individual_de_minimis_pct`, `annual_de_minimis_usd`, `annual_de_minimis_pct` to enumerate the sweep-exemption thresholds.
- `investment_pathway`: distinct pathways for investments to other entities (Q3-class reallocation context).
- `unsub_designation`: requirements for unrestricted-subsidiary designation (Q2-class context).

## STRICT RULES

1. CITATION REQUIRED — every factual claim must reference a norm_id, plus its section_reference and source_page when available.
2. ONLY USE PROVIDED DATA — if a fact isn't in the input, say "Not found in extracted data". Do not draw on general credit-agreement knowledge.
3. QUALIFICATIONS REQUIRED — if a defeater/condition/exception applies, mention it.
4. OBJECTIVE — report what the document states. No "borrower-friendly" / "aggressive" / etc.
5. VERIFY — every claim must trace to a norm attribute or condition predicate. Booleans-true are findings, not possibilities — don't hedge with "may" or "potentially" when the data is clear.
6. CONDITIONS — when a norm has a condition_tree, mention the conditions inline (e.g., "subject to first lien net leverage ≤ 6.25x").

## MUST-CITE LIST — STAGE 1 PRIMARY AUTHORITY

The payload includes a `must_cite_norm_ids` array, derived from Stage 1's PRIMARY classification. Every norm_id in that array MUST appear BOTH in `reasoning.primary_norms_considered` AND in the `citations` array.

If a must-cite norm does not contribute to your reasoning, still cite it with `quote: "(noted; not load-bearing for this question)"` and a short rationale entry in `primary_norms_considered`. Do not silently drop it. This makes Stage 1's selection auditable and enforces the architecture's authority hierarchy across questions where LLM attention defaults would otherwise override the picker.

## CATEGORY-SPECIFIC ANALYSIS GUIDANCE (from human-authored synthesis_guidance)

{category_guidance}

## OUTPUT FORMAT — STRICT JSON

Return ONLY a JSON object with this shape, no prose outside the JSON:

{{
  "reasoning": {{
    "issue": "<1-2 sentences: what the question asks>",
    "primary_norms_considered": ["<norm_id_1>", "<norm_id_2>", ...],
    "analysis": "<step-by-step reasoning over the primary norms; ≤300 words>",
    "interactions": "<how norms interact via contributes_to / defeated_by / proceeds_flows; ≤150 words; empty string if no interactions>",
    "conclusion": "<the load-bearing finding in 1-2 sentences>"
  }},
  "answer": "<lawyer-facing answer, lead-with-direct-answer; cite [Section X, p.N] inline; ≤4 sentences unless listing 4+ items>",
  "citations": [
    {{"norm_id": "<id>", "section": "<6.06(j)>", "page": <int>, "quote": "<verbatim source_text excerpt ≤30 words>"}},
    ...
  ]
}}

Citations must be a non-empty array unless the answer is "Not found in extracted data". Use the actual section / page from each norm's scalars.
"""


@dataclass
class Stage2Result:
    reasoning: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    must_cite_norm_ids: list[str] = field(default_factory=list)
    raw_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0


def _build_stage2_norm_full(norm: dict) -> dict:
    """Full per-norm payload for Stage 2 — no truncation. Includes the
    extracted_from v3 entity payload (essential for capacity_category +
    citations) and the condition_tree if present.
    """
    s = norm.get("scalars", {})
    out: dict[str, Any] = {
        "norm_id": norm["norm_id"],
        "scalars": {
            k: v for k, v in s.items()
            if k != "source_text" or len(s.get("source_text") or "") <= 1500
        },
    }
    # Source text gets its own field if long
    if s.get("source_text") and len(s["source_text"]) > 1500:
        out["source_text"] = s["source_text"]
    out["scope"] = norm.get("scope_edges", [])
    if norm.get("contributes_to"):
        out["contributes_to"] = norm["contributes_to"]
    if norm.get("defeated_by"):
        out["defeated_by"] = norm["defeated_by"]
    if "condition_tree" in norm:
        out["condition_tree"] = norm["condition_tree"]
    if norm.get("extracted_from"):
        out["extracted_from"] = norm["extracted_from"]
    return out


def run_stage2(client: anthropic.Anthropic, model: str, question: str,
                context: dict, stage1: Stage1Result,
                category_guidance: str) -> Stage2Result:
    """Stage 2 — structured-JSON synthesis given Stage 1's PRIMARY +
    SUPPLEMENTARY filtered context."""
    primary = [
        _build_stage2_norm_full(n) for n in context["norms"]
        if n["norm_id"] in stage1.primary_norm_ids
    ]
    supplementary = [
        _build_stage2_norm_full(n) for n in context["norms"]
        if n["norm_id"] in stage1.supplementary_norm_ids
    ]
    primary_defeaters = [
        d for d in context["defeaters"]
        if d["defeater_id"] in stage1.primary_norm_ids
        or d["defeater_id"] in stage1.supplementary_norm_ids
    ]

    # Must-cite layer (I.1): every norm Stage 1 classified as PRIMARY must
    # appear in Stage 2's reasoning.primary_norms_considered AND citations.
    # Initial scope: all primary_norm_ids. Sorted for determinism.
    must_cite_norm_ids = sorted(
        nid for nid in stage1.primary_norm_ids
        if any(n["norm_id"] == nid for n in context["norms"])
    )

    payload = {
        "question": question,
        "must_cite_norm_ids": must_cite_norm_ids,
        "primary_norms": primary,
        "supplementary_norms": supplementary,
        "defeaters": primary_defeaters,
        "proceeds_flows": context.get("proceeds_flows", []),
        "provision_level_entities": context.get("provision_level_entities", {}),
    }
    user_message = (
        f"## QUESTION\n\n{question}\n\n"
        f"## CONTEXT (Stage 1 filtered)\n\n```json\n"
        f"{json.dumps(payload, indent=2, default=str)}\n```"
    )

    system_prompt = _STAGE2_SYSTEM_TEMPLATE.format(
        category_guidance=(category_guidance or "(no category-specific guidance for this question)"),
    )

    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=4000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    latency_ms = (time.perf_counter() - start) * 1000

    raw = response.content[0].text.strip()
    parsed = _extract_json_object(raw)

    result = Stage2Result(
        raw_response=raw,
        must_cite_norm_ids=must_cite_norm_ids,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
    )
    result.cost_usd = _estimate_cost(model, result.input_tokens, result.output_tokens)

    if not parsed:
        logger.warning("Stage 2 did not return valid JSON; using raw text as answer")
        result.answer = raw
    else:
        result.reasoning = parsed.get("reasoning", {})
        result.answer = parsed.get("answer", "")
        result.citations = parsed.get("citations", [])
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# JSON extraction helper (handles fenced blocks)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json_object(text: str) -> dict | None:
    """Extract the first balanced JSON object from text. Strips markdown
    fences and pre/post prose. Uses json.JSONDecoder.raw_decode to parse
    only up to the end of the first complete object — robust to trailing
    prose or stray closing braces in commentary that follows the JSON."""
    if not text:
        return None
    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    start = text.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _end_pos = decoder.raw_decode(text[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed: %s", str(exc)[:120])
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level: synthesize_one_question
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SynthesisResult:
    question: str
    deal_id: str
    model: str
    route_result: dict[str, Any] = field(default_factory=dict)
    fetched_norm_count: int = 0
    fetched_defeater_count: int = 0
    stage1: Stage1Result = field(default_factory=Stage1Result)
    stage2: Stage2Result = field(default_factory=Stage2Result)
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "deal_id": self.deal_id,
            "model": self.model,
            "route_result": self.route_result,
            "fetched_norm_count": self.fetched_norm_count,
            "fetched_defeater_count": self.fetched_defeater_count,
            "stage1": {
                "classifications": self.stage1.classifications,
                "primary_count": len(self.stage1.primary_norm_ids),
                "primary_norm_ids": sorted(self.stage1.primary_norm_ids),
                "supplementary_count": len(self.stage1.supplementary_norm_ids),
                "skip_count": len(self.stage1.skip_norm_ids),
                "input_tokens": self.stage1.input_tokens,
                "output_tokens": self.stage1.output_tokens,
                "latency_ms": round(self.stage1.latency_ms, 1),
                "cost_usd": round(self.stage1.cost_usd, 5),
                "raw_response": self.stage1.raw_response,
            },
            "stage2": {
                "reasoning": self.stage2.reasoning,
                "answer": self.stage2.answer,
                "citations": self.stage2.citations,
                "must_cite_norm_ids": self.stage2.must_cite_norm_ids,
                "input_tokens": self.stage2.input_tokens,
                "output_tokens": self.stage2.output_tokens,
                "latency_ms": round(self.stage2.latency_ms, 1),
                "cost_usd": round(self.stage2.cost_usd, 5),
                "raw_response": self.stage2.raw_response,
            },
            "total_latency_ms": round(self.total_latency_ms, 1),
            "total_cost_usd": round(self.total_cost_usd, 5),
        }


def synthesize_one_question(question: str, deal_id: str, db: str,
                              model: str = "claude-sonnet-4-6"
                              ) -> SynthesisResult:
    """End-to-end: route → fetch → Stage 1 → Stage 2 for a single question."""
    # Initialize TypeDB client + topic router
    typedb_client.connect()
    router = TopicRouter(typedb_client)
    route = router.route(question)

    # Build category_guidance (matched first; fallback to all-for-covenant)
    guidance = router.get_synthesis_guidance(route.matched_categories)
    if not guidance:
        all_cats = router.get_all_categories()
        rp_cats = [c for c in all_cats.values() if c.covenant_type.upper() == "RP"]
        guidance = router.get_synthesis_guidance(rp_cats)

    # Stage 1 picker guidance — only from matched categories (no covenant-wide
    # fallback; picker bias must be category-specific to be safe).
    picker_guidance = router.get_stage1_picker_guidance(route.matched_categories)

    # Phase I.3 — collect category keywords for the relevance-scoring
    # sort. Empty when no categories matched (defensive); the fetch
    # falls back to Phase G's tier-1 markers in that case.
    category_keywords: set[str] = set()
    for cat in route.matched_categories:
        category_keywords |= getattr(cat, "keywords", set())

    # Fetch norm context
    fetch_driver = connect_typedb()
    try:
        context = fetch_norm_context(
            fetch_driver, db, deal_id,
            question=question,
            category_keywords=category_keywords if category_keywords else None,
        )
    finally:
        fetch_driver.close()

    client = anthropic.Anthropic()
    overall_start = time.perf_counter()

    s1 = run_stage1(client, model, question, context, picker_guidance)
    s2 = run_stage2(client, model, question, context, s1, guidance)

    total_latency = (time.perf_counter() - overall_start) * 1000

    result = SynthesisResult(
        question=question,
        deal_id=deal_id,
        model=model,
        route_result={
            "covenant_type": route.covenant_type,
            "matched_categories": [
                {"id": c.category_id, "name": c.name}
                for c in route.matched_categories
            ],
            "is_specific": route.is_specific,
            "question_ids": route.question_ids,
        },
        fetched_norm_count=len(context["norms"]),
        fetched_defeater_count=len(context["defeaters"]),
        stage1=s1,
        stage2=s2,
        total_latency_ms=total_latency,
        total_cost_usd=s1.cost_usd + s2.cost_usd,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _load_eval_set(eval_set: str) -> dict:
    path = REPO_ROOT / "app" / "data" / "gold_standard" / f"{eval_set}.json"
    if not path.exists():
        raise FileNotFoundError(f"Eval set not found: {path}")
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deal", required=True)
    parser.add_argument("--db", default="valence_v4")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        help="Anthropic model id (claude-sonnet-4-6 default; "
                             "claude-opus-4-7 for higher quality)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--question", help="Single question (ad-hoc)")
    src.add_argument("--eval-set",
                     help="Gold standard set name, e.g. lawyer_dc_rp")
    parser.add_argument("--output",
                        help="Output JSON path (default: stdout). For "
                             "--eval-set, defaults to "
                             "docs/v4_phase_d_lawyer_qa/<eval_set>_<timestamp>.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set in environment")
        return 2

    if args.question:
        result = synthesize_one_question(args.question, args.deal, args.db,
                                         args.model)
        out = result.to_json_dict()
        text = json.dumps(out, indent=2, default=str)
        if args.output:
            Path(args.output).write_text(text)
            logger.info("Wrote single-question result to %s", args.output)
        else:
            print(text)
        return 0

    # Eval-set batch run
    eval_data = _load_eval_set(args.eval_set)
    questions = eval_data.get("questions", [])
    logger.info("Running %d questions from eval set '%s' (model=%s)",
                len(questions), args.eval_set, args.model)

    results: list[dict] = []
    total_cost = 0.0
    total_latency = 0.0
    for i, q in enumerate(questions, 1):
        qid = q.get("question_id", f"q{i}")
        question_text = q["question"]
        logger.info("[%d/%d] %s — %s", i, len(questions), qid,
                    question_text[:80])
        try:
            res = synthesize_one_question(question_text, args.deal, args.db,
                                          args.model)
            r = res.to_json_dict()
            r["question_id"] = qid
            r["gold_answer"] = q.get("gold_answer", "")
            r["category"] = q.get("category", "")
            r["requires_entities"] = q.get("requires_entities", [])
            results.append(r)
            total_cost += r["total_cost_usd"]
            total_latency += r["total_latency_ms"]
            logger.info("  ✓ stage1: %d primary / %d supp / %d skip — "
                        "stage2: %d cites — $%.4f, %.1fs",
                        r["stage1"]["primary_count"],
                        r["stage1"]["supplementary_count"],
                        r["stage1"]["skip_count"],
                        len(r["stage2"]["citations"]),
                        r["total_cost_usd"], r["total_latency_ms"] / 1000)
        except Exception as exc:
            logger.error("  ✗ FAILED: %s", exc)
            results.append({
                "question_id": qid,
                "question": question_text,
                "error": str(exc),
            })

    logger.info("=" * 60)
    logger.info("Eval set total cost: $%.4f, total latency: %.1fs",
                total_cost, total_latency / 1000)

    if not args.output:
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out_dir = REPO_ROOT / "docs" / "v4_phase_d_lawyer_qa"
        out_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(out_dir / f"{args.eval_set}_{timestamp}.json")

    Path(args.output).write_text(json.dumps({
        "eval_set": args.eval_set,
        "deal_id": args.deal,
        "db": args.db,
        "model": args.model,
        "total_cost_usd": round(total_cost, 5),
        "total_latency_ms": round(total_latency, 1),
        "questions": results,
    }, indent=2, default=str))
    logger.info("Wrote eval results to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
