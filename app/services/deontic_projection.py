"""
Valence v4 — Projection Engine (Prompt 07 Part 3).

Reads v3 extracted entities from valence_v4, applies declarative
deontic_mapping rules, emits v4 norms with full relational structure
(scopes, conditions, provenance).

Architecture contract: §8.3 of docs/v4_deontic_architecture.md.

Graph-native by design. Mapping lookup, scope edge emission, condition
tree construction — all driven from graph state. Two entity-type-specific
concessions (flagged at module level):

  1. Builder basket sub-source emission (sub-sources live as flattened
     `has_cni_source` / `has_ecf_source` / etc. booleans on the parent
     builder_basket entity; projection must expand each active flag into
     a sub-source norm contributing to the parent via
     norm_contributes_to_capacity).
  2. J.Crew blocker exception emission (blocker_exception entities
     attached via blocker_has_exception; projection emits a defeater +
     defeats edge per exception).

Both concessions are documented inline where the code diverges from the
mapping-driven path. Alternative — generalize mapping schema with
sub-emission specs — is more work than the pilot warrants; flag for
post-pilot review when a third entity type needs the pattern.

CLI:
    py -3.12 -m app.services.deontic_projection --deal <deal_id> --dry-run
    py -3.12 -m app.services.deontic_projection --deal <deal_id>
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from typedb.driver import TransactionType

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# Data records (extracted from graph rows)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MappingRecord:
    mapping_id: str
    source_entity_type: str
    target_norm_kind: str
    target_modality: str
    default_subject_role: str                  # comma-separated
    default_action_scope_kind: str
    condition_builder_spec_ref: str            # "none" | spec name
    action_labels: list[str] = field(default_factory=list)
    object_labels: list[str] = field(default_factory=list)


@dataclass
class SpecRecord:
    condition_builder_name: str
    condition_topology_emitted: str
    condition_operator_root: str
    description: str
    # Ordered list of (predicate_label_or_concept, slot, child_index). For
    # canonical predicates the projection uses the concept directly; for
    # per-threshold ratio predicates the spec records the LABEL only and
    # projection resolves instance via construct_state_predicate_id.
    predicate_bindings: list[dict] = field(default_factory=list)


@dataclass
class V3Entity:
    """Record for a v3 extracted entity (basket or blocker) for a deal."""
    entity_type: str                           # concrete subtype (builder_basket, etc.)
    basket_id: str | None                      # for baskets
    attrs: dict                                # all owned attributes as python values
    deal_id: str | None = None                 # deal prefix for non-basket norm_id fallback


@dataclass
class ProjectionReport:
    deal_id: str
    dry_run: bool
    entities_scanned: int = 0
    entities_projected: int = 0
    norms_created: int = 0
    conditions_created: int = 0
    scope_edges_created: int = 0               # action + object + subject
    condition_refs_created: int = 0
    extracted_from_edges_created: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mapping_gaps: set[str] = field(default_factory=set)        # v3 types with no mapping
    predicate_lookup_failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"ProjectionReport(deal_id={self.deal_id}, dry_run={self.dry_run})",
            f"  entities scanned: {self.entities_scanned}",
            f"  entities projected: {self.entities_projected}",
            f"  norms created: {self.norms_created}",
            f"  conditions created: {self.conditions_created}",
            f"  scope edges (action+object+subject): {self.scope_edges_created}",
            f"  condition_references_predicate edges: {self.condition_refs_created}",
            f"  norm_extracted_from edges: {self.extracted_from_edges_created}",
        ]
        if self.mapping_gaps:
            lines.append(f"  mapping gaps (v3 types without deontic_mapping): {sorted(self.mapping_gaps)}")
        if self.predicate_lookup_failures:
            lines.append(f"  predicate lookup failures: {len(self.predicate_lookup_failures)}")
            for f in self.predicate_lookup_failures[:5]:
                lines.append(f"    {f}")
        if self.warnings:
            lines.append(f"  warnings: {len(self.warnings)}")
            for w in self.warnings[:5]:
                lines.append(f"    {w}")
        if self.errors:
            lines.append(f"  errors: {len(self.errors)}")
            for e in self.errors[:5]:
                lines.append(f"    {e}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TQL helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _attr_or_none(row, key: str):
    concept = row.get(key)
    if concept is None:
        return None
    return concept.as_attribute().get_value()


def _execute_write(driver, db_name: str, tql: str) -> None:
    tx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        tx.query(tql).resolve()
        tx.commit()
    except Exception:
        if tx.is_open():
            tx.close()
        raise


# Phase B — temporal anchor defaults by norm_kind.
# Builder-related kinds describe the Cumulative Amount and its sub-sources;
# they accumulate from closing-date fiscal-quarter-start. Ratio-gated kinds
# measure leverage at a test date (LTM). Everything else: not_applicable.
# Imported by load_ground_truth.py so YAML and projection share the same
# default surface.
_BUILDER_TEMPORAL_KINDS = frozenset({
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
})

_LTM_TEST_DATE_KINDS = frozenset({
    "ratio_rp_basket_permission",
})


def _temporal_for_norm_kind(norm_kind: str) -> tuple[str, str]:
    """Return (growth_start_anchor, reference_period_kind) defaults for a norm_kind.

    Builder-related kinds get the Cumulative Amount temporal anchor (cumulative
    since closing-date fiscal-quarter-start). Ratio-gated kinds use LTM at
    test date. All other kinds default to not_applicable.
    """
    if norm_kind in _BUILDER_TEMPORAL_KINDS:
        return ("closing_date_fiscal_quarter_start", "cumulative_since_anchor")
    if norm_kind in _LTM_TEST_DATE_KINDS:
        return ("not_applicable", "ltm_at_test_date")
    return ("not_applicable", "not_applicable")


# ═══════════════════════════════════════════════════════════════════════════════
# Graph reads: mappings, specs, v3 entities
# ═══════════════════════════════════════════════════════════════════════════════

def load_mappings(driver, db_name: str) -> dict[str, MappingRecord]:
    """Read every deontic_mapping + its action/object edges into MappingRecord."""
    mappings: dict[str, MappingRecord] = {}
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        result = tx.query("""
            match
              $m isa deontic_mapping,
                has mapping_id $mid,
                has source_entity_type $sty,
                has target_norm_kind $tnk,
                has target_modality $mod,
                has default_subject_role $dsr,
                has default_action_scope_kind $dsk,
                has condition_builder_spec_ref $cbs;
            select $mid, $sty, $tnk, $mod, $dsr, $dsk, $cbs;
        """).resolve()
        for row in result.as_concept_rows():
            mid = row.get("mid").as_attribute().get_value()
            mappings[row.get("sty").as_attribute().get_value()] = MappingRecord(
                mapping_id=mid,
                source_entity_type=row.get("sty").as_attribute().get_value(),
                target_norm_kind=row.get("tnk").as_attribute().get_value(),
                target_modality=row.get("mod").as_attribute().get_value(),
                default_subject_role=row.get("dsr").as_attribute().get_value(),
                default_action_scope_kind=row.get("dsk").as_attribute().get_value(),
                condition_builder_spec_ref=row.get("cbs").as_attribute().get_value(),
            )

        # action labels per mapping
        result = tx.query("""
            match
              $m isa deontic_mapping, has source_entity_type $sty;
              (mapping: $m, action: $a) isa mapping_targets_action;
              $a has action_class_label $lbl;
            select $sty, $lbl;
        """).resolve()
        for row in result.as_concept_rows():
            sty = row.get("sty").as_attribute().get_value()
            lbl = row.get("lbl").as_attribute().get_value()
            if sty in mappings:
                mappings[sty].action_labels.append(lbl)

        # object labels per mapping
        result = tx.query("""
            match
              $m isa deontic_mapping, has source_entity_type $sty;
              (mapping: $m, object: $o) isa mapping_targets_object;
              $o has object_class_label $lbl;
            select $sty, $lbl;
        """).resolve()
        for row in result.as_concept_rows():
            sty = row.get("sty").as_attribute().get_value()
            lbl = row.get("lbl").as_attribute().get_value()
            if sty in mappings:
                mappings[sty].object_labels.append(lbl)
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass
    return mappings


def load_specs(driver, db_name: str) -> dict[str, SpecRecord]:
    """Read every condition_builder_spec + its predicate bindings."""
    specs: dict[str, SpecRecord] = {}
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        result = tx.query("""
            match
              $s isa condition_builder_spec,
                has condition_builder_name $name,
                has condition_topology_emitted $topo,
                has condition_operator_root $op,
                has description $desc;
            select $name, $topo, $op, $desc;
        """).resolve()
        for row in result.as_concept_rows():
            name = row.get("name").as_attribute().get_value()
            specs[name] = SpecRecord(
                condition_builder_name=name,
                condition_topology_emitted=row.get("topo").as_attribute().get_value(),
                condition_operator_root=row.get("op").as_attribute().get_value(),
                description=row.get("desc").as_attribute().get_value(),
            )

        result = tx.query("""
            match
              $s isa condition_builder_spec, has condition_builder_name $name;
              (builder_spec: $s, predicate: $p) isa builder_spec_uses_predicate,
                has predicate_slot $slot,
                has child_index $idx;
              $p has state_predicate_id $pid, has state_predicate_label $plabel;
            select $name, $slot, $idx, $pid, $plabel;
        """).resolve()
        for row in result.as_concept_rows():
            name = row.get("name").as_attribute().get_value()
            if name in specs:
                specs[name].predicate_bindings.append({
                    "predicate_id": row.get("pid").as_attribute().get_value(),
                    "predicate_label": row.get("plabel").as_attribute().get_value(),
                    "slot": row.get("slot").as_attribute().get_value(),
                    "child_index": row.get("idx").as_attribute().get_value(),
                })
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass
    return specs


# Concrete v3 types projection walks (polymorphic fetch under these).
# Mirrors the `plays norm_extracted_from:fact` list in schema §4.10.
_V3_BASKET_TYPES = [
    "builder_basket", "ratio_basket", "general_rp_basket",
    "management_equity_basket", "tax_distribution_basket",
    "holdco_overhead_basket", "equity_award_basket",
    "unsub_distribution_basket", "general_investment_basket",
    "refinancing_rdp_basket", "general_rdp_basket", "ratio_rdp_basket",
    "builder_rdp_basket", "equity_funded_rdp_basket",
]
_V3_NON_BASKET_TYPES = ["jcrew_blocker"]


def load_v3_entities_for_deal(driver, db_name: str, deal_id: str) -> list[V3Entity]:
    """Polymorphic fetch of all RP-relevant v3 extracted entities for a deal.

    Baskets link to deal via provision_has_basket → rp_provision →
    deal_has_provision. jcrew_blocker links via provision_has_extracted_entity
    family (provision_has_blocker in current schema).
    """
    entities: list[V3Entity] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        # Scope basket fetches to deal via basket_id prefix rather than a
        # three-hop match. TypeDB 3.x type inference rejects the joined form
        # for role-aliased abstract relations (INF11). v3 basket_id format
        # always starts with the deal_id (see v3 extraction, e.g.
        # "6e76ed06_builder_basket"). Filter at the attribute level.
        basket_prefix = f"{deal_id}_"
        for v3_type in _V3_BASKET_TYPES:
            q = f"""
                match
                  $b isa! {v3_type}, has basket_id $bid;
                  $bid contains {_tq_string(basket_prefix)};
                select $b, $bid;
            """
            try:
                result = tx.query(q).resolve()
                for row in result.as_concept_rows():
                    bid = row.get("bid").as_attribute().get_value()
                    entities.append(V3Entity(
                        entity_type=v3_type,
                        basket_id=bid,
                        attrs=_fetch_entity_attrs(tx, None, bid),
                        deal_id=deal_id,
                    ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("v3 basket fetch failed for %s: %s", v3_type, str(exc)[:160])

        # J.Crew blocker — non-basket entity. Fetch attributes including
        # source_text/section/page so projection can populate A1-required
        # provenance on the projected prohibition norm.
        for v3_type in _V3_NON_BASKET_TYPES:
            # Use blocker_id if present (jcrew_blocker has it); fall back to
            # _instance convention for types without a distinct key attr.
            # Note: jcrew_blocker owns section_reference (not source_section);
            # referencing source_section here — even inside a try-block —
            # triggers TypeDB 3.x INF11 at type-inference time, which aborts
            # the whole query and suppresses the prohibition norm and its
            # defeaters downstream. Query only attributes the type actually
            # owns; project_entity normalises section_reference → source_section.
            q = f'''
                match
                  $e isa! {v3_type};
                  try {{ $e has blocker_id $kid; }};
                  try {{ $e has source_text $st; }};
                  try {{ $e has section_reference $sref; }};
                  try {{ $e has source_page $sp; }};
                select $e, $kid, $st, $sref, $sp;
            '''
            try:
                result = tx.query(q).resolve()
                for row in result.as_concept_rows():
                    attrs: dict = {}
                    st_c = row.get("st")
                    if st_c is not None:
                        attrs["source_text"] = st_c.as_attribute().get_value()
                    sref_c = row.get("sref")
                    if sref_c is not None:
                        attrs["section_reference"] = sref_c.as_attribute().get_value()
                    sp_c = row.get("sp")
                    if sp_c is not None:
                        attrs["source_page"] = sp_c.as_attribute().get_value()
                    entities.append(V3Entity(
                        entity_type=v3_type,
                        basket_id=None,
                        attrs=attrs,
                        deal_id=deal_id,
                    ))
            except Exception as exc:  # noqa: BLE001
                logger.warning("v3 non-basket fetch failed for %s: %s", v3_type, str(exc)[:160])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass
    return entities


def _fetch_entity_attrs(tx, concept, identifier: str | None) -> dict:
    """Read all owned attributes of an entity concept into a python dict.

    Uses a per-attribute scan rather than the $b.* wildcard fetch (which
    requires the fetch syntax variant). For the pilot this is adequate; if
    attribute counts grow large the wildcard form is a one-line swap.
    """
    # A more targeted query: iterate the per-entity attribute ownership.
    # For simplicity, we select every pattern-bound attribute of the concept
    # via dynamic introspection. Using a parametric match.
    attrs: dict = {}
    # Identify the concept via its id or via a pattern-bound identifier attr.
    if identifier is None:
        return attrs
    q = f"""
        match
          $e isa $type, has basket_id {_tq_string(identifier)};
          $e has $attr;
          $attr isa $atype;
        select $attr, $atype;
    """
    try:
        result = tx.query(q).resolve()
        for row in result.as_concept_rows():
            attr = row.get("attr").as_attribute()
            atype = row.get("atype").get_label()
            val = attr.get_value()
            # Multi-valued attributes: just keep the first for the pilot
            if atype not in attrs:
                attrs[atype] = val
    except Exception as exc:  # noqa: BLE001
        logger.debug("attr fetch degraded for %s: %s", identifier, str(exc)[:120])
    return attrs


# ═══════════════════════════════════════════════════════════════════════════════
# Projection: per-entity norm emission
# ═══════════════════════════════════════════════════════════════════════════════

def _make_norm_id(entity: V3Entity, mapping: MappingRecord) -> str:
    """Phase-A deal-agnostic norm_id scheme: <deal_id>_<categorical_kind>.

    See docs/v4_norm_id_rename_map.md for the rename rationale. The
    categorical norm_kind from the deontic_mapping doubles as the
    norm_id slug — keeps GT and projection aligned on the same string
    so A4 round-trip matching works.

    Disambiguators are NOT needed for the pilot deal: each
    (source_entity_type, deal) pair produces one norm. Multi-instance
    cases (sweep tiers, post_ipo components) are emitted by
    _project_builder_sub_sources / future helpers with
    kind-suffix disambiguators.
    """
    deal_prefix = entity.deal_id or "unknown_deal"
    return f"{deal_prefix}_{mapping.target_norm_kind}"


def _make_condition_id(norm_id: str, path: str) -> str:
    return f"{norm_id}__{path}"


def _object_is_instrument(label: str, instrument_labels: set[str]) -> bool:
    return label in instrument_labels


def _split_multiselect(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _resolve_predicate_id_for_ratio(label: str, threshold: float | None,
                                     op: str | None = "at_or_below") -> str:
    """Build state_predicate_id for a per-threshold ratio predicate via the
    canonical construction rule. Matches construct_state_predicate_id output."""
    from app.services.predicate_id import construct_state_predicate_id
    return construct_state_predicate_id(
        label=label,
        threshold_value_double=threshold,
        operator_comparison=op,
        reference_predicate_label=None,
    )


def project_entity(driver, db_name: str, entity: V3Entity,
                   mapping: MappingRecord, specs: dict[str, SpecRecord],
                   instrument_labels: set[str],
                   report: ProjectionReport, dry_run: bool) -> None:
    """Apply a mapping to one v3 entity, emitting norm + scope + condition edges.

    Mapping-driven core (steps 1-9 of §8.3 contract). Builder sub-source and
    jcrew_blocker defeater emission (steps 10-11) live in separate functions
    noted below.
    """
    nid = _make_norm_id(entity, mapping)
    attrs = entity.attrs

    cap_usd = attrs.get("cap_usd") or attrs.get("basket_amount_usd") or attrs.get("annual_cap_usd")
    cap_grower = attrs.get("cap_grower_pct") or attrs.get("basket_grower_pct") or attrs.get("annual_cap_pct_ebitda")
    # Scale coercion: v3 stores basket_grower_pct as fractions (1.0 for
    # 100% of EBITDA, 0.15 for 15%). GT authors percentages (100.0, 15.0).
    # Covenant grower-pct values span 1–200% (0.01–2.00 in fraction form);
    # legitimate percentage values are ≥ 5.0, so value ≤ 5.0 reliably
    # identifies fractions needing 100× up-scaling. Same heuristic as
    # _project_builder_sub_sources (Prompt 08 Fix 5).
    if cap_grower is not None and cap_grower <= 5.0:
        cap_grower = cap_grower * 100.0
    cap_uses_greater_of = attrs.get("cap_uses_greater_of")
    capacity_comp = attrs.get("capacity_composition") or "additive"       # reasonable default
    cap_agg = attrs.get("capacity_aggregation_function") or "n_a"
    source_section = attrs.get("section_reference") or ""
    source_page = attrs.get("source_page")
    source_text = attrs.get("source_text") or ""
    confidence = attrs.get("confidence")

    # Phase B temporal anchors — default not_applicable; builder_usage_permission
    # (the Cumulative Amount root) gets cumulative_since_anchor + closing_date_fiscal_quarter_start.
    growth_anchor, ref_period = _temporal_for_norm_kind(mapping.target_norm_kind)

    owns = [
        f'has norm_id {_tq_string(nid)}',
        f'has norm_kind {_tq_string(mapping.target_norm_kind)}',
        f'has modality {_tq_string(mapping.target_modality)}',
        f'has capacity_composition {_tq_string(capacity_comp)}',
        f'has action_scope {_tq_string(mapping.default_action_scope_kind)}',
        f'has growth_start_anchor {_tq_string(growth_anchor)}',
        f'has reference_period_kind {_tq_string(ref_period)}',
    ]
    if cap_usd is not None:
        owns.append(f"has cap_usd {float(cap_usd)}")
    if cap_grower is not None:
        owns.append(f"has cap_grower_pct {float(cap_grower)}")
    if cap_uses_greater_of is not None:
        owns.append(f'has cap_uses_greater_of {"true" if cap_uses_greater_of else "false"}')
    if source_section:
        owns.append(f'has source_section {_tq_string(str(source_section))}')
    if isinstance(source_page, int):
        owns.append(f"has source_page {source_page}")
    if source_text:
        owns.append(f'has source_text {_tq_string(str(source_text))}')
    if isinstance(confidence, (int, float)):
        owns.append(f"has confidence {float(confidence)}")

    norm_q = f"insert $n isa norm, {', '.join(owns)};"

    if dry_run:
        report.norms_created += 1
    else:
        try:
            _execute_write(driver, db_name, norm_q)
            report.norms_created += 1
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"norm insert failed for {nid}: {str(exc)[:200]}")
            return

    # Subject bindings
    for role in mapping.default_subject_role.split(","):
        role = role.strip()
        if not role:
            continue
        q = f"""
            match
              $n isa norm, has norm_id {_tq_string(nid)};
              $p isa party, has party_role {_tq_string(role)};
            insert
              (norm: $n, subject: $p) isa norm_binds_subject;
        """
        if dry_run:
            report.scope_edges_created += 1
        else:
            try:
                _execute_write(driver, db_name, q)
                report.scope_edges_created += 1
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"subject bind skipped for {nid}/{role}: {str(exc)[:160]}")

    # Action scopes
    for alabel in mapping.action_labels:
        q = f"""
            match
              $n isa norm, has norm_id {_tq_string(nid)};
              $a isa action_class, has action_class_label {_tq_string(alabel)};
            insert
              (norm: $n, action: $a) isa norm_scopes_action;
        """
        if dry_run:
            report.scope_edges_created += 1
        else:
            try:
                _execute_write(driver, db_name, q)
                report.scope_edges_created += 1
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"action scope skipped for {nid}/{alabel}: {str(exc)[:160]}")

    # Object scopes — mapping defaults UNION extracted object_class_multiselect
    extracted_objects = _split_multiselect(attrs.get("object_class_multiselect"))
    object_labels = set(mapping.object_labels) | set(extracted_objects)
    for olabel in object_labels:
        if _object_is_instrument(olabel, instrument_labels):
            q = f"""
                match
                  $n isa norm, has norm_id {_tq_string(nid)};
                  $oc isa instrument_class, has instrument_class_label {_tq_string(olabel)};
                insert
                  (norm: $n, instrument: $oc) isa norm_scopes_instrument;
            """
        else:
            q = f"""
                match
                  $n isa norm, has norm_id {_tq_string(nid)};
                  $oc isa object_class, has object_class_label {_tq_string(olabel)};
                insert
                  (norm: $n, object: $oc) isa norm_scopes_object;
            """
        if dry_run:
            report.scope_edges_created += 1
        else:
            try:
                _execute_write(driver, db_name, q)
                report.scope_edges_created += 1
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"object scope skipped for {nid}/{olabel}: {str(exc)[:160]}")

    # Condition tree emission — only if spec != "none" AND partial_applicability
    spec_name = mapping.condition_builder_spec_ref
    partial_app = attrs.get("partial_applicability", False)
    if spec_name and spec_name != "none" and partial_app:
        spec = specs.get(spec_name)
        if spec is None:
            report.warnings.append(f"condition_builder_spec '{spec_name}' not found for {nid}")
        else:
            _emit_condition_tree(driver, db_name, nid, spec, entity, report, dry_run)

    # Segment membership: norm_in_segment edge. Emit when the extracted norm's
    # source_section starts with any seeded segment_prefix_pattern. Source
    # attribute priority: norm.source_section (already owned on the projected
    # norm) → entity.attrs["section_reference"] or "source_section".
    section_ref = (
        attrs.get("section_reference")
        or attrs.get("source_section")
        or source_section
    )
    if section_ref:
        q_seg = f"""
            match
              $n isa norm, has norm_id {_tq_string(nid)};
              $s isa document_segment_type, has segment_type_id $sid,
                has segment_prefix_pattern $prefix;
              {_tq_string(str(section_ref))} like $prefix;
            insert
              (norm: $n, segment: $s) isa norm_in_segment;
        """
        # TypeDB 3.x `like` is regex; prefix-containment via `contains`
        # reversed (pattern is the constant, attr is the variable) is easier.
        q_seg = f"""
            match
              $n isa norm, has norm_id {_tq_string(nid)};
              $s isa document_segment_type,
                has segment_prefix_pattern $prefix;
              {_tq_string(str(section_ref))} contains $prefix;
            insert
              (norm: $n, segment: $s) isa norm_in_segment;
        """
        if dry_run:
            pass
        else:
            try:
                _execute_write(driver, db_name, q_seg)
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(
                    f"norm_in_segment skipped for {nid}: {str(exc)[:160]}"
                )

    # Builder basket sub-source emission (Fix 5) — per-entity-type concession
    # documented in the module docstring. Only fires when the entity is a
    # builder_basket with has_*_source flags populated.
    if entity.entity_type == "builder_basket" and not dry_run:
        _project_builder_sub_sources(
            driver, db_name, nid, entity, mapping, report,
        )

    # J.Crew blocker defeater emission (Fix 6) — per-entity-type concession.
    # Each blocker_exception attached to the jcrew_blocker becomes a defeater
    # entity with a defeats edge to this prohibition norm.
    if entity.entity_type == "jcrew_blocker" and not dry_run:
        _project_jcrew_defeaters(driver, db_name, nid, entity, report)
    elif entity.entity_type == "builder_basket" and dry_run:
        # Count sub-sources for the dry-run report
        source_flags = {
            "starter":             entity.attrs.get("has_starter_amount_source"),
            "cni":                 entity.attrs.get("has_cni_source"),
            "ecf":                 entity.attrs.get("has_ecf_source"),
            "ebitda_fc":           entity.attrs.get("has_ebitda_fc_source"),
            "equity_proceeds":    entity.attrs.get("has_equity_proceeds_source"),
            "asset_proceeds":     entity.attrs.get("has_asset_proceeds_source"),
            "investment_returns": entity.attrs.get("has_investment_returns_source"),
            "debt_conversion":    entity.attrs.get("has_debt_conversion_source"),
        }
        active = [k for k, v in source_flags.items() if v]
        report.norms_created += len(active)  # dry-run report includes planned sub-source norms

    # Provenance: norm_extracted_from:fact
    if entity.basket_id:
        q = f"""
            match
              $n isa norm, has norm_id {_tq_string(nid)};
              $fact isa! {entity.entity_type}, has basket_id {_tq_string(entity.basket_id)};
            insert
              (norm: $n, fact: $fact) isa norm_extracted_from;
        """
        if dry_run:
            report.extracted_from_edges_created += 1
        else:
            try:
                _execute_write(driver, db_name, q)
                report.extracted_from_edges_created += 1
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"norm_extracted_from skipped for {nid}: {str(exc)[:160]}")


def _emit_condition_tree(driver, db_name: str, norm_id: str, spec: SpecRecord,
                          entity: V3Entity, report: ProjectionReport,
                          dry_run: bool) -> None:
    """Emit condition entities + edges per spec's topology.

    Topologies supported:
      - atomic: single condition node with condition_references_predicate
      - or_of_atomics / and_of_atomics: root + N atomic children

    The or_of_and_of_atomics topology (Strategy A flattened) is emitted by
    an entity-specific branch that splits the spec's atomic leaves into two
    AND conjunctions sharing one member. Only used for §2.10(c)(iv)-style
    product-line exemptions in the pilot.
    """
    topology = spec.condition_topology_emitted
    operator = spec.condition_operator_root
    root_cid = _make_condition_id(norm_id, "c0")

    # Root
    owns = [
        f'has condition_id {_tq_string(root_cid)}',
        f'has condition_operator {_tq_string(operator)}',
        f'has condition_topology {_tq_string(topology)}',
    ]
    root_q = f"insert $c isa condition, {', '.join(owns)};"
    if dry_run:
        report.conditions_created += 1
    else:
        try:
            _execute_write(driver, db_name, root_q)
            report.conditions_created += 1
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"condition root insert failed for {norm_id}: {str(exc)[:200]}")
            return

    # Link norm to root
    link_q = f"""
        match
          $n isa norm, has norm_id {_tq_string(norm_id)};
          $c isa condition, has condition_id {_tq_string(root_cid)};
        insert
          (norm: $n, root: $c) isa norm_has_condition;
    """
    if not dry_run:
        try:
            _execute_write(driver, db_name, link_q)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"norm_has_condition skipped for {norm_id}: {str(exc)[:160]}")

    # Per-topology atomic leaf emission
    if topology == "atomic":
        # The single atomic IS the root — attach its predicate reference.
        if not spec.predicate_bindings:
            report.warnings.append(f"atomic spec {spec.condition_builder_name} has no predicate binding")
            return
        binding = spec.predicate_bindings[0]
        _attach_predicate(driver, db_name, root_cid, binding["predicate_id"],
                          entity, report, dry_run)
        return

    if topology in ("or_of_atomics", "and_of_atomics"):
        _emit_flat_compound(driver, db_name, root_cid, spec, entity, report, dry_run)
        return

    if topology == "or_of_and_of_atomics":
        _emit_strategy_a_flattened(driver, db_name, root_cid, spec, entity, report, dry_run)
        return

    report.warnings.append(
        f"unrecognized topology {topology!r} for norm {norm_id}; no children emitted"
    )


def _emit_flat_compound(driver, db_name, root_cid, spec, entity, report, dry_run):
    """Root is OR or AND; children are atomic leaves (one per predicate binding
    plus one resolved-at-projection per-basket ratio threshold leaf if the
    spec is ratio_with_no_worse)."""
    # Primary leaf — for ratio_with_no_worse, resolve threshold from entity.
    # Build the list of (predicate_id_or_None, label) to emit as atomic children.
    leaves: list[dict] = []

    # Primary per-threshold leaf for ratio_with_no_worse
    if spec.condition_builder_name == "ratio_with_no_worse":
        threshold = (entity.attrs.get("ratio_threshold")
                     or entity.attrs.get("asset_proceeds_ratio_threshold"))
        label = "first_lien_net_leverage_at_or_below"
        if threshold is not None:
            pid = _resolve_predicate_id_for_ratio(label, float(threshold), "at_or_below")
            leaves.append({"predicate_id": pid, "child_index": 0})
        else:
            report.warnings.append(
                f"ratio_with_no_worse: no threshold extracted for {root_cid}; primary leaf skipped"
            )

    # Attach any seeded bindings (e.g., secondary pro_forma_no_worse)
    for b in sorted(spec.predicate_bindings, key=lambda b: b["child_index"]):
        leaves.append({"predicate_id": b["predicate_id"], "child_index": b["child_index"]})

    for leaf in leaves:
        idx = leaf["child_index"]
        child_cid = f"{root_cid}_{idx}"
        child_q = f"""
            insert
              $c isa condition,
                has condition_id {_tq_string(child_cid)},
                has condition_operator "atomic";
        """
        if dry_run:
            report.conditions_created += 1
        else:
            try:
                _execute_write(driver, db_name, child_q)
                report.conditions_created += 1
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"condition child insert skipped for {child_cid}: {str(exc)[:160]}")
                continue

        # Parent-child link
        link_q = f"""
            match
              $parent isa condition, has condition_id {_tq_string(root_cid)};
              $child isa condition, has condition_id {_tq_string(child_cid)};
            insert
              (parent: $parent, child: $child) isa condition_has_child,
                has child_index {idx};
        """
        if not dry_run:
            try:
                _execute_write(driver, db_name, link_q)
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(f"condition_has_child skipped for {child_cid}: {str(exc)[:160]}")

        # Predicate reference
        _attach_predicate(driver, db_name, child_cid, leaf["predicate_id"],
                          entity, report, dry_run)


def _emit_strategy_a_flattened(driver, db_name, root_cid, spec, entity, report, dry_run):
    """or_of_and_of_atomics: two AND branches, each a conjunction of the spec's
    primary (shared) atomic with one of the ratio/no-worse disjuncts. Pilot
    stub — not exercised for Duck Creek RP scope beyond §2.10(c)(iv), which
    is an asset-sale covenant (out of current scope)."""
    report.warnings.append(
        f"or_of_and_of_atomics emission for {root_cid} deferred — stub in pilot; "
        f"spec {spec.condition_builder_name} requires two AND branches with "
        f"threshold resolved per-entity"
    )


def _attach_predicate(driver, db_name, cid: str, pid: str,
                      entity: V3Entity, report: ProjectionReport, dry_run: bool) -> None:
    """Emit condition_references_predicate for an atomic condition node."""
    q = f"""
        match
          $c isa condition, has condition_id {_tq_string(cid)};
          $p isa state_predicate, has state_predicate_id {_tq_string(pid)};
        insert
          (condition: $c, predicate: $p) isa condition_references_predicate;
    """
    if dry_run:
        report.condition_refs_created += 1
        return
    try:
        _execute_write(driver, db_name, q)
        report.condition_refs_created += 1
    except Exception as exc:  # noqa: BLE001
        # If the predicate_id doesn't exist in the DB, this will fail. Track it.
        report.predicate_lookup_failures.append(
            f"  cid={cid} pid={pid}: {str(exc)[:200]}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestration
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# Per-entity-type concessions (documented complicity — see module docstring)
# ═══════════════════════════════════════════════════════════════════════════════


# builder_basket sub-source specs. Each entry describes one projected sub-
# source norm: the v3 flag it triggers on, the norm_kind it emits (matching
# ground-truth builder_source_* conventions), and the cap/grower attributes
# to pull from the parent builder_basket's extracted attrs.
#
# Aggregation: three of these (cni, ecf, ebitda_fc) feed the "greatest_of"
# inner aggregate via a builder_source_b_aggregate intermediate; the remaining
# sources contribute to the parent directly via sum. Builder projects as:
#
#   Cumulative Amount (parent, greatest_of)
#     ├── starter_source (sum of the inner group)
#     └── b_aggregate (greatest_of)
#         ├── cni_source
#         ├── ecf_source
#         └── ebitda_fc_source
#     └── equity_proceeds_source (sum)
#     └── asset_proceeds_source  (sum)
#     └── investment_returns_source (sum)
#     └── debt_conversion_source (sum)
_BUILDER_SUB_SOURCES = [
    # (flag_attr, norm_kind, cap_usd_attr, cap_grower_attr, cap_grower_ref, aggregates_into)
    # Phase-A renames:
    #   builder_source_b_aggregate         -> builder_source_three_test_aggregate
    #   builder_source_retained_asset_sale -> builder_source_retained_asset_sale_proceeds
    # See docs/v4_norm_id_rename_map.md
    ("has_starter_amount_source", "builder_source_starter",
        "starter_dollar_amount", "starter_ebitda_pct", "consolidated_ebitda_ltm", "parent"),
    ("has_cni_source", "builder_source_cni",
        None, "cni_percentage", "consolidated_net_income", "b_aggregate"),
    ("has_ecf_source", "builder_source_ecf",
        None, None, "excess_cash_flow", "b_aggregate"),
    ("has_ebitda_fc_source", "builder_source_ebitda_fc",
        None, "ebitda_fc_multiplier", "consolidated_ebitda_ltm", "b_aggregate"),
    ("has_equity_proceeds_source", "builder_source_other",
        None, "equity_proceeds_pct", "equity_proceeds_usd", "parent"),
    ("has_asset_proceeds_source", "builder_source_retained_asset_sale_proceeds",
        None, None, None, "parent"),
    ("has_investment_returns_source", "builder_source_investment_returns",
        None, None, None, "parent"),
    ("has_debt_conversion_source", "builder_source_other",
        None, None, None, "parent"),
]


# Two builder sub-source kinds (`builder_source_other`) collide for a
# single deal because both has_equity_proceeds_source and
# has_debt_conversion_source map to it. Disambiguator below keeps each
# norm_id unique within the deal while the kind stays categorical.
_BUILDER_OTHER_DISAMBIGUATOR: dict[str, str] = {
    "has_equity_proceeds_source": "equity_proceeds",
    "has_debt_conversion_source": "debt_conversion",
}


def _project_builder_sub_sources(driver, db_name: str, parent_nid: str,
                                  entity: V3Entity, mapping: MappingRecord,
                                  report: ProjectionReport) -> None:
    """Emit one sub-source norm per active has_*_source flag on the extracted
    builder_basket, plus a b_aggregate intermediate norm grouping the three
    "greatest of" ratio tests (CNI, ECF, EBITDA-FC).

    Each sub-source contributes_to either the parent builder norm (sum) or
    the b_aggregate (greatest_of). The b_aggregate contributes to the parent
    via greatest_of, reproducing the ground-truth topology of the Cumulative
    Amount.
    """
    attrs = entity.attrs
    deal_id = entity.deal_id or "unknown_deal"

    # 1. Emit b_aggregate intermediate — only when any of the 3 inner sources
    #    is active. Phase-A: kind renamed builder_source_b_aggregate ->
    #    builder_source_three_test_aggregate; norm_id is the categorical
    #    <deal_id>_<kind>.
    inner_flags = ("has_cni_source", "has_ecf_source", "has_ebitda_fc_source")
    needs_b_aggregate = any(attrs.get(f) for f in inner_flags)
    # Pull parent builder provenance — sub-sources inherit it so structural
    # completeness (A1) holds. Without this they'd lack source_text/section/page.
    parent_text = attrs.get("source_text") or ""
    parent_section = attrs.get("section_reference") or attrs.get("source_section") or ""
    parent_page = attrs.get("source_page")
    subject_roles = [r.strip() for r in (mapping.default_subject_role or "").split(",") if r.strip()]

    b_agg_nid = f"{deal_id}_builder_source_three_test_aggregate"
    if needs_b_aggregate:
        agg_owns = [
            f'has norm_id {_tq_string(b_agg_nid)}',
            'has norm_kind "builder_source_three_test_aggregate"',
            'has modality "permission"',
            'has capacity_composition "computed_from_sources"',
            'has action_scope "specific"',
            'has growth_start_anchor "closing_date_fiscal_quarter_start"',
            'has reference_period_kind "cumulative_since_anchor"',
        ]
        if parent_text:
            agg_owns.append(f'has source_text {_tq_string(str(parent_text))}')
        if parent_section:
            agg_owns.append(f'has source_section {_tq_string(str(parent_section))}')
        if isinstance(parent_page, int):
            agg_owns.append(f"has source_page {parent_page}")
        agg_q = f"insert $n isa norm, {', '.join(agg_owns)};"
        try:
            _execute_write(driver, db_name, agg_q)
            report.norms_created += 1
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"b_aggregate emit failed: {str(exc)[:160]}")

        # Subject bindings — inherit from parent's default_subject_role so
        # norm_is_structurally_complete passes.
        for role in subject_roles:
            q_sub = f"""
                match
                  $n isa norm, has norm_id {_tq_string(b_agg_nid)};
                  $p isa party, has party_role {_tq_string(role)};
                insert
                  (norm: $n, subject: $p) isa norm_binds_subject;
            """
            try:
                _execute_write(driver, db_name, q_sub)
            except Exception:
                pass

        # b_aggregate also inherits parent-scope actions+object so its tuple
        # matches the corresponding GT builder_source_b_aggregate norm.
        for action_label in ("make_dividend_payment", "repurchase_equity",
                              "pay_subordinated_debt", "make_investment"):
            q_act = f"""
                match
                  $n isa norm, has norm_id {_tq_string(b_agg_nid)};
                  $a isa action_class, has action_class_label {_tq_string(action_label)};
                insert
                  (norm: $n, action: $a) isa norm_scopes_action;
            """
            try:
                _execute_write(driver, db_name, q_act)
            except Exception:  # noqa: BLE001
                pass
        q_obj = f"""
            match
              $n isa norm, has norm_id {_tq_string(b_agg_nid)};
              $o isa object_class, has object_class_label "cash";
            insert
              (norm: $n, object: $o) isa norm_scopes_object;
        """
        try:
            _execute_write(driver, db_name, q_obj)
        except Exception:  # noqa: BLE001
            pass

        # b_aggregate contributes to parent with greatest_of, add direction
        edge_q = f"""
            match
              $p isa norm, has norm_id {_tq_string(parent_nid)};
              $c isa norm, has norm_id {_tq_string(b_agg_nid)};
            insert
              (contributor: $c, pool: $p) isa norm_contributes_to_capacity,
                has aggregation_function "greatest_of",
                has aggregation_direction "add",
                has child_index 0;
        """
        try:
            _execute_write(driver, db_name, edge_q)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"b_aggregate edge failed: {str(exc)[:160]}")

    # 2. Emit per-source norms + contribution edges
    idx = 1
    for flag, kind, cap_usd_attr, cap_grower_attr, grower_ref, aggregates_into in _BUILDER_SUB_SOURCES:
        if not attrs.get(flag):
            continue

        # Phase-A: norm_id is <deal_id>_<categorical_kind>, with a
        # categorical disambiguator only when two flags map to the same
        # kind (the two `builder_source_other` cases).
        if kind == "builder_source_other":
            disambiguator = _BUILDER_OTHER_DISAMBIGUATOR.get(flag, flag)
            sub_nid = f"{deal_id}_{kind}_{disambiguator}"
        else:
            sub_nid = f"{deal_id}_{kind}"
        cap_usd = attrs.get(cap_usd_attr) if cap_usd_attr else None
        cap_grower = attrs.get(cap_grower_attr) if cap_grower_attr else None
        # cap_grower_pct is stored as percentage (0–100), but v3 extraction
        # stores some as fractions (e.g., 0.5 for 50%, 1.4 for 140%). Scale up.
        if cap_grower is not None and cap_grower <= 5.0:
            cap_grower = cap_grower * 100.0

        # Phase B — all builder sub-sources accumulate from closing-date fiscal-quarter-start.
        owns = [
            f'has norm_id {_tq_string(sub_nid)}',
            f'has norm_kind {_tq_string(kind)}',
            'has modality "permission"',
            'has capacity_composition "additive"',
            'has action_scope "specific"',
            'has growth_start_anchor "closing_date_fiscal_quarter_start"',
            'has reference_period_kind "cumulative_since_anchor"',
        ]
        if cap_usd is not None:
            owns.append(f"has cap_usd {float(cap_usd)}")
        if cap_grower is not None:
            owns.append(f"has cap_grower_pct {float(cap_grower)}")
        if grower_ref:
            owns.append(f'has cap_grower_reference {_tq_string(grower_ref)}')
        # Sub-sources inherit parent builder's provenance so A1 structural
        # completeness passes (source_text/section/page required).
        if parent_text:
            owns.append(f'has source_text {_tq_string(str(parent_text))}')
        if parent_section:
            owns.append(f'has source_section {_tq_string(str(parent_section))}')
        if isinstance(parent_page, int):
            owns.append(f"has source_page {parent_page}")

        norm_q = f"insert $n isa norm, {', '.join(owns)};"
        try:
            _execute_write(driver, db_name, norm_q)
            report.norms_created += 1
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"builder sub-source {kind} emit failed: {str(exc)[:160]}")
            continue

        # Subject bindings — inherit parent's default_subject_role for A1.
        for role in subject_roles:
            q_sub = f"""
                match
                  $n isa norm, has norm_id {_tq_string(sub_nid)};
                  $p isa party, has party_role {_tq_string(role)};
                insert
                  (norm: $n, subject: $p) isa norm_binds_subject;
            """
            try:
                _execute_write(driver, db_name, q_sub)
            except Exception:
                pass

        # Scope edges — inherit from the builder parent so the structural
        # tuple (norm_kind, modality, primary_action, primary_object) matches
        # ground-truth authoring. GT sub-sources bind all 4 Cumulative-Amount
        # action classes (make_dividend_payment, repurchase_equity,
        # pay_subordinated_debt, make_investment) and scope cash. Tuple
        # lookup is primary-first-sorted → sorted[0] is make_dividend_payment,
        # object is cash.
        for action_label in ("make_dividend_payment", "repurchase_equity",
                              "pay_subordinated_debt", "make_investment"):
            q_act = f"""
                match
                  $n isa norm, has norm_id {_tq_string(sub_nid)};
                  $a isa action_class, has action_class_label {_tq_string(action_label)};
                insert
                  (norm: $n, action: $a) isa norm_scopes_action;
            """
            try:
                _execute_write(driver, db_name, q_act)
            except Exception as exc:  # noqa: BLE001
                report.warnings.append(
                    f"builder sub-source action edge {kind}/{action_label}: {str(exc)[:160]}"
                )
        q_obj = f"""
            match
              $n isa norm, has norm_id {_tq_string(sub_nid)};
              $o isa object_class, has object_class_label "cash";
            insert
              (norm: $n, object: $o) isa norm_scopes_object;
        """
        try:
            _execute_write(driver, db_name, q_obj)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(
                f"builder sub-source object edge {kind}: {str(exc)[:160]}"
            )

        # Contribute: to b_aggregate (greatest_of) for CNI/ECF/EBITDA-FC,
        # to parent (sum) for others.
        target_nid = b_agg_nid if aggregates_into == "b_aggregate" else parent_nid
        agg_fn = "greatest_of" if aggregates_into == "b_aggregate" else "sum"
        edge_q = f"""
            match
              $p isa norm, has norm_id {_tq_string(target_nid)};
              $c isa norm, has norm_id {_tq_string(sub_nid)};
            insert
              (contributor: $c, pool: $p) isa norm_contributes_to_capacity,
                has aggregation_function {_tq_string(agg_fn)},
                has aggregation_direction "add",
                has child_index {idx};
        """
        try:
            _execute_write(driver, db_name, edge_q)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(
                f"builder sub-source contribution edge failed for {kind}: {str(exc)[:160]}"
            )
        idx += 1


def _project_jcrew_defeaters(driver, db_name: str, prohibition_nid: str,
                               entity: V3Entity, report: ProjectionReport) -> None:
    """Emit one defeater per v3 blocker_exception linked to the jcrew_blocker.

    Each blocker_exception's concrete subtype names the defeater_type
    (ordinary_course_exception, nonexclusive_license_exception, etc.).
    The defeater's defeats edge terminates at the J.Crew prohibition norm
    so later reasoning can ask "what exceptions would block this
    prohibition from firing?"

    Note: the current blocker_exception subtypes map directly to
    defeater_type labels. If future extraction adds new exception
    subtypes, they'll flow through automatically via this generic query.
    """
    # Fetch distinct exceptions — query on the abstract blocker_exception
    # parent rather than each subtype. `isa! blocker_exception` would fail
    # since blocker_exception is itself abstract; use `isa` and dedupe by
    # exception_id.
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = f"""
            match
              $b isa jcrew_blocker;
              (blocker: $b, exception: $e) isa blocker_has_exception;
              $e has exception_id $eid;
              try {{ $e has exception_name $ename; }};
              try {{ $e has source_text $stext; }};
              try {{ $e has source_page $spage; }};
              try {{ $e has section_reference $sref; }};
            select $e, $eid, $ename, $stext, $spage, $sref;
        """
        rows = list(tx.query(q).resolve().as_concept_rows())
    except Exception as exc:  # noqa: BLE001
        report.warnings.append(f"jcrew defeater fetch failed: {str(exc)[:200]}")
        return
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Dedupe by exception_id (the polymorphic match returns each instance
    # once per matching isa type — abstract + concrete = 2 rows).
    seen: set[str] = set()
    unique: list[dict] = []
    for r in rows:
        eid = r.get("eid").as_attribute().get_value()
        if eid in seen:
            continue
        seen.add(eid)
        concept = r.get("e")
        concrete_type = concept.get_type().get_label() if hasattr(concept, "get_type") else None
        name_c = r.get("ename")
        text_c = r.get("stext")
        page_c = r.get("spage")
        sref_c = r.get("sref")
        unique.append({
            "eid": eid,
            "concrete_type": concrete_type,
            "name": name_c.as_attribute().get_value() if name_c else None,
            "text": text_c.as_attribute().get_value() if text_c else None,
            "page": page_c.as_attribute().get_value() if page_c else None,
            "sref": sref_c.as_attribute().get_value() if sref_c else None,
        })

    # For each exception, emit a defeater + defeats edge.
    # Phase-A: defeater_id is <deal_id>_jcrew_<subtype>_exception per the
    # rename map (matches GT defeater_ids). The exception's concrete v3
    # subtype (e.g., ordinary_course_exception) IS the categorical slug
    # already; just prefix with the deal_id.
    deal_id = entity.deal_id or "unknown_deal"
    for exc_data in unique:
        concrete = exc_data["concrete_type"] or "exception"
        # concrete may be a Label object; extract .name if so
        concrete_name = concrete.name if hasattr(concrete, "name") else str(concrete)
        defeater_id = f"{deal_id}_jcrew_{concrete_name}"
        # defeater_type is a closed taxonomy {exception, lex_specialis, ...}
        # — concrete_name is e.g. ordinary_course_exception which is NOT
        # one of those. Use the literal taxonomy value.
        defeater_type = "exception"
        owns = [
            f'has defeater_id {_tq_string(defeater_id)}',
            f'has defeater_type {_tq_string(defeater_type)}',
        ]
        if exc_data["name"]:
            owns.append(f'has defeater_name {_tq_string(exc_data["name"])}')
        if exc_data["text"]:
            owns.append(f'has source_text {_tq_string(str(exc_data["text"]))}')
        if exc_data["sref"]:
            owns.append(f'has source_section {_tq_string(str(exc_data["sref"]))}')
        if isinstance(exc_data["page"], int):
            owns.append(f"has source_page {exc_data['page']}")

        q_def = f"insert $d isa defeater, {', '.join(owns)};"
        try:
            _execute_write(driver, db_name, q_def)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"defeater insert failed for {defeater_id}: {str(exc)[:160]}")
            continue

        # defeats edge
        q_def_edge = f"""
            match
              $d isa defeater, has defeater_id {_tq_string(defeater_id)};
              $n isa norm, has norm_id {_tq_string(prohibition_nid)};
            insert
              (defeater: $d, defeated: $n) isa defeats;
        """
        try:
            _execute_write(driver, db_name, q_def_edge)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"defeats edge insert failed for {defeater_id}: {str(exc)[:160]}")


def clear_v4_projection_for_deal(driver, db_name: str, deal_id: str) -> dict:
    """Remove existing v4 projection output for a deal before re-projecting.

    Preserves v3 extracted entities (rp_basket, jcrew_blocker, etc.) — those
    are the $12.95 extraction artifact and must NOT be dropped. Only v4 norms,
    conditions, defeaters, and their relations are cleared, scoped to the
    specific deal via norm_id prefix.

    Returns counts of what was removed per type.
    """
    counts = {"norms": 0, "conditions": 0, "defeaters": 0}

    # Count + delete norms and any relations/conditions anchored to them.
    # Norm IDs produced by project_entity start with the deal_id + "_rp_" or
    # include it as a prefix. We match via `contains deal_id` on norm_id, which
    # catches both scheme variants.
    nid_pattern = deal_id

    # Run each delete in its own transaction so a failure on one (e.g., a
    # type that doesn't exist in this schema yet) doesn't abort the others.
    clear_queries = [
        ("conditions", f'''
            match
              $c isa condition, has condition_id $cid;
              $cid contains "{nid_pattern}";
            delete $c;
        '''),
        ("norms", f'''
            match
              $n isa norm, has norm_id $nid;
              $nid contains "{nid_pattern}";
            delete $n;
        '''),
        # Defeaters emitted in Fix 6 carry defeater_id starting with the
        # deal prefix. If the defeater_id attribute isn't defined yet
        # (pre-Fix-6 schemas) this query fails with INF2 and is skipped.
        ("defeaters", f'''
            match
              $d isa defeater, has defeater_id $did;
              $did contains "{nid_pattern}";
            delete $d;
        '''),
    ]
    for label, q in clear_queries:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(q).resolve()
            tx.commit()
        except Exception as exc:  # noqa: BLE001
            if tx.is_open():
                tx.close()
            msg = str(exc)
            # Schema-absent errors are expected when a downstream Fix hasn't
            # landed yet; swallow without losing the rest of the clear.
            if "INF2" in msg or "not found" in msg:
                logger.debug("clear/%s skipped (type not in schema): %s", label, msg[:120])
            else:
                logger.warning("clear/%s failed: %s", label, msg[:200])

    # Post-clear inventory (for reporting)
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query(f'match $n isa norm, has norm_id $nid; $nid contains "{nid_pattern}"; select $n;').resolve()
        counts["norms_remaining"] = len(list(r.as_concept_rows()))
    except Exception:
        counts["norms_remaining"] = -1
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return counts


def project_deal(driver, db_name: str, deal_id: str, dry_run: bool = False) -> ProjectionReport:
    """Project all v3 extracted entities for a deal into v4 norms.

    Steps:
      1. Load mappings + specs + instrument_label set from graph
      2. Non-dry-run: clear existing v4 projection output for the deal
         (preserves v3 extraction)
      3. Polymorphic fetch v3 entities for deal
      4. Per entity: look up mapping, apply projection
      5. Report structural completeness + predicate lookup failures

    Returns the ProjectionReport summary regardless of dry_run.
    """
    report = ProjectionReport(deal_id=deal_id, dry_run=dry_run)
    logger.info("Loading mappings + specs from %s", db_name)
    mappings = load_mappings(driver, db_name)
    specs = load_specs(driver, db_name)
    logger.info("  mappings=%d specs=%d", len(mappings), len(specs))

    # Instrument labels for scope dispatch
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query("match $t sub instrument_class; select $t;").resolve()
        instrument_labels = {row.get("t").get_label() for row in r.as_concept_rows()}
        instrument_labels.discard("instrument_class")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass

    # Clear previous projection output so re-runs are idempotent. v3 extraction
    # entities (rp_basket, jcrew_blocker) are not touched.
    if not dry_run:
        clear_counts = clear_v4_projection_for_deal(driver, db_name, deal_id)
        logger.info("cleared previous v4 projection output: %s", clear_counts)

    logger.info("Fetching v3 entities for deal %s", deal_id)
    entities = load_v3_entities_for_deal(driver, db_name, deal_id)
    report.entities_scanned = len(entities)
    logger.info("  %d v3 entities", len(entities))

    if not entities:
        report.warnings.append(
            f"no v3 entities found for deal {deal_id!r} — has RP extraction run against {db_name}?"
        )
        return report

    for entity in entities:
        mapping = mappings.get(entity.entity_type)
        if mapping is None:
            report.mapping_gaps.add(entity.entity_type)
            continue
        report.entities_projected += 1
        project_entity(driver, db_name, entity, mapping, specs, instrument_labels,
                       report, dry_run)

    # Phase B — project v3 basket_reallocates_to instances into v4
    # norm_reallocates_capacity_from. Defensive: emits zero edges if v3 has no
    # reallocation data (current state for Duck Creek 2026-04-27).
    if not dry_run:
        _project_reallocations(driver, db_name, deal_id, report)

    return report


def _project_reallocations(driver, db_name: str, deal_id: str,
                            report: ProjectionReport) -> None:
    """Read v3 basket_reallocates_to instances, emit v4 norm_reallocates_capacity_from.

    Pre-flight verified zero v3 reallocation instances on Duck Creek as of
    2026-04-27 — function is a defensive hook for when extraction starts
    capturing the data. Maps v3 attributes to v4 enum values:
      - is_bidirectional + reduces_source_basket → reduction_direction
      - reduction_is_dollar_for_dollar → reallocation_mechanism
    """
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        # Read v3 reallocation edges. Pattern #14: links form for attribute access.
        q = """
match
    $r isa basket_reallocates_to,
        links (source_basket: $src, target_basket: $tgt);
    $src has basket_id $sid;
    $tgt has basket_id $tid;
    try { $r has is_bidirectional $bidir; };
    try { $r has reduction_is_dollar_for_dollar $d4d; };
select $sid, $tid, $bidir, $d4d;
"""
        try:
            result = tx.query(q).resolve()
            rows = list(result.as_concept_rows())
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"basket_reallocates_to query failed: {str(exc)[:160]}")
            rows = []
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass

    if not rows:
        # Expected on Duck Creek 2026-04-27 — nothing to log loudly.
        return

    for row in rows:
        sid = _attr_or_none(row, "sid")
        tid = _attr_or_none(row, "tid")
        bidir = _attr_or_none(row, "bidir")
        d4d = _attr_or_none(row, "d4d")
        if not sid or not tid:
            continue

        # Map v3 booleans to v4 enums. Defaults conservative.
        mech = "shares_pool" if d4d else "separate_pool"
        direction = "bidirectional" if bidir else "receiver_only"

        # v3 basket_id is the source-side projection norm_id (basket extraction
        # projects with deal_id_basket_id pattern, but for the pilot the v4
        # norm_id is <deal_id>_<categorical_kind>; resolve via norm_extracted_from).
        # Simplest path: look up the projected norm_id by walking norm_extracted_from
        # back to the basket.
        edge_q = f"""
match
    $src_basket has basket_id {_tq_string(sid)};
    $tgt_basket has basket_id {_tq_string(tid)};
    $src_norm isa norm;
    $tgt_norm isa norm;
    (norm: $src_norm, fact: $src_basket) isa norm_extracted_from;
    (norm: $tgt_norm, fact: $tgt_basket) isa norm_extracted_from;
insert
    (reallocation_receiver: $tgt_norm, reallocation_source: $src_norm)
        isa norm_reallocates_capacity_from,
        has reallocation_mechanism {_tq_string(mech)},
        has reduction_direction {_tq_string(direction)};
"""
        try:
            _execute_write(driver, db_name, edge_q)
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(
                f"reallocation edge insert failed for {sid} → {tid}: {str(exc)[:160]}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _connect():
    from dotenv import load_dotenv
    main_env = Path("C:/Users/olive/ValenceV3/.env")
    if main_env.exists():
        load_dotenv(main_env, override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)
    from app.config import settings
    from typedb.driver import TypeDB, Credentials, DriverOptions
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    ), settings.typedb_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Project v3 extracted entities into v4 norms.")
    parser.add_argument("--deal", required=True, help="deal_id in valence_v4")
    parser.add_argument("--dry-run", action="store_true",
                        help="read + construct in memory; no writes")
    parser.add_argument("--database", default=None,
                        help="override target database (default: settings.typedb_database)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    driver, default_db = _connect()
    db_name = args.database or default_db
    try:
        report = project_deal(driver, db_name, args.deal, dry_run=args.dry_run)
        print(report.summary())
        return 0 if not report.errors else 1
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
