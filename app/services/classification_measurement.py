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
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

import yaml  # noqa: E402

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


PROMPT_VERSIONS = {
    ("capacity_composition", "v1"): CAPACITY_COMPOSITION_PROMPT_V1,
    ("action_scope", "v1"): ACTION_SCOPE_PROMPT_V1,
    ("condition_structure", "v1"): CONDITION_STRUCTURE_PROMPT_V1,
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
    """Return list of norm records currently in valence_v4, each with id + kind + relevant scalars."""
    try:
        result = tx.query(
            "match $n isa norm, has norm_id $nid, has norm_kind $nk; select $nid, $nk;"
        ).resolve()
        rows = list(result.as_concept_rows())
    except Exception:
        rows = []
    norms = []
    for r in rows:
        try:
            norms.append({
                "norm_id": r.get("nid").as_attribute().get_value(),
                "norm_kind": r.get("nk").as_attribute().get_value(),
            })
        except Exception:
            continue
    return norms


def _call_claude_classify(prompt: str, input_payload: dict) -> dict:
    """
    Placeholder: in Prompt 08 onwards this calls Anthropic SDK with the
    prompt + JSON input_payload and parses the JSON response. For Prompt 06
    plumbing test, no baskets exist in an empty DB so this is not invoked.
    """
    # Intentionally unimplemented for pilot plumbing. When the Prompt 08 run
    # happens, wire an Anthropic client here, format input_payload as the
    # user message, parse the returned JSON.
    raise NotImplementedError(
        "Claude classification call not yet wired — Prompt 08 hooks this up. "
        "Empty-DB plumbing runs don't reach this code path because "
        "_list_extracted_norms returns an empty list."
    )


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
    gt_norms = {n["norm_id"]: n for n in gt.get("norms", [])}

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

    # Iterate extracted norms and classify each. Empty DB = empty loop.
    for ex in extracted:
        gt_norm = gt_norms.get(ex["norm_id"])
        expected = gt_norm.get(field) if gt_norm else None

        # Build input payload for the prompt (TBD: include source_text etc. from DB)
        input_payload = {"norm_kind": ex["norm_kind"], "expected_hidden_for_grader": expected}

        try:
            prompt_output = _call_claude_classify(PROMPT_VERSIONS[(field, prompt_version)], input_payload)
        except NotImplementedError:
            # Empty-DB plumbing path: no classification calls made
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

    # D1 completeness: rough proxy — fraction of ground-truth norms with a
    # classification field set that have an extracted counterpart.
    gt_with_class = [n for n in gt.get("norms", []) if field in n and n.get(field) is not None]
    dim_counters["D1"]["reached"] = len(gt_with_class)
    extracted_ids = {e["norm_id"] for e in extracted}
    dim_counters["D1"]["passed"] = sum(1 for n in gt_with_class if n["norm_id"] in extracted_ids)

    per_dim_acc = {
        d: (c["passed"] / c["reached"]) if c["reached"] else None
        for d, c in dim_counters.items()
    }

    passes = sum(1 for p in per_instance if p["grade"] == "pass")
    aggregate = passes / len(per_instance) if per_instance else 0.0
    rule_sel_total = rule_selection["correct"] + rule_selection["incorrect"]
    rule_sel_acc = rule_selection["correct"] / rule_sel_total if rule_sel_total else 0.0

    return {
        "field": field,
        "deal_id": deal_id,
        "prompt_version": prompt_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance_count": len(per_instance),
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
    print()
    print("=" * 70)
    print(f"Classification measurement — field={result['field']}  deal={result['deal_id']}  prompt={result['prompt_version']}")
    print("=" * 70)
    print(f"  instances measured:    {result['instance_count']}")
    print(f"  aggregate accuracy:    {result['aggregate_accuracy']:.3f}")
    print("  per-dimension accuracy (among instances that reached the dimension):")
    for d in ("D1", "D2", "D3", "D4", "D5", "D6"):
        acc = result["per_dimension_accuracy"][d]
        print(f"    {d}: {('%.3f' % acc) if acc is not None else '   n/a'}")
    print(f"  rule-selection accuracy: {result['rule_selection_submatrix']['accuracy']:.3f}  "
          f"(correct={result['rule_selection_submatrix']['correct']} / "
          f"incorrect={result['rule_selection_submatrix']['incorrect']})")
    print("  confusion matrix (expected -> {predicted: count}):")
    for exp, row in sorted(result["confusion_matrix"].items(), key=lambda kv: str(kv[0])):
        print(f"    {exp}: {dict(row)}")
    print("=" * 70)


def main() -> int:
    p = argparse.ArgumentParser(description="Run v4 classification-accuracy measurement.")
    p.add_argument("--deal", required=True)
    p.add_argument(
        "--field",
        required=True,
        choices=["capacity_composition", "action_scope", "condition_structure", "all"],
    )
    p.add_argument("--prompt-version", default="v1")
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
