"""
Valence v4 — operations layer

Exposes the deontic graph as structured queries. Each public function is one
operation from docs/v4_deontic_architecture.md §6. Per Rule 8.1
(docs/v4_foundational_rules.md §VIII), operations split into:

  STRUCTURAL — pure graph reads, no world-state input:
    describe_norm(deal_id, norm_id) -> dict
    get_attribute(deal_id, entity_id, attribute_name) -> dict
    enumerate_linked(deal_id, entity_id, relation_type, role_played) -> dict
    trace_pathways(deal_id, anchor_type, anchor_value,
                   include_annotations=False) -> dict
    filter_norms(deal_id, criteria) -> dict

  EVALUATED — consumer supplies world_state; response echoes it + a
  computation_trace. (Commit 4.)
    evaluate_feasibility(deal_id, norm_id, supplied_world_state) -> dict
    evaluate_capacity(deal_id, norm_id, supplied_world_state) -> dict

Uniform response envelope:
  {
    "operation":       "<name>",
    "deal_id":         "<id>",
    "parameters":      {<echoed inputs>},
    "result":          {<operation-specific payload>},
    "computation_trace": [...],    # [] for structural; populated for evaluated
    "supplied_world_state": {...}  # evaluated only; omitted for structural
  }

CLI:
  py -3.12 -m app.services.operations <op_name> --deal <deal_id> [...]
  optional --compact for one-line JSON

Database target. By default, operations query valence_v4_ground_truth
because the GT graph is the structural authority for the pilot (Rule 7.2).
Projection output lives in valence_v4 and is validated against GT by the
harness. Override with --db.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Load .env before importing settings so credentials are picked up when this
# module is invoked via `py -3.12 -m app.services.operations`.
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(_REPO_ROOT / ".env", override=False)

from typedb.driver import (  # noqa: E402
    TypeDB, Credentials, DriverOptions, TransactionType,
)

from app.config import settings  # noqa: E402


logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("operations")


DEFAULT_DB = "valence_v4_ground_truth"


# ─── Connection + low-level query helpers ─────────────────────────────────────

def _connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def _rows(tx, q: str) -> list:
    """Run a match-select, return concept rows list (empty on any error)."""
    try:
        return list(tx.query(q).resolve().as_concept_rows())
    except Exception as e:  # noqa: BLE001
        logger.debug("query failed: %s  q=%s", e, q[:140])
        return []


def _attr(row, var: str) -> Any:
    try:
        c = row.get(var)
        if c is None:
            return None
        return c.as_attribute().get_value()
    except Exception:  # noqa: BLE001
        try:
            return row.get(var).as_value().get_value()
        except Exception:
            return None


def _escape(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


# ─── Role maps (relation -> (role_a, role_b, edge_attrs)) ────────────────────
# Populated from schema_v4_deontic.tql §4.7–4.8. Used by enumerate_linked and
# trace_pathways. Keeping this as a Python constant rather than introspecting
# the schema keeps operations fast; new relations added later mean one edit
# here. Explicit is the point (Rule 1.1).

_RELATION_ROLES: dict[str, dict[str, Any]] = {
    "norm_binds_subject":         {"roles": ("norm", "subject"),     "edge_attrs": ()},
    "norm_held_by":               {"roles": ("norm", "beneficiary"), "edge_attrs": ()},
    "norm_scopes_action":         {"roles": ("norm", "action"),      "edge_attrs": ()},
    "norm_scopes_instrument":     {"roles": ("norm", "instrument"),  "edge_attrs": ()},
    "norm_scopes_object":         {"roles": ("norm", "object"),      "edge_attrs": ()},
    "norm_has_condition":         {"roles": ("norm", "root"),        "edge_attrs": ()},
    "norm_has_violation_consequent": {"roles": ("norm", "consequent"), "edge_attrs": ()},
    "norm_extracted_from":        {"roles": ("norm", "fact"),        "edge_attrs": ()},
    "norm_contributes_to_capacity": {
        "roles": ("contributor", "pool"),
        "edge_attrs": ("child_index", "aggregation_function", "aggregation_direction"),
    },
    "norm_provides_carryforward_to": {
        "roles": ("carryforward_source", "carryforward_recipient"),
        "edge_attrs": ("carryforward_years",),
    },
    "norm_provides_carryback_to": {
        "roles": ("carryback_source", "carryback_recipient"),
        "edge_attrs": ("carryback_years",),
    },
    "norm_in_segment":            {"roles": ("norm", "segment"),     "edge_attrs": ()},
    "norm_serves_question":       {"roles": ("norm", "question"),    "edge_attrs": ("serves_role",)},
    "condition_references_predicate": {"roles": ("condition", "predicate"), "edge_attrs": ()},
    "condition_has_child":        {"roles": ("parent", "child"),     "edge_attrs": ("child_index",)},
    "defeater_has_condition":     {"roles": ("defeater", "root"),    "edge_attrs": ()},
    "defeats":                    {"roles": ("defeater", "defeated"), "edge_attrs": ()},
}


# Which attribute to use when looking up an entity by id. Order: most-specific first.
_ID_ATTRIBUTES = [
    "norm_id", "condition_id", "defeater_id", "party_id",
    "event_instance_id", "question_id", "state_predicate_id",
    "basket_id", "blocker_id", "exception_id", "pathway_id",
]


def _type_label(concept) -> str:
    """Extract the concrete type label string from a TypeDB concept."""
    try:
        lbl = concept.get_type().get_label()
        # TypeDB 3.x: label may be a Label object with .name or a bare str
        return lbl.name if hasattr(lbl, "name") else str(lbl)
    except Exception:  # noqa: BLE001
        return "entity"


def _find_entity(tx, entity_id: str) -> tuple[str, str] | None:
    """Probe known id attributes to find an entity matching entity_id.

    Returns (id_attribute_name, entity_type_label) or None.
    """
    for id_attr in _ID_ATTRIBUTES:
        q = f'match $e has {id_attr} "{_escape(entity_id)}"; select $e;'
        rows = _rows(tx, q)
        if not rows:
            continue
        return id_attr, _type_label(rows[0].get("e"))
    return None


def _envelope(operation: str, deal_id: str, parameters: dict, result: Any,
              trace: list | None = None,
              supplied_world_state: dict | None = None) -> dict:
    resp = {
        "operation": operation,
        "deal_id": deal_id,
        "parameters": parameters,
        "result": result,
        "computation_trace": trace if trace is not None else [],
    }
    if supplied_world_state is not None:
        resp["supplied_world_state"] = supplied_world_state
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
#  STRUCTURAL OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════


def describe_norm(deal_id: str, norm_id: str, db: str = DEFAULT_DB) -> dict:
    """Complete structural description of a single norm.

    Returns:
      { operation, deal_id, parameters, result, computation_trace: [] }

    result shape:
      { norm_id, norm_kind, modality,
        subject_roles: [str],
        scoped_actions: [str],
        scoped_objects: [str],          # union of object_class + instrument_class
        capacity_composition, action_scope,
        cap_usd, cap_grower_pct, cap_grower_reference, floor_value,
        condition: <recursive tree or None>,
        source_section, source_text, source_page, confidence,
        contributes_to: [{pool_norm_id, aggregation_function, aggregation_direction, child_index}],
        contributors:   [{contributor_norm_id, aggregation_function, aggregation_direction, child_index}],
        defeaters:      [{defeater_id, defeater_type, defeater_name, condition}],
        serves_questions: [{question_id, serves_role}]
      }
    """
    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            # 1. Norm scalars
            q_scalars = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  try {{ $n has norm_kind $nk; }};
                  try {{ $n has modality $mod; }};
                  try {{ $n has capacity_composition $cc; }};
                  try {{ $n has action_scope $sc; }};
                  try {{ $n has cap_usd $cu; }};
                  try {{ $n has cap_grower_pct $cg; }};
                  try {{ $n has cap_grower_reference $cgr; }};
                  try {{ $n has floor_value $fv; }};
                  try {{ $n has source_section $ss; }};
                  try {{ $n has source_text $st; }};
                  try {{ $n has source_page $sp; }};
                  try {{ $n has confidence $cf; }};
                select $nk, $mod, $cc, $sc, $cu, $cg, $cgr, $fv, $ss, $st, $sp, $cf;
            '''
            rows = _rows(tx, q_scalars)
            if not rows:
                return _envelope("describe_norm", deal_id,
                                 {"norm_id": norm_id, "db": db},
                                 {"error": "norm_not_found", "norm_id": norm_id})

            r = rows[0]
            result = {
                "norm_id": norm_id,
                "norm_kind": _attr(r, "nk"),
                "modality": _attr(r, "mod"),
                "capacity_composition": _attr(r, "cc"),
                "action_scope": _attr(r, "sc"),
                "cap_usd": _attr(r, "cu"),
                "cap_grower_pct": _attr(r, "cg"),
                "cap_grower_reference": _attr(r, "cgr"),
                "floor_value": _attr(r, "fv"),
                "source_section": _attr(r, "ss"),
                "source_text": _attr(r, "st"),
                "source_page": _attr(r, "sp"),
                "confidence": _attr(r, "cf"),
            }

            # 2. Subject roles
            q_sub = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  (norm: $n, subject: $p) isa norm_binds_subject;
                  $p has party_role $r;
                select $r;
            '''
            result["subject_roles"] = sorted({_attr(row, "r") for row in _rows(tx, q_sub)})

            # 3. Scoped actions
            q_act = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  (norm: $n, action: $a) isa norm_scopes_action;
                  $a has action_class_label $l;
                select $l;
            '''
            result["scoped_actions"] = sorted({_attr(row, "l") for row in _rows(tx, q_act)})

            # 4. Scoped objects — union object_class + instrument_class
            q_obj = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  (norm: $n, object: $o) isa norm_scopes_object;
                  $o has object_class_label $l;
                select $l;
            '''
            q_instr = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  (norm: $n, instrument: $i) isa norm_scopes_instrument;
                  $i has instrument_class_label $l;
                select $l;
            '''
            objs = {_attr(row, "l") for row in _rows(tx, q_obj)}
            objs |= {_attr(row, "l") for row in _rows(tx, q_instr)}
            result["scoped_objects"] = sorted(x for x in objs if x is not None)

            # 5. Condition tree (root via norm_has_condition.root; recurse
            #    condition_has_child + condition_references_predicate)
            result["condition"] = _describe_condition_for_norm(tx, norm_id)

            # 6. contributes_to (this norm is the contributor)
            result["contributes_to"] = _list_contribution_edges(tx, norm_id, as_role="contributor")

            # 7. contributors (this norm is the pool)
            result["contributors"] = _list_contribution_edges(tx, norm_id, as_role="pool")

            # 8. Defeaters attached to this norm
            result["defeaters"] = _list_defeaters(tx, norm_id)

            # 9. Serves questions
            q_sq = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  $rel (norm: $n, question: $q) isa norm_serves_question;
                  $q has question_id $qid;
                  try {{ $rel has serves_role $sr; }};
                select $qid, $sr;
            '''
            result["serves_questions"] = [
                {"question_id": _attr(row, "qid"), "serves_role": _attr(row, "sr")}
                for row in _rows(tx, q_sq)
            ]

            return _envelope("describe_norm", deal_id,
                             {"norm_id": norm_id, "db": db},
                             result)
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


def _describe_condition_for_norm(tx, norm_id: str) -> dict | None:
    """Read the condition tree attached to a norm via norm_has_condition:root.

    Returns None if the norm has no condition.
    """
    q_root = f'''
        match
          $n isa norm, has norm_id "{_escape(norm_id)}";
          (norm: $n, root: $c) isa norm_has_condition;
          $c has condition_id $cid;
          try {{ $c has condition_operator $op; }};
          try {{ $c has condition_topology $topo; }};
        select $cid, $op, $topo;
    '''
    rows = _rows(tx, q_root)
    if not rows:
        return None
    r = rows[0]
    cid = _attr(r, "cid")
    tree = _describe_condition(tx, cid)
    if tree is not None:
        tree["topology"] = _attr(r, "topo")
    return tree


def _describe_condition(tx, condition_id: str) -> dict | None:
    """Recursive condition-tree description."""
    q = f'''
        match
          $c isa condition, has condition_id "{_escape(condition_id)}";
          try {{ $c has condition_operator $op; }};
        select $op;
    '''
    rows = _rows(tx, q)
    if not rows:
        return None
    op = _attr(rows[0], "op")

    out: dict = {"condition_id": condition_id, "operator": op}

    if op == "atomic":
        q_pred = f'''
            match
              $c isa condition, has condition_id "{_escape(condition_id)}";
              (condition: $c, predicate: $p) isa condition_references_predicate;
              $p has state_predicate_id $pid;
              try {{ $p has state_predicate_label $lab; }};
              try {{ $p has threshold_value_double $tv; }};
              try {{ $p has operator_comparison $oc; }};
              try {{ $p has reference_predicate_label $rpl; }};
            select $pid, $lab, $tv, $oc, $rpl;
        '''
        rows_p = _rows(tx, q_pred)
        if rows_p:
            rp = rows_p[0]
            out["predicate_ref"] = {
                "state_predicate_id": _attr(rp, "pid"),
                "state_predicate_label": _attr(rp, "lab"),
                "threshold_value_double": _attr(rp, "tv"),
                "operator_comparison": _attr(rp, "oc"),
                "reference_predicate_label": _attr(rp, "rpl"),
            }
        return out

    # Compound (or/and) — walk children.
    # Note: we cannot combine the relation pattern + `try { $rel has
    # child_index ... }` in one query — TypeDB 3.x reports
    # "'rel' cannot be declared as both a 'Object' and as a 'ThingType'"
    # when the same variable is referenced via isa-pattern and via has-try
    # inside one match. Two queries instead.
    q_children = f'''
        match
          $p isa condition, has condition_id "{_escape(condition_id)}";
          (parent: $p, child: $ch) isa condition_has_child;
          $ch has condition_id $chid;
        select $chid;
    '''
    child_ids = [_attr(row, "chid") for row in _rows(tx, q_children)]

    # Per-child index fetch (optional, may be absent).
    def _child_idx(child_id: str) -> int | None:
        q = f'''
            match
              $p isa condition, has condition_id "{_escape(condition_id)}";
              $ch isa condition, has condition_id "{_escape(child_id)}";
              $rel (parent: $p, child: $ch) isa condition_has_child;
              try {{ $rel has child_index $idx; }};
            select $idx;
        '''
        rows = _rows(tx, q)
        return _attr(rows[0], "idx") if rows else None

    children_pairs = [(_child_idx(cid) if _child_idx(cid) is not None else 0, cid)
                      for cid in child_ids]
    children_pairs.sort(key=lambda x: x[0])
    out["children"] = [_describe_condition(tx, cid) for _, cid in children_pairs]
    return out


def _list_contribution_edges(tx, norm_id: str, as_role: str) -> list[dict]:
    """List norm_contributes_to_capacity edges. as_role ∈ {contributor, pool}."""
    other_role = "pool" if as_role == "contributor" else "contributor"
    q = f'''
        match
          $n isa norm, has norm_id "{_escape(norm_id)}";
          $rel ({as_role}: $n, {other_role}: $other) isa norm_contributes_to_capacity;
          $other has norm_id $oid;
          try {{ $rel has aggregation_function $af; }};
          try {{ $rel has aggregation_direction $ad; }};
          try {{ $rel has child_index $ci; }};
        select $oid, $af, $ad, $ci;
    '''
    out = []
    for row in _rows(tx, q):
        key = "pool_norm_id" if as_role == "contributor" else "contributor_norm_id"
        out.append({
            key: _attr(row, "oid"),
            "aggregation_function": _attr(row, "af"),
            "aggregation_direction": _attr(row, "ad"),
            "child_index": _attr(row, "ci"),
        })
    out.sort(key=lambda d: (d.get("child_index") is None, d.get("child_index")))
    return out


def _list_defeaters(tx, norm_id: str) -> list[dict]:
    """List defeaters attached to a norm via defeats."""
    q = f'''
        match
          $n isa norm, has norm_id "{_escape(norm_id)}";
          (defeater: $d, defeated: $n) isa defeats;
          $d has defeater_id $did;
          try {{ $d has defeater_type $dt; }};
          try {{ $d has defeater_name $dn; }};
          try {{ $d has source_section $ss; }};
          try {{ $d has source_text $st; }};
          try {{ $d has source_page $sp; }};
        select $did, $dt, $dn, $ss, $st, $sp;
    '''
    out = []
    for row in _rows(tx, q):
        did = _attr(row, "did")
        # Optionally describe the defeater's own condition via defeater_has_condition
        q_cond = f'''
            match
              $d isa defeater, has defeater_id "{_escape(did)}";
              (defeater: $d, root: $c) isa defeater_has_condition;
              $c has condition_id $cid;
            select $cid;
        '''
        c_rows = _rows(tx, q_cond)
        cond = _describe_condition(tx, _attr(c_rows[0], "cid")) if c_rows else None
        out.append({
            "defeater_id": did,
            "defeater_type": _attr(row, "dt"),
            "defeater_name": _attr(row, "dn"),
            "source_section": _attr(row, "ss"),
            "source_text": _attr(row, "st"),
            "source_page": _attr(row, "sp"),
            "condition": cond,
        })
    return out


def get_attribute(deal_id: str, entity_id: str, attribute_name: str,
                  db: str = DEFAULT_DB) -> dict:
    """Fetch a single attribute value from a named entity.

    Returns an envelope with result.value = the attribute value (or null if
    missing / entity not found).
    """
    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            found = _find_entity(tx, entity_id)
            if not found:
                return _envelope("get_attribute", deal_id,
                                 {"entity_id": entity_id, "attribute_name": attribute_name, "db": db},
                                 {"value": None, "error": "entity_not_found",
                                  "entity_id": entity_id, "entity_type": None,
                                  "attribute_name": attribute_name})
            id_attr, entity_type = found
            q = f'''
                match
                  $e has {id_attr} "{_escape(entity_id)}";
                  try {{ $e has {attribute_name} $v; }};
                select $v;
            '''
            rows = _rows(tx, q)
            if not rows or rows[0].get("v") is None:
                return _envelope("get_attribute", deal_id,
                                 {"entity_id": entity_id, "attribute_name": attribute_name, "db": db},
                                 {"value": None, "entity_id": entity_id,
                                  "entity_type": entity_type,
                                  "attribute_name": attribute_name})
            val = _attr(rows[0], "v")
            return _envelope("get_attribute", deal_id,
                             {"entity_id": entity_id, "attribute_name": attribute_name, "db": db},
                             {"value": val, "entity_id": entity_id,
                              "entity_type": entity_type,
                              "attribute_name": attribute_name})
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


def enumerate_linked(deal_id: str, entity_id: str, relation_type: str,
                     role_played: str, db: str = DEFAULT_DB) -> dict:
    """List entities linked to entity_id via relation_type playing role_played.

    Direct edges only (no transitive closure). Returns linked entities + edge
    attributes. Roles and edge-attribute schema sourced from
    _RELATION_ROLES (mirrors schema_v4_deontic.tql §4.7-4.8).
    """
    params = {"entity_id": entity_id, "relation_type": relation_type,
              "role_played": role_played, "db": db}

    spec = _RELATION_ROLES.get(relation_type)
    if spec is None:
        return _envelope("enumerate_linked", deal_id, params,
                         {"error": f"unknown_relation_type: {relation_type}",
                          "known_relations": sorted(_RELATION_ROLES.keys()),
                          "linked": []})
    roles = spec["roles"]
    if role_played not in roles:
        return _envelope("enumerate_linked", deal_id, params,
                         {"error": f"role_played {role_played!r} not in relation roles {roles}",
                          "linked": []})
    other_role = roles[1] if role_played == roles[0] else roles[0]
    edge_attrs = spec["edge_attrs"]

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            found = _find_entity(tx, entity_id)
            if not found:
                return _envelope("enumerate_linked", deal_id, params,
                                 {"error": "entity_not_found", "linked": []})
            id_attr, anchor_entity_type = found

            # TypeDB 3.x quirk (docs/typedb_patterns.md #1bis): a relation
            # variable `$rel (role: $var) isa $reltype` combined with
            # `try { $rel has attr $v }` trips "variable cannot be both
            # Object and ThingType." Workaround seen in v3 graph_storage.py:
            # use `$rel isa <reltype>, links (role: $var, ...)` syntax, which
            # binds $rel unambiguously as a relation instance.
            #
            # Other-entity id fetch: we don't know what id attribute the
            # other entity uses (norm, condition, party, etc.). Probe
            # _ID_ATTRIBUTES in order and take the first that binds.
            edge_try_clauses = "\n        ".join(
                f'try {{ $rel has {a} ${a}; }};' for a in edge_attrs
            )
            edge_select = ", ".join(f"${a}" for a in edge_attrs)
            edge_select_prefix = (", " + edge_select) if edge_select else ""

            # One query per candidate id attribute for the other side. First
            # that returns rows wins.
            linked: list[dict] = []
            for other_id_attr in _ID_ATTRIBUTES:
                q = f'''
                    match
                      $a has {id_attr} "{_escape(entity_id)}";
                      $other has {other_id_attr} $oid;
                      $rel isa {relation_type},
                        links ({role_played}: $a, {other_role}: $other);
                      {edge_try_clauses}
                    select $oid, $other{edge_select_prefix};
                '''
                rows = _rows(tx, q)
                if not rows:
                    continue
                for row in rows:
                    oid = _attr(row, "oid")
                    other_type = _type_label(row.get("other"))
                    edge_data = {a: _attr(row, a) for a in edge_attrs}
                    linked.append({
                        "entity_id": oid,
                        "entity_type": other_type,
                        "role": other_role,
                        "edge_attributes": edge_data,
                    })
                break  # first id attribute that yields rows is definitive

            # Dedupe by (entity_id, edge_attributes) — rare, but e.g.
            # condition_has_child is single-valued per parent/child pair.
            seen = set()
            unique = []
            for item in linked:
                key = (item["entity_id"],
                       tuple(sorted((item["edge_attributes"] or {}).items())))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            unique.sort(key=lambda d: (d["entity_id"] or ""))
            return _envelope("enumerate_linked", deal_id, params,
                             {"count": len(unique),
                              "anchor_entity_type": anchor_entity_type,
                              "linked": unique})
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  TRACE_PATHWAYS — polymorphic anchor (action_class or state_predicate)
# ═══════════════════════════════════════════════════════════════════════════════


def trace_pathways(deal_id: str, anchor_type: str, anchor_value: str,
                   include_annotations: bool = False,
                   db: str = DEFAULT_DB) -> dict:
    """Trace pathways through the deontic graph from an anchor.

    Two anchor types:
      - "action_class": returns all norms scoping to the action class,
        grouped by modality. Each norm's entry includes its contributor
        chain (walked up via norm_contributes_to_capacity), any conditions
        it carries, and any defeaters targeting it.
      - "state_predicate": returns all norms whose condition tree
        references the given state_predicate_id, with the path through
        the tree to the referencing leaf.

    include_annotations: when True, attach source_text / source_section
    excerpts alongside each node. When False, pure structure.
    """
    params = {
        "anchor_type": anchor_type,
        "anchor_value": anchor_value,
        "include_annotations": include_annotations,
        "db": db,
    }

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            if anchor_type == "action_class":
                result = _trace_from_action_class(
                    tx, anchor_value, include_annotations)
            elif anchor_type == "state_predicate":
                result = _trace_from_state_predicate(
                    tx, anchor_value, include_annotations)
            else:
                result = {
                    "error": f"unknown anchor_type: {anchor_type}",
                    "supported": ["action_class", "state_predicate"],
                }
            return _envelope("trace_pathways", deal_id, params, result)
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


def _trace_from_action_class(tx, action_label: str,
                              include_annotations: bool) -> dict:
    """Find all norms scoping to action_label, group by modality,
    attach contributor chains, conditions, defeaters."""
    # 1. Norms that scope directly to this action.
    q = f'''
        match
          $a isa action_class, has action_class_label "{_escape(action_label)}";
          (norm: $n, action: $a) isa norm_scopes_action;
          $n has norm_id $nid;
          try {{ $n has modality $mod; }};
          try {{ $n has norm_kind $nk; }};
          try {{ $n has capacity_composition $cc; }};
          try {{ $n has cap_usd $cu; }};
          try {{ $n has cap_grower_pct $cg; }};
          try {{ $n has action_scope $sc; }};
          try {{ $n has source_section $ss; }};
          try {{ $n has source_text $st; }};
        select $nid, $mod, $nk, $cc, $cu, $cg, $sc, $ss, $st;
    '''
    rows = _rows(tx, q)

    permissions: list[dict] = []
    prohibitions: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        nid = _attr(row, "nid")
        if nid in seen:
            continue
        seen.add(nid)

        node = {
            "norm_id": nid,
            "norm_kind": _attr(row, "nk"),
            "capacity_composition": _attr(row, "cc"),
            "cap_usd": _attr(row, "cu"),
            "cap_grower_pct": _attr(row, "cg"),
            "action_scope": _attr(row, "sc"),
            # contributor-chain walk-up
            "contributes_to_chain": _walk_contribution_chain(tx, nid),
            # any condition tree
            "conditions_required": _describe_condition_for_norm(tx, nid),
            # defeaters attached
            "defeaters_potential": _list_defeaters(tx, nid),
        }
        if include_annotations:
            node["source_section"] = _attr(row, "ss")
            node["source_text"] = _truncate(_attr(row, "st"), 200)

        modality = _attr(row, "mod")
        if modality == "prohibition":
            prohibitions.append(node)
        else:
            permissions.append(node)

    permissions.sort(key=lambda n: n["norm_id"])
    prohibitions.sort(key=lambda n: n["norm_id"])

    return {
        "anchor": {"type": "action_class", "value": action_label},
        "permissions": permissions,
        "prohibitions": prohibitions,
        "summary": {
            "permission_count": len(permissions),
            "prohibition_count": len(prohibitions),
        },
    }


def _walk_contribution_chain(tx, norm_id: str, max_hops: int = 5,
                              seen: set[str] | None = None) -> list[dict]:
    """Walk up norm_contributes_to_capacity from contributor -> pool.

    Returns a list describing the chain from the starting norm to each
    reachable pool. Each list entry is one hop: {pool_norm_id,
    aggregation_function, aggregation_direction}. Stops at max_hops or
    on cycle. Stores nothing if the norm doesn't contribute to anything.
    """
    if seen is None:
        seen = set()
    if norm_id in seen or max_hops <= 0:
        return []
    seen.add(norm_id)

    q = f'''
        match
          $n isa norm, has norm_id "{_escape(norm_id)}";
          $rel isa norm_contributes_to_capacity,
            links (contributor: $n, pool: $pool);
          $pool has norm_id $pid;
          try {{ $rel has aggregation_function $af; }};
          try {{ $rel has aggregation_direction $ad; }};
        select $pid, $af, $ad;
    '''
    rows = _rows(tx, q)
    chain = []
    for row in rows:
        pid = _attr(row, "pid")
        hop = {
            "pool_norm_id": pid,
            "aggregation_function": _attr(row, "af"),
            "aggregation_direction": _attr(row, "ad"),
        }
        # Recurse to the pool's parent pool (if any)
        hop["parent_chain"] = _walk_contribution_chain(
            tx, pid, max_hops=max_hops - 1, seen=seen)
        chain.append(hop)
    return chain


def _trace_from_state_predicate(tx, predicate_id: str,
                                  include_annotations: bool) -> dict:
    """Find all norms whose condition tree references this predicate.

    For each referencing norm, report:
      - norm_id + minimal structure (kind, modality, source_section)
      - condition_path: list of condition_ids from root to the atomic
        leaf that references this predicate
      - logical_role: how the atomic sits in its parent — atomic (no
        parent), or_branch (parent is `or`), or and_branch (parent is
        `and`)
    """
    # Find conditions that reference this predicate directly.
    q_refs = f'''
        match
          $p isa state_predicate, has state_predicate_id "{_escape(predicate_id)}";
          (condition: $c, predicate: $p) isa condition_references_predicate;
          $c has condition_id $cid;
        select $cid;
    '''
    ref_rows = _rows(tx, q_refs)
    atomic_ids = [_attr(r, "cid") for r in ref_rows]

    referencing: list[dict] = []
    for atomic_cid in atomic_ids:
        # Walk up to root via condition_has_child (parent).
        path = _walk_condition_up(tx, atomic_cid)
        root_cid = path[0] if path else atomic_cid

        # Find the norm via norm_has_condition:root on the path's root.
        q_norm = f'''
            match
              $c isa condition, has condition_id "{_escape(root_cid)}";
              (norm: $n, root: $c) isa norm_has_condition;
              $n has norm_id $nid;
              try {{ $n has modality $mod; }};
              try {{ $n has norm_kind $nk; }};
              try {{ $n has source_section $ss; }};
              try {{ $n has source_text $st; }};
            select $nid, $mod, $nk, $ss, $st;
        '''
        n_rows = _rows(tx, q_norm)
        if not n_rows:
            # May belong to a defeater instead
            q_def = f'''
                match
                  $c isa condition, has condition_id "{_escape(root_cid)}";
                  (defeater: $d, root: $c) isa defeater_has_condition;
                  $d has defeater_id $did;
                select $did;
            '''
            d_rows = _rows(tx, q_def)
            if d_rows:
                referencing.append({
                    "referrer_type": "defeater",
                    "defeater_id": _attr(d_rows[0], "did"),
                    "condition_path_from_root": path + [atomic_cid] if len(path) > 0 and path[-1] != atomic_cid else [atomic_cid],
                    "logical_role": _atomic_logical_role(tx, atomic_cid),
                })
            continue

        row = n_rows[0]
        node = {
            "referrer_type": "norm",
            "norm_id": _attr(row, "nid"),
            "modality": _attr(row, "mod"),
            "norm_kind": _attr(row, "nk"),
            "condition_path_from_root": path + [atomic_cid] if (not path or path[-1] != atomic_cid) else path,
            "logical_role": _atomic_logical_role(tx, atomic_cid),
        }
        if include_annotations:
            node["source_section"] = _attr(row, "ss")
            node["source_text"] = _truncate(_attr(row, "st"), 200)
        referencing.append(node)

    return {
        "anchor": {"type": "state_predicate", "value": predicate_id},
        "referencing_norms": [r for r in referencing if r.get("referrer_type") == "norm"],
        "referencing_defeaters": [r for r in referencing if r.get("referrer_type") == "defeater"],
        "summary": {
            "norm_count": sum(1 for r in referencing if r.get("referrer_type") == "norm"),
            "defeater_count": sum(1 for r in referencing if r.get("referrer_type") == "defeater"),
        },
    }


def _walk_condition_up(tx, condition_id: str, max_hops: int = 8) -> list[str]:
    """Walk condition_has_child from child to parent until no parent found.

    Returns list of condition_ids from root to the child BEFORE the
    starting condition_id (exclusive of the starting id). Empty list if
    the starting condition is already a root.
    """
    path: list[str] = []
    current = condition_id
    hops = 0
    while hops < max_hops:
        q = f'''
            match
              $ch isa condition, has condition_id "{_escape(current)}";
              (parent: $p, child: $ch) isa condition_has_child;
              $p has condition_id $pid;
            select $pid;
        '''
        rows = _rows(tx, q)
        if not rows:
            break
        pid = _attr(rows[0], "pid")
        path.append(pid)
        current = pid
        hops += 1
    path.reverse()  # root-first
    return path


def _atomic_logical_role(tx, atomic_cid: str) -> str:
    """Determine the logical role of an atomic condition: atomic (root,
    no parent), or_branch, or and_branch."""
    q = f'''
        match
          $ch isa condition, has condition_id "{_escape(atomic_cid)}";
          (parent: $p, child: $ch) isa condition_has_child;
          $p has condition_operator $op;
        select $op;
    '''
    rows = _rows(tx, q)
    if not rows:
        return "atomic"
    op = _attr(rows[0], "op")
    if op == "or":
        return "or_branch"
    if op == "and":
        return "and_branch"
    return op or "atomic"


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n - 1].rstrip() + "\u2026"


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════


def _print(resp: dict, compact: bool) -> None:
    if compact:
        print(json.dumps(resp, default=str))
    else:
        print(json.dumps(resp, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valence v4 operations layer CLI",
    )
    sub = parser.add_subparsers(dest="op", required=True)
    parser.add_argument("--compact", action="store_true",
                        help="Print single-line JSON instead of pretty.")

    # describe_norm
    p_dn = sub.add_parser("describe_norm")
    p_dn.add_argument("--deal", required=True)
    p_dn.add_argument("--norm", required=True, help="norm_id")
    p_dn.add_argument("--db", default=DEFAULT_DB)
    p_dn.add_argument("--compact", action="store_true")

    # get_attribute
    p_ga = sub.add_parser("get_attribute")
    p_ga.add_argument("--deal", required=True)
    p_ga.add_argument("--entity", required=True)
    p_ga.add_argument("--attr", required=True)
    p_ga.add_argument("--db", default=DEFAULT_DB)
    p_ga.add_argument("--compact", action="store_true")

    # enumerate_linked
    p_el = sub.add_parser("enumerate_linked")
    p_el.add_argument("--deal", required=True)
    p_el.add_argument("--entity", required=True)
    p_el.add_argument("--relation", required=True)
    p_el.add_argument("--role", required=True)
    p_el.add_argument("--db", default=DEFAULT_DB)
    p_el.add_argument("--compact", action="store_true")

    # trace_pathways
    p_tp = sub.add_parser("trace_pathways")
    p_tp.add_argument("--deal", required=True)
    p_tp.add_argument("--anchor-type", required=True,
                      choices=["action_class", "state_predicate"])
    p_tp.add_argument("--anchor-value", required=True,
                      help="e.g., make_dividend_payment OR "
                           "'first_lien_net_leverage_at_or_below|5.75|at_or_below|None'")
    ann_group = p_tp.add_mutually_exclusive_group()
    ann_group.add_argument("--with-annotations", action="store_true",
                           dest="annotations")
    ann_group.add_argument("--no-annotations", action="store_false",
                           dest="annotations", default=False)
    p_tp.add_argument("--db", default=DEFAULT_DB)
    p_tp.add_argument("--compact", action="store_true")

    args = parser.parse_args()
    compact = bool(getattr(args, "compact", False))

    if args.op == "describe_norm":
        resp = describe_norm(args.deal, args.norm, db=args.db)
    elif args.op == "get_attribute":
        resp = get_attribute(args.deal, args.entity, args.attr, db=args.db)
    elif args.op == "enumerate_linked":
        resp = enumerate_linked(args.deal, args.entity, args.relation, args.role, db=args.db)
    elif args.op == "trace_pathways":
        resp = trace_pathways(
            args.deal, args.anchor_type, args.anchor_value,
            include_annotations=args.annotations, db=args.db)
    else:
        parser.error(f"unknown op: {args.op}")
        return 2

    _print(resp, compact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
