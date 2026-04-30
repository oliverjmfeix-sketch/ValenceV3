"""
Phase C — projection_rule executor (Commit 1.5 minimal version).

Reads typed projection_rule subgraphs from TypeDB and emits v4 norms.
Pilot scope: scalar attribute emission only (no scope edges, no
condition trees, no defeaters, no contributes-to relations). Subsequent
commits extend coverage.

Architecture (per docs/v4_phase_c_design.md):
  1. Read all projection_rule entities
  2. For each rule, walk match_criterion subgraph -> build match query
  3. Run match query, get v3 entity attributes
  4. For each match, walk attribute_emission subgraph -> resolve each
     attribute value via value_source dispatch
  5. Insert norm with resolved attribute values
  6. Emit produced_by_rule provenance edge (deferred to future commit)

Value source dispatch table (Commit 1.5 supports):
  literal_string_value_source       -> literal_string_value
  literal_double_value_source       -> literal_double_value
  literal_long_value_source      -> literal_long_value
  literal_boolean_value_source      -> literal_boolean_value
  v3_attribute_value_source         -> attrs[reads_v3_attribute_name]
  deal_id_value_source              -> deal_id from context
  concatenation_value_source        -> concat(parts in sequence_index order)

Future support (raises NotImplementedError until then):
  arithmetic_value_source
  conditional_value_source
  produced_norm_id_value_source
  value_source_has_default fallback
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from typedb.driver import TransactionType

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Data records
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutorContext:
    """Per-match context: the v3 entity's attributes + projection's deal_id."""
    deal_id: str
    v3_attrs: dict[str, Any]


@dataclass
class ExecutionReport:
    rule_id: str
    matches: int = 0
    norms_emitted: int = 0
    relations_emitted: int = 0
    conditions_emitted: int = 0
    provenance_emitted: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tq_literal(value: Any) -> str:
    """Render a Python value as a TypeQL literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _tq_string(value)
    raise ValueError(f"unsupported literal type {type(value).__name__}: {value!r}")


def _attr_or_none(row, key: str):
    concept = row.get(key)
    if concept is None:
        return None
    return concept.as_attribute().get_value()


# Commit 3.2 — transaction-reuse helper. Most read functions open their
# own tx by default, but accept an optional `tx` parameter to reuse a
# caller's long-lived READ transaction. Significantly reduces tx setup
# overhead during execute_rule's hot path (~60 reads per rule).

def _open_read(driver, db_name: str, tx=None):
    """Return (tx, owns_tx). owns_tx is True when the caller should close
    the tx (we created it); False when reusing a caller-supplied tx.
    """
    if tx is not None:
        try:
            if tx.is_open():
                return tx, False
        except Exception:
            pass
    return driver.transaction(db_name, TransactionType.READ), True


def _close_if_owned(tx, owns_tx: bool):
    if not owns_tx or tx is None:
        return
    try:
        if tx.is_open():
            tx.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Rule + value-source resolution
# ═══════════════════════════════════════════════════════════════════════════════

def load_rules(driver, db_name: str) -> list[dict]:
    """Read every projection_rule + its match criteria + norm_template."""
    rules: list[dict] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        # Read rules
        r = tx.query(
            "match $r isa projection_rule, has projection_rule_id $rid; select $rid;"
        ).resolve()
        for row in r.as_concept_rows():
            rules.append({"rule_id": row.get("rid").as_attribute().get_value()})
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return rules


def load_match_v3_entity_type(driver, db_name: str, rule_id: str) -> str | None:
    """Read the rule's entity_type_criterion. Used as the primary table
    in fetch_v3_entity_attrs; additional attribute_value criteria are
    applied as a post-fetch filter (see load_attribute_filters)."""
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, applied_criterion: $c) isa rule_has_match_criterion;\n'
            f'    $c isa entity_type_criterion, has matches_v3_entity_type $tn;\n'
            f'select $tn;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                return row.get("tn").as_attribute().get_value()
        except Exception as exc:
            logger.warning(f"load_match for {rule_id} failed: {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return None


def load_attribute_filters(driver, db_name: str, rule_id: str) -> list[dict]:
    """Read the rule's attribute_value_criterion entries.

    Two cases:
    (a) Top-level attribute_value_criterion: applied as AND filter.
    (b) match_criterion_group with combinator='or': all attribute_value
        criteria under it are OR'd together; the group is then ANDed
        with other top-level criteria.

    Returns a list of filter groups: each is {"combinator": "and"|"or",
    "filters": [{"attr": str, "op": str, "value": <python>}]}.
    Top-level attribute_value criteria are returned as a single AND group.
    """
    groups: list[dict] = []

    # (a) Top-level attribute_value_criterion
    tx = driver.transaction(db_name, TransactionType.READ)
    top_level: list[dict] = []
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, applied_criterion: $c) isa rule_has_match_criterion;\n'
            f'    $c isa attribute_value_criterion,\n'
            f'        has checks_v3_attribute_name $name,\n'
            f'        has comparison_operator $op;\n'
            f'    (owning_criterion: $c, comparison_value_source: $vs) isa criterion_uses_comparison_value;\n'
            f'select $name, $op, $vs;\n'
        )
        try:
            r = tx.query(q).resolve()
            ctx = ExecutorContext(deal_id="", v3_attrs={})  # not used for literals
            for row in r.as_concept_rows():
                vs_iid = row.get("vs").get_iid()
                top_level.append({
                    "attr": row.get("name").as_attribute().get_value(),
                    "op": row.get("op").as_attribute().get_value(),
                    "value": resolve_value_source(driver, db_name, vs_iid, ctx),
                })
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    if top_level:
        groups.append({"combinator": "and", "filters": top_level})

    # (b) match_criterion_group (OR groups under the rule)
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, applied_criterion: $g) isa rule_has_match_criterion;\n'
            f'    $g isa match_criterion_group, has group_combinator $cmb;\n'
            f'    (parent_group: $g, member_criterion: $c) isa criterion_group_has_member;\n'
            f'    $c isa attribute_value_criterion,\n'
            f'        has checks_v3_attribute_name $name,\n'
            f'        has comparison_operator $op;\n'
            f'    (owning_criterion: $c, comparison_value_source: $vs) isa criterion_uses_comparison_value;\n'
            f'select $g, $cmb, $name, $op, $vs;\n'
        )
        try:
            r = tx.query(q).resolve()
            by_group: dict[str, dict] = {}
            ctx = ExecutorContext(deal_id="", v3_attrs={})
            for row in r.as_concept_rows():
                g_iid = row.get("g").get_iid()
                cmb = row.get("cmb").as_attribute().get_value()
                vs_iid = row.get("vs").get_iid()
                if g_iid not in by_group:
                    by_group[g_iid] = {"combinator": cmb, "filters": []}
                by_group[g_iid]["filters"].append({
                    "attr": row.get("name").as_attribute().get_value(),
                    "op": row.get("op").as_attribute().get_value(),
                    "value": resolve_value_source(driver, db_name, vs_iid, ctx),
                })
            groups.extend(by_group.values())
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    return groups


def _eval_filter(v3_attrs: dict, f: dict) -> bool:
    """Evaluate one attribute_value filter against a v3 entity's attrs."""
    actual = v3_attrs.get(f["attr"])
    op = f["op"]
    value = f["value"]
    if op == "equals":
        return actual == value
    if op == "not_equals":
        return actual != value
    if op == "greater_than":
        return actual is not None and value is not None and actual > value
    if op == "less_than":
        return actual is not None and value is not None and actual < value
    if op == "greater_or_equal":
        return actual is not None and value is not None and actual >= value
    if op == "less_or_equal":
        return actual is not None and value is not None and actual <= value
    if op == "in":
        return actual in (value or [])
    return False


def matches_filters(v3_attrs: dict, groups: list[dict]) -> bool:
    """Evaluate all filter groups (AND across groups; combinator within
    each group). Returns True if all groups satisfied."""
    for g in groups:
        results = [_eval_filter(v3_attrs, f) for f in g["filters"]]
        if g["combinator"] == "or":
            if not any(results):
                return False
        else:  # and (default)
            if not all(results):
                return False
    return True


def load_attribute_emissions(driver, db_name: str, rule_id: str,
                               template_kind: str = "norm_template") -> list[tuple[str, str]]:
    """Read every (attribute_name, value_source_id) pair the rule emits.
    template_kind: "norm_template" or "defeater_template".
    """
    pairs: list[tuple[str, str]] = []
    rule_produces = (
        "rule_produces_norm_template" if template_kind == "norm_template"
        else "rule_produces_defeater_template"
    )
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, produced_template: $nt) isa {rule_produces};\n'
            f'    (emitting_template: $nt, emitted_attribute: $ae) isa template_emits_attribute;\n'
            f'    $ae has emitted_attribute_name $name;\n'
            f'    (owning_emission: $ae, source_value: $vs) isa attribute_emission_uses_value;\n'
            f'select $name, $vs;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                attr_name = row.get("name").as_attribute().get_value()
                vs_iid = row.get("vs").get_iid()
                pairs.append((attr_name, vs_iid))
        except Exception as exc:
            logger.warning(f"load_emissions for {rule_id} failed: {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return pairs


def resolve_value_source(driver, db_name: str, vs_iid: str, ctx: ExecutorContext,
                          tx=None) -> Any:
    """Walk a value_source subgraph and produce a Python value.

    Dispatched on the value_source's concrete type. Recursive for
    composition (concatenation, arithmetic, conditional).

    Accepts an optional `tx` (Commit 3.2): a caller-supplied READ
    transaction reused across many reads. When None, opens its own.
    """
    rtx, owns = _open_read(driver, db_name, tx)
    try:
        # Identify the concrete type. Use isa! for exact-type match (avoids
        # the polymorphic default which would return abstract parents).
        q = f'match $vs iid {vs_iid}, isa! $type; select $type;'
        try:
            result = rtx.query(q).resolve()
            row = next(iter(result.as_concept_rows()), None)
            if row is None:
                return None
            type_label = row.get("type").get_label()
        except Exception as exc:
            logger.warning(f"resolve: type lookup for {vs_iid} failed: {str(exc).splitlines()[0][:100]}")
            return None
    finally:
        _close_if_owned(rtx, owns)

    # Dispatch — pass tx down so nested reads share the same transaction
    if type_label == "literal_string_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_string_value", tx=tx)
    if type_label == "literal_double_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_double_value", tx=tx)
    if type_label == "literal_long_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_long_value", tx=tx)
    if type_label == "literal_boolean_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_boolean_value", tx=tx)
    if type_label == "deal_id_value_source":
        return ctx.deal_id
    if type_label == "v3_attribute_value_source":
        attr_name = _read_attr_value(driver, db_name, vs_iid, "reads_v3_attribute_name", tx=tx)
        if attr_name is None:
            return None
        v = ctx.v3_attrs.get(attr_name)
        if v is not None:
            return v
        # Fallback: walk value_source_has_default if present
        return _resolve_default_fallback(driver, db_name, vs_iid, ctx, tx=tx)
    if type_label == "concatenation_value_source":
        return _resolve_concatenation(driver, db_name, vs_iid, ctx, tx=tx)
    if type_label in (
        "arithmetic_value_source",
        "conditional_value_source",
        "produced_norm_id_value_source",
    ):
        raise NotImplementedError(
            f"value_source type {type_label!r} not yet supported "
            f"(Commit 1.5 minimal scope; extend in subsequent commits)"
        )
    raise ValueError(f"unknown value_source type: {type_label}")


def _read_attr_value(driver, db_name: str, owner_iid: str, attr_name: str, tx=None):
    rtx, owns = _open_read(driver, db_name, tx)
    try:
        q = f'match $x iid {owner_iid}, has {attr_name} $v; select $v;'
        try:
            result = rtx.query(q).resolve()
            for row in result.as_concept_rows():
                return row.get("v").as_attribute().get_value()
        except Exception:
            return None
    finally:
        _close_if_owned(rtx, owns)
    return None


def _resolve_default_fallback(driver, db_name: str, vs_iid: str, ctx: ExecutorContext,
                                tx=None) -> Any:
    """If a v3_attribute_value_source has a value_source_has_default edge,
    resolve the default value source. Returns None if no default is wired."""
    rtx, owns = _open_read(driver, db_name, tx)
    try:
        q = (
            f'match\n'
            f'    $primary iid {vs_iid};\n'
            f'    (primary_source: $primary, default_source: $default) isa value_source_has_default;\n'
            f'select $default;\n'
        )
        try:
            result = rtx.query(q).resolve()
            row = next(iter(result.as_concept_rows()), None)
            if row is None:
                return None
            default_iid = row.get("default").get_iid()
        except Exception:
            return None
    finally:
        _close_if_owned(rtx, owns)
    return resolve_value_source(driver, db_name, default_iid, ctx, tx=tx)


def _resolve_concatenation(driver, db_name: str, vs_iid: str, ctx: ExecutorContext,
                            tx=None) -> str:
    """Walk concatenation_has_ordered_part edges in sequence_index order
    and concatenate resolved parts."""
    parts: list[tuple[int, str]] = []
    rtx, owns = _open_read(driver, db_name, tx)
    try:
        q = (
            f'match\n'
            f'    $cat iid {vs_iid};\n'
            f'    (owning_concatenation: $cat, concatenation_part: $p) isa concatenation_has_ordered_part,\n'
            f'        has sequence_index $idx;\n'
            f'select $p, $idx;\n'
        )
        try:
            result = rtx.query(q).resolve()
            for row in result.as_concept_rows():
                idx = row.get("idx").as_attribute().get_value()
                part_iid = row.get("p").get_iid()
                parts.append((idx, part_iid))
        except Exception as exc:
            logger.warning(f"resolve_concat for {vs_iid} failed: {str(exc).splitlines()[0][:120]}")
            return ""
    finally:
        _close_if_owned(rtx, owns)

    parts.sort(key=lambda t: t[0])
    resolved = []
    for _, part_iid in parts:
        part_value = resolve_value_source(driver, db_name, part_iid, ctx, tx=tx)
        if part_value is None:
            resolved.append("")
        else:
            resolved.append(str(part_value))
    return "".join(resolved)


# ═══════════════════════════════════════════════════════════════════════════════
# Entity emission
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_v3_entity_attrs(driver, db_name: str, entity_type: str, deal_id: str) -> list[dict]:
    """Find v3 entities of the given type for the given deal and return
    their attributes as Python dicts.

    Tries two paths:
    (1) Direct: deal -> deal_has_provision -> provision -> provision_has_extracted_entity -> entity
        (catches rp_baskets, rdp_baskets, blockers, etc.)
    (2) Nested: provision_has_extracted_entity -> parent -> entity_has_child -> entity
        (catches blocker_exception, basket sub-entities, etc.)

    Falls back to (2) if (1) returns nothing.
    """
    def collect(query: str) -> dict[str, dict]:
        entity_attrs: dict[str, dict] = {}
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            try:
                result = tx.query(query).resolve()
                for row in result.as_concept_rows():
                    bid = row.get("b").get_iid()
                    attr = row.get("attr").as_attribute()
                    attr_label = attr.get_type().get_label()
                    attr_value = attr.get_value()
                    if bid not in entity_attrs:
                        entity_attrs[bid] = {
                            "_iid": bid,
                            "_type": row.get("b").get_type().get_label(),
                        }
                    entity_attrs[bid][attr_label] = attr_value
            except Exception as exc:
                logger.debug(f"fetch_v3 collect: {str(exc).splitlines()[0][:80]}")
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass
        return entity_attrs

    # (1) Direct path
    q1 = (
        f'match\n'
        f'    $d isa deal, has deal_id "{deal_id}";\n'
        f'    (deal: $d, provision: $p) isa deal_has_provision;\n'
        f'    (provision: $p, extracted: $b) isa provision_has_extracted_entity;\n'
        f'    $b isa {entity_type}, has $attr;\n'
        f'select $b, $attr;\n'
    )
    entities = collect(q1)
    if entities:
        return list(entities.values())

    # (2) Nested path: parent -> entity_has_child -> entity
    q2 = (
        f'match\n'
        f'    $d isa deal, has deal_id "{deal_id}";\n'
        f'    (deal: $d, provision: $p) isa deal_has_provision;\n'
        f'    (provision: $p, extracted: $parent) isa provision_has_extracted_entity;\n'
        f'    (parent: $parent, child: $b) isa entity_has_child;\n'
        f'    $b isa {entity_type}, has $attr;\n'
        f'select $b, $attr;\n'
    )
    entities = collect(q2)
    return list(entities.values())


def load_relation_templates(driver, db_name: str, rule_id: str,
                             template_kind: str = "norm_template") -> list[dict]:
    """Read each relation_template the rule's template emits.
    template_kind: "norm_template" or "defeater_template"."""
    templates: list[dict] = []
    rule_produces = (
        "rule_produces_norm_template" if template_kind == "norm_template"
        else "rule_produces_defeater_template"
    )
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, produced_template: $nt) isa {rule_produces};\n'
            f'    (emitting_template: $nt, emitted_relation: $rt) isa template_emits_relation;\n'
            f'    $rt has emits_relation_type $rtype;\n'
            f'select $rt, $rtype;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                templates.append({
                    "iid": row.get("rt").get_iid(),
                    "relation_type": row.get("rtype").as_attribute().get_value(),
                })
        except Exception as exc:
            logger.debug(f"load_relation_templates {rule_id}: {str(exc).splitlines()[0][:80]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return templates


def load_role_assignments(driver, db_name: str, rt_iid: str) -> list[dict]:
    """Read each role_assignment under a relation_template + its filler info."""
    assignments: list[dict] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $rt iid {rt_iid};\n'
            f'    (owning_relation_template: $rt, emitted_role_assignment: $ra) isa relation_template_assigns_role;\n'
            f'    $ra has assigned_role_name $rn;\n'
            f'    (owning_role_assignment: $ra, assignment_filler: $f) isa role_assignment_filled_by;\n'
            f'    $f isa! $f_type;\n'
            f'select $ra, $rn, $f, $f_type;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                assignments.append({
                    "ra_iid": row.get("ra").get_iid(),
                    "role_name": row.get("rn").as_attribute().get_value(),
                    "filler_iid": row.get("f").get_iid(),
                    "filler_type": row.get("f_type").get_label(),
                })
        except Exception as exc:
            logger.debug(f"load_role_assignments {rt_iid}: {str(exc).splitlines()[0][:80]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return assignments


def resolve_filler(driver, db_name: str, filler_iid: str, filler_type: str,
                    emitted_entity_id: str, ctx: ExecutorContext,
                    role_name: str = "",
                    emitted_entity_type: str = "norm",
                    emitted_id_attr: str = "norm_id") -> dict | None:
    """Resolve a role_filler to a TypeQL match clause.

    Returns {"match_clause": "<tql line>", "var_name": "$x"}.
    The caller composes match clauses + insert into a final write query.

    For emitted_norm_role_filler: matches the entity emitted by THIS rule
    (by emitted_entity_type + id_attr). The schema's role_filler subtype
    name is historical; semantically it refers to "the entity emitted by
    the parent template" regardless of whether that's a norm or defeater.
    """
    iid_clean = filler_iid.replace("0x", "").replace(" ", "")
    var_name = f"$f_{role_name}_{iid_clean[-12:]}" if role_name else f"$f_{iid_clean[-12:]}"
    if filler_type == "emitted_norm_role_filler":
        return {
            "var_name": var_name,
            "match_clause": f'{var_name} isa {emitted_entity_type}, has {emitted_id_attr} "{emitted_entity_id}";',
        }
    if filler_type == "static_lookup_role_filler":
        lookup_entity = _read_attr_value(driver, db_name, filler_iid, "lookup_entity_type")
        lookup_attr = _read_attr_value(driver, db_name, filler_iid, "lookup_attribute_name")
        if not lookup_entity or not lookup_attr:
            logger.warning(f"static_lookup_role_filler {filler_iid} missing lookup_entity_type or lookup_attribute_name")
            return None
        # Walk static_lookup_uses_value to get the target value
        value_iid = _lookup_static_value_source_iid(driver, db_name, filler_iid)
        if value_iid is None:
            logger.warning(f"static_lookup_role_filler {filler_iid} has no value source")
            return None
        value = resolve_value_source(driver, db_name, value_iid, ctx)
        if value is None:
            return None
        return {
            "var_name": var_name,
            "match_clause": f'{var_name} isa {lookup_entity}, has {lookup_attr} {_tq_literal(value)};',
        }
    if filler_type == "produced_norm_role_filler":
        # Cross-rule reference; resolve target template's emitted norm_id
        # via produced_norm_filler_references_template. Defer to subsequent
        # commit (Commit 2.4 builder sub-source rules need this).
        raise NotImplementedError(
            "produced_norm_role_filler not yet supported — Commit 2.4 will add cross-rule reference resolution"
        )
    raise ValueError(f"unknown role_filler type: {filler_type}")


def _lookup_static_value_source_iid(driver, db_name: str, filler_iid: str) -> str | None:
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $f iid {filler_iid};\n'
            f'    (owning_filler: $f, lookup_value_source: $vs) isa static_lookup_uses_value;\n'
            f'select $vs;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                return row.get("vs").get_iid()
        except Exception:
            return None
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return None


def load_root_condition_template(driver, db_name: str, rule_id: str) -> str | None:
    """Return the root condition_template iid for a rule's norm_template,
    or None if no condition is authored."""
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, produced_template: $nt) isa rule_produces_norm_template;\n'
            f'    (emitting_template: $nt, root_condition: $ct) isa template_emits_root_condition;\n'
            f'select $ct;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                return row.get("ct").get_iid()
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return None


def load_condition_template(driver, db_name: str, ct_iid: str) -> dict | None:
    """Read condition_template attrs + its predicate_specifier (if atomic)
    + ordered children (if compound)."""
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $ct iid {ct_iid}, has target_topology $topo, has target_operator $op;\n'
            f'select $topo, $op;\n'
        )
        topo = op = None
        try:
            r = tx.query(q).resolve()
            row = next(iter(r.as_concept_rows()), None)
            if row:
                topo = row.get("topo").as_attribute().get_value()
                op = row.get("op").as_attribute().get_value()
        except Exception:
            return None
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Fetch predicate_specifier (for atomic conditions)
    spec_iid = None
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $ct iid {ct_iid};\n'
            f'    (owning_condition_template: $ct, referenced_specifier: $spec) isa atomic_condition_references_predicate;\n'
            f'select $spec;\n'
        )
        try:
            r = tx.query(q).resolve()
            row = next(iter(r.as_concept_rows()), None)
            if row:
                spec_iid = row.get("spec").get_iid()
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Fetch ordered children (for compound conditions)
    children: list[tuple[int, str]] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $parent iid {ct_iid};\n'
            f'    $r isa condition_template_has_child, links (parent_condition: $parent, child_condition: $child);\n'
            f'    $r has child_template_index $idx;\n'
            f'select $child, $idx;\n'
        )
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                children.append((
                    row.get("idx").as_attribute().get_value(),
                    row.get("child").get_iid(),
                ))
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    children.sort(key=lambda t: t[0])

    return {"iid": ct_iid, "topology": topo, "operator": op,
            "spec_iid": spec_iid, "child_iids": [c[1] for c in children]}


def resolve_predicate_id(driver, db_name: str, spec_iid: str, ctx: ExecutorContext) -> str | None:
    """Construct the state_predicate_id for an atomic condition's
    predicate_specifier. Two cases:
      - No predicate_specifier_uses_value edge: specifies_predicate_id is
        the full composite id (canonical predicate).
      - With predicate_specifier_uses_value: specifies_predicate_id is the
        label; combine with resolved value source + specifies_operator +
        specifies_reference_label via construct_state_predicate_id.
    """
    label = _read_attr_value(driver, db_name, spec_iid, "specifies_predicate_id")
    if label is None:
        return None
    op = _read_attr_value(driver, db_name, spec_iid, "specifies_operator")
    ref = _read_attr_value(driver, db_name, spec_iid, "specifies_reference_label")

    # Look for dynamic value source
    tx = driver.transaction(db_name, TransactionType.READ)
    dyn_iid = None
    try:
        q = (
            f'match\n'
            f'    $spec iid {spec_iid};\n'
            f'    (owning_specifier: $spec, dynamic_value_source: $vs) isa predicate_specifier_uses_value;\n'
            f'select $vs;\n'
        )
        try:
            r = tx.query(q).resolve()
            row = next(iter(r.as_concept_rows()), None)
            if row:
                dyn_iid = row.get("vs").get_iid()
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    if dyn_iid is None:
        # Canonical case — label IS the full composite id
        return label

    # Dynamic case — construct composite id
    threshold = resolve_value_source(driver, db_name, dyn_iid, ctx)
    if threshold is None:
        return None
    from app.services.predicate_id import construct_state_predicate_id
    return construct_state_predicate_id(
        label=label,
        threshold_value_double=float(threshold),
        operator_comparison=op,
        reference_predicate_label=ref,
    )


def emit_condition_subtree(driver, db_name: str, ct_iid: str,
                            norm_id: str, parent_cond_id: str | None,
                            ctx: ExecutorContext,
                            report: ExecutionReport,
                            path_suffix: str = "root") -> str | None:
    """Recursively emit condition entities for a condition_template subtree.

    Returns the condition_id of the emitted root condition (for the parent
    to wire condition_has_child or norm_has_condition).
    """
    tpl = load_condition_template(driver, db_name, ct_iid)
    if tpl is None:
        return None

    cond_id = f"{norm_id}__cond_{path_suffix}"

    # Insert the condition entity
    # Schema: condition owns condition_id, condition_operator, condition_topology
    # condition_topology populated on root only; internal nodes have only operator.
    if path_suffix == "root":
        cond_q = (
            f'insert $c isa condition,\n'
            f'    has condition_id "{cond_id}",\n'
            f'    has condition_topology "{tpl["topology"]}",\n'
            f'    has condition_operator "{tpl["operator"]}";\n'
        )
    else:
        cond_q = (
            f'insert $c isa condition,\n'
            f'    has condition_id "{cond_id}",\n'
            f'    has condition_operator "{tpl["operator"]}";\n'
        )
    wtx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        try:
            wtx.query(cond_q).resolve()
            wtx.commit()
            report.conditions_emitted += 1
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            report.warnings.append(f"emit condition {cond_id}: {str(exc).splitlines()[0][:120]}")
            return None
    except Exception:
        return None

    # If atomic, wire condition_references_predicate to the resolved predicate
    if tpl["operator"] == "atomic" and tpl["spec_iid"]:
        pred_id = resolve_predicate_id(driver, db_name, tpl["spec_iid"], ctx)
        if pred_id:
            link_q = (
                f'match\n'
                f'    $c isa condition, has condition_id "{cond_id}";\n'
                f'    $p isa state_predicate, has state_predicate_id "{pred_id}";\n'
                f'insert (condition: $c, predicate: $p) isa condition_references_predicate;\n'
            )
            wtx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                try:
                    wtx.query(link_q).resolve()
                    wtx.commit()
                except Exception as exc:
                    if wtx.is_open():
                        wtx.close()
                    report.warnings.append(f"link {cond_id} to predicate {pred_id}: {str(exc).splitlines()[0][:120]}")
            except Exception:
                pass

    # Recurse for compound — emit children + condition_has_child edges
    for idx, child_iid in enumerate(tpl["child_iids"]):
        child_id = emit_condition_subtree(
            driver, db_name, child_iid, norm_id, cond_id, ctx, report,
            path_suffix=f"{path_suffix}_{idx}",
        )
        if child_id:
            child_link_q = (
                f'match\n'
                f'    $parent isa condition, has condition_id "{cond_id}";\n'
                f'    $child isa condition, has condition_id "{child_id}";\n'
                f'insert (parent_condition: $parent, child_condition: $child)\n'
                f'    isa condition_template_has_child, has child_template_index {idx};\n'
            )
            # NOTE: actual relation in schema is `condition_has_child` not
            # condition_template_has_child. Fix below.
            child_link_q = (
                f'match\n'
                f'    $parent isa condition, has condition_id "{cond_id}";\n'
                f'    $child isa condition, has condition_id "{child_id}";\n'
                f'insert (parent: $parent, child: $child) isa condition_has_child,\n'
                f'    has child_index {idx};\n'
            )
            wtx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                try:
                    wtx.query(child_link_q).resolve()
                    wtx.commit()
                except Exception as exc:
                    if wtx.is_open():
                        wtx.close()
                    report.warnings.append(f"link child {child_id} to {cond_id}: {str(exc).splitlines()[0][:120]}")
            except Exception:
                pass

    return cond_id


def emit_provenance(driver, db_name: str, rule_id: str,
                     emitted_entity_type: str, emitted_id_attr: str,
                     emitted_entity_id: str, v3_entity_iid: str,
                     v3_entity_type: str,
                     report: ExecutionReport) -> bool:
    """Emit a produced_by_rule edge linking the just-emitted v4 entity
    to the projection_rule and the v3 entity that triggered the match.

    Per Phase C design §C.8: every emitted v4 entity gets a provenance
    edge for audit. Query: 'why does this norm have this kind?' becomes
    a graph walk from the entity's produced_by_rule edge.
    """
    if not v3_entity_iid or not v3_entity_type:
        return False
    # Type constraint on $v required so TypeDB type-inference narrows it
    # to a type that plays produced_by_rule:triggering_v3_entity. iid
    # alone doesn't constrain the type at compile time.
    q = (
        f'match\n'
        f'    $e isa {emitted_entity_type}, has {emitted_id_attr} "{emitted_entity_id}";\n'
        f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
        f'    $v isa {v3_entity_type}, iid {v3_entity_iid};\n'
        f'insert (produced_entity: $e, owning_rule: $r, triggering_v3_entity: $v)\n'
        f'    isa produced_by_rule;\n'
    )
    wtx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        try:
            wtx.query(q).resolve()
            wtx.commit()
            report.provenance_emitted += 1
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            report.warnings.append(
                f"provenance edge for {emitted_entity_id}: {str(exc).splitlines()[0][:120]}"
            )
            return False
    except Exception:
        return False

    # Phase C Commit 4: also emit norm_extracted_from for the v4 norm
    # case (defeaters don't carry this edge — schema only declares
    # `norm plays norm_extracted_from:norm`). The validation harness's
    # A5 rule-selection check queries this relation; without it, A5
    # falls back to "n/a (projection not run)" and we lose visibility.
    #
    # Mirror python projection's contract: norm_extracted_from anchors
    # the PRIMARY norm-per-v3-entity to its source v3 entity (mapping
    # rules only). Sub-source builder norms (each emitted from the same
    # builder_basket via b_aggregate or builder_source_* rules) do NOT
    # claim the v3 entity — A5 would mis-classify them as wrong rule
    # selection. python projection's _project_builder_sub_sources
    # likewise emits no norm_extracted_from for sub-source norms.
    SUB_SOURCE_RULE_PREFIX = "rule_conv_builder_builder_source_"
    B_AGGREGATE_RULE = "rule_conv_builder_b_aggregate"
    is_sub_source = (
        rule_id.startswith(SUB_SOURCE_RULE_PREFIX) or rule_id == B_AGGREGATE_RULE
    )
    if emitted_entity_type == "norm" and not is_sub_source:
        q2 = (
            f'match\n'
            f'    $n isa norm, has norm_id "{emitted_entity_id}";\n'
            f'    $v isa {v3_entity_type}, iid {v3_entity_iid};\n'
            f'insert (norm: $n, fact: $v) isa norm_extracted_from;\n'
        )
        wtx2 = driver.transaction(db_name, TransactionType.WRITE)
        try:
            try:
                wtx2.query(q2).resolve()
                wtx2.commit()
            except Exception as exc:
                if wtx2.is_open():
                    wtx2.close()
                report.warnings.append(
                    f"norm_extracted_from for {emitted_entity_id}: {str(exc).splitlines()[0][:120]}"
                )
        except Exception:
            pass

    return True


def emit_root_condition(driver, db_name: str, rule_id: str, emitted_norm_id: str,
                         ctx: ExecutorContext, report: ExecutionReport) -> bool:
    """Walk template_emits_root_condition for the rule and emit the
    full condition tree + norm_has_condition edge."""
    root_iid = load_root_condition_template(driver, db_name, rule_id)
    if root_iid is None:
        return False

    root_cond_id = emit_condition_subtree(
        driver, db_name, root_iid, emitted_norm_id, None, ctx, report,
    )
    if root_cond_id is None:
        return False

    # Wire norm_has_condition
    link_q = (
        f'match\n'
        f'    $n isa norm, has norm_id "{emitted_norm_id}";\n'
        f'    $c isa condition, has condition_id "{root_cond_id}";\n'
        f'insert (norm: $n, root: $c) isa norm_has_condition;\n'
    )
    wtx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        try:
            wtx.query(link_q).resolve()
            wtx.commit()
            return True
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            report.warnings.append(f"norm_has_condition link for {emitted_norm_id}: {str(exc).splitlines()[0][:120]}")
            return False
    except Exception:
        return False


def load_relation_edge_attributes(driver, db_name: str, rt_iid: str,
                                    ctx: ExecutorContext) -> list[tuple[str, Any]]:
    """Read edge attributes for a relation_template via
    relation_template_emits_edge_attribute. Returns list of
    (attribute_name, resolved_value)."""
    result: list[tuple[str, Any]] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $rt iid {rt_iid};\n'
            f'    (owning_relation_template: $rt, emitted_edge_attribute: $ae) isa relation_template_emits_edge_attribute;\n'
            f'    $ae has emitted_attribute_name $name;\n'
            f'    (owning_emission: $ae, source_value: $vs) isa attribute_emission_uses_value;\n'
            f'select $name, $vs;\n'
        )
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                attr_name = row.get("name").as_attribute().get_value()
                vs_iid = row.get("vs").get_iid()
                value = resolve_value_source(driver, db_name, vs_iid, ctx)
                if value is not None:
                    result.append((attr_name, value))
        except Exception:
            pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return result


def emit_relation_templates(driver, db_name: str, rule_id: str,
                             emitted_entity_id: str, ctx: ExecutorContext,
                             report: ExecutionReport,
                             emitted_entity_type: str = "norm",
                             emitted_id_attr: str = "norm_id",
                             template_kind: str = "norm_template") -> int:
    """For each relation_template the rule's template emits, build a
    match-insert query that creates the v4 relation. Returns count of
    relations successfully emitted.

    template_kind: "norm_template" or "defeater_template" — controls which
    rule_produces_* relation is walked.
    """
    templates = load_relation_templates(driver, db_name, rule_id, template_kind=template_kind)
    emitted = 0
    for rt in templates:
        assignments = load_role_assignments(driver, db_name, rt["iid"])
        if not assignments:
            report.warnings.append(f"relation_template {rt['relation_type']} has no role_assignments")
            continue

        match_lines = []
        role_bindings = []
        skip = False
        for ra in assignments:
            try:
                resolved = resolve_filler(
                    driver, db_name, ra["filler_iid"], ra["filler_type"],
                    emitted_entity_id, ctx, role_name=ra["role_name"],
                    emitted_entity_type=emitted_entity_type,
                    emitted_id_attr=emitted_id_attr,
                )
            except NotImplementedError as exc:
                report.warnings.append(f"relation {rt['relation_type']}: {exc}")
                skip = True
                break
            if resolved is None:
                skip = True
                break
            match_lines.append(resolved["match_clause"])
            role_bindings.append(f'{ra["role_name"]}: {resolved["var_name"]}')
        if skip:
            continue

        # Resolve edge attributes (if any)
        edge_attrs = load_relation_edge_attributes(driver, db_name, rt["iid"], ctx)
        edge_attr_clauses = []
        for attr_name, value in edge_attrs:
            try:
                edge_attr_clauses.append(f"has {attr_name} {_tq_literal(value)}")
            except ValueError as exc:
                report.warnings.append(f"edge attr {attr_name}: {exc}")

        match_q = "match\n  " + "\n  ".join(match_lines)
        insert_clauses = [f"({', '.join(role_bindings)}) isa {rt['relation_type']}"]
        if edge_attr_clauses:
            insert_clauses[0] += ",\n    " + ",\n    ".join(edge_attr_clauses)
        insert_q = f"insert {insert_clauses[0]};"
        full_q = f"{match_q}\n{insert_q}"

        wtx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            try:
                wtx.query(full_q).resolve()
                wtx.commit()
                emitted += 1
            except Exception as exc:
                if wtx.is_open():
                    wtx.close()
                report.warnings.append(
                    f"emit relation {rt['relation_type']}: {str(exc).splitlines()[0][:120]}"
                )
        except Exception:
            pass
    return emitted


def emit_entity(driver, db_name: str, entity_type: str, id_attr: str,
                attrs: dict[str, Any], dry_run: bool = False) -> bool:
    """Emit a single entity (norm or defeater) with resolved attributes.

    entity_type: "norm" or "defeater"
    id_attr: "norm_id" or "defeater_id"
    """
    if not attrs.get(id_attr):
        logger.warning(f"emit_entity({entity_type}): missing {id_attr}; skipping")
        return False
    owns_clauses = []
    for name, value in attrs.items():
        if value is None:
            continue
        try:
            owns_clauses.append(f"has {name} {_tq_literal(value)}")
        except ValueError as exc:
            logger.warning(f"emit_entity: skipping {name}: {exc}")
    var_letter = entity_type[0]  # $n for norm, $d for defeater
    insert_q = f"insert ${var_letter} isa {entity_type}, {', '.join(owns_clauses)};"

    if dry_run:
        logger.info(f"[dry-run] {insert_q}")
        return True

    wtx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        wtx.query(insert_q).resolve()
        wtx.commit()
        return True
    except Exception as exc:
        if wtx.is_open():
            wtx.close()
        logger.error(f"emit_entity failed for {entity_type} {attrs.get(id_attr)}: {str(exc).splitlines()[0][:200]}")
        return False


def emit_norm(driver, db_name: str, attrs: dict[str, Any], dry_run: bool = False) -> bool:
    """Backwards-compatible alias for emit_entity('norm', 'norm_id', ...)."""
    return emit_entity(driver, db_name, "norm", "norm_id", attrs, dry_run)


# ═══════════════════════════════════════════════════════════════════════════════
# Top-level: execute one rule
# ═══════════════════════════════════════════════════════════════════════════════

def execute_rule(driver, db_name: str, rule_id: str, deal_id: str,
                 dry_run: bool = False) -> ExecutionReport:
    """Run one projection_rule against deal_id's v3 entities. Emits norms."""
    report = ExecutionReport(rule_id=rule_id)

    entity_type = load_match_v3_entity_type(driver, db_name, rule_id)
    if entity_type is None:
        report.errors.append("no entity_type_criterion found")
        return report
    logger.info(f"rule {rule_id}: matches v3 type {entity_type}")

    matches = fetch_v3_entity_attrs(driver, db_name, entity_type, deal_id)
    # Apply attribute_value_criterion filters (Commit 2.4)
    filter_groups = load_attribute_filters(driver, db_name, rule_id)
    if filter_groups:
        before = len(matches)
        matches = [m for m in matches if matches_filters(m, filter_groups)]
        if before != len(matches):
            logger.info(f"rule {rule_id}: filtered {before} -> {len(matches)} v3 entities")
    report.matches = len(matches)
    logger.info(f"rule {rule_id}: matched {len(matches)} v3 entities")

    # Determine template kind: norm or defeater. A rule produces one or
    # the other (not both). Try norm first; fall back to defeater.
    norm_emissions = load_attribute_emissions(driver, db_name, rule_id, "norm_template")
    defeater_emissions = load_attribute_emissions(driver, db_name, rule_id, "defeater_template")
    if norm_emissions:
        emissions = norm_emissions
        template_kind = "norm_template"
        entity_type_emit = "norm"
        id_attr = "norm_id"
    elif defeater_emissions:
        emissions = defeater_emissions
        template_kind = "defeater_template"
        entity_type_emit = "defeater"
        id_attr = "defeater_id"
    else:
        report.errors.append("no attribute_emissions found on any template")
        return report
    logger.info(f"rule {rule_id}: {len(emissions)} {entity_type_emit} attribute emissions")

    # Commit 3.2 — open one long-lived READ tx for the per-match attribute
    # resolution loop. Reused across resolve_value_source / _read_attr_value /
    # nested concatenation/default-fallback walks. Reduces tx setup overhead
    # from ~50/match to ~1/match.
    for v3_attrs in matches:
        ctx = ExecutorContext(deal_id=deal_id, v3_attrs=v3_attrs)
        resolved: dict[str, Any] = {}
        shared_rtx = driver.transaction(db_name, TransactionType.READ)
        try:
            for attr_name, vs_iid in emissions:
                try:
                    resolved[attr_name] = resolve_value_source(
                        driver, db_name, vs_iid, ctx, tx=shared_rtx,
                    )
                except NotImplementedError as exc:
                    report.errors.append(f"resolve {attr_name}: {exc}")
                    resolved[attr_name] = None
                except Exception as exc:
                    report.warnings.append(f"resolve {attr_name}: {str(exc).splitlines()[0][:120]}")
                    resolved[attr_name] = None
        finally:
            try:
                if shared_rtx.is_open():
                    shared_rtx.close()
            except Exception:
                pass

        if emit_entity(driver, db_name, entity_type_emit, id_attr, resolved, dry_run=dry_run):
            report.norms_emitted += 1
            if not dry_run and resolved.get(id_attr):
                # Emit relation templates (scope edges, defeats edge, etc.)
                report.relations_emitted += emit_relation_templates(
                    driver, db_name, rule_id, resolved[id_attr], ctx, report,
                    emitted_entity_type=entity_type_emit,
                    emitted_id_attr=id_attr,
                    template_kind=template_kind,
                )
                # Emit root condition tree (norms only — python projection
                # doesn't emit defeater_has_condition for jcrew defeaters)
                if template_kind == "norm_template":
                    emit_root_condition(
                        driver, db_name, rule_id, resolved[id_attr], ctx, report,
                    )
                # Emit provenance edge linking emitted entity to rule + v3 source
                v3_iid = v3_attrs.get("_iid")
                v3_type = v3_attrs.get("_type")
                if v3_iid and v3_type:
                    emit_provenance(
                        driver, db_name, rule_id,
                        entity_type_emit, id_attr, resolved[id_attr],
                        v3_iid, v3_type, report,
                    )

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Phase C Commit 4 — orchestration: clear, list, order, project_deal
# ═══════════════════════════════════════════════════════════════════════════════
#
# Moved from app/services/deontic_projection.py + app/scripts/
# phase_c_commit_3_parallel_run.py as part of the python-projection
# deletion. project_deal is the new top-level entry point: wipes prior
# v4 output for the deal, then walks every projection_rule and emits
# norms / defeaters / conditions / scope edges via the typed-dispatch
# interpreter above.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ASSET_SALE_PROCEEDS_SEED = REPO_ROOT / "app" / "data" / "asset_sale_proceeds_seed.tql"
ASSET_SALE_GOVERNANCE_SEED = REPO_ROOT / "app" / "data" / "asset_sale_governance_seed.tql"

# norm_ids the governance seed authors. Used by the helper for idempotent
# delete-before-insert and by clear_v4_projection_for_deal to clean up
# event_governed_by_norm + scope edges before deleting the norm.
_GOVERNANCE_NORM_ID_SUFFIXES = (
    "_sweep_tier_100pct",
    "_sweep_tier_50pct",
    "_sweep_tier_0pct",
    "_unlimited_asset_sale_basket_permission",
    "_sweep_exemption_product_line",
)


def clear_v4_projection_for_deal(driver, db_name: str, deal_id: str) -> dict:
    """Remove existing v4 projection output for a deal before re-projecting.

    Preserves v3 extracted entities (rp_basket, jcrew_blocker, etc.) — those
    are the $12.95 extraction artifact and must NOT be dropped. Only v4 norms,
    conditions, defeaters, and their relations are cleared, scoped to the
    specific deal via norm_id substring match (catches both legacy
    no-prefix python output and any conv_/pilot_-prefixed legacy output).

    TypeDB 3.x cascade caveat: deleting a norm does NOT auto-delete
    relations where the norm plays a non-owner role (produced_by_rule's
    produced_entity, norm_extracted_from's norm, event_provides_proceeds_to_norm's
    proceeds_target_norm). We delete those relations explicitly BEFORE
    deleting the norms; otherwise dangling-role relations accumulate
    across re-runs.

    Returns counts of what was removed per type.
    """
    counts = {"norms": 0, "conditions": 0, "defeaters": 0}
    nid_pattern = deal_id

    clear_queries = [
        # Explicit relation cleanup first — these don't cascade when the
        # norm/defeater/condition is deleted in TypeDB 3.x. The `links`
        # syntax is required for binding a relation variable to a role
        # pattern in 3.x; the older `$r (role: $x) isa relation` form
        # parses but raises empty TypeDBDriverException at execution.
        ("produced_by_rule_for_norms", f'''
            match
              $n isa norm, has norm_id $nid;
              $nid contains "{nid_pattern}";
              $r isa produced_by_rule, links (produced_entity: $n);
            delete $r;
        '''),
        ("produced_by_rule_for_defeaters", f'''
            match
              $d isa defeater, has defeater_id $did;
              $did contains "{nid_pattern}";
              $r isa produced_by_rule, links (produced_entity: $d);
            delete $r;
        '''),
        ("produced_by_rule_for_conditions", f'''
            match
              $c isa condition, has condition_id $cid;
              $cid contains "{nid_pattern}";
              $r isa produced_by_rule, links (produced_entity: $c);
            delete $r;
        '''),
        ("norm_extracted_from", f'''
            match
              $n isa norm, has norm_id $nid;
              $nid contains "{nid_pattern}";
              $r isa norm_extracted_from, links (norm: $n);
            delete $r;
        '''),
        ("event_provides_proceeds_to_norm", f'''
            match
              $n isa norm, has norm_id $nid;
              $nid contains "{nid_pattern}";
              $r isa event_provides_proceeds_to_norm, links (proceeds_target_norm: $n);
            delete $r;
        '''),
        # Phase I.2 — event_governed_by_norm relations don't auto-cascade
        # when the governing norm is deleted, same as the proceeds-flow
        # relations above. Clean up explicitly.
        ("event_governed_by_norm", f'''
            match
              $n isa norm, has norm_id $nid;
              $nid contains "{nid_pattern}";
              $r isa event_governed_by_norm, links (governing_norm: $n);
            delete $r;
        '''),
        # Now the entity deletes.
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
        ("defeaters", f'''
            match
              $d isa defeater, has defeater_id $did;
              $did contains "{nid_pattern}";
            delete $d;
        '''),
    ]
    for label, q in clear_queries:
        wtx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            wtx.query(q).resolve()
            wtx.commit()
        except Exception as exc:  # noqa: BLE001
            if wtx.is_open():
                wtx.close()
            msg = str(exc)
            if "INF2" in msg or "not found" in msg:
                logger.debug("clear/%s skipped (type not in schema): %s", label, msg[:120])
            else:
                logger.warning("clear/%s failed: %s", label, msg[:200])

    # Post-clear inventory (for reporting)
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query(
            f'match $n isa norm, has norm_id $nid; $nid contains "{nid_pattern}"; select $n;'
        ).resolve()
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


def list_rule_ids(driver, db_name: str) -> list[str]:
    """All projection_rule IDs currently in the database."""
    ids: list[str] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        try:
            r = tx.query(
                'match $r isa projection_rule, has projection_rule_id $rid; '
                'select $rid;'
            ).resolve()
            for row in r.as_concept_rows():
                ids.append(row.get("rid").as_attribute().get_value())
        except Exception as exc:
            logger.error(f"list_rule_ids: {str(exc).splitlines()[0][:200]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return ids


def order_rule_ids(rule_ids: list[str]) -> list[str]:
    """Return rule IDs in execution order:
      1. Mapping-derived rules (basket-level — entity_type, blocker, etc.)
      2. b_aggregate (rule_conv_builder_b_aggregate) — must run before
         sub-sources whose contributes_to references it
      3. Builder sub-source rules (rule_conv_builder_builder_source_*)
      4. Defeater rules (rule_conv_*_defeater) — must run after the
         jcrew_blocker rule emits its prohibition norm
    """
    mapping = []
    b_agg = []
    sub_source = []
    defeaters = []
    other = []
    for rid in rule_ids:
        if rid == "rule_conv_builder_b_aggregate":
            b_agg.append(rid)
        elif rid.startswith("rule_conv_builder_builder_source_"):
            sub_source.append(rid)
        elif rid.endswith("_defeater"):
            defeaters.append(rid)
        elif rid.startswith("rule_conv_"):
            mapping.append(rid)
        else:
            # Unrecognized prefix — append at end so it doesn't break the
            # builder ordering. Reachable when a future rule kind lands.
            other.append(rid)
    return mapping + b_agg + sub_source + defeaters + other


def emit_asset_sale_proceeds_flows(driver, db_name: str, deal_id: str) -> int:
    """Load app/data/asset_sale_proceeds_seed.tql and emit one
    event_provides_proceeds_to_norm edge per record, substituting
    <deal_id> in target norm_ids.

    Rule 5.2 concession (see docs/v4_known_gaps.md). The proceeds_flow
    source is the deal-agnostic event_class entity, which the
    executor's deal-scoped fetch path doesn't currently reach.

    Returns the count of edges successfully inserted.
    """
    if not ASSET_SALE_PROCEEDS_SEED.exists():
        logger.warning(
            "asset_sale_proceeds_seed.tql not found at %s; skipping",
            ASSET_SALE_PROCEEDS_SEED,
        )
        return 0

    seed_text = ASSET_SALE_PROCEEDS_SEED.read_text(encoding="utf-8")
    # The seed file contains multiple match-insert blocks separated by
    # blank lines; each block ends with `;` after the insert. Split on
    # `;\n` boundaries that precede a top-level `match` keyword.
    blocks = []
    current: list[str] = []
    for line in seed_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                # End of block if we hit a blank line and the block is non-empty
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))

    # Count actual inserted edges by querying before/after. TypeDB 3.x
    # match-insert silently no-ops when the match returns 0 rows (no
    # exception raised), so per-block exception counting would over-report.
    def _count_proceeds_edges(driver, db_name) -> int:
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            try:
                r = tx.query(
                    'match $r isa event_provides_proceeds_to_norm; select $r;'
                ).resolve()
                return len(list(r.as_concept_rows()))
            except Exception:
                return 0
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass

    before = _count_proceeds_edges(driver, db_name)
    attempted = 0
    for block in blocks:
        if "match" not in block or "insert" not in block:
            continue
        rendered = block.replace("<deal_id>", deal_id)
        attempted += 1
        wtx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            wtx.query(rendered).resolve()
            wtx.commit()
        except Exception as exc:  # noqa: BLE001
            if wtx.is_open():
                wtx.close()
            logger.warning(
                "asset_sale_proceeds_seed: block raised exception (rare; "
                "match-no-rows is silent): %s",
                str(exc).splitlines()[0][:160],
            )
    after = _count_proceeds_edges(driver, db_name)
    inserted = max(0, after - before)
    if attempted and inserted < attempted:
        logger.info(
            "asset_sale_proceeds_seed: %d/%d blocks emitted (others skipped — "
            "target norm not present for this deal)",
            inserted, attempted,
        )
    return inserted


def emit_asset_sale_governance_norms(driver, db_name: str, deal_id: str) -> int:
    """Phase I.2 — Author 5 v4 norms (3 sweep_tier + 2 carveouts) and
    attach event_governed_by_norm edges to asset_sale_event for `deal_id`.

    The seed (asset_sale_governance_seed.tql) has 5 match-insert blocks.
    Each block uses match-conditional patterns so blocks emit only when
    the relevant v3 substrate exists on this deal:
      - sweep_tier blocks need the corresponding sweep_tier v3 entity
      - 6.05(z) carveout needs permits_section_6_05_z_unlimited=true
      - 2.10(c)(iv) carveout needs permits_product_line_exemption_2_10_c_iv=true

    Idempotent: this helper FIRST deletes any prior governance norms
    matching `_GOVERNANCE_NORM_ID_SUFFIXES` for the deal (and their
    norm_scopes_action / norm_scopes_object / event_governed_by_norm
    edges) BEFORE running the seed inserts. This makes re-runs safe
    without requiring a full clear_v4_projection_for_deal pass.

    Returns the count of norms actually inserted (5 if all blocks match;
    fewer if v3 substrate is partial).
    """
    if not ASSET_SALE_GOVERNANCE_SEED.exists():
        logger.warning(
            "asset_sale_governance_seed.tql not found at %s; skipping",
            ASSET_SALE_GOVERNANCE_SEED,
        )
        return 0

    # Step 1 — idempotent cleanup. Delete prior governance norms +
    # their edges. norm_scopes_action / norm_scopes_object DO cascade
    # when the norm is deleted (per existing behavior of the 23
    # baseline norms), so we delete them explicitly anyway to be
    # defensive.
    target_norm_ids = [f"{deal_id}{suffix}" for suffix in _GOVERNANCE_NORM_ID_SUFFIXES]
    cleanup_queries = []
    for nid in target_norm_ids:
        cleanup_queries.extend([
            (f"egn[{nid}]", f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  $r isa event_governed_by_norm, links (governing_norm: $n);
                delete $r;
            '''),
            (f"nbs[{nid}]", f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  $r isa norm_binds_subject, links (norm: $n);
                delete $r;
            '''),
            (f"nsa[{nid}]", f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  $r isa norm_scopes_action, links (norm: $n);
                delete $r;
            '''),
            (f"nso[{nid}]", f'''
                match
                  $n isa norm, has norm_id "{nid}";
                  $r isa norm_scopes_object, links (norm: $n);
                delete $r;
            '''),
            (f"norm[{nid}]", f'''
                match
                  $n isa norm, has norm_id "{nid}";
                delete $n;
            '''),
        ])
    for label, q in cleanup_queries:
        wtx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            wtx.query(q).resolve()
            wtx.commit()
        except Exception as exc:  # noqa: BLE001
            if wtx.is_open():
                wtx.close()
            # Match-with-no-rows is silent in 3.x; this catches genuine errors.
            msg = str(exc).splitlines()[0][:160]
            if "no rows" not in msg.lower() and "0 rows" not in msg.lower():
                logger.debug(
                    "governance cleanup %s: %s", label, msg,
                )

    # Step 2 — load and run the seed.
    seed_text = ASSET_SALE_GOVERNANCE_SEED.read_text(encoding="utf-8")
    blocks: list[str] = []
    current: list[str] = []
    for line in seed_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))

    def _count_governance_norms(driver, db_name) -> int:
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            try:
                # Count event_governed_by_norm edges as a proxy for
                # governance norms emitted (each block emits exactly 1
                # edge). Cleaner than counting norms by suffix.
                r = tx.query(
                    'match $r isa event_governed_by_norm; select $r;'
                ).resolve()
                return len(list(r.as_concept_rows()))
            except Exception:
                return 0
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass

    before = _count_governance_norms(driver, db_name)
    attempted = 0
    for block in blocks:
        if "match" not in block or "insert" not in block:
            continue
        rendered = block.replace("<deal_id>", deal_id)
        attempted += 1
        wtx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            wtx.query(rendered).resolve()
            wtx.commit()
        except Exception as exc:  # noqa: BLE001
            if wtx.is_open():
                wtx.close()
            logger.warning(
                "asset_sale_governance_seed: block raised exception: %s",
                str(exc).splitlines()[0][:160],
            )
    after = _count_governance_norms(driver, db_name)
    inserted = max(0, after - before)
    logger.info(
        "asset_sale_governance_seed: %d/%d blocks emitted (deal=%s)",
        inserted, attempted, deal_id,
    )
    return inserted


def project_deal(driver, db_name: str, deal_id: str,
                  dry_run: bool = False) -> ExecutionReport:
    """Project all v3 entities for a deal into v4 norms via rule-based
    emission. Replaces deontic_projection.project_deal as part of
    Phase C Commit 4.

    Steps:
      1. Wipe prior v4 output for the deal (idempotent re-runs)
      2. Walk every projection_rule in dependency order, emit via
         execute_rule
      3. Load the asset_sale_proceeds seed (Rule 5.2 concession)
      4. Return aggregate ExecutionReport

    Use dry_run=True to skip writes.
    """
    if not dry_run:
        cleared = clear_v4_projection_for_deal(driver, db_name, deal_id)
        logger.info("project_deal: cleared prior v4 output for deal %s: %s",
                    deal_id, cleared)

    rule_ids = order_rule_ids(list_rule_ids(driver, db_name))
    logger.info("project_deal: executing %d projection_rules", len(rule_ids))

    aggregate = ExecutionReport(rule_id="<aggregate>")
    for rid in rule_ids:
        report = execute_rule(driver, db_name, rid, deal_id, dry_run=dry_run)
        aggregate.matches += report.matches
        aggregate.norms_emitted += report.norms_emitted
        aggregate.relations_emitted += report.relations_emitted
        aggregate.conditions_emitted += report.conditions_emitted
        aggregate.provenance_emitted += report.provenance_emitted
        aggregate.errors.extend(report.errors)
        aggregate.warnings.extend(report.warnings)

    if not dry_run:
        proceeds = emit_asset_sale_proceeds_flows(driver, db_name, deal_id)
        if proceeds:
            logger.info(
                "project_deal: emitted %d event_provides_proceeds_to_norm "
                "edges from seed", proceeds,
            )
        # Phase I.2 — author asset-sale governance norms + event_governed_by_norm
        # edges. Same templated-seed pattern as proceeds_flows; runs after the
        # main projection so the rp_provision and v3 sweep_tier / asset_sale_sweep
        # entities are guaranteed to be present.
        governance = emit_asset_sale_governance_norms(driver, db_name, deal_id)
        if governance:
            logger.info(
                "project_deal: emitted %d asset-sale governance norm(s) + "
                "event_governed_by_norm edge(s) from seed", governance,
            )
            aggregate.norms_emitted += governance
            aggregate.relations_emitted += governance

    logger.info(
        "project_deal: aggregate matches=%d norms_emitted=%d relations_emitted=%d "
        "conditions_emitted=%d provenance_emitted=%d",
        aggregate.matches, aggregate.norms_emitted, aggregate.relations_emitted,
        aggregate.conditions_emitted, aggregate.provenance_emitted,
    )
    return aggregate


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _connect():
    """Open a TypeDB driver using the standard .env config."""
    import os
    from dotenv import load_dotenv
    from typedb.driver import TypeDB, Credentials, DriverOptions

    main_env = Path("C:/Users/olive/ValenceV3/.env")
    if main_env.exists():
        load_dotenv(main_env, override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)

    address = os.environ.get("TYPEDB_ADDRESS")
    username = os.environ.get("TYPEDB_USERNAME")
    password = os.environ.get("TYPEDB_PASSWORD")
    return TypeDB.driver(address, Credentials(username, password), DriverOptions())


def main() -> int:
    import argparse
    import os
    parser = argparse.ArgumentParser(
        description="Project a deal's v3 entities into v4 norms via rule-based "
                    "emission (Phase C Commit 4)."
    )
    parser.add_argument("--deal", required=True, help="deal_id to project")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve attribute values without writing")
    args = parser.parse_args()

    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    if db != "valence_v4":
        logger.error("TYPEDB_DATABASE must be 'valence_v4' (got %r)", db)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    driver = _connect()
    try:
        report = project_deal(driver, db, args.deal, dry_run=args.dry_run)
        if report.errors:
            logger.error("project_deal completed with %d errors:", len(report.errors))
            for err in report.errors[:10]:
                logger.error("  %s", err)
            return 1
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
