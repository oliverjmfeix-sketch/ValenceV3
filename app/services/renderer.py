"""
Valence v4 — rendering layer

Takes the structured response envelope from the intent parser (which
wraps the operations-layer response) and produces lawyer-readable prose.

Pure Python. Deterministic. No Claude SDK. No legal reasoning — all
conclusions come from the structured result.

Three top-level branches matching the intent parser's classification:
  - operation_call -> per-operation renderer with inline citations
  - clarification_needed -> prose + enumerated interpretations
  - out_of_scope -> short decline with category + reason

Per-operation renderers:
  describe_norm, get_attribute, enumerate_linked, trace_pathways,
  filter_norms, evaluate_feasibility, evaluate_capacity.

State predicate labels are machine-readable
(first_lien_net_leverage_at_or_below, pro_forma_no_worse, etc.); the
predicate-label map renders them as plain-English clauses with
threshold-template substitution. Unknown predicate labels fall
through as `predicate [label]` rather than being silently paraphrased.
"""

from __future__ import annotations

from typing import Any


# ─── Predicate label → prose templates ────────────────────────────────────────

PREDICATE_LABEL_TO_PROSE: dict[str, str] = {
    "first_lien_net_leverage_at_or_below":
        "First Lien Net Leverage Ratio at or below {threshold_value_double}x",
    "first_lien_net_leverage_above":
        "First Lien Net Leverage Ratio above {threshold_value_double}x",
    "senior_secured_leverage_at_or_below":
        "Senior Secured Leverage Ratio at or below {threshold_value_double}x",
    "total_leverage_at_or_below":
        "Total Leverage Ratio at or below {threshold_value_double}x",
    "individual_proceeds_at_or_below":
        "individual Asset Sale proceeds at or below the Individual Asset Sale Threshold",
    "annual_aggregate_at_or_below":
        "annual aggregate Asset Sale proceeds at or below the Annual Asset Sale Threshold",
    "pro_forma_no_worse":
        "pro forma First Lien Net Leverage Ratio is no worse than pre-transaction",
    "no_event_of_default_exists": "no Event of Default exists",
    "pro_forma_compliance_financial_covenants":
        "the borrower is in pro forma compliance with the maintenance financial covenants",
    "qualified_ipo_has_occurred": "a Qualified IPO has occurred",
    "retained_asset_sale_proceeds": "Retained Asset Sale Proceeds are available",
    "is_product_line_or_line_of_business_sale":
        "the transaction is a sale of all or substantially all of a product line or line of business",
    "unsub_would_own_or_license_material_ip_at_designation":
        "the Unrestricted Subsidiary would own or hold an exclusive license to Material Intellectual Property at designation",
    "is_ordinary_course_transfer":
        "the transfer is in the ordinary course of business (or of immaterial / obsolete IP)",
    "is_nonexclusive_license":
        "the transfer is a license / sublicense of Intellectual Property otherwise permitted under §6.02",
    "is_intercompany_transfer_within_restricted_group":
        "the transfer is an intercompany transfer within the restricted group",
    "is_immaterial_or_obsolete_ip":
        "the property transferred is immaterial or obsolete",
    "transfer_is_at_or_above_fair_market_value":
        "the transfer is at or above fair market value",
    "prior_year_capacity_was_unused":
        "the prior fiscal year's management-equity-basket capacity was not fully consumed",
    "base_capacity_will_be_unused_in_subsequent_year":
        "the subsequent fiscal year's base capacity will not be fully consumed",
    "incurrence_test_satisfied": "the applicable incurrence test is satisfied",
    "officer_certificate_delivered": "an officer's certificate has been delivered",
    "board_approval_obtained": "board of directors approval has been obtained",
}


def _render_predicate(pred_ref: dict | None) -> str:
    """Render an atomic predicate reference as prose."""
    if not pred_ref:
        return "(predicate reference missing)"
    label = pred_ref.get("state_predicate_label") or "(unknown)"
    template = PREDICATE_LABEL_TO_PROSE.get(label)
    if template is None:
        return f"predicate `{label}`"
    try:
        return template.format(**{
            "threshold_value_double": pred_ref.get("threshold_value_double"),
            "reference_predicate_label": pred_ref.get("reference_predicate_label"),
        })
    except (KeyError, IndexError):
        return f"predicate `{label}` (threshold={pred_ref.get('threshold_value_double')})"


def _render_condition(cond: dict | None) -> str:
    """Render a condition tree as a single prose clause."""
    if cond is None:
        return "Unconditional"
    op = cond.get("operator") or "atomic"
    if op == "atomic":
        return _render_predicate(cond.get("predicate_ref"))
    children = cond.get("children") or []
    if not children:
        return f"({op} over no children — degenerate condition)"
    rendered_children = [_render_condition(ch) for ch in children]
    if op == "or":
        if len(rendered_children) == 2:
            return f"either ({rendered_children[0]}) or ({rendered_children[1]})"
        joined = ", ".join(f"({c})" for c in rendered_children[:-1])
        return f"any of {joined}, or ({rendered_children[-1]})"
    if op == "and":
        return " and ".join(f"({c})" for c in rendered_children)
    return f"({op} of {len(rendered_children)} children)"


def _fmt_usd(x: Any) -> str:
    if x is None:
        return "null"
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _render_capacity(norm_result: dict) -> str:
    """One-line capacity description for a norm-describe result."""
    cc = norm_result.get("capacity_composition")
    cap_usd = norm_result.get("cap_usd")
    cap_grower = norm_result.get("cap_grower_pct")
    grower_ref = norm_result.get("cap_grower_reference")
    uses_greater = norm_result.get("cap_uses_greater_of")

    if cc == "n_a":
        return "No capacity concept applies (structural norm)."
    if cc == "unlimited_on_condition":
        return "Unlimited capacity when conditions hold."
    if cc == "computed_from_sources":
        n = len(norm_result.get("contributors") or [])
        return f"Composed of {n} contributing source(s); aggregated at the parent."
    if cc == "categorical":
        return "Scope-restricted permission; no dollar cap applies."
    if cc == "additive":
        parts = []
        if cap_usd is not None:
            parts.append(f"fixed cap of {_fmt_usd(cap_usd)}")
        if cap_grower is not None and grower_ref:
            parts.append(f"{cap_grower:.1f}% of {grower_ref}")
        if not parts:
            return "Additive capacity (cap not populated in GT)."
        join = " or " if uses_greater else " plus "
        return "Additive capacity: " + join.join(parts) + "."
    return f"Capacity composition: {cc}."


# ─── Top-level dispatch ────────────────────────────────────────────────────────


def render_response(parser_response: dict) -> str:
    """Entry point. Dispatches on intent_classification."""
    if not isinstance(parser_response, dict):
        return "(renderer: non-dict response)"
    classification = parser_response.get("intent_classification")

    if classification == "operation_call":
        return _render_operation_call(parser_response)
    if classification == "clarification_needed":
        return _render_clarification(parser_response)
    if classification == "out_of_scope":
        return _render_out_of_scope(parser_response)
    if classification == "parser_error":
        return (f"Parser error: {parser_response.get('parser_error','unknown')}. "
                f"Interpretation: {parser_response.get('parsed_as','n/a')}")
    return f"(renderer: unknown classification {classification!r})"


# ─── operation_call renderers ──────────────────────────────────────────────────


def _render_operation_call(pr: dict) -> str:
    """Render an operation_call response. Dispatches per-operation."""
    op = pr.get("operation")
    resp = pr.get("operation_response") or {}
    if "error" in resp:
        return (f"Valence attempted to route this question to the `{op}` operation, "
                f"but execution failed: {resp['error']}")

    result = resp.get("result", {}) if isinstance(resp, dict) else {}

    if op == "describe_norm":
        return _render_describe_norm(result)
    if op == "get_attribute":
        return _render_get_attribute(result)
    if op == "enumerate_linked":
        return _render_enumerate_linked(resp, result)
    if op == "trace_pathways":
        return _render_trace_pathways(result)
    if op == "filter_norms":
        return _render_filter_norms(result)
    if op == "evaluate_feasibility":
        return _render_evaluate_feasibility(pr, result)
    if op == "evaluate_capacity":
        return _render_evaluate_capacity(pr, result)
    return f"(renderer: no renderer for operation {op!r})"


def _render_describe_norm(r: dict) -> str:
    if "error" in r:
        return f"Norm not found: {r.get('norm_id','?')}."

    kind = r.get("norm_kind", "norm")
    mod = r.get("modality", "?")
    section = r.get("source_section", "?")
    page = r.get("source_page", "?")

    lines = [
        f"{kind.replace('_', ' ').title()} — {mod} under §{section} (p.{page})",
        "",
    ]

    subjects = r.get("subject_roles") or []
    actions = r.get("scoped_actions") or []
    objects = r.get("scoped_objects") or []
    lines.append(f"Subject: {', '.join(subjects) if subjects else '—'}")
    lines.append(f"Scoped actions: {', '.join(actions) if actions else '—'}")
    lines.append(f"Scoped objects: {', '.join(objects) if objects else '—'}")
    scope = r.get("action_scope")
    if scope:
        lines.append(f"Action scope: {scope}")
    lines.append("")

    lines.append(f"Capacity: {_render_capacity(r)}")

    cond = r.get("condition")
    lines.append(f"Conditions: {_render_condition(cond)}")
    lines.append("")

    source_text = r.get("source_text")
    if source_text:
        excerpt = source_text if len(source_text) <= 500 else source_text[:497] + "..."
        lines.append(f"Source text: \"{excerpt}\"")
        lines.append("")

    contribs = r.get("contributors") or []
    if contribs:
        lines.append(f"Contributing sources ({len(contribs)}):")
        for c in contribs:
            direction = c.get("aggregation_direction") or "add"
            fn = c.get("aggregation_function") or "sum"
            lines.append(f"  - {c.get('contributor_norm_id')} ({direction} via {fn})")
        lines.append("")

    contributes_to = r.get("contributes_to") or []
    if contributes_to:
        lines.append(f"Contributes to ({len(contributes_to)}):")
        for c in contributes_to:
            lines.append(f"  - {c.get('pool_norm_id')}")
        lines.append("")

    defeaters = r.get("defeaters") or []
    if defeaters:
        lines.append(f"Potential defeaters ({len(defeaters)}):")
        for d in defeaters:
            when = _render_condition(d.get("condition"))
            lines.append(f"  - {d.get('defeater_name') or d.get('defeater_id')} "
                         f"(§{d.get('source_section','?')}, when {when})")
        lines.append("")

    serves = r.get("serves_questions") or []
    if serves:
        lines.append("Serves gold questions: "
                     + ", ".join(f"{q.get('question_id')} ({q.get('serves_role')})"
                                 for q in serves))

    return "\n".join(lines).rstrip() + "\n"


def _render_get_attribute(r: dict) -> str:
    if "error" in r and r.get("value") is None:
        return (f"{r.get('entity_id','?')}.{r.get('attribute_name','?')}: "
                f"(not found — {r.get('error')})")
    val = r.get("value")
    return (f"{r.get('entity_id','?')}.{r.get('attribute_name','?')} = {val!r}"
            f" (entity type: {r.get('entity_type','?')})")


def _render_enumerate_linked(resp: dict, r: dict) -> str:
    anchor = resp.get("parameters", {}).get("entity_id", "?")
    rel = resp.get("parameters", {}).get("relation_type", "?")
    role = resp.get("parameters", {}).get("role_played", "?")
    count = r.get("count", 0)
    linked = r.get("linked") or []

    lines = [
        f"Entities linked to `{anchor}` via `{rel}` (playing role `{role}`): "
        f"{count} result(s).",
        "",
    ]
    for item in linked:
        edge = item.get("edge_attributes") or {}
        edge_str = ""
        if edge:
            non_null = {k: v for k, v in edge.items() if v is not None}
            if non_null:
                edge_str = " [" + ", ".join(f"{k}={v}" for k, v in non_null.items()) + "]"
        lines.append(f"  - {item.get('entity_id')} "
                     f"({item.get('entity_type','?')}, as `{item.get('role')}`)"
                     f"{edge_str}")
    return "\n".join(lines) + "\n"


def _render_trace_pathways(r: dict) -> str:
    if "error" in r:
        return f"trace_pathways error: {r['error']}"
    anchor = r.get("anchor", {})
    anchor_type = anchor.get("type", "?")
    anchor_value = anchor.get("value", "?")

    if anchor_type == "action_class":
        perms = r.get("permissions") or []
        prohibs = r.get("prohibitions") or []
        summary = r.get("summary", {})
        collapsed = summary.get("collapsed_count", 0)

        lines = [f"Pathways scoping action class `{anchor_value}`:", ""]
        lines.append(f"Permissions ({len(perms)}):")
        for p in perms:
            cap_note = (f"cap ${p.get('cap_usd'):,.0f}" if p.get("cap_usd") is not None
                        else ("grower-only" if p.get("cap_grower_pct") is not None
                              else "no dollar cap"))
            cond = p.get("conditions_required")
            cond_note = _render_condition(cond) if cond else "unconditional"
            lines.append(f"  - {p.get('norm_id')} "
                         f"({p.get('norm_kind','?')}, {cap_note}, {cond_note})")
        lines.append("")
        if prohibs:
            lines.append(f"Prohibitions ({len(prohibs)}):")
            for p in prohibs:
                cond = p.get("conditions_required")
                cond_note = _render_condition(cond) if cond else "unconditional"
                defeaters = p.get("defeaters_potential") or []
                d_note = f", {len(defeaters)} defeater(s)" if defeaters else ""
                lines.append(f"  - {p.get('norm_id')} "
                             f"({p.get('norm_kind','?')}, {cond_note}{d_note})")
            lines.append("")
        if collapsed:
            lines.append(f"[{collapsed} capacity contributor(s) rolled up into their "
                         f"parent pools. Re-run with --no-collapse-contributors to "
                         f"see every scoping norm.]")
        return "\n".join(lines).rstrip() + "\n"

    # state_predicate anchor
    refs = r.get("referencing_norms") or []
    ref_defs = r.get("referencing_defeaters") or []
    lines = [f"Norms / defeaters referencing state predicate `{anchor_value}`:", ""]
    if refs:
        lines.append(f"Referencing norms ({len(refs)}):")
        for n in refs:
            lines.append(f"  - {n.get('norm_id')} "
                         f"({n.get('modality','?')}, role in condition: "
                         f"{n.get('logical_role','?')})")
        lines.append("")
    if ref_defs:
        lines.append(f"Referencing defeaters ({len(ref_defs)}):")
        for d in ref_defs:
            lines.append(f"  - {d.get('defeater_id')} "
                         f"(role: {d.get('logical_role','?')})")
    return "\n".join(lines).rstrip() + "\n"


def _render_filter_norms(r: dict) -> str:
    count = r.get("count", 0)
    norms = r.get("norms") or []
    lines = [f"Norms matching the filter: {count} result(s).", ""]
    for i, n in enumerate(norms, start=1):
        lines.append(f"  {i}. {n.get('norm_id')} — "
                     f"{n.get('norm_kind','?')} — "
                     f"§{n.get('source_section','?')} — "
                     f"{n.get('modality','?')}, cc={n.get('capacity_composition','?')}")
    return "\n".join(lines) + "\n"


def _render_evaluate_feasibility(pr: dict, r: dict) -> str:
    norm_id = r.get("norm_id", "?")
    mod = r.get("modality", "?")
    applicable = r.get("applicable")
    reason = r.get("reason", "")

    if applicable is True:
        verdict = "APPLICABLE"
    elif applicable is False:
        verdict = "NOT APPLICABLE"
    else:
        verdict = "INCONCLUSIVE"

    lines = [
        f"Feasibility of `{norm_id}` ({mod}): {verdict}",
        f"Reason: {reason}",
        "",
    ]

    ws = pr.get("supplied_world_state") or {}
    pv = ws.get("predicate_values") or {}
    pa = ws.get("proposed_action") or {}
    if pv or pa:
        lines.append("Supplied inputs used:")
        for k, v in sorted(pv.items()):
            lines.append(f"  - predicate_values.{k} = {v}")
        for k, v in sorted(pa.items()):
            if v is None:
                continue
            lines.append(f"  - proposed_action.{k} = {v}")
        lines.append("")

    trace = pr.get("computation_trace") or []
    if trace:
        lines.append(f"Evaluation trace ({len(trace)} step(s)):")
        for t in trace:
            lines.append(
                f"  {t.get('step','?'):>2}. {t.get('operation','?')}: "
                f"outcome={t.get('outcome')!r}  "
                f"{t.get('reasoning','')[:120]}"
            )
        lines.append("")

    lines.append("Note: Valence computed this from your supplied values. Verify "
                 "against the borrower's actual financial state.")
    return "\n".join(lines).rstrip() + "\n"


def _render_evaluate_capacity(pr: dict, r: dict) -> str:
    norm_id = r.get("norm_id", "?")
    cc = r.get("capacity_composition", "?")
    cap = r.get("capacity_usd")
    if cap is None:
        if cc == "unlimited_on_condition":
            cap_str = "unlimited (conditions held)"
        else:
            cap_str = "could not compute — see trace"
    else:
        cap_str = _fmt_usd(cap)

    lines = [
        f"Capacity under `{norm_id}` (composition: {cc}): {cap_str}",
        "",
    ]

    ws = pr.get("supplied_world_state") or {}
    pv = ws.get("predicate_values") or {}
    if pv:
        lines.append("Supplied inputs used:")
        for k, v in sorted(pv.items()):
            lines.append(f"  - {k} = {v}")
        lines.append("")

    trace = pr.get("computation_trace") or []
    if trace:
        lines.append(f"Capacity computation ({len(trace)} step(s)):")
        for t in trace:
            lines.append(
                f"  {t.get('step','?'):>2}. {t.get('operation','?')} "
                f"norm={t.get('norm_id','-')}: "
                f"outcome={t.get('outcome')!r}  {t.get('reasoning','')[:100]}"
            )
        lines.append("")

    lines.append("Note: Valence computed this from your supplied values. Verify "
                 "against the borrower's actual financial state.")
    return "\n".join(lines).rstrip() + "\n"


# ─── clarification_needed + out_of_scope ───────────────────────────────────────


def _render_clarification(pr: dict) -> str:
    clar = pr.get("clarification_request", "(no prose)")
    interps = pr.get("possible_interpretations") or []
    lines = [
        "Clarification needed.",
        "",
        f"Parser interpretation: {pr.get('parsed_as', '(none)')}",
        f"Question for you: {clar}",
        "",
    ]
    if interps:
        lines.append("Possible interpretations:")
        for i, interp in enumerate(interps, start=1):
            op = interp.get("implied_operation", "?")
            desc = interp.get("interpretation", "(no description)")
            shape = interp.get("example_output_shape", "")
            lines.append(f"  {i}. {desc}")
            lines.append(f"     -> would call `{op}`"
                         + (f". Output: {shape}" if shape else ""))
        lines.append("")
    lines.append("Please re-ask specifying which interpretation applies.")
    return "\n".join(lines) + "\n"


def _render_out_of_scope(pr: dict) -> str:
    cat = pr.get("out_of_scope_category", "unknown")
    reason = pr.get("out_of_scope_reason", "(no reason provided)")
    return (f"This question is out of scope for Valence.\n\n"
            f"Category: {cat}\n"
            f"Reason: {reason}\n")
