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
  - All 14 new projection_rules in valence_v4 (15 mappings minus pilot)
  - Per-rule report: scalars matched / mismatched / missing
  - Aggregate report: rules-passing / rules-failing the parity check
  - Schema gaps flagged on rules whose scalar parity fails

Idempotent: re-running drops all converted rules (norm_id prefix "conv_")
and rebuilds. The pilot rule is preserved across runs.

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

# Pilot rule's source_entity_type — skipped during conversion
PILOT_SOURCE_TYPE = "general_rp_basket"

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

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════════════
# Apply rules + run executor
# ═══════════════════════════════════════════════════════════════════════════════

def cleanup_converted_rules(driver, db: str) -> None:
    """Remove all converter-emitted rules + emitted norms (norm_id prefix
    NORM_ID_PREFIX). Pilot rule (rule_general_rp_basket) is preserved."""
    queries = [
        # Delete converter-emitted norms
        f'match $n isa norm, has norm_id $nid; $nid like "{NORM_ID_PREFIX}.*"; delete $n;',
        # Delete converter-emitted projection_rules (id prefix "rule_conv_")
        'match $r isa projection_rule, has projection_rule_id $rid; $rid like "rule_conv_.*"; delete $r;',
        # Delete converter-emitted norm_templates (id prefix "nt_conv_")
        'match $t isa norm_template, has norm_template_id $tid; $tid like "nt_conv_.*"; delete $t;',
        # Delete converter-emitted relation_templates (id prefix "rt_conv_")
        'match $rt isa relation_template, has relation_template_id $rid; $rid like "rt_conv_.*"; delete $rt;',
        # Delete orphan role_assignments / role_fillers / attribute_emissions / value_sources / match_criteria
        # All such entities are only created by rule authoring; the pilot rule's
        # entities are linked to the retained pilot projection_rule, but the executor
        # walks from rule -> templates -> emissions, so orphans are unreachable.
        # However, sweep-deleting orphans is dangerous if pilot is also retained.
        # For now we only sweep entity types whose @key is namespaced by "conv_"
        # or where the entity itself can be matched orphan-style by parents.
        #
        # Orphan attribute_emission/value_source/match_criterion/role_assignment/
        # role_filler entities accumulate. They don't break correctness (executor
        # walks from rule each run; templates are recreated with fresh iids each
        # rule load). Sweeping them safely requires knowing which ones belong to
        # the pilot rule vs orphans — deferred.
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

    # Orphaned attribute_emissions / value_sources / match_criteria from
    # converter rules are harder to target precisely (no shared @key).
    # Pragmatic: delete ALL such entities EXCEPT those tied to the pilot
    # rule. Implementation: walk from each non-pilot projection_rule (none
    # remain after the delete above), so any orphan ae/vs/crit must be from
    # converter cleanup that didn't fully cascade. Sweep-delete is safe
    # while pilot is the only retained rule, since attribute_emissions/
    # value_sources/match_criteria are only created by rule authoring; the
    # pilot rule's are linked to its retained projection_rule.
    #
    # ACTUALLY: pilot rule's emissions/sources/criteria are still in the
    # graph and needed. We must NOT sweep-delete them. Skip orphan cleanup
    # for now — orphans accumulate across re-runs but don't affect
    # correctness (executor reads emissions per-rule via template walks).


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

        # Skip the pilot's source entity type to avoid re-authoring
        to_convert = [m for m in mappings if m["source_entity_type"] != PILOT_SOURCE_TYPE]
        logger.info(
            f"converting {len(to_convert)} mappings (skipping pilot's "
            f"{PILOT_SOURCE_TYPE})"
        )

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
        logger.info(f"applied {applied}/{len(to_convert)} rules")

        # Run executor for each, parity-check
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

        # Aggregate report
        logger.info("=" * 60)
        passed = sum(1 for r in results if r["status"] == "PASS")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        no_data = sum(1 for r in results if r["status"] == "no_v3_data")
        no_ref = sum(1 for r in results if r["status"] == "no_reference")
        emit_failed = sum(1 for r in results if r["status"] == "emit_failed")
        logger.info(
            f"AGGREGATE: {passed} PASS, {failed} FAIL, {no_data} no_v3_data, "
            f"{no_ref} no_reference, {emit_failed} emit_failed"
        )

        return 0 if failed == 0 and emit_failed == 0 else 1
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
