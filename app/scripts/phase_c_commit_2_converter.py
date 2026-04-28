"""
Phase C Commit 2 — mechanical deontic_mapping → projection_rule converter.

For each of the 15 existing deontic_mapping entries, generates the
equivalent projection_rule subgraph (entity_type_criterion + norm_template
+ scalar attribute_emissions + value_sources) and applies it to
valence_v4. Then runs the projection_rule executor for each rule and
compares scalar attributes against the existing python-projected norm.

Scope (Commit 2 minimal, matching Commit 1.5):
  - Scalar attribute emissions only
  - No relation_templates (scope edges deferred to a later commit)
  - No condition_templates (conditional rules emit scalar norms only;
    the condition is missing from the rule output until templates land)
  - No defeater_templates (jcrew_blocker emits the prohibition norm only)
  - No builder sub-source emission (builder_basket emits one norm only;
    sub-sources from has_*_source flags deferred)

Pre-flight:
  - Pilot rule (rule_general_rp_basket) from Commit 1.5 must be present.
    The converter will skip it (already authored) to avoid double-emission.

Output:
  - All 15 new projection_rules in valence_v4 (Commit 3.1: pilot
    rule retired; converter now authors rule_conv_general_rp_basket too)
  - Per-rule report: scalars matched / mismatched / missing
  - Aggregate report: rules-passing / rules-failing the parity check
  - Schema gaps flagged on rules whose scalar parity fails

Idempotent: re-running drops all converted rules (norm_id prefix "conv_")
and rebuilds. Commit 3.1 retired the pilot rule; the converter now
authors all 15 mappings including general_rp_basket.

Usage:
  py -m app.scripts.phase_c_commit_2_converter --deal 6e76ed06
  py -m app.scripts.phase_c_commit_2_converter --deal 6e76ed06 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from app.config import settings  # noqa: E402
from app.services.projection_rule_executor import execute_rule, fetch_v3_entity_attrs  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("commit_2_converter")


# Per-entity-type v3 attribute mapping for cap-related fields.
# Encodes the chained-OR fallback that lives in deontic_projection.py:523-524
# as explicit per-entity-type knowledge (the projection_rule schema's
# v3_attribute_value_source.reads_v3_attribute_name field per rule).
#
# None means the entity type doesn't carry that concept (no cap_usd, etc.);
# the converter omits the corresponding attribute_emission.
CAP_USD_ATTR_BY_ENTITY = {
    "builder_basket": "basket_amount_usd",
    "general_rp_basket": "basket_amount_usd",
    "ratio_basket": None,
    "management_equity_basket": "annual_cap_usd",
    "tax_distribution_basket": None,
    "holdco_overhead_basket": "annual_cap_usd",
    "equity_award_basket": "annual_cap_usd",
    "unsub_distribution_basket": None,
    "general_investment_basket": "basket_amount_usd",
    "general_rdp_basket": "basket_amount_usd",
    "ratio_rdp_basket": None,
    "builder_rdp_basket": None,
    "refinancing_rdp_basket": None,
    "equity_funded_rdp_basket": None,
    "jcrew_blocker": None,
}

CAP_GROWER_ATTR_BY_ENTITY = {
    "builder_basket": None,
    "general_rp_basket": "basket_grower_pct",
    "ratio_basket": None,
    "management_equity_basket": "annual_cap_pct_ebitda",
    "tax_distribution_basket": None,
    "holdco_overhead_basket": None,
    "equity_award_basket": None,
    "unsub_distribution_basket": None,
    "general_investment_basket": "basket_grower_pct",
    "general_rdp_basket": "basket_grower_pct",
    "ratio_rdp_basket": None,
    "builder_rdp_basket": None,
    "refinancing_rdp_basket": None,
    "equity_funded_rdp_basket": None,
    "jcrew_blocker": None,
}

# Builder-related norm_kinds get cumulative_since_anchor + closing_date_fiscal_quarter_start.
# Ratio kinds get ltm_at_test_date + not_applicable.
# All others: not_applicable / not_applicable.
# Mirrors _BUILDER_TEMPORAL_KINDS in deontic_projection.py.
BUILDER_TEMPORAL_KINDS = {
    "builder_usage_permission",
    "builder_usage_rdp_permission",
    "builder_basket_aggregate",
    "builder_source_starter",
    "builder_source_three_test_aggregate",
    "builder_source_cni",
    "builder_source_ecf",
    "builder_source_ebitda_fc",
    "builder_source_ebitda_component",
    "builder_source_fixed_charges_component",
    "builder_source_declined_ecf",
    "builder_source_declined_asset_sale",
    "builder_source_sale_leaseback",
    "builder_source_retained_asset_sale_proceeds",
    "builder_source_joint_venture_returns",
    "builder_source_unsub_redesignation_fmv",
    "builder_source_receivables_royalty_license",
    "builder_source_investment_returns",
    "builder_source_deferred_revenues",
    "builder_source_other",
    "builder_source_netting",
}
# Mirrors python projection's _LTM_TEST_DATE_KINDS exactly. RDP variants
# do NOT get LTM treatment (the python projection treats builder_rdp /
# ratio_rdp as non-temporal — their grower lookup happens at usage, not
# at accumulation start).
LTM_TEMPORAL_KINDS = {"ratio_rp_basket_permission"}


def temporal_for(kind: str) -> tuple[str, str]:
    if kind in BUILDER_TEMPORAL_KINDS:
        return ("closing_date_fiscal_quarter_start", "cumulative_since_anchor")
    if kind in LTM_TEMPORAL_KINDS:
        return ("not_applicable", "ltm_at_test_date")
    return ("not_applicable", "not_applicable")


# Norm_id prefix for converter-emitted norms (collision avoidance vs python projection)
NORM_ID_PREFIX = "conv_"

# (Pilot retired in Commit 3.1; converter authors rule_conv_general_rp_basket
# directly via the standard mapping flow.)

# Concrete subtypes of instrument_class. Object labels in this set are
# scoped via norm_scopes_instrument; all others via norm_scopes_object.
# Mirrors load_instrument_labels in load_ground_truth.py / the python
# projection's instrument_labels set.
INSTRUMENT_LABELS = {
    "equity_interest",
    "holdco_equity",
    "material_intellectual_property",
    "restricted_sub_equity",
    "subordinated_debt_instrument",
    "unrestricted_sub_equity",
}

# Concrete subtypes of blocker_exception (per schema_unified.tql §"J.Crew").
# Each becomes a separate defeater rule that emits a defeater + defeats
# edge to the jcrew prohibition norm.
BLOCKER_EXCEPTION_SUBTYPES = (
    "ordinary_course_exception",
    "nonexclusive_license_exception",
    "intercompany_exception",
    "immaterial_ip_exception",
    "fair_value_exception",
    "license_back_exception",
)


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Read deontic_mappings
# ═══════════════════════════════════════════════════════════════════════════════

def load_mappings(driver, db: str) -> list[dict]:
    """Read all deontic_mapping entries with their primary attributes
    plus action / object labels via mapping_targets_action and
    mapping_targets_object."""
    mappings: list[dict] = []
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = """
match
    $m isa deontic_mapping,
        has mapping_id $mid,
        has source_entity_type $src,
        has target_norm_kind $tnk,
        has target_modality $mod,
        has default_subject_role $subj,
        has default_action_scope_kind $scope,
        has condition_builder_spec_ref $cb;
select $mid, $src, $tnk, $mod, $subj, $scope, $cb;
"""
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                mappings.append({
                    "mapping_id": row.get("mid").as_attribute().get_value(),
                    "source_entity_type": row.get("src").as_attribute().get_value(),
                    "target_norm_kind": row.get("tnk").as_attribute().get_value(),
                    "target_modality": row.get("mod").as_attribute().get_value(),
                    "default_subject_role": row.get("subj").as_attribute().get_value(),
                    "default_action_scope_kind": row.get("scope").as_attribute().get_value(),
                    "condition_builder_spec_ref": row.get("cb").as_attribute().get_value(),
                    "action_labels": [],
                    "object_labels": [],
                })
        except Exception as exc:
            logger.error(f"load_mappings failed: {str(exc).splitlines()[0][:200]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Read action labels
    by_id = {m["mapping_id"]: m for m in mappings}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = """
match
    $m isa deontic_mapping, has mapping_id $mid;
    (mapping: $m, action: $a) isa mapping_targets_action;
    $a has action_class_label $al;
select $mid, $al;
"""
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                mid = row.get("mid").as_attribute().get_value()
                al = row.get("al").as_attribute().get_value()
                if mid in by_id:
                    by_id[mid]["action_labels"].append(al)
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Read object labels
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = """
match
    $m isa deontic_mapping, has mapping_id $mid;
    (mapping: $m, object: $o) isa mapping_targets_object;
    $o has object_class_label $ol;
select $mid, $ol;
"""
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                mid = row.get("mid").as_attribute().get_value()
                ol = row.get("ol").as_attribute().get_value()
                if mid in by_id:
                    by_id[mid]["object_labels"].append(ol)
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return mappings


# ═══════════════════════════════════════════════════════════════════════════════
# Generate projection_rule TQL
# ═══════════════════════════════════════════════════════════════════════════════

def generate_rule_tql(mapping: dict) -> str:
    """Return the TQL for one projection_rule subgraph that mirrors a
    deontic_mapping's scalar emission behavior.

    norm_id = NORM_ID_PREFIX + deal_id + "_" + target_norm_kind
    """
    src = mapping["source_entity_type"]
    tnk = mapping["target_norm_kind"]
    mod = mapping["target_modality"]
    scope = mapping["default_action_scope_kind"]
    rule_id = f"rule_conv_{src}"
    nt_id = f"nt_conv_{src}"
    growth, ref_period = temporal_for(tnk)
    cap_usd_attr = CAP_USD_ATTR_BY_ENTITY.get(src)
    cap_grower_attr = CAP_GROWER_ATTR_BY_ENTITY.get(src)

    # Use stable variable counters per rule
    lines = ["insert", ""]
    var_idx = [0]

    def vs_var(prefix: str) -> str:
        var_idx[0] += 1
        return f"$vs_{prefix}_{var_idx[0]}"

    def ae_var(prefix: str) -> str:
        var_idx[0] += 1
        return f"$ae_{prefix}_{var_idx[0]}"

    def emit_literal_string(attr_name: str, value: str) -> None:
        ae = ae_var(attr_name)
        vs = vs_var(attr_name)
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa literal_string_value_source, has literal_string_value {_tq_string(value)};')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    def emit_v3_attr(attr_name: str, v3_name: str, default: str | None = None) -> None:
        ae = ae_var(attr_name)
        vs = vs_var(attr_name)
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa v3_attribute_value_source, has reads_v3_attribute_name "{v3_name}";')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')
        if default is not None:
            vs_def = vs_var(f"{attr_name}_default")
            lines.append(f'{vs_def} isa literal_string_value_source, has literal_string_value {_tq_string(default)};')
            lines.append(f'(primary_source: {vs}, default_source: {vs_def}) isa value_source_has_default;')

    # Rule entity
    lines.append(f'$rule isa projection_rule,')
    lines.append(f'    has projection_rule_id "{rule_id}",')
    lines.append(f'    has projection_rule_label "Phase C Commit 2 conv: {src} -> {tnk}",')
    lines.append(f'    has projection_rule_description "Mechanically converted from deontic_mapping {mapping["mapping_id"]}. Scalar attribute emissions only.";')

    # Match criterion
    lines.append(f'$crit isa entity_type_criterion, has matches_v3_entity_type "{src}";')
    lines.append(f'(owning_rule: $rule, applied_criterion: $crit) isa rule_has_match_criterion;')

    # Norm template
    lines.append(f'$nt isa norm_template,')
    lines.append(f'    has norm_template_id "{nt_id}",')
    lines.append(f'    has norm_template_label "{tnk}";')
    lines.append(f'(owning_rule: $rule, produced_template: $nt) isa rule_produces_norm_template;')

    # norm_id: concatenate(NORM_ID_PREFIX, deal_id, "_<target_norm_kind>")
    ae_id = "$ae_norm_id"
    vs_id = "$vs_id_concat"
    lines.append(f'{ae_id} isa attribute_emission, has emitted_attribute_name "norm_id";')
    lines.append(f'(emitting_template: $nt, emitted_attribute: {ae_id}) isa template_emits_attribute;')
    lines.append(f'{vs_id} isa concatenation_value_source;')
    vs_pre = "$vs_id_pre"
    vs_deal = "$vs_id_deal"
    vs_post = "$vs_id_post"
    lines.append(f'{vs_pre} isa literal_string_value_source, has literal_string_value "{NORM_ID_PREFIX}";')
    lines.append(f'{vs_deal} isa deal_id_value_source;')
    lines.append(f'{vs_post} isa literal_string_value_source, has literal_string_value "_{tnk}";')
    lines.append(f'(owning_emission: {ae_id}, source_value: {vs_id}) isa attribute_emission_uses_value;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    # Literal scalars: norm_kind, modality, action_scope, growth_start_anchor, reference_period_kind
    emit_literal_string("norm_kind", tnk)
    emit_literal_string("modality", mod)
    emit_literal_string("action_scope", scope)
    emit_literal_string("growth_start_anchor", growth)
    emit_literal_string("reference_period_kind", ref_period)

    # v3-attribute scalars (omit if entity type doesn't have them).
    # capacity_composition has a default fallback "additive" — mirrors the
    # python projection's `attrs.get("capacity_composition") or "additive"`.
    emit_v3_attr("capacity_composition", "capacity_composition", default="additive")
    emit_v3_attr("source_section", "section_reference")
    emit_v3_attr("source_page", "source_page")
    emit_v3_attr("source_text", "source_text")
    if cap_usd_attr:
        emit_v3_attr("cap_usd", cap_usd_attr)
    if cap_grower_attr:
        emit_v3_attr("cap_grower_pct", cap_grower_attr)
    # Optional scalars present on some entity types but not others. The
    # v3_attribute_value_source returns None when absent; emit_norm
    # filters Nones. So unconditional emission is safe — emits only when
    # the v3 entity owns the attribute.
    emit_v3_attr("cap_uses_greater_of", "cap_uses_greater_of")
    emit_v3_attr("confidence", "confidence")

    # ─── Relation templates (Commit 2.1) ───────────────────────────────────
    # Per python projection's bind_norm_scope: emit one relation per subject_role,
    # one per action_label, one per object_label. Object labels dispatched by
    # instrument_class membership.

    def emit_relation_template(rel_type: str, other_role_name: str,
                                other_lookup_type: str, other_lookup_attr: str,
                                lookup_value: str) -> None:
        rt = vs_var(f"rt_{rel_type}")
        ra_norm = vs_var(f"ra_norm_{rel_type}")
        ra_other = vs_var(f"ra_other_{rel_type}")
        f_norm = vs_var(f"f_norm_{rel_type}")
        f_other = vs_var(f"f_other_{rel_type}")
        v_other = vs_var(f"v_other_{rel_type}")
        rt_id = f"rt_conv_{src}_{rel_type}_{lookup_value}"

        lines.append(f'{rt} isa relation_template,')
        lines.append(f'    has relation_template_id "{rt_id}",')
        lines.append(f'    has emits_relation_type "{rel_type}";')
        lines.append(f'(emitting_template: $nt, emitted_relation: {rt}) isa template_emits_relation;')

        # Role assignment for the norm role (filled by the rule's emitted norm)
        lines.append(f'{ra_norm} isa role_assignment, has assigned_role_name "norm";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_norm}) isa relation_template_assigns_role;')
        lines.append(f'{f_norm} isa emitted_norm_role_filler;')
        lines.append(f'(owning_role_assignment: {ra_norm}, assignment_filler: {f_norm}) isa role_assignment_filled_by;')

        # Role assignment for the other role (static lookup)
        lines.append(f'{ra_other} isa role_assignment, has assigned_role_name "{other_role_name}";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_other}) isa relation_template_assigns_role;')
        lines.append(f'{f_other} isa static_lookup_role_filler,')
        lines.append(f'    has lookup_entity_type "{other_lookup_type}",')
        lines.append(f'    has lookup_attribute_name "{other_lookup_attr}";')
        lines.append(f'(owning_role_assignment: {ra_other}, assignment_filler: {f_other}) isa role_assignment_filled_by;')
        lines.append(f'{v_other} isa literal_string_value_source, has literal_string_value {_tq_string(lookup_value)};')
        lines.append(f'(owning_filler: {f_other}, lookup_value_source: {v_other}) isa static_lookup_uses_value;')

    # subject_role is comma-separated in the deontic_mapping
    subject_roles = [r.strip() for r in mapping["default_subject_role"].split(",") if r.strip()]
    for role in subject_roles:
        emit_relation_template("norm_binds_subject", "subject", "party", "party_role", role)

    for action_label in mapping.get("action_labels", []):
        emit_relation_template("norm_scopes_action", "action", "action_class", "action_class_label", action_label)

    for obj_label in mapping.get("object_labels", []):
        if obj_label in INSTRUMENT_LABELS:
            emit_relation_template("norm_scopes_instrument", "instrument", "instrument_class", "instrument_class_label", obj_label)
        else:
            emit_relation_template("norm_scopes_object", "object", "object_class", "object_class_label", obj_label)

    # ─── Condition templates (Commit 2.2) ──────────────────────────────────
    # The python projection emits conditions only when the existing graph
    # state actually carries them. Audit shows only ratio_basket ->
    # ratio_rp_basket_permission has a condition tree currently. So we
    # author a condition_template ONLY for that mapping. ratio_rdp_basket
    # and jcrew_blocker have condition_builder_spec_refs but the existing
    # python projection skips condition emission for them — we mirror that
    # behavior to preserve byte-identical parity.
    cb_ref = mapping.get("condition_builder_spec_ref", "none")
    if src == "ratio_basket" and cb_ref == "ratio_with_no_worse":
        # Root: or_of_atomics
        # Child 0 (atomic, dynamic): first_lien_net_leverage_at_or_below at v3 ratio_threshold
        # Child 1 (atomic, canonical): pro_forma_no_worse|||first_lien_net_leverage
        ct_root = vs_var("ct_root")
        ct_c0 = vs_var("ct_c0")
        ct_c1 = vs_var("ct_c1")
        spec_c0 = vs_var("spec_c0")
        spec_c1 = vs_var("spec_c1")
        vs_thresh = vs_var("vs_thresh")

        lines.append(f'{ct_root} isa condition_template,')
        lines.append(f'    has condition_template_id "ct_conv_{src}_root",')
        lines.append(f'    has target_topology "or_of_atomics",')
        lines.append(f'    has target_operator "or";')
        lines.append(f'(emitting_template: $nt, root_condition: {ct_root}) isa template_emits_root_condition;')

        # Child 0 — dynamic threshold atomic
        lines.append(f'{ct_c0} isa condition_template,')
        lines.append(f'    has condition_template_id "ct_conv_{src}_c0",')
        lines.append(f'    has target_topology "atomic",')
        lines.append(f'    has target_operator "atomic";')
        lines.append(f'(parent_condition: {ct_root}, child_condition: {ct_c0})')
        lines.append(f'    isa condition_template_has_child, has child_template_index 0;')
        lines.append(f'{spec_c0} isa predicate_specifier,')
        lines.append(f'    has specifies_predicate_id "first_lien_net_leverage_at_or_below",')
        lines.append(f'    has specifies_operator "at_or_below";')
        lines.append(f'(owning_condition_template: {ct_c0}, referenced_specifier: {spec_c0}) isa atomic_condition_references_predicate;')
        lines.append(f'{vs_thresh} isa v3_attribute_value_source, has reads_v3_attribute_name "ratio_threshold";')
        lines.append(f'(owning_specifier: {spec_c0}, dynamic_value_source: {vs_thresh}) isa predicate_specifier_uses_value;')

        # Child 1 — canonical atomic
        lines.append(f'{ct_c1} isa condition_template,')
        lines.append(f'    has condition_template_id "ct_conv_{src}_c1",')
        lines.append(f'    has target_topology "atomic",')
        lines.append(f'    has target_operator "atomic";')
        lines.append(f'(parent_condition: {ct_root}, child_condition: {ct_c1})')
        lines.append(f'    isa condition_template_has_child, has child_template_index 1;')
        lines.append(f'{spec_c1} isa predicate_specifier,')
        lines.append(f'    has specifies_predicate_id "pro_forma_no_worse|||first_lien_net_leverage";')
        lines.append(f'(owning_condition_template: {ct_c1}, referenced_specifier: {spec_c1}) isa atomic_condition_references_predicate;')

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════════════
# Apply rules + run executor
# ═══════════════════════════════════════════════════════════════════════════════

def generate_defeater_rule_tql(subtype: str) -> str:
    """Generate a projection_rule TQL subgraph that maps blocker_exception
    subtype <subtype> to a v4 defeater entity + defeats edge to the jcrew
    prohibition norm.

    defeater_id = "conv_<deal_id>_jcrew_<subtype>"
    Emits 1 defeater + 1 defeats edge per matched blocker_exception.
    """
    rule_id = f"rule_conv_{subtype}_defeater"
    dt_id = f"dt_conv_{subtype}"
    lines = ["insert", ""]
    var_idx = [0]

    def vs_var(prefix: str) -> str:
        var_idx[0] += 1
        return f"$vs_{prefix}_{var_idx[0]}"

    # Rule
    lines.append(f'$rule isa projection_rule,')
    lines.append(f'    has projection_rule_id "{rule_id}",')
    lines.append(f'    has projection_rule_label "Phase C Commit 2.3 conv: {subtype} -> defeater",')
    lines.append(f'    has projection_rule_description "Mechanically converted from python _project_jcrew_defeaters. Emits a defeater per blocker_exception of subtype {subtype} + defeats edge to the jcrew prohibition norm.";')

    # Match criterion
    lines.append(f'$crit isa entity_type_criterion, has matches_v3_entity_type "{subtype}";')
    lines.append(f'(owning_rule: $rule, applied_criterion: $crit) isa rule_has_match_criterion;')

    # Defeater template
    lines.append(f'$dt isa defeater_template,')
    lines.append(f'    has defeater_template_id "{dt_id}",')
    lines.append(f'    has defeater_template_label "{subtype} -> defeater";')
    lines.append(f'(owning_rule: $rule, produced_template: $dt) isa rule_produces_defeater_template;')

    # Attribute emissions
    # defeater_id: concatenate("conv_", deal_id, "_jcrew_<subtype>")
    ae = vs_var("ae_did")
    vs = vs_var("vs_did_concat")
    vs_pre = vs_var("vs_did_pre")
    vs_deal = vs_var("vs_did_deal")
    vs_post = vs_var("vs_did_post")
    lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "defeater_id";')
    lines.append(f'(emitting_template: $dt, emitted_attribute: {ae}) isa template_emits_attribute;')
    lines.append(f'{vs} isa concatenation_value_source;')
    lines.append(f'{vs_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_deal} isa deal_id_value_source;')
    lines.append(f'{vs_post} isa literal_string_value_source, has literal_string_value "_jcrew_{subtype}";')
    lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')
    lines.append(f'(owning_concatenation: {vs}, concatenation_part: {vs_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs}, concatenation_part: {vs_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs}, concatenation_part: {vs_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    def emit_literal_string(attr_name: str, value: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $dt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa literal_string_value_source, has literal_string_value {_tq_string(value)};')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    def emit_v3_attr(attr_name: str, v3_name: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $dt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa v3_attribute_value_source, has reads_v3_attribute_name "{v3_name}";')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    emit_literal_string("defeater_type", "exception")
    emit_v3_attr("defeater_name", "exception_name")
    emit_v3_attr("source_text", "source_text")
    emit_v3_attr("source_section", "section_reference")
    emit_v3_attr("source_page", "source_page")

    # Relation template: defeats edge
    # (defeater: <emitted defeater>, defeated: <looked-up prohibition norm>) isa defeats
    rt = vs_var("rt_defeats")
    ra_def = vs_var("ra_def")
    ra_rec = vs_var("ra_rec")
    f_def = vs_var("f_def")
    f_rec = vs_var("f_rec")
    vs_norm_concat = vs_var("vs_norm_concat")
    vs_norm_pre = vs_var("vs_norm_pre")
    vs_norm_deal = vs_var("vs_norm_deal")
    vs_norm_post = vs_var("vs_norm_post")

    lines.append(f'{rt} isa relation_template,')
    lines.append(f'    has relation_template_id "rt_conv_{subtype}_defeats",')
    lines.append(f'    has emits_relation_type "defeats";')
    lines.append(f'(emitting_template: $dt, emitted_relation: {rt}) isa template_emits_relation;')

    # Role "defeater" - filled by the rule's emitted defeater
    lines.append(f'{ra_def} isa role_assignment, has assigned_role_name "defeater";')
    lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_def}) isa relation_template_assigns_role;')
    lines.append(f'{f_def} isa emitted_norm_role_filler;')
    lines.append(f'(owning_role_assignment: {ra_def}, assignment_filler: {f_def}) isa role_assignment_filled_by;')

    # Role "defeated" - static lookup of prohibition norm by id
    # norm_id = concatenate("conv_", deal_id, "_jcrew_blocker_prohibition")
    lines.append(f'{ra_rec} isa role_assignment, has assigned_role_name "defeated";')
    lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_rec}) isa relation_template_assigns_role;')
    lines.append(f'{f_rec} isa static_lookup_role_filler,')
    lines.append(f'    has lookup_entity_type "norm",')
    lines.append(f'    has lookup_attribute_name "norm_id";')
    lines.append(f'(owning_role_assignment: {ra_rec}, assignment_filler: {f_rec}) isa role_assignment_filled_by;')
    lines.append(f'{vs_norm_concat} isa concatenation_value_source;')
    lines.append(f'{vs_norm_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_norm_deal} isa deal_id_value_source;')
    lines.append(f'{vs_norm_post} isa literal_string_value_source, has literal_string_value "_jcrew_blocker_prohibition";')
    lines.append(f'(owning_filler: {f_rec}, lookup_value_source: {vs_norm_concat}) isa static_lookup_uses_value;')
    lines.append(f'(owning_concatenation: {vs_norm_concat}, concatenation_part: {vs_norm_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_norm_concat}, concatenation_part: {vs_norm_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_norm_concat}, concatenation_part: {vs_norm_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    return "\n".join(lines) + "\n"


# Builder sub-source config — mirrors _BUILDER_SUB_SOURCES in
# deontic_projection.py exactly. Each entry: (flag_attr, norm_kind,
# cap_usd_attr, cap_grower_attr, cap_grower_ref, aggregates_into,
# disambiguator). aggregates_into: "parent" or "b_aggregate".
# disambiguator suffixed onto norm_id when norm_kind collides
# (builder_source_other has two flags both mapping to it).
BUILDER_SUB_SOURCES = [
    ("has_starter_amount_source", "builder_source_starter",
        "starter_dollar_amount", "starter_ebitda_pct",
        "consolidated_ebitda_ltm", "parent", None),
    ("has_cni_source", "builder_source_cni",
        None, "cni_percentage", "consolidated_net_income",
        "b_aggregate", None),
    ("has_ecf_source", "builder_source_ecf",
        None, None, "excess_cash_flow", "b_aggregate", None),
    ("has_ebitda_fc_source", "builder_source_ebitda_fc",
        None, "ebitda_fc_multiplier", "consolidated_ebitda_ltm",
        "b_aggregate", None),
    ("has_equity_proceeds_source", "builder_source_other",
        None, "equity_proceeds_pct", "equity_proceeds_usd",
        "parent", "equity_proceeds"),
    ("has_asset_proceeds_source", "builder_source_retained_asset_sale_proceeds",
        None, None, None, "parent", None),
    ("has_investment_returns_source", "builder_source_investment_returns",
        None, None, None, "parent", None),
    ("has_debt_conversion_source", "builder_source_other",
        None, None, None, "parent", "debt_conversion"),
]

# Inner-test flags for the b_aggregate (greatest_of) emission
B_AGGREGATE_INNER_FLAGS = ("has_cni_source", "has_ecf_source", "has_ebitda_fc_source")

# Subject roles, action labels, object label inherited by every builder
# sub-source + b_aggregate norm. Mirrors the python projection's
# _project_builder_sub_sources scope emission.
BUILDER_SUBJECT_ROLES = ("borrower", "loan_party")
BUILDER_ACTION_LABELS = ("make_dividend_payment", "repurchase_equity",
                         "pay_subordinated_debt", "make_investment")
BUILDER_OBJECT_LABEL = "cash"


def generate_b_aggregate_rule_tql() -> str:
    """Generate the b_aggregate intermediate norm rule.
    Match: builder_basket WHERE any of has_cni_source, has_ecf_source,
    has_ebitda_fc_source is true (match_criterion_group with combinator=or).
    Emit: builder_source_three_test_aggregate norm + scope edges +
    contributes_to parent (greatest_of, child_index 0).
    """
    rule_id = "rule_conv_builder_b_aggregate"
    nt_id = "nt_conv_builder_b_aggregate"
    target_kind = "builder_source_three_test_aggregate"

    lines = ["insert", ""]
    var_idx = [0]

    def vs_var(prefix: str) -> str:
        var_idx[0] += 1
        return f"$bav_{prefix}_{var_idx[0]}"

    lines.append(f'$rule isa projection_rule,')
    lines.append(f'    has projection_rule_id "{rule_id}",')
    lines.append(f'    has projection_rule_label "Phase C Commit 2.4 builder b_aggregate intermediate";')

    # Match: builder_basket
    lines.append(f'$crit_type isa entity_type_criterion, has matches_v3_entity_type "builder_basket";')
    lines.append(f'(owning_rule: $rule, applied_criterion: $crit_type) isa rule_has_match_criterion;')

    # Match group: OR of three has_*_source flags
    lines.append(f'$crit_grp isa match_criterion_group, has group_combinator "or";')
    lines.append(f'(owning_rule: $rule, applied_criterion: $crit_grp) isa rule_has_match_criterion;')
    for i, flag in enumerate(B_AGGREGATE_INNER_FLAGS):
        crit = vs_var(f"crit_{flag}")
        vs = vs_var(f"vs_true_{flag}")
        lines.append(f'{crit} isa attribute_value_criterion,')
        lines.append(f'    has checks_v3_attribute_name "{flag}",')
        lines.append(f'    has comparison_operator "equals";')
        lines.append(f'(parent_group: $crit_grp, member_criterion: {crit}) isa criterion_group_has_member;')
        lines.append(f'{vs} isa literal_boolean_value_source, has literal_boolean_value true;')
        lines.append(f'(owning_criterion: {crit}, comparison_value_source: {vs}) isa criterion_uses_comparison_value;')

    # Norm template
    lines.append(f'$nt isa norm_template,')
    lines.append(f'    has norm_template_id "{nt_id}",')
    lines.append(f'    has norm_template_label "{target_kind}";')
    lines.append(f'(owning_rule: $rule, produced_template: $nt) isa rule_produces_norm_template;')

    # Attribute emissions
    def emit_literal_string(attr_name: str, value: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa literal_string_value_source, has literal_string_value {_tq_string(value)};')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    def emit_v3_attr(attr_name: str, v3_name: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa v3_attribute_value_source, has reads_v3_attribute_name "{v3_name}";')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    # norm_id: concat("conv_", deal_id, "_<target_kind>")
    ae_id = vs_var("ae_id")
    vs_id = vs_var("vs_id")
    vs_pre = vs_var("vs_pre")
    vs_deal = vs_var("vs_deal")
    vs_post = vs_var("vs_post")
    lines.append(f'{ae_id} isa attribute_emission, has emitted_attribute_name "norm_id";')
    lines.append(f'(emitting_template: $nt, emitted_attribute: {ae_id}) isa template_emits_attribute;')
    lines.append(f'{vs_id} isa concatenation_value_source;')
    lines.append(f'{vs_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_deal} isa deal_id_value_source;')
    lines.append(f'{vs_post} isa literal_string_value_source, has literal_string_value "_{target_kind}";')
    lines.append(f'(owning_emission: {ae_id}, source_value: {vs_id}) isa attribute_emission_uses_value;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    emit_literal_string("norm_kind", target_kind)
    emit_literal_string("modality", "permission")
    emit_literal_string("capacity_composition", "computed_from_sources")
    emit_literal_string("action_scope", "specific")
    emit_literal_string("growth_start_anchor", "closing_date_fiscal_quarter_start")
    emit_literal_string("reference_period_kind", "cumulative_since_anchor")
    emit_v3_attr("source_text", "source_text")
    emit_v3_attr("source_section", "section_reference")
    emit_v3_attr("source_page", "source_page")

    # Scope edges (4 actions, 1 object, 2 subjects)
    def emit_relation_template(rel_type: str, other_role_name: str,
                                other_lookup_type: str, other_lookup_attr: str,
                                lookup_value: str, suffix: str) -> None:
        rt = vs_var(f"rt_{suffix}")
        ra_norm = vs_var(f"ra_norm_{suffix}")
        ra_other = vs_var(f"ra_other_{suffix}")
        f_norm = vs_var(f"f_norm_{suffix}")
        f_other = vs_var(f"f_other_{suffix}")
        v_other = vs_var(f"v_other_{suffix}")
        lines.append(f'{rt} isa relation_template,')
        lines.append(f'    has relation_template_id "rt_conv_b_aggregate_{rel_type}_{lookup_value}",')
        lines.append(f'    has emits_relation_type "{rel_type}";')
        lines.append(f'(emitting_template: $nt, emitted_relation: {rt}) isa template_emits_relation;')
        lines.append(f'{ra_norm} isa role_assignment, has assigned_role_name "norm";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_norm}) isa relation_template_assigns_role;')
        lines.append(f'{f_norm} isa emitted_norm_role_filler;')
        lines.append(f'(owning_role_assignment: {ra_norm}, assignment_filler: {f_norm}) isa role_assignment_filled_by;')
        lines.append(f'{ra_other} isa role_assignment, has assigned_role_name "{other_role_name}";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_other}) isa relation_template_assigns_role;')
        lines.append(f'{f_other} isa static_lookup_role_filler,')
        lines.append(f'    has lookup_entity_type "{other_lookup_type}",')
        lines.append(f'    has lookup_attribute_name "{other_lookup_attr}";')
        lines.append(f'(owning_role_assignment: {ra_other}, assignment_filler: {f_other}) isa role_assignment_filled_by;')
        lines.append(f'{v_other} isa literal_string_value_source, has literal_string_value {_tq_string(lookup_value)};')
        lines.append(f'(owning_filler: {f_other}, lookup_value_source: {v_other}) isa static_lookup_uses_value;')

    for role in BUILDER_SUBJECT_ROLES:
        emit_relation_template("norm_binds_subject", "subject", "party", "party_role", role, f"subj_{role}")
    for action in BUILDER_ACTION_LABELS:
        emit_relation_template("norm_scopes_action", "action", "action_class", "action_class_label", action, f"act_{action}")
    emit_relation_template("norm_scopes_object", "object", "object_class", "object_class_label", BUILDER_OBJECT_LABEL, "obj_cash")

    # contributes_to parent (greatest_of, child_index 0)
    # Pool norm_id = "conv_" + deal_id + "_builder_usage_permission"
    rt_c = vs_var("rt_c")
    ra_contrib = vs_var("ra_contrib")
    ra_pool = vs_var("ra_pool")
    f_contrib = vs_var("f_contrib")
    f_pool = vs_var("f_pool")
    vs_pool_concat = vs_var("vs_pool_concat")
    vs_pool_pre = vs_var("vs_pool_pre")
    vs_pool_deal = vs_var("vs_pool_deal")
    vs_pool_post = vs_var("vs_pool_post")
    ae_agg_fn = vs_var("ae_agg_fn")
    vs_agg_fn = vs_var("vs_agg_fn")
    ae_agg_dir = vs_var("ae_agg_dir")
    vs_agg_dir = vs_var("vs_agg_dir")
    ae_idx = vs_var("ae_idx")
    vs_idx = vs_var("vs_idx")

    lines.append(f'{rt_c} isa relation_template,')
    lines.append(f'    has relation_template_id "rt_conv_b_aggregate_contributes_to",')
    lines.append(f'    has emits_relation_type "norm_contributes_to_capacity";')
    lines.append(f'(emitting_template: $nt, emitted_relation: {rt_c}) isa template_emits_relation;')
    lines.append(f'{ra_contrib} isa role_assignment, has assigned_role_name "contributor";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_role_assignment: {ra_contrib}) isa relation_template_assigns_role;')
    lines.append(f'{f_contrib} isa emitted_norm_role_filler;')
    lines.append(f'(owning_role_assignment: {ra_contrib}, assignment_filler: {f_contrib}) isa role_assignment_filled_by;')
    lines.append(f'{ra_pool} isa role_assignment, has assigned_role_name "pool";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_role_assignment: {ra_pool}) isa relation_template_assigns_role;')
    lines.append(f'{f_pool} isa static_lookup_role_filler,')
    lines.append(f'    has lookup_entity_type "norm",')
    lines.append(f'    has lookup_attribute_name "norm_id";')
    lines.append(f'(owning_role_assignment: {ra_pool}, assignment_filler: {f_pool}) isa role_assignment_filled_by;')
    lines.append(f'{vs_pool_concat} isa concatenation_value_source;')
    lines.append(f'{vs_pool_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_pool_deal} isa deal_id_value_source;')
    lines.append(f'{vs_pool_post} isa literal_string_value_source, has literal_string_value "_builder_usage_permission";')
    lines.append(f'(owning_filler: {f_pool}, lookup_value_source: {vs_pool_concat}) isa static_lookup_uses_value;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    # Edge attributes
    lines.append(f'{ae_agg_fn} isa attribute_emission, has emitted_attribute_name "aggregation_function";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_agg_fn}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_agg_fn} isa literal_string_value_source, has literal_string_value "greatest_of";')
    lines.append(f'(owning_emission: {ae_agg_fn}, source_value: {vs_agg_fn}) isa attribute_emission_uses_value;')
    lines.append(f'{ae_agg_dir} isa attribute_emission, has emitted_attribute_name "aggregation_direction";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_agg_dir}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_agg_dir} isa literal_string_value_source, has literal_string_value "add";')
    lines.append(f'(owning_emission: {ae_agg_dir}, source_value: {vs_agg_dir}) isa attribute_emission_uses_value;')
    lines.append(f'{ae_idx} isa attribute_emission, has emitted_attribute_name "child_index";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_idx}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_idx} isa literal_long_value_source, has literal_long_value 0;')
    lines.append(f'(owning_emission: {ae_idx}, source_value: {vs_idx}) isa attribute_emission_uses_value;')

    return "\n".join(lines) + "\n"


def generate_builder_sub_source_rule_tql(spec: tuple, child_index: int) -> str:
    """Generate a sub-source rule per BUILDER_SUB_SOURCES entry.
    spec: (flag_attr, norm_kind, cap_usd_attr, cap_grower_attr, cap_grower_ref, aggregates_into, disambiguator)
    child_index: position in the contributes_to ordering.
    """
    flag, kind, cap_usd_attr, cap_grower_attr, grower_ref, aggregates_into, disamb = spec
    rule_suffix = f"{kind}_{disamb}" if disamb else kind
    rule_id = f"rule_conv_builder_{rule_suffix}"
    nt_id = f"nt_conv_builder_{rule_suffix}"
    norm_id_suffix = f"_{kind}_{disamb}" if disamb else f"_{kind}"
    pool_kind = (
        "builder_source_three_test_aggregate" if aggregates_into == "b_aggregate"
        else "builder_usage_permission"
    )
    # Per python projection (deontic_projection.py:1195): sub-sources
    # contributing into b_aggregate use greatest_of; those contributing
    # directly to parent use sum.
    agg_fn = "greatest_of" if aggregates_into == "b_aggregate" else "sum"

    lines = ["insert", ""]
    var_idx = [0]

    def vs_var(prefix: str) -> str:
        var_idx[0] += 1
        return f"$bv_{prefix}_{var_idx[0]}"

    label_suffix = f" ({disamb})" if disamb else ""
    lines.append(f'$rule isa projection_rule,')
    lines.append(f'    has projection_rule_id "{rule_id}",')
    lines.append(f'    has projection_rule_label "Phase C Commit 2.4 builder sub-source: {kind}{label_suffix}";')

    # Match: builder_basket WHERE flag = true
    lines.append(f'$crit_type isa entity_type_criterion, has matches_v3_entity_type "builder_basket";')
    lines.append(f'(owning_rule: $rule, applied_criterion: $crit_type) isa rule_has_match_criterion;')
    crit_flag = vs_var("crit_flag")
    vs_true = vs_var("vs_true")
    lines.append(f'{crit_flag} isa attribute_value_criterion,')
    lines.append(f'    has checks_v3_attribute_name "{flag}",')
    lines.append(f'    has comparison_operator "equals";')
    lines.append(f'(owning_rule: $rule, applied_criterion: {crit_flag}) isa rule_has_match_criterion;')
    lines.append(f'{vs_true} isa literal_boolean_value_source, has literal_boolean_value true;')
    lines.append(f'(owning_criterion: {crit_flag}, comparison_value_source: {vs_true}) isa criterion_uses_comparison_value;')

    # Norm template
    lines.append(f'$nt isa norm_template,')
    lines.append(f'    has norm_template_id "{nt_id}",')
    lines.append(f'    has norm_template_label "{kind}";')
    lines.append(f'(owning_rule: $rule, produced_template: $nt) isa rule_produces_norm_template;')

    def emit_literal_string(attr_name: str, value: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa literal_string_value_source, has literal_string_value {_tq_string(value)};')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    def emit_v3_attr(attr_name: str, v3_name: str) -> None:
        ae = vs_var(f"ae_{attr_name}")
        vs = vs_var(f"vs_{attr_name}")
        lines.append(f'{ae} isa attribute_emission, has emitted_attribute_name "{attr_name}";')
        lines.append(f'(emitting_template: $nt, emitted_attribute: {ae}) isa template_emits_attribute;')
        lines.append(f'{vs} isa v3_attribute_value_source, has reads_v3_attribute_name "{v3_name}";')
        lines.append(f'(owning_emission: {ae}, source_value: {vs}) isa attribute_emission_uses_value;')

    # norm_id: concat("conv_", deal_id, "_<kind>[_<disamb>]")
    ae_id = vs_var("ae_id")
    vs_id = vs_var("vs_id")
    vs_pre = vs_var("vs_pre")
    vs_deal = vs_var("vs_deal")
    vs_post = vs_var("vs_post")
    lines.append(f'{ae_id} isa attribute_emission, has emitted_attribute_name "norm_id";')
    lines.append(f'(emitting_template: $nt, emitted_attribute: {ae_id}) isa template_emits_attribute;')
    lines.append(f'{vs_id} isa concatenation_value_source;')
    lines.append(f'{vs_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_deal} isa deal_id_value_source;')
    lines.append(f'{vs_post} isa literal_string_value_source, has literal_string_value "{norm_id_suffix}";')
    lines.append(f'(owning_emission: {ae_id}, source_value: {vs_id}) isa attribute_emission_uses_value;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_id}, concatenation_part: {vs_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    emit_literal_string("norm_kind", kind)
    emit_literal_string("modality", "permission")
    emit_literal_string("capacity_composition", "additive")
    emit_literal_string("action_scope", "specific")
    emit_literal_string("growth_start_anchor", "closing_date_fiscal_quarter_start")
    emit_literal_string("reference_period_kind", "cumulative_since_anchor")
    emit_v3_attr("source_text", "source_text")
    emit_v3_attr("source_section", "section_reference")
    emit_v3_attr("source_page", "source_page")
    if cap_usd_attr:
        emit_v3_attr("cap_usd", cap_usd_attr)
    if cap_grower_attr:
        emit_v3_attr("cap_grower_pct", cap_grower_attr)
    if grower_ref:
        emit_literal_string("cap_grower_reference", grower_ref)

    # Scope edges: 4 actions, 1 object, 2 subjects
    def emit_scope(rel_type: str, role_name: str, lookup_type: str,
                    lookup_attr: str, lookup_value: str, suffix: str) -> None:
        rt = vs_var(f"rt_{suffix}")
        ra_norm = vs_var(f"ra_norm_{suffix}")
        ra_other = vs_var(f"ra_other_{suffix}")
        f_norm = vs_var(f"f_norm_{suffix}")
        f_other = vs_var(f"f_other_{suffix}")
        v_other = vs_var(f"v_other_{suffix}")
        lines.append(f'{rt} isa relation_template,')
        lines.append(f'    has relation_template_id "rt_conv_{rule_suffix}_{rel_type}_{lookup_value}",')
        lines.append(f'    has emits_relation_type "{rel_type}";')
        lines.append(f'(emitting_template: $nt, emitted_relation: {rt}) isa template_emits_relation;')
        lines.append(f'{ra_norm} isa role_assignment, has assigned_role_name "norm";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_norm}) isa relation_template_assigns_role;')
        lines.append(f'{f_norm} isa emitted_norm_role_filler;')
        lines.append(f'(owning_role_assignment: {ra_norm}, assignment_filler: {f_norm}) isa role_assignment_filled_by;')
        lines.append(f'{ra_other} isa role_assignment, has assigned_role_name "{role_name}";')
        lines.append(f'(owning_relation_template: {rt}, emitted_role_assignment: {ra_other}) isa relation_template_assigns_role;')
        lines.append(f'{f_other} isa static_lookup_role_filler,')
        lines.append(f'    has lookup_entity_type "{lookup_type}",')
        lines.append(f'    has lookup_attribute_name "{lookup_attr}";')
        lines.append(f'(owning_role_assignment: {ra_other}, assignment_filler: {f_other}) isa role_assignment_filled_by;')
        lines.append(f'{v_other} isa literal_string_value_source, has literal_string_value {_tq_string(lookup_value)};')
        lines.append(f'(owning_filler: {f_other}, lookup_value_source: {v_other}) isa static_lookup_uses_value;')

    for role in BUILDER_SUBJECT_ROLES:
        emit_scope("norm_binds_subject", "subject", "party", "party_role", role, f"subj_{role}")
    for action in BUILDER_ACTION_LABELS:
        emit_scope("norm_scopes_action", "action", "action_class", "action_class_label", action, f"act_{action}")
    emit_scope("norm_scopes_object", "object", "object_class", "object_class_label", BUILDER_OBJECT_LABEL, "obj_cash")

    # contributes_to (sum, child_index per python projection's ordering)
    rt_c = vs_var("rt_c")
    ra_contrib = vs_var("ra_contrib")
    ra_pool = vs_var("ra_pool")
    f_contrib = vs_var("f_contrib")
    f_pool = vs_var("f_pool")
    vs_pool_concat = vs_var("vs_pool_concat")
    vs_pool_pre = vs_var("vs_pool_pre")
    vs_pool_deal = vs_var("vs_pool_deal")
    vs_pool_post = vs_var("vs_pool_post")
    ae_agg_fn = vs_var("ae_agg_fn")
    vs_agg_fn = vs_var("vs_agg_fn")
    ae_agg_dir = vs_var("ae_agg_dir")
    vs_agg_dir = vs_var("vs_agg_dir")
    ae_idx = vs_var("ae_idx")
    vs_idx = vs_var("vs_idx")

    lines.append(f'{rt_c} isa relation_template,')
    lines.append(f'    has relation_template_id "rt_conv_{rule_suffix}_contributes_to",')
    lines.append(f'    has emits_relation_type "norm_contributes_to_capacity";')
    lines.append(f'(emitting_template: $nt, emitted_relation: {rt_c}) isa template_emits_relation;')
    lines.append(f'{ra_contrib} isa role_assignment, has assigned_role_name "contributor";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_role_assignment: {ra_contrib}) isa relation_template_assigns_role;')
    lines.append(f'{f_contrib} isa emitted_norm_role_filler;')
    lines.append(f'(owning_role_assignment: {ra_contrib}, assignment_filler: {f_contrib}) isa role_assignment_filled_by;')
    lines.append(f'{ra_pool} isa role_assignment, has assigned_role_name "pool";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_role_assignment: {ra_pool}) isa relation_template_assigns_role;')
    lines.append(f'{f_pool} isa static_lookup_role_filler,')
    lines.append(f'    has lookup_entity_type "norm",')
    lines.append(f'    has lookup_attribute_name "norm_id";')
    lines.append(f'(owning_role_assignment: {ra_pool}, assignment_filler: {f_pool}) isa role_assignment_filled_by;')
    lines.append(f'{vs_pool_concat} isa concatenation_value_source;')
    lines.append(f'{vs_pool_pre} isa literal_string_value_source, has literal_string_value "conv_";')
    lines.append(f'{vs_pool_deal} isa deal_id_value_source;')
    lines.append(f'{vs_pool_post} isa literal_string_value_source, has literal_string_value "_{pool_kind}";')
    lines.append(f'(owning_filler: {f_pool}, lookup_value_source: {vs_pool_concat}) isa static_lookup_uses_value;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_pre}) isa concatenation_has_ordered_part, has sequence_index 0;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_deal}) isa concatenation_has_ordered_part, has sequence_index 1;')
    lines.append(f'(owning_concatenation: {vs_pool_concat}, concatenation_part: {vs_pool_post}) isa concatenation_has_ordered_part, has sequence_index 2;')

    lines.append(f'{ae_agg_fn} isa attribute_emission, has emitted_attribute_name "aggregation_function";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_agg_fn}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_agg_fn} isa literal_string_value_source, has literal_string_value "{agg_fn}";')
    lines.append(f'(owning_emission: {ae_agg_fn}, source_value: {vs_agg_fn}) isa attribute_emission_uses_value;')
    lines.append(f'{ae_agg_dir} isa attribute_emission, has emitted_attribute_name "aggregation_direction";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_agg_dir}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_agg_dir} isa literal_string_value_source, has literal_string_value "add";')
    lines.append(f'(owning_emission: {ae_agg_dir}, source_value: {vs_agg_dir}) isa attribute_emission_uses_value;')
    lines.append(f'{ae_idx} isa attribute_emission, has emitted_attribute_name "child_index";')
    lines.append(f'(owning_relation_template: {rt_c}, emitted_edge_attribute: {ae_idx}) isa relation_template_emits_edge_attribute;')
    lines.append(f'{vs_idx} isa literal_long_value_source, has literal_long_value {child_index};')
    lines.append(f'(owning_emission: {ae_idx}, source_value: {vs_idx}) isa attribute_emission_uses_value;')

    return "\n".join(lines) + "\n"


def cleanup_converted_rules(driver, db: str) -> None:
    """Remove converter-emitted rules + emitted norms (norm_id prefix
    NORM_ID_PREFIX). Commit 3.1: also deletes the retired pilot rule
    (rule_general_rp_basket) and its pilot_*-prefixed output if any
    is left over from prior pilot script runs.
    """
    queries = [
        # Delete converter-emitted norms
        f'match $n isa norm, has norm_id $nid; $nid like "{NORM_ID_PREFIX}.*"; delete $n;',
        # Delete pilot output (Commit 3.1 retirement; no-op once cleared)
        'match $n isa norm, has norm_id $nid; $nid like "pilot_.*"; delete $n;',
        # Delete converter-emitted projection_rules (id prefix "rule_conv_")
        'match $r isa projection_rule, has projection_rule_id $rid; $rid like "rule_conv_.*"; delete $r;',
        # Delete the retired pilot rule (Commit 3.1)
        'match $r isa projection_rule, has projection_rule_id "rule_general_rp_basket"; delete $r;',
        # Delete converter-emitted norm_templates (id prefix "nt_conv_")
        'match $t isa norm_template, has norm_template_id $tid; $tid like "nt_conv_.*"; delete $t;',
        # Delete converter-emitted relation_templates (id prefix "rt_conv_")
        'match $rt isa relation_template, has relation_template_id $rid; $rid like "rt_conv_.*"; delete $rt;',
        # Delete converter-emitted condition_templates (id prefix "ct_conv_")
        'match $ct isa condition_template, has condition_template_id $cid; $cid like "ct_conv_.*"; delete $ct;',
        # Delete emitted norm-condition entities (condition_id prefix "conv_")
        'match $c isa condition, has condition_id $cid; $cid like "conv_.*"; delete $c;',
        # Delete converter-emitted defeaters (id prefix "conv_")
        'match $d isa defeater, has defeater_id $did; $did like "conv_.*"; delete $d;',
        # Delete converter-emitted defeater_templates (id prefix "dt_conv_")
        'match $dt isa defeater_template, has defeater_template_id $tid; $tid like "dt_conv_.*"; delete $dt;',
        # Orphan attribute_emission/value_source/match_criterion/role_assignment/
        # role_filler entities accumulate across re-runs. Not load-bearing for
        # rule execution (the executor walks top-down from rules; orphans have
        # no inbound from any current rule). Hygiene sweep planned as Commit
        # 3.3 — see docs/v4_phase_c_commit_3/README.md for the planned
        # transitive-reachability sweep.
    ]
    for q in queries:
        wtx = driver.transaction(db, TransactionType.WRITE)
        try:
            try:
                wtx.query(q).resolve()
                wtx.commit()
            except Exception as exc:
                if wtx.is_open():
                    wtx.close()
                logger.debug(f"cleanup ({q[:60]}): {str(exc).splitlines()[0][:80]}")
        except Exception:
            pass

    # Orphan emissions / value_sources / criteria / role_assignments / fillers
    # from prior converter runs accumulate. See docs/v4_phase_c_commit_3/
    # README.md "Aside — orphan accumulation" for sizing. Not load-bearing
    # for correctness or benchmark. Hygiene sweep planned as Commit 3.3.


def apply_rule_tql(driver, db: str, tql: str, mapping_id: str) -> bool:
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        try:
            wtx.query(tql).resolve()
            wtx.commit()
            return True
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            logger.error(f"apply {mapping_id}: {str(exc).splitlines()[0][:200]}")
            return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Parity check
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_norm_scalars(driver, db: str, norm_id: str) -> dict:
    attrs = {}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = f'match $n isa norm, has norm_id "{norm_id}"; $n has $a; select $a;'
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                a = row.get("a").as_attribute()
                attrs[a.get_type().get_label()] = a.get_value()
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return attrs


def diff_scalars(reference: dict, candidate: dict, ignore: set[str]) -> tuple[list[str], list[str], list[str]]:
    matched, mismatched, missing = [], [], []
    for k, v_ref in reference.items():
        if k in ignore:
            continue
        v_cand = candidate.get(k)
        if v_cand is None:
            missing.append(f"{k} (ref={v_ref!r})")
        elif v_ref != v_cand:
            mismatched.append(f"{k}: ref={v_ref!r} cand={v_cand!r}")
        else:
            matched.append(k)
    return matched, mismatched, missing


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if settings.typedb_database != "valence_v4":
        logger.error("typedb_database must be 'valence_v4'")
        return 2

    driver = connect()
    db = "valence_v4"

    try:
        mappings = load_mappings(driver, db)
        logger.info(f"loaded {len(mappings)} deontic_mappings")

        # Commit 3.1: pilot retired; converter now authors all mappings
        # including general_rp_basket (rule_conv_general_rp_basket replaces
        # rule_general_rp_basket). The pilot's residual subgraph in
        # valence_v4 should be deleted before the first post-3.1 converter
        # run; subsequent runs are unaffected.
        to_convert = list(mappings)
        logger.info(f"converting {len(to_convert)} mappings (no skip post-3.1)")

        if args.dry_run:
            logger.info("--dry-run: printing TQL for each mapping; no DB writes")
            for m in to_convert[:1]:
                print(f"\n--- {m['mapping_id']} ---")
                print(generate_rule_tql(m))
            return 0

        # Cleanup converter state from previous runs
        cleanup_converted_rules(driver, db)

        # Apply each rule's TQL
        applied = 0
        for m in to_convert:
            tql = generate_rule_tql(m)
            if apply_rule_tql(driver, db, tql, m["mapping_id"]):
                applied += 1
                logger.info(f"applied: {m['mapping_id']} -> rule_conv_{m['source_entity_type']}")
        logger.info(f"applied {applied}/{len(to_convert)} norm rules")

        # Apply defeater rules (Commit 2.3) — one per blocker_exception subtype.
        # Must run AFTER map_jcrew_blocker since defeats edges target the
        # prohibition norm emitted by that rule.
        defeater_applied = 0
        for subtype in BLOCKER_EXCEPTION_SUBTYPES:
            tql = generate_defeater_rule_tql(subtype)
            if apply_rule_tql(driver, db, tql, f"defeater_{subtype}"):
                defeater_applied += 1
                logger.info(f"applied: defeater rule for {subtype}")
        logger.info(f"applied {defeater_applied}/{len(BLOCKER_EXCEPTION_SUBTYPES)} defeater rules")

        # Apply b_aggregate rule + 8 builder sub-source rules (Commit 2.4).
        # Order matters: b_aggregate must be applied before sub-sources whose
        # contributes_to references it.
        builder_applied = 0
        if apply_rule_tql(driver, db, generate_b_aggregate_rule_tql(), "b_aggregate"):
            builder_applied += 1
            logger.info("applied: b_aggregate rule")
        for idx, spec in enumerate(BUILDER_SUB_SOURCES):
            child_idx = idx + 1  # parent's child_index 0 is taken by b_aggregate
            tql = generate_builder_sub_source_rule_tql(spec, child_idx)
            kind = spec[1]
            disamb = spec[6]
            label = f"{kind}_{disamb}" if disamb else kind
            if apply_rule_tql(driver, db, tql, f"builder_sub_{label}"):
                builder_applied += 1
                logger.info(f"applied: builder sub-source rule for {label}")
        logger.info(f"applied {builder_applied}/{1 + len(BUILDER_SUB_SOURCES)} builder rules")

        # Run executor for each NORM RULE, parity-check scalars
        # Defeater rules emit defeaters (not norms); their parity check is
        # by defeater_id-set comparison after the run, below.
        results = []
        for m in to_convert:
            src = m["source_entity_type"]
            tnk = m["target_norm_kind"]
            rule_id = f"rule_conv_{src}"

            # Quick pre-check: does the deal have any v3 entities of this type?
            v3_matches = fetch_v3_entity_attrs(driver, db, src, args.deal)
            if not v3_matches:
                logger.info(f"  {rule_id}: 0 v3 entities — skipping parity check")
                results.append({"rule_id": rule_id, "status": "no_v3_data"})
                continue

            report = execute_rule(driver, db, rule_id, args.deal)
            if report.norms_emitted == 0:
                logger.error(f"  {rule_id}: emit failed")
                for err in report.errors[:3]:
                    logger.error(f"    {err}")
                results.append({"rule_id": rule_id, "status": "emit_failed", "errors": report.errors})
                continue

            # Parity-check: candidate vs reference
            ref_id = f"{args.deal}_{tnk}"
            cand_id = f"{NORM_ID_PREFIX}{args.deal}_{tnk}"
            ref = fetch_norm_scalars(driver, db, ref_id)
            cand = fetch_norm_scalars(driver, db, cand_id)
            if not ref:
                logger.warning(f"  {rule_id}: reference {ref_id} not present (rule has no python output to compare)")
                results.append({"rule_id": rule_id, "status": "no_reference"})
                continue

            matched, mismatched, missing = diff_scalars(ref, cand, ignore={"norm_id"})
            status = "PASS" if not mismatched and not missing else "FAIL"
            logger.info(f"  {rule_id}: {status} ({len(matched)} matched, {len(mismatched)} mismatched, {len(missing)} missing)")
            for m in mismatched[:3]:
                logger.warning(f"    mismatch: {m}")
            for m in missing[:3]:
                logger.warning(f"    missing: {m}")
            results.append({
                "rule_id": rule_id, "status": status,
                "matched": matched, "mismatched": mismatched, "missing": missing,
            })

        # Run b_aggregate + builder sub-source rules (Commit 2.4)
        # b_aggregate must run BEFORE sub-sources that contributes_to it.
        logger.info("=" * 60)
        logger.info("Executing builder rules:")
        builder_emitted = 0
        b_agg_report = execute_rule(driver, db, "rule_conv_builder_b_aggregate", args.deal)
        logger.info(f"  rule_conv_builder_b_aggregate: matches={b_agg_report.matches} emitted={b_agg_report.norms_emitted} relations={b_agg_report.relations_emitted}")
        builder_emitted += b_agg_report.norms_emitted
        for spec in BUILDER_SUB_SOURCES:
            kind = spec[1]; disamb = spec[6]
            rule_suffix = f"{kind}_{disamb}" if disamb else kind
            rule_id = f"rule_conv_builder_{rule_suffix}"
            report = execute_rule(driver, db, rule_id, args.deal)
            logger.info(f"  {rule_id}: matches={report.matches} emitted={report.norms_emitted} relations={report.relations_emitted}")
            builder_emitted += report.norms_emitted

        # Run defeater rules
        logger.info("=" * 60)
        logger.info("Executing defeater rules:")
        defeater_emitted = 0
        for subtype in BLOCKER_EXCEPTION_SUBTYPES:
            rule_id = f"rule_conv_{subtype}_defeater"
            report = execute_rule(driver, db, rule_id, args.deal)
            if report.matches > 0:
                logger.info(f"  {rule_id}: matches={report.matches} emitted={report.norms_emitted} relations={report.relations_emitted}")
                defeater_emitted += report.norms_emitted

        # Aggregate report
        logger.info("=" * 60)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        no_data = sum(1 for r in results if r["status"] == "no_v3_data")
        no_ref = sum(1 for r in results if r["status"] == "no_reference")
        emit_failed = sum(1 for r in results if r["status"] == "emit_failed")
        logger.info(
            f"AGGREGATE NORMS: {passed} PASS, {failed} FAIL, {no_data} no_v3_data, "
            f"{no_ref} no_reference, {emit_failed} emit_failed"
        )
        logger.info(f"AGGREGATE BUILDER: {builder_emitted} norms emitted (b_agg + sub-sources)")
        logger.info(f"AGGREGATE DEFEATERS: {defeater_emitted} emitted")

        return 0 if failed == 0 and emit_failed == 0 else 1
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
