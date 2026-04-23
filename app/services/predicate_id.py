"""
Valence v4 — state_predicate_id construction rule.

Per architecture doc §4.5.1.1: every state_predicate instance has a composite
id serving as its @key. The id is a pipe-delimited concatenation of the
instance's structural tuple:

    "{label}|{threshold_value_double}|{operator_comparison}|{reference_predicate_label}"

Where any field is null, render as empty string. Numeric fields render with
Python's default string representation of a float (e.g., 5.75 → "5.75").

This function is the SINGLE source of truth for the concatenation. All four
consumers import from here:

    - app/data/state_predicates_seed.tql authoring (hand-authored today;
      future generator can emit ids by calling this function)
    - app/scripts/load_ground_truth.py (resolves atomic condition leaves'
      (label, threshold, op, ref) tuples to state_predicate instances)
    - Projection code in Prompt 07 (creates new state_predicate instances
      when extraction surfaces previously-unseen tuples)
    - Any query code that looks up state_predicates by tuple

Divergence in id construction across consumers causes silent lookup failures
that manifest as missing `condition_references_predicate` edges at load time.
"""

from __future__ import annotations


def construct_state_predicate_id(
    label: str,
    threshold_value_double: float | None = None,
    operator_comparison: str | None = None,
    reference_predicate_label: str | None = None,
) -> str:
    """Build the composite state_predicate_id per §4.5.1.1.

    Examples:
        >>> construct_state_predicate_id("no_event_of_default_exists")
        'no_event_of_default_exists|||'
        >>> construct_state_predicate_id("first_lien_net_leverage_at_or_below", 5.75, "at_or_below")
        'first_lien_net_leverage_at_or_below|5.75|at_or_below|'
        >>> construct_state_predicate_id("pro_forma_no_worse", reference_predicate_label="first_lien_net_leverage")
        'pro_forma_no_worse|||first_lien_net_leverage'
    """
    parts = [
        label,
        "" if threshold_value_double is None else str(threshold_value_double),
        operator_comparison or "",
        reference_predicate_label or "",
    ]
    return "|".join(parts)
