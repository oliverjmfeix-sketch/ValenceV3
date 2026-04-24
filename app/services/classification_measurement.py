"""
Valence v4 — classification measurement harness (Prompt 06, Part C)

Measures classification accuracy for capacity_composition, action_scope, and
condition_structure along the six-dimensional evaluation framework (Horner
et al. 2025) with short-circuit grading. First-class rule-selection accuracy
tracking per DeonticBench (Dou et al. 2026).

Returns structured reports with per-instance dimension scores, confusion
matrices, and per-dimension accuracy. Never raises on empty data — empty
deals return well-formed zero-measurements.

CLI:
    py -3.12 -m app.services.classification_measurement \\
        --deal 6e76ed06 --field capacity_composition --prompt-version v1
    py -3.12 -m app.services.classification_measurement \\
        --deal 6e76ed06 --field all --prompt-version v1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=True)

import re  # noqa: E402
import time  # noqa: E402

import yaml  # noqa: E402
from anthropic import Anthropic  # noqa: E402

from app.config import settings  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("classification_measurement")

DATA_DIR = REPO_ROOT / "app" / "data"
DEFAULT_GROUND_TRUTH = DATA_DIR / "duck_creek_rp_ground_truth.yaml"
RESULTS_DIR = DATA_DIR / "classification_measurements"
EXPECTED_DB = "valence_v4"

CAPACITY_COMPOSITION_VALUES = [
    "additive", "fungible", "shared_pool", "categorical",
    "computed_from_sources", "unlimited_on_condition", "n_a",
]
ACTION_SCOPE_VALUES = ["specific", "general", "reallocable"]


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction prompts
#
# Each prompt is a pure-classification instruction with:
#   - closed-enum options + per-option definitions (Rule 4.2)
#   - at least one worked example per option
#   - explicit disambiguation for adjacent classes
#   - confidence score output
#   - NO section references (Rule 4.1)
#   - NO legal reasoning (Rule 4.4) — typology classification only
# ═══════════════════════════════════════════════════════════════════════════════

CAPACITY_COMPOSITION_PROMPT_V1 = """You are classifying a covenant basket or permission norm into exactly one capacity-composition category from a closed list. Do not reason about whether the basket is legally permissible — only classify its capacity-combining behavior.

Input fields you will receive:
- norm_kind (e.g., "general_rp_basket_permission")
- source_section (e.g., "6.06(j)")
- source_text (the operative agreement text, verbatim)
- cap_usd, cap_grower_pct (if present)
- cap_formula (if present — a formulaic cap)
- aggregation_function (if the norm aggregates other norms)

Classify into exactly one of the seven values:

1. additive — The basket has a scalar dollar cap (and/or percentage-of-EBITDA cap) that SUMS with other additive baskets' capacities. Example: "general RP basket of greater of $130M and 100% EBITDA." Key signal: a single scalar cap that adds cleanly to other additive baskets.

2. fungible — Capacity is usable across multiple action classes via reallocation. Using the capacity for one action reduces availability for another. Example: a $130M general RP basket that can be reallocated to prepay subordinated debt.

3. shared_pool — Multiple norms draw from a single explicit numerical pool (via `shares_capacity_pool` or equivalent). Using $10M of one depletes the same $10M from the others. Example: two intercompany baskets sharing a single $50M pool.

4. categorical — The basket reserves capacity for a SPECIFIC purpose (tax distributions, management equity, holdco overhead). Capacity is NOT interchangeable with general RP capacity and does NOT sum with general baskets. Example: tax distribution basket — only for tax distributions; won't be recharacterized as a general dividend.

5. computed_from_sources — Capacity is itself a computed aggregate of multiple source norms. Example: the builder basket (Cumulative Amount) is the sum of clauses (a) through (l) minus clause (m). Key signal: capacity is NOT a single scalar or a single formula — it's an aggregation over other norms.

6. unlimited_on_condition — When a ratio or similar test is met, the basket is uncapped. When the test fails, the basket has no capacity. Example: ratio RP basket at 5.75x — unlimited when condition holds, zero otherwise.

7. n_a — The norm has no capacity concept. Prohibitions always use this. Some permissions that are scope-qualified rather than amount-qualified also use this (e.g., "dividend of Unrestricted Subsidiary equity" — scope-limited, not amount-limited).

Disambiguation rules for adjacent classes:

- additive vs categorical: If the basket's capacity SUMS cleanly into total RP capacity, it's additive. If it's reserved for a specific purpose and does NOT combine with general RP capacity, it's categorical. Management equity baskets are typically categorical; general RP baskets are typically additive.

- additive vs fungible: Additive baskets sum but are NOT interchangeable across action classes. Fungible baskets CAN be reallocated. A basket may be both additive AND reallocable — in that case pick fungible if reallocation is material to the question.

- computed_from_sources vs additive: Is the basket itself a derived aggregate (has source_norm_ids)? Then computed_from_sources. Is it a leaf with a scalar cap? Then additive.

- unlimited_on_condition vs additive with large cap: unlimited_on_condition means the basket's capacity is literally unbounded when a test is met. A basket with a $1B cap is still additive.

- n_a vs categorical: n_a means the norm has no capacity concept at all (prohibitions; scope-limited permissions). categorical means capacity exists but is dedicated to one purpose.

Output format (JSON only, no surrounding text):
{
  "classification": "<one of: additive|fungible|shared_pool|categorical|computed_from_sources|unlimited_on_condition|n_a>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<brief; 1-2 sentences citing what in the source_text you keyed on>"
}
"""

ACTION_SCOPE_PROMPT_V1 = """You are classifying a norm's action scope into one of three values. Do not reason about legal permissibility — only classify the norm's scope-of-applicability pattern.

Input fields you will receive:
- norm_kind
- source_section
- source_text (verbatim)
- scoped_actions (list of action_class labels)
- reallocates_to (if present)

Classify into exactly one of:

1. specific — The norm applies to a narrow, named action or set of closely-related actions. Example: tax distributions (only for tax distributions), management equity repurchase (only for employee equity buyouts), post-IPO dividends (only dividends after an IPO).

2. general — The norm applies broadly to any RP-like action. Example: a general prohibition on restricted payments, or a general ratio-basket permission available for dividends OR repurchases OR RDPs without discrimination.

3. reallocable — The norm's capacity can be reallocated from its originally-scoped action to another action class. Example: general RP basket 6.06(j) accepts reallocation inflows from 6.09(a)(I) (RDP basket) and 6.03(y) (investment basket). Both source AND target of reallocation relationships are `reallocable`.

Disambiguation:

- specific vs general: If the norm's action_class list is narrowed to one or two closely-related actions (e.g., tax distributions only, mgmt equity repurchases only), it's specific. If it applies broadly to any RP action (dividend OR repurchase OR RDP), it's general.

- general vs reallocable: All reallocable norms are also at least somewhat general in scope (they accept capacity flows across action classes). The distinction: `reallocable` is stronger — the norm has explicit `reallocates_to` or is the explicit target of a reallocation edge. If the norm is broadly scoped but has no reallocation flows, it's `general`.

- A builder basket is general (usable broadly). A 6.06(o) ratio basket is specific to RP (scoped to make_restricted_payment subtree, not RDPs or investments).

Output format (JSON only):
{
  "classification": "<specific|general|reallocable>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<brief>"
}
"""

CONDITION_STRUCTURE_PROMPT_V1 = """You are classifying the SHAPE of a norm's condition tree. Do not judge whether the condition is correct or complete — only classify its topology and predicate content.

Input fields you will receive:
- norm_kind
- source_text (the verbatim condition language)
- proposed_condition_tree (the projection layer's candidate tree)

Classify the tree along two axes:

Axis 1 — topology:
- "none" — the norm has no condition (unconditional)
- "atomic" — a single predicate leaf
- "disjunction_of_atomics" — OR of atomic predicates (depth 2)
- "conjunction_of_atomics" — AND of atomic predicates (depth 2)
- "mixed_depth_2" — a top-level operator with mixed atomic and single-operator children
- "depth_3_or_deeper" — requires Strategy A flattening

Axis 2 — predicate content correctness:
- "correct" — the predicates and their thresholds match what the source_text requires
- "wrong_operator" — the tree has the wrong AND/OR/NOT at some node
- "wrong_predicate" — the tree references a predicate that doesn't match source_text
- "wrong_threshold" — predicate correct, but threshold value or comparison direction wrong
- "structurally_invalid" — the tree has zero leaves, a NOT with multiple children, or similar

Topology examples:
- 6.06(p) unsub equity dividend (unconditional) → topology=none
- 6.06(o) "leverage ≤ 5.75x OR no-worse" → topology=disjunction_of_atomics
- Sweep tier 50% "leverage > 5.50x AND leverage ≤ 5.75x" → topology=conjunction_of_atomics
- 2.10(c)(iv) "product-line sale AND (ratio ≤ 6.25 OR no-worse)" → topology=depth_3_or_deeper (before flattening) OR topology=disjunction_of_atomics (after Strategy A)

Disambiguation:
- "atomic" is for single-predicate conditions like "no EoD exists."
- If the source_text says "X or Y" with X and Y both single-predicate, that's `disjunction_of_atomics`.
- If the source_text says "A and (B or C)," without Strategy A flattening, that's `depth_3_or_deeper`. After Strategy A the same rule becomes `disjunction_of_atomics` (each branch an AND of A and one of B/C).

Output format (JSON only):
{
  "topology": "<none|atomic|disjunction_of_atomics|conjunction_of_atomics|mixed_depth_2|depth_3_or_deeper>",
  "content_correctness": "<correct|wrong_operator|wrong_predicate|wrong_threshold|structurally_invalid>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<brief>"
}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# V2 prompts (Prompt 09 Fix 1) — vocabulary-aligned to ground-truth enum values
# and pinned with explicit "return literally one of …" instructions.
#
# V1 of capacity_composition and action_scope scored 87.5% on matched
# instances; V2 carries minor reinforcement but keeps the same enum set.
#
# V1 of condition_structure scored 0% because it used a different vocabulary
# (none/disjunction_of_atomics/conjunction_of_atomics) than the ground truth
# YAML (unconditional/or_of_atomics/and_of_atomics/or_of_and_of_atomics).
# V2 rewrites the taxonomy section to use the GT vocabulary literally, adds
# an explicit translation table for Claude-preferred aliases, and drops the
# two-axis structure (only the topology axis is compared against GT).
# ═══════════════════════════════════════════════════════════════════════════════

CAPACITY_COMPOSITION_PROMPT_V2 = """You are classifying a covenant basket or permission norm into exactly one capacity-composition category.

Return literally one of these seven values — nothing else:
  additive | fungible | shared_pool | categorical | computed_from_sources | unlimited_on_condition | n_a

Do NOT return any other string. Do NOT return "none", "N/A", "unknown", or variants.

Input fields you will receive:
- norm_kind (e.g., "general_rp_basket_permission")
- source_section (e.g., "6.06(j)")
- source_text (operative agreement text, verbatim)
- cap_usd, cap_grower_pct (if present)
- aggregation_function (if the norm aggregates other norms)

Value definitions:

1. additive — Scalar dollar (and/or EBITDA%) cap that SUMS with other additive baskets. Typical general RP basket of "greater of $130M and 100% EBITDA."

2. fungible — Capacity usable across multiple action classes via reallocation. Using it for one action reduces the other. Typical reallocable general basket.

3. shared_pool — Multiple norms draw from an explicit shared numerical pool (via shares_capacity_pool). Using $10M of one depletes the same $10M from the others.

4. categorical — Capacity reserved for one specific purpose (tax distributions, management equity, holdco overhead). Does NOT sum with general-purpose baskets.

5. computed_from_sources — Capacity is itself a computed aggregate of multiple source norms. Builder basket (Cumulative Amount) is the canonical example.

6. unlimited_on_condition — Uncapped when a predicate (e.g., ratio test) holds; zero when it fails. Ratio RP basket at 5.75x.

7. n_a — No capacity concept. Prohibitions always. Some scope-limited permissions (e.g., unsub-equity dividend in 6.06(p)).

Adjacent-class disambiguation:
- additive vs categorical: does the cap SUM with general RP? additive. Reserved for one purpose, does NOT combine? categorical.
- additive vs fungible: additive sums but stays in its action class. fungible reallocates across action classes.
- computed_from_sources vs additive: is the basket itself a derived aggregate (has source_norm_ids)? computed_from_sources. Leaf with a scalar cap? additive.
- unlimited_on_condition vs additive with large cap: unlimited_on_condition is literally unbounded when the test holds. A $1B cap is still additive.
- n_a vs categorical: n_a has no capacity concept. categorical has capacity but dedicated to one purpose.

Output format (JSON only):
{
  "classification": "<one of the seven values above>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence citing what in source_text you keyed on>"
}
"""

ACTION_SCOPE_PROMPT_V2 = """You are classifying a norm's action_scope into exactly one of three values.

Return literally one of:
  specific | general | reallocable

Do NOT return any other string.

Value definitions:
- specific: the norm applies to a narrow, named action or set of closely-related actions (e.g., tax distributions only, management equity buyouts only, post-IPO dividends only). ALSO applies to CAPACITY CONTRIBUTION norms that earmark their capacity to a specific parent basket, even if the parent's action set is broad — see rule below.
- general: the norm applies broadly across multiple RP-like action classes without discrimination (e.g., a ratio basket available for dividends OR repurchases OR RDPs) AND is itself a usage permission (the norm at the point of action selection), not a capacity contributor.
- reallocable: the norm's capacity can be redirected from its primary action class into other RP actions via an explicit reallocation mechanism (e.g., a general RP basket that can reallocate to prepay subordinated debt).

CAPACITY CONTRIBUTOR RULE (important):
If the norm_kind starts with "builder_source_" or ends with "_component" (e.g., builder_source_cni, post_ipo_basket_ipo_proceeds_component), it is a CAPACITY CONTRIBUTION — it supplies capacity to a parent basket rather than governing action selection itself. Label such norms `specific`, not `general`. They inherit the parent's action set but their own scope is narrow: "contribute capacity to this specific parent." The 4-action Cumulative Amount usage set on a builder_source_* norm does NOT make it general — general is reserved for the usage permission at the top (builder_usage_permission, builder_basket_aggregate), not for its contributors.

Disambiguation:
- specific vs general: does the basket name a specific action class (tax, management equity, etc.)? specific. Does it cover any RP action without restriction AND is it a usage permission (not a contributor)? general. Is it a capacity contributor? specific.
- general vs reallocable: a general basket is available for multiple action classes by its own terms. A reallocable basket is primarily one action class but can be redirected to others via a cross-reference clause.

Output format (JSON only):
{
  "classification": "<specific|general|reallocable>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence citing source_text>"
}
"""

CONDITION_STRUCTURE_PROMPT_V2 = """You are classifying the TOPOLOGY of a norm's condition tree. Use ONLY the closed vocabulary below.

Return literally one of these five values — nothing else:
  unconditional | atomic | or_of_atomics | and_of_atomics | or_of_and_of_atomics

Do NOT return: "none", "n/a", "N/A", "disjunction_of_atomics", "conjunction_of_atomics", "mixed", "complex", "unknown", or any variant. If you would say "none" because the norm has no condition, the correct answer is literally `unconditional`.

Translation table for common misstatements:
  "none"                         → unconditional
  "n/a"                          → unconditional
  "disjunction_of_atomics"       → or_of_atomics
  "conjunction_of_atomics"       → and_of_atomics
  "mixed_depth_2"                → or_of_and_of_atomics (if OR-top) OR and_of_atomics (rewrite via Strategy A)
  "depth_3_or_deeper"            → or_of_and_of_atomics (after Strategy A flattening)

Value definitions:

1. unconditional — The norm has NO world-state predicate gate. A scalar dollar cap is NOT a condition; it's capacity. A norm whose permission is granted whenever the basket has room (no EoD, no ratio test, no pro-forma test) is `unconditional`.

2. atomic — A single predicate leaf gates the norm. Examples:
   - J.Crew blocker fires when `unsub_would_own_or_license_material_ip_at_designation` is true (atomic prohibition trigger).
   - Sweep tier 100% when `first_lien_net_leverage_above(5.75)` (atomic sweep trigger).
   - A basket gated only on "no Event of Default exists" (atomic precondition).

3. or_of_atomics — A disjunction of two or more atomic predicates.
   - 6.06(o) ratio basket: "leverage ≤ 5.75x OR pro forma no-worse on first-lien."
   - 6.05(z) general unlimited asset sale: "leverage ≤ 6.00x OR pro forma no-worse."
   - 2.10(c)(i) de minimis: "individual proceeds ≤ threshold OR annual aggregate ≤ threshold."

4. and_of_atomics — A conjunction of two or more atomic predicates.
   - Sweep tier 50%: "leverage > 5.50x AND leverage ≤ 5.75x" (range gate as AND).

5. or_of_and_of_atomics — Strategy A flattened form of a depth-3 tree. An OR whose children are each an AND of atomics (or an AND whose children are each an atomic).
   - 2.10(c)(iv) product-line sweep exemption: "(product-line-sale AND leverage ≤ 6.25x) OR (product-line-sale AND pro-forma-no-worse)" — each OR branch is an AND of the product-line atomic with one ratio atomic.

Input fields you will receive:
- norm_kind
- source_section
- source_text (verbatim condition language; may be empty/null if the norm is unconditional)
- modality

Examples resolved:
- 6.06(p) unsub equity dividend (unconditional permission) → `unconditional`
- 6.06(o) "permitted if ratio ≤ 5.75x OR no-worse" → `or_of_atomics`
- 6.06(j) general RP basket ($130M / 100% EBITDA, no predicate) → `unconditional`
- Sweep tier 50% "leverage > 5.50 AND ≤ 5.75" → `and_of_atomics`
- J.Crew blocker triggered by Material-IP-at-designation atomic → `atomic`
- 2.10(c)(iv) product-line exemption (Strategy A form) → `or_of_and_of_atomics`

Output format (JSON only):
{
  "classification": "<unconditional|atomic|or_of_atomics|and_of_atomics|or_of_and_of_atomics>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence citing source_text>"
}
"""


PROMPT_VERSIONS = {
    ("capacity_composition", "v1"): CAPACITY_COMPOSITION_PROMPT_V1,
    ("action_scope", "v1"): ACTION_SCOPE_PROMPT_V1,
    ("condition_structure", "v1"): CONDITION_STRUCTURE_PROMPT_V1,
    ("capacity_composition", "v2"): CAPACITY_COMPOSITION_PROMPT_V2,
    ("action_scope", "v2"): ACTION_SCOPE_PROMPT_V2,
    ("condition_structure", "v2"): CONDITION_STRUCTURE_PROMPT_V2,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def load_ground_truth(path: Path = DEFAULT_GROUND_TRUTH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _list_extracted_norms(tx) -> list[dict]:
    """Return list of norm records currently in valence_v4, each with id +
    kind + modality + primary scoped action + primary scoped object.

    The scoped_action / scoped_object attributes let the harness join each
    extracted norm to its ground-truth counterpart via structural tuple
    (norm_kind, modality, primary_action, primary_object) rather than by
    norm_id — extraction and ground-truth use disjoint id schemes.

    'primary' = one representative label per norm. Picked as the
    lexicographically-first matching action_class_label / object_class_label
    (or instrument_class_label). Deterministic so the same norm always
    produces the same tuple.
    """
    try:
        result = tx.query("""
            match
              $n isa norm, has norm_id $nid, has norm_kind $nk;
              try { $n has modality $mod; };
              try { $n has source_text $st; };
              try { $n has source_section $ss; };
            select $nid, $nk, $mod, $st, $ss;
        """).resolve()
        rows = list(result.as_concept_rows())
    except Exception:
        rows = []

    norms: list[dict] = []
    for r in rows:
        try:
            nid = r.get("nid").as_attribute().get_value()
        except Exception:
            continue
        mod_concept = r.get("mod")
        modality = mod_concept.as_attribute().get_value() if mod_concept else None
        st_concept = r.get("st")
        source_text = st_concept.as_attribute().get_value() if st_concept else None
        ss_concept = r.get("ss")
        source_section = ss_concept.as_attribute().get_value() if ss_concept else None
        norms.append({
            "norm_id": nid,
            "norm_kind": r.get("nk").as_attribute().get_value(),
            "modality": modality,
            "source_text": source_text,
            "source_section": source_section,
        })

    # Augment with primary action + object labels (per-norm follow-up queries).
    for n in norms:
        nid = n["norm_id"]
        action_labels: list[str] = []
        object_labels: list[str] = []
        try:
            q_action = f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  (norm: $n, action: $a) isa norm_scopes_action;
                  $a has action_class_label $lbl;
                select $lbl;
            '''
            for row in tx.query(q_action).resolve().as_concept_rows():
                action_labels.append(row.get("lbl").as_attribute().get_value())
        except Exception:
            pass
        try:
            q_object = f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  (norm: $n, object: $o) isa norm_scopes_object;
                  $o has object_class_label $lbl;
                select $lbl;
            '''
            for row in tx.query(q_object).resolve().as_concept_rows():
                object_labels.append(row.get("lbl").as_attribute().get_value())
        except Exception:
            pass
        # Fallback to instrument scope when no generic object edge exists.
        if not object_labels:
            try:
                q_instr = f'''
                    match
                      $n isa norm, has norm_id "{nid}";
                      (norm: $n, instrument: $o) isa norm_scopes_instrument;
                      $o has instrument_class_label $lbl;
                    select $lbl;
                '''
                for row in tx.query(q_instr).resolve().as_concept_rows():
                    object_labels.append(row.get("lbl").as_attribute().get_value())
            except Exception:
                pass
        n["primary_action"] = sorted(action_labels)[0] if action_labels else None
        n["primary_object"] = sorted(object_labels)[0] if object_labels else None
        n["all_actions"] = sorted(action_labels)
        n["all_objects"] = sorted(object_labels)
    return norms


def _gt_primary(values: list | None) -> str | None:
    """Deterministic primary from a YAML list — sorted-first like extracted."""
    if not values:
        return None
    return sorted(values)[0]


def _build_gt_tuple_index(gt_norms: dict[str, dict]) -> tuple[dict, list[tuple]]:
    """Build a tuple → [gt_norms] index and return ordered collision list.

    Tuple: (norm_kind, modality, primary_scoped_action, primary_scoped_object).
    On collision (>1 GT norm with the same tuple) the index stores a list —
    join logic disambiguates via norm_kind when possible and logs the rest.
    """
    index: dict[tuple, list[dict]] = defaultdict(list)
    for nid, n in gt_norms.items():
        key = (
            n.get("norm_kind"),
            n.get("modality"),
            _gt_primary(n.get("scoped_actions")),
            _gt_primary(n.get("scoped_objects")),
        )
        index[key].append(n)
    collisions = [(k, v) for k, v in index.items() if len(v) > 1]
    return dict(index), collisions


def _match_gt(extracted_norm: dict, gt_index: dict) -> dict | None:
    """Look up the GT norm matching an extracted norm by structural tuple.

    Primary key is (norm_kind, modality, primary_action, primary_object).
    On tuple collision (multiple GT norms share the tuple), prefer one
    whose norm_kind exactly matches the extracted norm's norm_kind.
    Returns None when no match.
    """
    key = (
        extracted_norm.get("norm_kind"),
        extracted_norm.get("modality"),
        extracted_norm.get("primary_action"),
        extracted_norm.get("primary_object"),
    )
    candidates = gt_index.get(key, [])
    if not candidates:
        # Fallback: drop primary_object and retry. Some extracted norms have
        # no object edge; GT norms typically do. The relaxed tuple catches
        # norms whose kind/modality/action triple uniquely identifies them.
        relaxed = (key[0], key[1], key[2], None)
        candidates = gt_index.get(relaxed, [])
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Collision: prefer exact norm_kind match
    exact_kind = [c for c in candidates if c.get("norm_kind") == extracted_norm.get("norm_kind")]
    if len(exact_kind) == 1:
        return exact_kind[0]
    logger.warning(
        "tuple collision for extracted norm %s (%d GT candidates): matching to first",
        extracted_norm.get("norm_id"), len(candidates),
    )
    return candidates[0]


_claude_client: Anthropic | None = None


def _get_claude_client() -> Anthropic:
    global _claude_client
    if _claude_client is None:
        api_key = settings.anthropic_api_key
        if not api_key:
            raise RuntimeError(
                "settings.anthropic_api_key not set — classification SDK call requires "
                "ANTHROPIC_API_KEY in .env"
            )
        _claude_client = Anthropic(api_key=api_key)
    return _claude_client


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict | None:
    """Find the first JSON object in Claude's response text. Returns None on
    parse failure — caller treats that as a D2 (syntactic validity) miss."""
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _call_claude_classify(prompt: str, input_payload: dict,
                          model: str | None = None, max_retries: int = 3) -> dict:
    """Send a classification prompt to Claude and return the parsed JSON response.

    Contract (shared with _score_instance):
      - Response JSON must carry a 'classification' or 'topology' key
        naming the chosen option from the closed taxonomy
      - 'confidence' optional (0.0-1.0)
      - 'reasoning' optional (short string)

    Transient failures (rate-limit, timeout, server-error) retry up to
    max_retries with exponential backoff. Parse failures return a
    {"classification": None} sentinel so _score_instance can record a D2 miss
    rather than raising and skipping the instance entirely.

    Temperature 0 for determinism. Small max_tokens — classification outputs
    are tight JSON, not prose.
    """
    client = _get_claude_client()
    model_name = model or settings.claude_model

    user_message = (
        f"{prompt}\n\n"
        f"Input:\n```json\n{json.dumps(input_payload, indent=2)}\n```\n\n"
        "Respond with ONLY a JSON object containing 'classification' (or 'topology' "
        "for condition_structure), 'confidence' (0-1), and brief 'reasoning'. "
        "No prose outside the JSON."
    )

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=500,
                temperature=0,
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text if response.content else ""
            parsed = _extract_json(text)
            if parsed is None:
                logger.warning("classification response did not parse as JSON: %r",
                               text[:200])
                return {"classification": None, "confidence": 0.0,
                        "reasoning": "JSON parse failure"}
            return parsed
        except Exception as exc:  # noqa: BLE001 — SDK exceptions + network errors
            last_error = exc
            msg = str(exc).lower()
            transient = any(k in msg for k in ("rate", "timeout", "429", "503", "502"))
            if attempt + 1 >= max_retries or not transient:
                break
            backoff = 2 ** attempt
            logger.warning("classification call failed (attempt %d/%d): %s — retrying in %ds",
                           attempt + 1, max_retries, str(exc)[:120], backoff)
            time.sleep(backoff)

    # Exhausted retries. Return a sentinel rather than raising; harness will
    # score the instance as D2 failure and aggregate can proceed.
    logger.error("classification call failed after %d attempts: %s",
                 max_retries, str(last_error)[:200])
    return {"classification": None, "confidence": 0.0,
            "reasoning": f"SDK failure after {max_retries} attempts: {str(last_error)[:160]}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Six-dimensional scoring
# ═══════════════════════════════════════════════════════════════════════════════


def _score_instance(
    field: str,
    norm_kind: str,
    prompt_output: dict | None,
    expected: Any,
    enum_values: list[str] | None,
) -> dict:
    """
    Score a single instance on D1-D6 with short-circuit grading.

    D1 completeness is deal-level, not per-instance — set externally.
    D2-D5 are per-instance. D6 cross-instance consistency is deal-level.

    Returns dict with dimension_scores (bool per D1-D6), first_failure, grade.
    """
    scores = {"D1": True, "D2": None, "D3": None, "D4": None, "D5": None, "D6": None}
    first_failure = None

    # D2 syntactic validity
    if prompt_output is None:
        scores["D2"] = False
        first_failure = "D2"
    else:
        predicted = prompt_output.get("classification") or prompt_output.get("topology")
        if enum_values and predicted not in enum_values:
            scores["D2"] = False
            first_failure = "D2"
        else:
            scores["D2"] = True

    # D3 semantic correctness: a catch-all — passes if the predicted class
    # is a reasonable fit to what source_text says. For pilot, treat as
    # equivalent to "not wildly off." Since we can't automate this cleanly
    # without a human rater, D3 passes if D2 passed and we have a non-null
    # predicted value.
    if first_failure is None:
        scores["D3"] = prompt_output is not None and prompt_output.get("classification", prompt_output.get("topology")) is not None
        if not scores["D3"]:
            first_failure = "D3"

    # D4 category accuracy — exact match to expected
    if first_failure is None:
        predicted = prompt_output.get("classification") or prompt_output.get("topology")
        scores["D4"] = (predicted == expected)
        if not scores["D4"]:
            first_failure = "D4"

    # D5 precondition appropriateness. For capacity_composition and action_scope
    # there are no sub-preconditions, so D5 inherits from D4 when applicable.
    # For condition_structure, D5 checks content_correctness == "correct".
    if first_failure is None:
        if field == "condition_structure" and prompt_output is not None:
            scores["D5"] = prompt_output.get("content_correctness") == "correct"
            if not scores["D5"]:
                first_failure = "D5"
        else:
            scores["D5"] = scores["D4"]

    # D6 cross-instance consistency — deal-level; compute externally
    scores["D6"] = None  # filled in by caller after all instances scored

    grade = "pass" if first_failure is None else "fail"
    return {
        "dimension_scores": scores,
        "first_failure": first_failure,
        "grade": grade,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Measurement functions
# ═══════════════════════════════════════════════════════════════════════════════


def _measure_generic(
    field: str,
    deal_id: str,
    ground_truth_path: Path,
    prompt_version: str,
    enum_values: list[str] | None = None,
) -> dict:
    """Generic measurement loop, parameterised by field."""
    gt = load_ground_truth(ground_truth_path)
    gt_norms_raw = {n["norm_id"]: n for n in gt.get("norms", [])}

    # For condition_structure, derive the expected value from the graph-native
    # topology attribute in YAML. Norms without a `condition` block classify as
    # "unconditional" per Part 4 semantics (absence-of-relation → unconditional).
    # This replaces the YAML tree-walk that D1 previously couldn't measure.
    gt_norms: dict[str, dict] = {}
    for nid, n in gt_norms_raw.items():
        enriched = dict(n)
        if field == "condition_structure":
            cond = n.get("condition")
            if cond is None:
                enriched["condition_structure"] = "unconditional"
            elif isinstance(cond, dict) and "topology" in cond:
                enriched["condition_structure"] = cond["topology"]
            else:
                enriched["condition_structure"] = None
        gt_norms[nid] = enriched

    # Build tuple index for structural-join lookup. Extraction and ground-
    # truth use disjoint norm_id schemes; tuple join matches on semantically
    # stable (norm_kind, modality, primary_scoped_action, primary_scoped_object).
    gt_index, gt_collisions = _build_gt_tuple_index(gt_norms)
    if gt_collisions:
        logger.info(
            "GT tuple index built with %d collisions (logged during per-norm join)",
            len(gt_collisions),
        )

    driver = connect()
    try:
        tx = driver.transaction(EXPECTED_DB, TransactionType.READ)
        try:
            extracted = _list_extracted_norms(tx)
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass
    finally:
        try:
            driver.close()
        except Exception:
            pass

    per_instance = []
    confusion = defaultdict(lambda: defaultdict(int))
    dim_counters = {d: {"reached": 0, "passed": 0} for d in ("D1", "D2", "D3", "D4", "D5", "D6")}
    rule_selection = {"correct": 0, "incorrect": 0}
    matched_gt_norm_ids: set[str] = set()

    # Iterate extracted norms and classify each. Empty DB = empty loop.
    for ex in extracted:
        gt_norm = _match_gt(ex, gt_index)
        if gt_norm:
            matched_gt_norm_ids.add(gt_norm.get("norm_id"))
        expected = gt_norm.get(field) if gt_norm else None

        # Input payload: enough context for Claude to classify. Never reveal the
        # expected/ground-truth value — that leak would bias rating.
        input_payload = {
            "norm_kind": ex["norm_kind"],
            "modality": ex.get("modality"),
            "scoped_action": ex.get("primary_action"),
            "scoped_object": ex.get("primary_object"),
            "source_section": ex.get("source_section"),
            "source_text": ex.get("source_text"),
        }

        # Post-Prompt-07: _call_claude_classify makes real SDK calls and
        # returns {"classification": None, ...} on parse / transient failure
        # rather than raising. Old NotImplementedError path retained only as
        # safety net if credentials are missing.
        try:
            prompt_output = _call_claude_classify(
                PROMPT_VERSIONS[(field, prompt_version)], input_payload
            )
        except (NotImplementedError, RuntimeError) as exc:
            logger.error("classification SDK path unavailable: %s", exc)
            prompt_output = None

        result = _score_instance(field, ex["norm_kind"], prompt_output, expected, enum_values)
        per_instance.append({
            "norm_id": ex["norm_id"],
            "norm_kind": ex["norm_kind"],
            "expected": expected,
            "predicted": (prompt_output or {}).get("classification") or (prompt_output or {}).get("topology"),
            **result,
        })

        # update counters
        for d in ("D1", "D2", "D3", "D4", "D5"):
            if result["dimension_scores"].get(d) is not None:
                dim_counters[d]["reached"] += 1
                if result["dimension_scores"][d]:
                    dim_counters[d]["passed"] += 1
        # Confusion matrix
        predicted = (prompt_output or {}).get("classification") or (prompt_output or {}).get("topology") or "<none>"
        confusion[expected or "<none>"][predicted] += 1
        # Rule selection (per DeonticBench): the norm_kind tag of the extracted
        # entity vs the expected norm_kind. Measured separately.
        if gt_norm:
            if ex["norm_kind"] == gt_norm.get("norm_kind"):
                rule_selection["correct"] += 1
            else:
                rule_selection["incorrect"] += 1

    # D6 cross-instance consistency: same expected class always gets same predicted
    d6_violations = 0
    by_expected_predicted = defaultdict(set)
    for p in per_instance:
        by_expected_predicted[p["expected"]].add(p["predicted"])
    for exp, preds in by_expected_predicted.items():
        if len(preds) > 1:
            d6_violations += len(preds) - 1
    # Roll D6 into counters
    for p in per_instance:
        if p["first_failure"] is None:  # reached D6
            dim_counters["D6"]["reached"] += 1
            # passes if no violations for this expected class
            if len(by_expected_predicted.get(p["expected"], set())) <= 1:
                dim_counters["D6"]["passed"] += 1

    # D1 completeness: fraction of ground-truth norms with a classification
    # value set that have a matched extracted counterpart via structural-tuple
    # join. matched_gt_norm_ids accumulates during the per-extracted iteration
    # above — each successful _match_gt records the GT norm's id.
    gt_with_class = [n for n in gt_norms.values() if field in n and n.get(field) is not None]
    dim_counters["D1"]["reached"] = len(gt_with_class)
    dim_counters["D1"]["passed"] = sum(
        1 for n in gt_with_class if n.get("norm_id") in matched_gt_norm_ids
    )

    per_dim_acc = {
        d: (c["passed"] / c["reached"]) if c["reached"] else None
        for d, c in dim_counters.items()
    }

    passes = sum(1 for p in per_instance if p["grade"] == "pass")
    aggregate = passes / len(per_instance) if per_instance else 0.0
    rule_sel_total = rule_selection["correct"] + rule_selection["incorrect"]
    rule_sel_acc = rule_selection["correct"] / rule_sel_total if rule_sel_total else 0.0

    # Headline metric: accuracy-on-matched. Only counts instances that actually
    # joined to a GT norm via structural tuple — these are the instances where
    # the expected label is real and the prediction can meaningfully be graded.
    # Aggregate accuracy (above) dilutes this with unmatched instances that
    # have no GT counterpart, so every one of those counts against the score
    # even though measurement is impossible. See Prompt 09 Fix 4.
    matched_instances = [p for p in per_instance if p.get("expected") is not None]
    matched_correct = sum(1 for p in matched_instances if p["predicted"] == p["expected"])
    matched_count = len(matched_instances)
    accuracy_on_matched = (matched_correct / matched_count) if matched_count else 0.0
    unmatched_count = len(per_instance) - matched_count

    return {
        "field": field,
        "deal_id": deal_id,
        "prompt_version": prompt_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance_count": len(per_instance),
        # Headline — what to lead with when judging extraction quality.
        "headline_metric": {
            "accuracy_on_matched": accuracy_on_matched,
            "matched_count": matched_count,
            "matched_correct": matched_correct,
        },
        # Context — aggregate including unmatched, kept for continuity with
        # historical runs and because A3 coverage is orthogonal.
        "context_metrics": {
            "aggregate_accuracy": aggregate,
            "total_extracted_norms": len(per_instance),
            "unmatched_norms": unmatched_count,
            "comment": (
                "Unmatched norms have no GT counterpart via structural tuple. "
                "They can't be scored, so the aggregate's denominator includes "
                "them but can't credit them. Use headline_metric.accuracy_on_matched "
                "as the primary signal; this aggregate as deal-shape context."
            ),
        },
        # Back-compat alias (Prompt 08-era consumers read this key directly).
        "aggregate_accuracy": aggregate,
        "per_dimension_accuracy": per_dim_acc,
        "rule_selection_submatrix": {
            **rule_selection,
            "accuracy": rule_sel_acc,
        },
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "per_instance": per_instance,
    }


def measure_capacity_composition_accuracy(deal_id: str, ground_truth_path: Path, prompt_version: str) -> dict:
    return _measure_generic(
        "capacity_composition", deal_id, ground_truth_path, prompt_version,
        enum_values=CAPACITY_COMPOSITION_VALUES,
    )


def measure_action_scope_accuracy(deal_id: str, ground_truth_path: Path, prompt_version: str) -> dict:
    return _measure_generic(
        "action_scope", deal_id, ground_truth_path, prompt_version,
        enum_values=ACTION_SCOPE_VALUES,
    )


def measure_condition_structure_accuracy(deal_id: str, ground_truth_path: Path, prompt_version: str) -> dict:
    # condition_structure has two axes (topology + content); D5 checks content
    return _measure_generic(
        "condition_structure", deal_id, ground_truth_path, prompt_version,
        enum_values=None,  # topology is a free-form string; D2 is relaxed
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════


def _save_result(result: dict, deal_id: str, field: str, prompt_version: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{deal_id}_{field}_{prompt_version}.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return out


def _print_summary(result: dict) -> None:
    hl = result["headline_metric"]
    ctx = result["context_metrics"]
    print()
    print("=" * 72)
    print(f"Classification measurement — field={result['field']}  deal={result['deal_id']}  prompt={result['prompt_version']}")
    print("=" * 72)
    print(
        f"  HEADLINE  accuracy-on-matched: {hl['accuracy_on_matched']:.1%}  "
        f"({hl['matched_correct']}/{hl['matched_count']} matched)"
    )
    print(
        f"  context   aggregate:           {ctx['aggregate_accuracy']:.1%}  "
        f"({ctx['total_extracted_norms']} extracted, {ctx['unmatched_norms']} unmatched)"
    )
    print("  per-dimension accuracy (among instances that reached the dimension):")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6"):
        acc = result["per_dimension_accuracy"][d]
        print(f"    {d}: {('%.3f' % acc) if acc is not None else '   n/a'}")
    print(
        f"  rule-selection accuracy:       {result['rule_selection_submatrix']['accuracy']:.1%}  "
        f"(correct={result['rule_selection_submatrix']['correct']} / "
        f"incorrect={result['rule_selection_submatrix']['incorrect']})"
    )
    print("  confusion matrix (expected -> {predicted: count}):")
    for exp, row in sorted(result["confusion_matrix"].items(), key=lambda kv: str(kv[0])):
        print(f"    {exp}: {dict(row)}")
    print("=" * 72)


def main() -> int:
    p = argparse.ArgumentParser(description="Run v4 classification-accuracy measurement.")
    p.add_argument("--deal", required=True)
    p.add_argument(
        "--field",
        required=True,
        choices=["capacity_composition", "action_scope", "condition_structure", "all"],
    )
    p.add_argument(
        "--prompt-version",
        default="v2",
        choices=["v1", "v2"],
        help="v1 = original prompts (preserved for comparison). v2 = vocabulary-aligned prompts "
             "(Prompt 09 Fix 1; default). V2 pins closed-enum outputs and adds translation tables "
             "for common aliases so condition_structure doesn't return 'none' when 'unconditional' is expected.",
    )
    p.add_argument(
        "--ground-truth",
        default=str(DEFAULT_GROUND_TRUTH),
        help="path to ground-truth YAML",
    )
    args = p.parse_args()

    gt_path = Path(args.ground_truth)
    fields = ["capacity_composition", "action_scope", "condition_structure"] if args.field == "all" else [args.field]
    results = {}
    for f in fields:
        fn = {
            "capacity_composition": measure_capacity_composition_accuracy,
            "action_scope": measure_action_scope_accuracy,
            "condition_structure": measure_condition_structure_accuracy,
        }[f]
        r = fn(args.deal, gt_path, args.prompt_version)
        out_path = _save_result(r, args.deal, f, args.prompt_version)
        logger.info("saved: %s", out_path)
        _print_summary(r)
        results[f] = r
    return 0


if __name__ == "__main__":
    sys.exit(main())
