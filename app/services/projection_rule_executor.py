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
  literal_integer_value_source      -> literal_integer_value
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
    """Read the rule's entity_type_criterion (assumes simple single-type
    match for Commit 1.5; expanded in future commits)."""
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


def load_attribute_emissions(driver, db_name: str, rule_id: str) -> list[tuple[str, str]]:
    """Read every (attribute_name, value_source_id) pair the rule emits.
    Returns list of tuples: (emitted_attribute_name, value_source_iid).
    The iid is used to walk the value_source subgraph at resolution time.
    """
    pairs: list[tuple[str, str]] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $r isa projection_rule, has projection_rule_id "{rule_id}";\n'
            f'    (owning_rule: $r, produced_template: $nt) isa rule_produces_norm_template;\n'
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


def resolve_value_source(driver, db_name: str, vs_iid: str, ctx: ExecutorContext) -> Any:
    """Walk a value_source subgraph and produce a Python value.

    Dispatched on the value_source's concrete type. Recursive for
    composition (concatenation, arithmetic, conditional).
    """
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        # Identify the concrete type. Use isa! for exact-type match (avoids
        # the polymorphic default which would return abstract parents).
        q = f'match $vs iid {vs_iid}, isa! $type; select $type;'
        try:
            result = tx.query(q).resolve()
            row = next(iter(result.as_concept_rows()), None)
            if row is None:
                return None
            type_label = row.get("type").get_label()
        except Exception as exc:
            logger.warning(f"resolve: type lookup for {vs_iid} failed: {str(exc).splitlines()[0][:100]}")
            return None
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    # Dispatch
    if type_label == "literal_string_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_string_value")
    if type_label == "literal_double_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_double_value")
    if type_label == "literal_integer_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_integer_value")
    if type_label == "literal_boolean_value_source":
        return _read_attr_value(driver, db_name, vs_iid, "literal_boolean_value")
    if type_label == "deal_id_value_source":
        return ctx.deal_id
    if type_label == "v3_attribute_value_source":
        attr_name = _read_attr_value(driver, db_name, vs_iid, "reads_v3_attribute_name")
        if attr_name is None:
            return None
        v = ctx.v3_attrs.get(attr_name)
        if v is not None:
            return v
        # Fallback: walk value_source_has_default if present
        return _resolve_default_fallback(driver, db_name, vs_iid, ctx)
    if type_label == "concatenation_value_source":
        return _resolve_concatenation(driver, db_name, vs_iid, ctx)
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


def _read_attr_value(driver, db_name: str, owner_iid: str, attr_name: str):
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = f'match $x iid {owner_iid}, has {attr_name} $v; select $v;'
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                return row.get("v").as_attribute().get_value()
        except Exception:
            return None
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return None


def _resolve_default_fallback(driver, db_name: str, vs_iid: str, ctx: ExecutorContext) -> Any:
    """If a v3_attribute_value_source has a value_source_has_default edge,
    resolve the default value source. Returns None if no default is wired."""
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $primary iid {vs_iid};\n'
            f'    (primary_source: $primary, default_source: $default) isa value_source_has_default;\n'
            f'select $default;\n'
        )
        try:
            result = tx.query(q).resolve()
            row = next(iter(result.as_concept_rows()), None)
            if row is None:
                return None
            default_iid = row.get("default").get_iid()
        except Exception:
            return None
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return resolve_value_source(driver, db_name, default_iid, ctx)


def _resolve_concatenation(driver, db_name: str, vs_iid: str, ctx: ExecutorContext) -> str:
    """Walk concatenation_has_ordered_part edges in sequence_index order
    and concatenate resolved parts."""
    parts: list[tuple[int, str]] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $cat iid {vs_iid};\n'
            f'    (owning_concatenation: $cat, concatenation_part: $p) isa concatenation_has_ordered_part,\n'
            f'        has sequence_index $idx;\n'
            f'select $p, $idx;\n'
        )
        try:
            result = tx.query(q).resolve()
            for row in result.as_concept_rows():
                idx = row.get("idx").as_attribute().get_value()
                part_iid = row.get("p").get_iid()
                parts.append((idx, part_iid))
        except Exception as exc:
            logger.warning(f"resolve_concat for {vs_iid} failed: {str(exc).splitlines()[0][:120]}")
            return ""
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    parts.sort(key=lambda t: t[0])
    resolved = []
    for _, part_iid in parts:
        part_value = resolve_value_source(driver, db_name, part_iid, ctx)
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
    their attributes as Python dicts."""
    matches: list[dict] = []
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        q = (
            f'match\n'
            f'    $d isa deal, has deal_id "{deal_id}";\n'
            f'    (deal: $d, provision: $p) isa deal_has_provision;\n'
            f'    (provision: $p, extracted: $b) isa provision_has_extracted_entity;\n'
            f'    $b isa {entity_type}, has $attr;\n'
            f'select $b, $attr;\n'
        )
        try:
            result = tx.query(q).resolve()
            entity_attrs: dict[str, dict] = {}  # iid -> attrs
            for row in result.as_concept_rows():
                bid = row.get("b").get_iid()
                attr = row.get("attr").as_attribute()
                attr_label = attr.get_type().get_label()
                attr_value = attr.get_value()
                if bid not in entity_attrs:
                    entity_attrs[bid] = {"_iid": bid}
                entity_attrs[bid][attr_label] = attr_value
            matches = list(entity_attrs.values())
        except Exception as exc:
            logger.warning(f"fetch_v3 for {entity_type} failed: {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return matches


def emit_norm(driver, db_name: str, attrs: dict[str, Any], dry_run: bool = False) -> bool:
    """Emit a single norm with the resolved attributes."""
    if not attrs.get("norm_id"):
        logger.warning("emit_norm: missing norm_id; skipping")
        return False
    owns_clauses = []
    for name, value in attrs.items():
        if value is None:
            continue
        try:
            owns_clauses.append(f"has {name} {_tq_literal(value)}")
        except ValueError as exc:
            logger.warning(f"emit_norm: skipping {name}: {exc}")
    insert_q = f"insert $n isa norm, {', '.join(owns_clauses)};"

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
        logger.error(f"emit_norm failed for {attrs.get('norm_id')}: {str(exc).splitlines()[0][:200]}")
        return False


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

    emissions = load_attribute_emissions(driver, db_name, rule_id)
    if not emissions:
        report.errors.append("no attribute_emissions found on norm_template")
        return report
    logger.info(f"rule {rule_id}: {len(emissions)} attribute emissions")

    matches = fetch_v3_entity_attrs(driver, db_name, entity_type, deal_id)
    report.matches = len(matches)
    logger.info(f"rule {rule_id}: matched {len(matches)} v3 entities")

    for v3_attrs in matches:
        ctx = ExecutorContext(deal_id=deal_id, v3_attrs=v3_attrs)
        resolved: dict[str, Any] = {}
        for attr_name, vs_iid in emissions:
            try:
                resolved[attr_name] = resolve_value_source(driver, db_name, vs_iid, ctx)
            except NotImplementedError as exc:
                report.errors.append(f"resolve {attr_name}: {exc}")
                resolved[attr_name] = None
            except Exception as exc:
                report.warnings.append(f"resolve {attr_name}: {str(exc).splitlines()[0][:120]}")
                resolved[attr_name] = None

        if emit_norm(driver, db_name, resolved, dry_run=dry_run):
            report.norms_emitted += 1

    return report
