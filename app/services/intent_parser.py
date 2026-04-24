"""
Valence v4 — intent parser

Natural-language question -> structured operations-layer call.

Takes a lawyer's question about a Duck Creek credit agreement, asks Claude
to parse it into one of three intent classifications, and (Commit 2+)
dispatches the result through the operations layer.

Intent classifications:
  - operation_call: question maps to a specific §6 operation
  - clarification_needed: two or more operations could answer, materially
    different output
  - out_of_scope: not about the agreement / not RP pilot / not a Valence
    capability

Uniform response shape across all branches is documented in the
parse_intent / answer_question docstrings.

Claude SDK: default claude-sonnet-4-6 (intent classification is simpler
than classification measurement; Sonnet is plenty capable, substantially
cheaper). Overridable via settings.intent_parser_model or --model flag.
Temperature 0 for determinism. System prompt uses cache_control so the
(large, static) operation catalog + rules + examples get re-used across
calls.

CLI:
  py -3.12 -m app.services.intent_parser parse
    --deal <id> --question "<text>"

  py -3.12 -m app.services.intent_parser answer   # wired in Commit 2
    --deal <id> --question "<text>" [--world-state PATH]

Rule 8.1: evaluated operations (evaluate_feasibility, evaluate_capacity)
require the consumer to supply world state via --world-state. The parser
surfaces the missing-input state as a structured operation_response
error rather than inventing values.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Load .env before settings import so ANTHROPIC_API_KEY picks up.
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(_REPO_ROOT / ".env", override=False)

from anthropic import Anthropic  # noqa: E402

from app.config import settings  # noqa: E402


logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("intent_parser")


# Default to Sonnet for intent parsing. Intent classification is much
# simpler than the classification measurement harness's per-field parses;
# Sonnet handles this well at ~5x lower cost than Opus.
DEFAULT_MODEL = "claude-sonnet-4-6"


# ─── Static operation catalog ──────────────────────────────────────────────────
# Rewritten for parser consumption; mirrors docs/v4_deontic_architecture.md
# §6 but tuned for Claude's disambiguation decisions. When the operations
# layer changes shape, update here.

OPERATION_CATALOG = """\
## describe_norm

Purpose: Return the complete structural description of a single named
norm (basket, definition, exemption, or blocker).
Parameters: norm_id (required, string — e.g., "dc_rp_6_06_o_ratio_rp_basket")
Use when: the lawyer asks about a SPECIFIC named norm — "tell me about
6.06(o)", "what are the conditions on the ratio basket", "describe the
general RP basket".
Don't use when: the lawyer hasn't identified a specific norm — use
filter_norms or trace_pathways instead.

## get_attribute

Purpose: Return a single attribute value (e.g., cap_usd, cap_grower_pct,
source_section) from a named entity.
Parameters: entity_id (required), attribute_name (required)
Use when: the lawyer wants ONE specific fact about an identified norm —
"what's the cap on 6.06(j)", "what section is the ratio basket in",
"what's the grower percentage for the general RP basket".
Don't use when: the lawyer wants a description, structure, or anything
beyond one attribute. Use describe_norm instead.

## enumerate_linked

Purpose: List entities linked to a named entity via a specific relation
(contributions, scopes, defeaters, etc.).
Parameters: entity_id (required), relation_type (required — one of
norm_contributes_to_capacity, norm_scopes_action, norm_scopes_object,
defeats, norm_has_condition, condition_has_child, ...), role_played
(required — which role the entity_id plays in the relation).
Use when: the lawyer wants to know what's directly linked — "what
components feed the Cumulative Amount", "what defeaters apply to 6.06(p)",
"what actions does the ratio basket scope to".
Don't use when: the lawyer wants structural context or an indirect walk.
Use trace_pathways.

## trace_pathways

Purpose: Starting from an anchor (an action class or a state predicate),
return all norms that reach it, with their contributor chains,
conditions, and defeaters. Polymorphic: works for both action-centric
questions ("what baskets permit dividends") and state-centric questions
("what norms test leverage at 5.75x").
Parameters:
  - anchor_type (required — "action_class" or "state_predicate")
  - anchor_value (required — action_class_label like
    "make_dividend_payment", OR state_predicate_id like
    "first_lien_net_leverage_at_or_below|5.75|at_or_below|None")
  - include_annotations (optional, default false — when true, attach
    source_text/source_section excerpts)
  - collapse_contributors (optional, default true — when true, collapse
    capacity sub-sources whose parent is also in the result)
Use when: the lawyer asks a structural question across the graph — "what
baskets allow dividends", "which norms use the 5.75x ratio test", "what
are the paths to paying subordinated debt".
Don't use when: the lawyer has identified a specific norm (describe_norm)
or wants an evaluated answer given a financial state (evaluate_*).

## filter_norms

Purpose: Declarative filter returning norms matching a set of criteria
(modality, action_scope, capacity_composition, section prefix, etc.).
Parameters: criteria (required — a dict with any combination of:
modality, action_scope, capacity_composition, source_section_prefix,
norm_kind_prefix, scopes_action, scopes_object, has_condition,
serves_question).
Use when: the lawyer wants a filtered catalog — "list all prohibitions",
"show me the additive-capacity permissions", "what's in 6.09(a)".
Don't use when: the question is about a single norm (describe_norm), an
anchor's pathways (trace_pathways), or requires evaluation.

## evaluate_feasibility

Purpose: Given a named norm AND supplied world state, evaluate whether
the norm is applicable (condition holds + no defeaters fire).
Parameters: norm_id (required), supplied_world_state (required — a dict
with predicate_values + proposed_action per Rule 8.1)
Use when: the lawyer asks an applicability question with specific
financial context — "can the ratio basket be used if leverage is 6.0x
but no-worse pro forma", "does the unsub blocker fire for this
designation".
Don't use when: the question is purely structural (use describe_norm or
trace_pathways) or no world state is available.

## evaluate_capacity

Purpose: Given a named norm AND supplied world state, compute the dollar
capacity the norm provides.
Parameters: norm_id (required), supplied_world_state (required)
Use when: the lawyer asks for a dollar amount — "how much dividend
capacity is there under the general RP basket given EBITDA of $150m",
"what's the total Cumulative Amount given current inputs".
Don't use when: the question is structural ("describe the Cumulative
Amount" without asking for a resolved dollar figure).
"""


# ─── Classification rules + sub-categories ─────────────────────────────────────

CLASSIFICATION_RULES = """\
Classify the question as exactly one of:

**operation_call** — the question maps to one of the 7 operations with
reasonable confidence. Pick the operation, fill its parameters.

**clarification_needed** — two or more operations could plausibly answer
the question and would produce MATERIALLY DIFFERENT output (structural
enumeration vs evaluated dollar figure, or ambiguity about which norm is
being asked about). Provide 2-3 distinct possible interpretations, each
with the operation it would trigger.

**out_of_scope** — question cannot be answered by the operations layer.
Three sub-categories:
  - `not_about_agreement`: question is not about the credit agreement at
    all (weather, general knowledge, personal advice, etc.)
  - `non_rp_covenant`: question is about a covenant outside Restricted
    Payments — MFN, debt incurrence, negative pledge, asset sales as
    primary topic (vs asset-sale proceeds as a capacity source for RP),
    liens, etc.
  - `not_valence_capability`: question requests something Valence
    structurally does not do. Sub-signals:
      * Recommendation ("should the borrower", "would it be wise",
        "is this a good idea") — Valence describes permissibility,
        not advisability.
      * Prediction about third-party behavior ("will the agent consent",
        "will lenders waive") — Valence interprets the agreement, not
        counterparty behavior.
      * Legal conclusion ("is this legal", "would this be binding") —
        Valence provides structural analysis to support legal
        interpretation; it doesn't itself conclude legality.
      * Real-time financial state ("what's the current leverage",
        "what's today's capacity") asked WITHOUT supplying values —
        Valence never stores world state (Rule 8.1); the consumer must
        supply inputs. IMPORTANT: if the question SUPPLIES specific
        numbers (e.g., "given leverage of 4.5x", "assuming EBITDA of
        $150M", "a $50M dividend"), those ARE the supplied world state —
        route to operation_call with evaluate_feasibility or
        evaluate_capacity, even if the wording says "current" or "now."
        The "current" cue triggers out_of_scope ONLY when no numbers
        are provided.

For out_of_scope classifications, produce a short `out_of_scope_reason`
and set `out_of_scope_category` to one of the sub-categories above.

For "legal conclusion" borderlines ("is this legally permissible"),
prefer operation_call with evaluate_feasibility IF the lawyer has
clearly described the proposed transaction AND supplied-or-implied
world state. When they haven't, prefer clarification_needed.
"""


# ─── Example parses ────────────────────────────────────────────────────────────
# Deliberately NOT drawn from the 18 gold questions. These train the parser
# on classification boundaries, not on memorized answers.

EXAMPLES = """\
## Example parses

Example 1 (structural, specific norm):
Q: "What are the conditions on 6.06(o)?"
-> {
  "intent_classification": "operation_call",
  "parsed_as": "describe the 6.06(o) ratio RP basket, including its condition tree",
  "operation": "describe_norm",
  "parameters": {"norm_id": "dc_rp_6_06_o_ratio_rp_basket"},
  "intent_confidence": 0.9
}

Example 2 (structural, anchor-centric):
Q: "What norms use the 5.75x leverage threshold?"
-> {
  "intent_classification": "operation_call",
  "parsed_as": "trace norms that reference the first_lien_net_leverage predicate at 5.75x",
  "operation": "trace_pathways",
  "parameters": {
    "anchor_type": "state_predicate",
    "anchor_value": "first_lien_net_leverage_at_or_below|5.75|at_or_below|None"
  },
  "intent_confidence": 0.85
}

Example 3 (evaluated):
Q: "At 6.0x leverage no-worse pro forma, is the ratio RP basket available?"
-> {
  "intent_classification": "operation_call",
  "parsed_as": "evaluate 6.06(o) ratio basket feasibility given 6.0x leverage + no-worse flag",
  "operation": "evaluate_feasibility",
  "parameters": {
    "norm_id": "dc_rp_6_06_o_ratio_rp_basket",
    "supplied_world_state_keys_needed": ["first_lien_net_leverage_ratio", "is_pro_forma_no_worse"]
  },
  "intent_confidence": 0.9
}

Example 4 (ambiguous):
Q: "Tell me about investment capacity."
-> {
  "intent_classification": "clarification_needed",
  "parsed_as": "question could be asking for the investment-covenant structure OR a dollar capacity figure",
  "clarification_request": "Do you want the structure of investment norms, or a resolved dollar capacity given specific EBITDA / leverage inputs?",
  "possible_interpretations": [
    {
      "interpretation": "structural enumeration",
      "implied_operation": "trace_pathways",
      "implied_parameters": {"anchor_type": "action_class", "anchor_value": "make_investment"}
    },
    {
      "interpretation": "resolved dollar capacity",
      "implied_operation": "evaluate_capacity",
      "implied_parameters": {"norm_id": "dc_rp_6_03_y_general_investment_basket", "supplied_world_state": "<needed>"}
    }
  ],
  "intent_confidence": 0.5
}

Example 5 (out of scope — non-RP):
Q: "What's the MFN threshold for yield protection?"
-> {
  "intent_classification": "out_of_scope",
  "out_of_scope_category": "non_rp_covenant",
  "out_of_scope_reason": "MFN is outside the current pilot scope (Restricted Payments only). Valence will support MFN in future releases.",
  "parsed_as": "MFN yield protection question; not answerable in RP pilot",
  "intent_confidence": 0.95
}

Example 6 (out of scope — recommendation):
Q: "Should the Borrower pay a dividend now?"
-> {
  "intent_classification": "out_of_scope",
  "out_of_scope_category": "not_valence_capability",
  "out_of_scope_reason": "Valence describes what the agreement permits, not whether an action should be taken. For permissibility under a specific financial state, ask 'can the Borrower pay a dividend given <state>'.",
  "parsed_as": "recommendation request; Valence is descriptive, not prescriptive",
  "intent_confidence": 0.95
}
"""


# ─── System prompt assembly ────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""\
You are an intent parser for Valence, a covenant analysis platform. Your
job is to interpret a lawyer's question about a Duck Creek credit
agreement and determine which structured operation should answer it.

# Pilot scope

Valence's pilot covers RESTRICTED PAYMENTS (RP) only. Questions about
other covenants (MFN, debt incurrence, liens, asset sales as primary
topic, etc.) are out of scope.

# Operations catalog

{OPERATION_CATALOG}

# Classification rules

{CLASSIFICATION_RULES}

# Examples

{EXAMPLES}

# Output format

Return ONLY a JSON object. No prose outside the JSON. The JSON must
include all of:
  - intent_classification: "operation_call" | "clarification_needed" | "out_of_scope"
  - parsed_as: prose (1-2 sentences) explaining how you interpreted the question
  - intent_confidence: float 0.0-1.0

And, depending on classification:
  - operation_call: operation (string), parameters (dict)
  - clarification_needed: clarification_request (string), possible_interpretations (array)
  - out_of_scope: out_of_scope_category (string), out_of_scope_reason (string)

If the question names a specific norm, use the GT norm_id form
(dc_rp_6_06_j_general_rp_basket, dc_rp_cumulative_amount, etc.). If
unsure of the exact norm_id, return clarification_needed.
"""


# ─── Claude client + helpers ───────────────────────────────────────────────────

_claude_client: Anthropic | None = None


def _get_claude_client() -> Anthropic:
    global _claude_client
    if _claude_client is None:
        api_key = settings.anthropic_api_key
        if not api_key:
            raise RuntimeError(
                "settings.anthropic_api_key not set — intent parser requires "
                "ANTHROPIC_API_KEY in .env"
            )
        _claude_client = Anthropic(api_key=api_key)
    return _claude_client


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict | None:
    """Find the first JSON object in Claude's response text."""
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _validate_intent(intent: dict) -> tuple[bool, str]:
    """Check that the parsed intent has the required shape for its branch.

    Returns (is_valid, error_message). error_message empty if valid.
    """
    if not isinstance(intent, dict):
        return False, "intent is not a dict"

    cls = intent.get("intent_classification")
    if cls not in ("operation_call", "clarification_needed", "out_of_scope"):
        return False, f"invalid intent_classification: {cls!r}"
    if not isinstance(intent.get("parsed_as"), str) or not intent["parsed_as"]:
        return False, "missing parsed_as"

    if cls == "operation_call":
        if not isinstance(intent.get("operation"), str):
            return False, "operation_call missing `operation` field"
        if not isinstance(intent.get("parameters"), dict):
            return False, "operation_call missing `parameters` dict"
    elif cls == "clarification_needed":
        if not isinstance(intent.get("clarification_request"), str):
            return False, "clarification_needed missing `clarification_request`"
        if not isinstance(intent.get("possible_interpretations"), list):
            return False, "clarification_needed missing `possible_interpretations` list"
    elif cls == "out_of_scope":
        if not isinstance(intent.get("out_of_scope_category"), str):
            return False, "out_of_scope missing `out_of_scope_category`"
        if not isinstance(intent.get("out_of_scope_reason"), str):
            return False, "out_of_scope missing `out_of_scope_reason`"
    return True, ""


# ─── Public API ────────────────────────────────────────────────────────────────


def parse_intent(question: str, model: str | None = None,
                 max_retries: int = 2) -> dict:
    """Send a question to Claude and return a structured intent dict.

    Returns a dict with at least:
      - question: echo of the input
      - intent_classification: "operation_call" / "clarification_needed" / "out_of_scope"
      - parsed_as: prose (1-2 sentences)
      - intent_confidence: float 0.0-1.0
      - (branch-specific fields per the response shape)

    On transient errors (rate, timeout), retries up to max_retries with a
    small back-off between attempts. On a parse failure (Claude returned
    non-JSON or invalid-shape JSON), returns an error envelope rather
    than raising — the parser is best-effort infrastructure, not a
    pipeline failure point.
    """
    client = _get_claude_client()
    model_name = model or DEFAULT_MODEL

    # System prompt marked cacheable — the large static catalog+rules+examples
    # get reused across every parse. Per Anthropic SDK, cache_control on
    # system blocks caches for 5 min (ephemeral).
    system_blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    user_message = f"Question:\n{question}\n\nProduce the intent JSON now."

    last_error: Exception | None = None
    raw_text = ""
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=1500,
                temperature=0,
                system=system_blocks,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_text = response.content[0].text if response.content else ""
            parsed = _extract_json(raw_text)
            if parsed is None:
                logger.warning("intent parse did not return JSON; raw=%r", raw_text[:200])
                return {
                    "question": question,
                    "intent_classification": "parser_error",
                    "parsed_as": "Claude response was not valid JSON.",
                    "intent_confidence": 0.0,
                    "parser_error": "JSON parse failure",
                    "raw_claude_text": raw_text[:500],
                }
            ok, err = _validate_intent(parsed)
            if not ok:
                logger.warning("intent parse invalid shape: %s", err)
                return {
                    "question": question,
                    "intent_classification": "parser_error",
                    "parsed_as": "Claude JSON was valid but didn't match the required shape.",
                    "intent_confidence": 0.0,
                    "parser_error": err,
                    "raw_claude_text": raw_text[:500],
                    "raw_claude_parsed": parsed,
                }
            parsed["question"] = question
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            msg = str(exc).lower()
            transient = any(k in msg for k in ("rate", "timeout", "429", "503", "502"))
            if attempt + 1 >= max_retries or not transient:
                break

    return {
        "question": question,
        "intent_classification": "parser_error",
        "parsed_as": "SDK invocation failed.",
        "intent_confidence": 0.0,
        "parser_error": str(last_error) if last_error else "unknown",
        "raw_claude_text": raw_text[:500] if raw_text else "",
    }


# ─── CLI ───────────────────────────────────────────────────────────────────────


def _print(resp: dict, compact: bool) -> None:
    if compact:
        print(json.dumps(resp, default=str))
    else:
        print(json.dumps(resp, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valence v4 intent parser CLI"
    )
    sub = parser.add_subparsers(dest="op", required=True)

    # parse: Commit 1 scope — intent classification only, no execution
    p_parse = sub.add_parser("parse", help="Parse a question; return the intent dict without executing the operation.")
    p_parse.add_argument("--deal", required=True)
    p_parse.add_argument("--question", required=True)
    p_parse.add_argument("--model", default=DEFAULT_MODEL)
    p_parse.add_argument("--compact", action="store_true")

    args = parser.parse_args()
    compact = bool(getattr(args, "compact", False))

    if args.op == "parse":
        resp = parse_intent(args.question, model=args.model)
        # deal_id is bookkeeping at this layer — echo for CLI UX
        resp["deal_id"] = args.deal
        _print(resp, compact)
        return 0

    parser.error(f"unknown op: {args.op}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
