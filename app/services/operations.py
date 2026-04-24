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
                   collapse_contributors: bool = True,
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

    collapse_contributors: when True (default), filter out norms whose
    norm_contributes_to_capacity parent is already present in the
    result set. These norms are visible inside their parent's
    contribution chain; surfacing them as top-level entries is usually
    noise (builder sub-sources swamping the dividend-action list).
    Contributors whose parent is NOT in the result set are kept — they
    can stand alone. Pass False to preserve the raw scoping set.
    """
    params = {
        "anchor_type": anchor_type,
        "anchor_value": anchor_value,
        "include_annotations": include_annotations,
        "collapse_contributors": collapse_contributors,
        "db": db,
    }

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            if anchor_type == "action_class":
                result = _trace_from_action_class(
                    tx, anchor_value, include_annotations,
                    collapse_contributors=collapse_contributors)
            elif anchor_type == "state_predicate":
                result = _trace_from_state_predicate(
                    tx, anchor_value, include_annotations,
                    collapse_contributors=collapse_contributors)
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


def _contributors_whose_pool_is_in(tx, norm_ids: list[str]) -> set[str]:
    """Return the subset of norm_ids whose outgoing
    norm_contributes_to_capacity edge points at a pool that is ALSO in
    norm_ids.

    These are the contributors a caller would want to collapse away
    when surfacing top-level pathways — the parent pool already
    represents them. Contributors whose pool isn't in the input list
    are excluded from the returned set (callers keep them visible).
    """
    if not norm_ids:
        return set()
    # Build a single query that filters contributors whose pool norm_id
    # is in the given list. TypeDB 3.x supports inline literal sets via
    # disjunction; building one OR-clause per id keeps the query simple.
    pool_ors = " or ".join(
        f'{{ $pool has norm_id "{_escape(nid)}"; }}'
        for nid in norm_ids
    )
    q = f'''
        match
          $n isa norm, has norm_id $nid;
          $rel isa norm_contributes_to_capacity,
            links (contributor: $n, pool: $pool);
          {pool_ors};
        select $nid;
    '''
    rows = _rows(tx, q)
    in_set = {nid for nid in norm_ids}
    return {_attr(r, "nid") for r in rows
            if _attr(r, "nid") in in_set}


def _trace_from_action_class(tx, action_label: str,
                              include_annotations: bool,
                              collapse_contributors: bool = True) -> dict:
    """Find all norms scoping to action_label, group by modality,
    attach contributor chains, conditions, defeaters.

    When collapse_contributors is True, norms whose
    norm_contributes_to_capacity parent is also in the scoping set are
    removed from top-level permissions/prohibitions (they remain
    visible inside the parent's contributes_to_chain).
    """
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

    collapsed_ids: list[str] = []
    if collapse_contributors:
        all_ids = [n["norm_id"] for n in permissions + prohibitions]
        collapsed = _contributors_whose_pool_is_in(tx, all_ids)
        if collapsed:
            collapsed_ids = sorted(collapsed)
            permissions = [n for n in permissions if n["norm_id"] not in collapsed]
            prohibitions = [n for n in prohibitions if n["norm_id"] not in collapsed]

    return {
        "anchor": {"type": "action_class", "value": action_label},
        "permissions": permissions,
        "prohibitions": prohibitions,
        "summary": {
            "permission_count": len(permissions),
            "prohibition_count": len(prohibitions),
            "collapsed_contributors": collapsed_ids,
            "collapsed_count": len(collapsed_ids),
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
                                  include_annotations: bool,
                                  collapse_contributors: bool = True) -> dict:
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

    referencing_norms = [r for r in referencing if r.get("referrer_type") == "norm"]
    referencing_defeaters = [r for r in referencing if r.get("referrer_type") == "defeater"]

    collapsed_ids: list[str] = []
    if collapse_contributors and referencing_norms:
        ids = [r["norm_id"] for r in referencing_norms]
        collapsed = _contributors_whose_pool_is_in(tx, ids)
        if collapsed:
            collapsed_ids = sorted(collapsed)
            referencing_norms = [
                r for r in referencing_norms
                if r["norm_id"] not in collapsed
            ]

    return {
        "anchor": {"type": "state_predicate", "value": predicate_id},
        "referencing_norms": referencing_norms,
        "referencing_defeaters": referencing_defeaters,
        "summary": {
            "norm_count": len(referencing_norms),
            "defeater_count": len(referencing_defeaters),
            "collapsed_contributors": collapsed_ids,
            "collapsed_count": len(collapsed_ids),
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
#  FILTER_NORMS — declarative criteria filter
# ═══════════════════════════════════════════════════════════════════════════════


# Criterion-name → (TypeQL clause template, select_vars_to_add_if_any)
# Each template references `$n` as the norm and injects clauses based on
# the criterion value. "contains" is TypeDB 3.x's starts-with/substring
# test on string attributes.
_FILTER_CRITERIA: dict[str, str] = {
    "modality":              'modality',
    "action_scope":          'action_scope',
    "capacity_composition":  'capacity_composition',
    "norm_kind":             'norm_kind',
}


def filter_norms(deal_id: str, criteria: dict, db: str = DEFAULT_DB) -> dict:
    """Return norms matching every criterion in the provided dict.

    Supported criteria keys:
      - modality, action_scope, capacity_composition: exact string match
      - source_section_prefix: substring match on source_section
      - norm_kind_prefix:      substring match on norm_kind
      - scopes_action:         norm_scopes_action -> action_class_label
      - scopes_object:         norm_scopes_object -> object_class_label
                               (union with instrument_class)
      - has_condition:         bool — presence/absence of
                               norm_has_condition edge
      - serves_question:       norm_serves_question -> question_id

    All criteria AND together. Returns {count, norms: [{norm_id,
    norm_kind, modality, source_section, capacity_composition,
    action_scope}]}.
    """
    params = {"criteria": criteria, "db": db}

    # Build match clauses + extra patterns.
    match_lines: list[str] = [
        "$n isa norm, has norm_id $nid",
    ]
    # Scalars always fetched for the return shape
    select_scalars = [
        ("$nid", "nid"),
    ]
    # Pattern-matched equality clauses for scalar criteria
    scalar_eq = {
        "modality": "modality",
        "action_scope": "action_scope",
        "capacity_composition": "capacity_composition",
    }
    for key, attr in scalar_eq.items():
        if key in criteria and criteria[key] is not None:
            match_lines.append(
                f'$n has {attr} "{_escape(str(criteria[key]))}"'
            )

    # Substring criteria (TypeDB 3.x `contains` operator)
    substring_criteria = {
        "source_section_prefix": "source_section",
        "norm_kind_prefix": "norm_kind",
    }
    for key, attr in substring_criteria.items():
        if key in criteria and criteria[key] is not None:
            # $n has attr $val + substring check
            match_lines.append(f"$n has {attr} ${key}_v")
            match_lines.append(f'${key}_v contains "{_escape(str(criteria[key]))}"')

    # Relation-backed criteria
    if "scopes_action" in criteria and criteria["scopes_action"]:
        match_lines.append(
            f'$scoped_act isa action_class, '
            f'has action_class_label "{_escape(criteria["scopes_action"])}"'
        )
        match_lines.append(
            "(norm: $n, action: $scoped_act) isa norm_scopes_action"
        )

    if "scopes_object" in criteria and criteria["scopes_object"]:
        # Object or instrument — union. We express as two alternative paths
        # via a sub-match OR. TypeDB 3.x supports `or { ... };` pattern.
        obj_label = _escape(criteria["scopes_object"])
        match_lines.append(
            "{ $scoped_obj isa object_class, "
            f'has object_class_label "{obj_label}"; '
            "(norm: $n, object: $scoped_obj) isa norm_scopes_object; } or "
            "{ $scoped_instr isa instrument_class, "
            f'has instrument_class_label "{obj_label}"; '
            "(norm: $n, instrument: $scoped_instr) isa norm_scopes_instrument; }"
        )

    if "serves_question" in criteria and criteria["serves_question"]:
        match_lines.append(
            f'$sq_q isa gold_question, '
            f'has question_id "{_escape(criteria["serves_question"])}"'
        )
        match_lines.append(
            "(norm: $n, question: $sq_q) isa norm_serves_question"
        )

    # has_condition bool — positive is a match; negative uses not{...}
    if "has_condition" in criteria:
        if criteria["has_condition"]:
            match_lines.append(
                "(norm: $n, root: $hc_c) isa norm_has_condition"
            )
        else:
            # not { (norm: $n, root: $...) isa norm_has_condition; }
            match_lines.append(
                "not { (norm: $n, root: $hc_c) isa norm_has_condition; }"
            )

    # Additional scalar selects for the return shape
    match_lines.append("try { $n has modality $mod; }")
    match_lines.append("try { $n has norm_kind $nk; }")
    match_lines.append("try { $n has capacity_composition $cc; }")
    match_lines.append("try { $n has action_scope $sc; }")
    match_lines.append("try { $n has source_section $ss; }")

    q_match = "match\n  " + ";\n  ".join(match_lines) + ";"
    q_select = "select $nid, $mod, $nk, $cc, $sc, $ss;"
    full_q = f"{q_match}\n{q_select}"

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            rows = _rows(tx, full_q)
            seen = set()
            norms = []
            for row in rows:
                nid = _attr(row, "nid")
                if nid in seen:
                    continue
                seen.add(nid)
                norms.append({
                    "norm_id": nid,
                    "norm_kind": _attr(row, "nk"),
                    "modality": _attr(row, "mod"),
                    "capacity_composition": _attr(row, "cc"),
                    "action_scope": _attr(row, "sc"),
                    "source_section": _attr(row, "ss"),
                })
            norms.sort(key=lambda n: n["norm_id"])
            return _envelope("filter_norms", deal_id, params,
                             {"count": len(norms), "norms": norms})
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
#  EVALUATED OPERATIONS — evaluate_feasibility, evaluate_capacity
# ═══════════════════════════════════════════════════════════════════════════════
#
# Evaluator architecture (pilot scope, Rule 8.1 + 5.2 trade-off note):
#
# Rule 5.2 says deontic logic lives in TypeDB functions, not Python. The
# function library (deontic_*_functions.tql) does contain predicate_holds,
# condition_holds, and capacity aggregators taking $ws: event_instance.
#
# Ideally Python would call those functions directly with a transient
# event_instance instance inserted in-tx and never committed. TypeDB 3.x
# doesn't expose clean rollback semantics for WRITE transactions, and
# inserting a throwaway instance per call is a correctness risk (the
# instance persists if anything goes wrong between insert and close).
#
# Pilot fallback: Python walks the condition tree and evaluates atomic
# predicates against supplied_world_state. The evaluator is ~80 lines
# and contains zero legal reasoning — it does numeric/boolean comparison
# only, reading threshold and operator_comparison from the graph. The
# *rules* (which predicates, which thresholds, how conditions compose)
# remain graph-owned; Python is the thin caller Rule 5.2 contemplates.
#
# Post-pilot: revisit transient-event_instance approach. If TypeDB 3.x
# adds ergonomic rollback or a stateless function-call variant, swap
# this evaluator for function-library calls without changing the
# evaluated-operation API.


# Canonical supplied-value key per predicate label.
# A predicate with one of these labels reads the mapped field from
# supplied_world_state.predicate_values. Unmapped labels return None
# ("cannot evaluate without more input") with a trace entry.
_PREDICATE_TO_SUPPLIED_KEY: dict[str, str] = {
    "first_lien_net_leverage_at_or_below": "first_lien_net_leverage_ratio",
    "first_lien_net_leverage_above":        "first_lien_net_leverage_ratio",
    "senior_secured_leverage_at_or_below":  "senior_secured_leverage_ratio",
    "total_leverage_at_or_below":           "total_leverage_ratio",
    "individual_proceeds_at_or_below":      "individual_proceeds_amount_usd",
    "annual_aggregate_at_or_below":         "annual_aggregate_proceeds_amount_usd",
    "no_event_of_default_exists":           "no_event_of_default_exists",
    "pro_forma_compliance_financial_covenants": "pro_forma_compliance_financial_covenants",
    "qualified_ipo_has_occurred":           "qualified_ipo_has_occurred",
    "is_product_line_or_line_of_business_sale": "is_product_line_or_line_of_business_sale",
    "unsub_would_own_or_license_material_ip_at_designation": "unsub_would_own_or_license_material_ip_at_designation",
    "prior_year_capacity_was_unused":       "prior_year_capacity_was_unused",
    "base_capacity_will_be_unused_in_subsequent_year": "base_capacity_will_be_unused_in_subsequent_year",
    "incurrence_test_satisfied":            "incurrence_test_satisfied",
    "officer_certificate_delivered":        "officer_certificate_delivered",
    "board_approval_obtained":              "board_approval_obtained",
}


def _eval_predicate(pred_ref: dict, supplied: dict, trace: list,
                    step_counter: list) -> bool | None:
    """Evaluate a single atomic predicate against supplied state.

    Returns True/False/None (None = inconclusive for unmapped predicates
    or missing supplied values).
    """
    step_counter[0] += 1
    step = step_counter[0]
    label = pred_ref.get("state_predicate_label")
    op = pred_ref.get("operator_comparison")
    threshold = pred_ref.get("threshold_value_double")
    ref_pred = pred_ref.get("reference_predicate_label")

    predicate_values = supplied.get("predicate_values", {}) or {}
    proposed = supplied.get("proposed_action", {}) or {}

    # Special case: pro_forma_no_worse — reads the is_pro_forma_no_worse
    # flag from proposed_action (Rule 8.1 posture: consumer tells us
    # whether the hypothetical ratio is no-worse pro forma).
    if label == "pro_forma_no_worse":
        val = proposed.get("is_pro_forma_no_worse")
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label, "reference_predicate": ref_pred,
            "supplied_key": "proposed_action.is_pro_forma_no_worse",
            "supplied_value": val,
            "outcome": bool(val) if val is not None else None,
            "reasoning": ("consumer-supplied flag"
                          if val is not None
                          else "no is_pro_forma_no_worse in proposed_action"),
        })
        return bool(val) if val is not None else None

    # Special case: retained_asset_sale_proceeds is not a predicate to
    # evaluate — it's a state-name anchor used by trace_pathways. If
    # encountered here, treat as true (presence of asset-sale proceeds
    # is not a gate for norm applicability; it's a capacity source).
    if label == "retained_asset_sale_proceeds":
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label,
            "outcome": True,
            "reasoning": "state-name anchor, not a gating predicate",
        })
        return True

    supplied_key = _PREDICATE_TO_SUPPLIED_KEY.get(label)
    if supplied_key is None:
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label,
            "outcome": None,
            "reasoning": f"no evaluator mapping for predicate label {label!r}",
        })
        return None

    supplied_value = predicate_values.get(supplied_key)
    if supplied_value is None:
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label, "supplied_key": supplied_key,
            "outcome": None,
            "reasoning": f"supplied_world_state missing predicate_values[{supplied_key!r}]",
        })
        return None

    # Boolean predicate — operator_comparison absent, no threshold
    if op is None and threshold is None:
        outcome = bool(supplied_value)
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label, "supplied_key": supplied_key,
            "supplied_value": supplied_value,
            "outcome": outcome,
            "reasoning": "boolean predicate; truthiness of supplied value",
        })
        return outcome

    # Ratio / numeric threshold comparison
    try:
        sv = float(supplied_value)
        thr = float(threshold) if threshold is not None else None
    except (TypeError, ValueError):
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label, "supplied_key": supplied_key,
            "supplied_value": supplied_value, "threshold": threshold,
            "outcome": None,
            "reasoning": f"cannot coerce to float: supplied={supplied_value!r} threshold={threshold!r}",
        })
        return None

    outcome = None
    if op == "at_or_below":
        outcome = sv <= thr
    elif op == "at_or_above":
        outcome = sv >= thr
    elif op == "less_than":
        outcome = sv < thr
    elif op == "greater_than":
        outcome = sv > thr
    elif op == "equals":
        outcome = sv == thr
    else:
        trace.append({
            "step": step, "operation": "predicate_holds",
            "predicate_label": label, "operator_comparison": op,
            "outcome": None,
            "reasoning": f"unknown operator_comparison {op!r}",
        })
        return None

    trace.append({
        "step": step, "operation": "predicate_holds",
        "predicate_label": label, "supplied_key": supplied_key,
        "supplied_value": sv, "threshold": thr,
        "operator_comparison": op,
        "outcome": outcome,
        "reasoning": f"{sv} {op} {thr} = {outcome}",
    })
    return outcome


def _eval_condition_tree(cond: dict, supplied: dict, trace: list,
                          step_counter: list) -> bool | None:
    """Recursively evaluate a condition tree. Three-valued logic:
    True, False, or None (inconclusive due to missing inputs).
    """
    if cond is None:
        return True  # unconditional
    op = cond.get("operator")
    if op == "atomic":
        pred_ref = cond.get("predicate_ref") or {}
        return _eval_predicate(pred_ref, supplied, trace, step_counter)
    if op in ("or", "and"):
        children = cond.get("children", []) or []
        child_results = [
            _eval_condition_tree(ch, supplied, trace, step_counter)
            for ch in children
        ]
        if op == "or":
            if any(r is True for r in child_results):
                return True
            if all(r is False for r in child_results):
                return False
            return None  # at least one inconclusive, none true
        # and
        if all(r is True for r in child_results):
            return True
        if any(r is False for r in child_results):
            return False
        return None
    # unknown operator
    step_counter[0] += 1
    trace.append({
        "step": step_counter[0], "operation": "condition_holds",
        "outcome": None,
        "reasoning": f"unknown operator {op!r} in condition tree",
    })
    return None


def evaluate_feasibility(deal_id: str, norm_id: str,
                          supplied_world_state: dict,
                          db: str = DEFAULT_DB) -> dict:
    """Evaluate whether a norm is currently applicable given the consumer's
    supplied world state.

    Steps:
      1. Read the norm's condition tree via describe_norm helper.
      2. Evaluate the tree against supplied predicate_values +
         proposed_action. None (inconclusive) for any missing inputs.
      3. Evaluate defeaters — a defeater's condition holding against
         supplied state means the norm is defeated.
      4. applicable = (condition_holds OR unconditional) AND not defeated.

    Returns envelope with supplied_world_state echoed, computation_trace
    populated, and result.applicable plus result.reason.
    """
    params = {"norm_id": norm_id, "db": db}
    trace: list = []
    step_counter = [0]

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            # Pull norm shell and condition tree.
            q_scalars = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  try {{ $n has modality $mod; }};
                  try {{ $n has norm_kind $nk; }};
                select $mod, $nk;
            '''
            rows = _rows(tx, q_scalars)
            if not rows:
                return _envelope(
                    "evaluate_feasibility", deal_id, params,
                    {"applicable": None, "reason": f"norm_not_found: {norm_id}"},
                    trace=trace, supplied_world_state=supplied_world_state)
            modality = _attr(rows[0], "mod")
            norm_kind = _attr(rows[0], "nk")

            cond = _describe_condition_for_norm(tx, norm_id)
            step_counter[0] += 1
            if cond is None:
                trace.append({
                    "step": step_counter[0], "operation": "condition_holds",
                    "outcome": True,
                    "reasoning": "norm is unconditional; no predicates to evaluate",
                })
                condition_outcome: bool | None = True
            else:
                condition_outcome = _eval_condition_tree(
                    cond, supplied_world_state, trace, step_counter)
                step_counter[0] += 1
                trace.append({
                    "step": step_counter[0], "operation": "condition_holds",
                    "root_topology": cond.get("topology"),
                    "outcome": condition_outcome,
                    "reasoning": "composed from atomic predicate_holds above",
                })

            # Evaluate defeaters.
            defeaters = _list_defeaters(tx, norm_id)
            defeats_fired: list[dict] = []
            for d in defeaters:
                d_cond = d.get("condition")
                step_counter[0] += 1
                if d_cond is None:
                    # An unconditional defeater fires whenever attached.
                    trace.append({
                        "step": step_counter[0], "operation": "defeater_check",
                        "defeater_id": d.get("defeater_id"),
                        "outcome": True,
                        "reasoning": "defeater has no condition (always active)",
                    })
                    defeats_fired.append(d)
                    continue
                d_outcome = _eval_condition_tree(
                    d_cond, supplied_world_state, trace, step_counter)
                step_counter[0] += 1
                trace.append({
                    "step": step_counter[0], "operation": "defeater_check",
                    "defeater_id": d.get("defeater_id"),
                    "outcome": d_outcome,
                    "reasoning": "defeater condition evaluated",
                })
                if d_outcome is True:
                    defeats_fired.append(d)

            # Combine
            if condition_outcome is None:
                applicable = None
                reason = "condition evaluation inconclusive (missing supplied values)"
            elif condition_outcome is False:
                applicable = False
                reason = "norm condition does not hold against supplied state"
            elif defeats_fired:
                applicable = False
                reason = f"{len(defeats_fired)} defeater(s) fired"
            else:
                applicable = True
                reason = "condition holds (or norm is unconditional) and no defeaters fire"

            result = {
                "norm_id": norm_id,
                "modality": modality,
                "norm_kind": norm_kind,
                "applicable": applicable,
                "reason": reason,
                "defeaters_fired": [d.get("defeater_id") for d in defeats_fired],
            }
            return _envelope("evaluate_feasibility", deal_id, params,
                             result, trace=trace,
                             supplied_world_state=supplied_world_state)
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


def evaluate_capacity(deal_id: str, norm_id: str,
                       supplied_world_state: dict,
                       db: str = DEFAULT_DB) -> dict:
    """Compute dollar capacity available under a norm given supplied state.

    Dispatches on capacity_composition:
      - additive (with cap_usd alone, or cap_uses_greater_of + grower):
        capacity = greater_of(cap_usd, cap_grower_pct * reference_value)
        or just cap_usd if no grower.
      - categorical: capacity = cap_usd (single-purpose fixed)
      - computed_from_sources: recurse over contributors via
        norm_contributes_to_capacity; aggregate per per-edge
        aggregation_function and aggregation_direction.
      - unlimited_on_condition: evaluate the norm's condition; if it
        holds, capacity is None (interpretation: unlimited); else 0.
      - n_a: return None (not a capacity-bearing norm).

    floor_value is applied after computation if present.
    """
    params = {"norm_id": norm_id, "db": db}
    trace: list = []
    step_counter = [0]

    driver = _connect()
    try:
        tx = driver.transaction(db, TransactionType.READ)
        try:
            capacity = _compute_capacity_for_norm(
                tx, norm_id, supplied_world_state, trace, step_counter)
            # Extract scalars for the result envelope.
            q = f'''
                match
                  $n isa norm, has norm_id "{_escape(norm_id)}";
                  try {{ $n has capacity_composition $cc; }};
                select $cc;
            '''
            rows = _rows(tx, q)
            cc = _attr(rows[0], "cc") if rows else None
            result = {
                "norm_id": norm_id,
                "capacity_composition": cc,
                "capacity_usd": capacity,
            }
            return _envelope("evaluate_capacity", deal_id, params,
                             result, trace=trace,
                             supplied_world_state=supplied_world_state)
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


def _compute_capacity_for_norm(tx, norm_id: str, supplied: dict,
                                trace: list, step_counter: list,
                                max_depth: int = 5) -> float | None:
    """Compute dollar capacity for a norm; recurses into contributors."""
    if max_depth <= 0:
        step_counter[0] += 1
        trace.append({
            "step": step_counter[0], "operation": "capacity",
            "norm_id": norm_id,
            "outcome": None,
            "reasoning": "recursion depth exceeded; cycle guard",
        })
        return None

    # Read scalars
    q = f'''
        match
          $n isa norm, has norm_id "{_escape(norm_id)}";
          try {{ $n has capacity_composition $cc; }};
          try {{ $n has cap_usd $cu; }};
          try {{ $n has cap_grower_pct $cg; }};
          try {{ $n has cap_grower_reference $cgr; }};
          try {{ $n has cap_uses_greater_of $cugo; }};
          try {{ $n has floor_value $fv; }};
        select $cc, $cu, $cg, $cgr, $cugo, $fv;
    '''
    rows = _rows(tx, q)
    if not rows:
        step_counter[0] += 1
        trace.append({
            "step": step_counter[0], "operation": "capacity",
            "norm_id": norm_id, "outcome": None,
            "reasoning": "norm not found",
        })
        return None

    r = rows[0]
    cc = _attr(r, "cc")
    cap_usd = _attr(r, "cu")
    cap_grower = _attr(r, "cg")
    grower_ref = _attr(r, "cgr")
    uses_greater = _attr(r, "cugo")
    floor = _attr(r, "fv")

    predicate_values = supplied.get("predicate_values", {}) or {}
    step_counter[0] += 1
    step = step_counter[0]

    if cc == "n_a":
        trace.append({
            "step": step, "operation": "capacity",
            "norm_id": norm_id, "composition": cc,
            "outcome": None,
            "reasoning": "n_a composition — not a capacity-bearing norm",
        })
        return None

    if cc == "unlimited_on_condition":
        cond = _describe_condition_for_norm(tx, norm_id)
        cond_outcome = _eval_condition_tree(cond, supplied, trace, step_counter) if cond else True
        trace.append({
            "step": step, "operation": "capacity",
            "norm_id": norm_id, "composition": cc,
            "condition_outcome": cond_outcome,
            "outcome": (None if cond_outcome is True else 0 if cond_outcome is False else None),
            "reasoning": "unlimited when condition holds (None means no cap); 0 when false",
        })
        return None if cond_outcome is True else (0 if cond_outcome is False else None)

    if cc == "computed_from_sources":
        # Recurse into contributors.
        q_contrib = f'''
            match
              $pool isa norm, has norm_id "{_escape(norm_id)}";
              $rel isa norm_contributes_to_capacity,
                links (contributor: $contrib, pool: $pool);
              $contrib has norm_id $cid;
              try {{ $rel has aggregation_function $af; }};
              try {{ $rel has aggregation_direction $ad; }};
            select $cid, $af, $ad;
        '''
        contrib_rows = _rows(tx, q_contrib)
        components: list[tuple[str, float | None, str | None, str | None]] = []
        for row in contrib_rows:
            cid = _attr(row, "cid")
            af = _attr(row, "af")
            ad = _attr(row, "ad")
            sub_cap = _compute_capacity_for_norm(
                tx, cid, supplied, trace, step_counter,
                max_depth=max_depth - 1)
            components.append((cid, sub_cap, af, ad))

        # Aggregate per the pool's expected aggregation.
        # Precedence: use the per-edge aggregation_function if uniform;
        # otherwise default to sum for additive, greatest_of for
        # builder-pattern pools.
        defined_ops = {c[2] for c in components if c[2] is not None}
        agg_op = next(iter(defined_ops)) if len(defined_ops) == 1 else "sum"

        addable = [c[1] for c in components if c[3] == "add" and c[1] is not None]
        subtractable = [c[1] for c in components if c[3] == "subtract" and c[1] is not None]

        total: float | None
        if agg_op == "greatest_of":
            candidates = [c[1] for c in components
                          if c[3] != "subtract" and c[1] is not None]
            total = max(candidates) if candidates else None
        else:
            # sum / default
            total = (sum(addable) if addable else 0.0) - (sum(subtractable) if subtractable else 0.0)
            if not addable and not subtractable:
                total = None

        if floor is not None and total is not None:
            total = max(total, float(floor))

        trace.append({
            "step": step, "operation": "capacity",
            "norm_id": norm_id, "composition": cc,
            "aggregation_op": agg_op,
            "component_count": len(components),
            "floor_applied": floor,
            "outcome": total,
            "reasoning": f"aggregated {len(components)} contributor(s) via {agg_op}",
        })
        return total

    # additive / categorical / unknown — resolve cap_usd + grower
    base_dollar = float(cap_usd) if cap_usd is not None else None

    grower_dollar: float | None = None
    if cap_grower is not None and grower_ref:
        reference_value = predicate_values.get(grower_ref)
        if reference_value is not None:
            try:
                grower_dollar = (float(cap_grower) / 100.0) * float(reference_value)
            except (TypeError, ValueError):
                grower_dollar = None

    if uses_greater and base_dollar is not None and grower_dollar is not None:
        resolved = max(base_dollar, grower_dollar)
        reasoning = f"greater_of(cap_usd={base_dollar}, grower={grower_dollar})"
    elif base_dollar is not None and grower_dollar is not None:
        resolved = base_dollar + grower_dollar
        reasoning = "sum of cap_usd + grower_resolved"
    elif base_dollar is not None:
        resolved = base_dollar
        reasoning = "cap_usd only; no grower or grower_reference unresolved"
    elif grower_dollar is not None:
        resolved = grower_dollar
        reasoning = "grower resolved; no cap_usd floor"
    else:
        resolved = None
        reasoning = ("neither cap_usd nor grower resolvable "
                     "(may need cap_grower_reference value in supplied predicate_values)")

    if floor is not None and resolved is not None:
        resolved = max(resolved, float(floor))

    trace.append({
        "step": step, "operation": "capacity",
        "norm_id": norm_id, "composition": cc,
        "cap_usd": base_dollar, "cap_grower_pct": cap_grower,
        "cap_grower_reference": grower_ref,
        "cap_uses_greater_of": uses_greater,
        "floor_value": floor,
        "outcome": resolved,
        "reasoning": reasoning,
    })
    return resolved


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

    # filter_norms
    p_fn = sub.add_parser("filter_norms")
    p_fn.add_argument("--deal", required=True)
    p_fn.add_argument("--criteria", required=True,
                      help='JSON dict, e.g. \'{"modality":"permission"}\'')
    p_fn.add_argument("--db", default=DEFAULT_DB)
    p_fn.add_argument("--compact", action="store_true")

    # evaluate_feasibility
    p_ef = sub.add_parser("evaluate_feasibility")
    p_ef.add_argument("--deal", required=True)
    p_ef.add_argument("--norm", required=True)
    p_ef.add_argument("--world-state", required=True,
                      help="Path to JSON file containing supplied_world_state.")
    p_ef.add_argument("--db", default=DEFAULT_DB)
    p_ef.add_argument("--compact", action="store_true")

    # evaluate_capacity
    p_ec = sub.add_parser("evaluate_capacity")
    p_ec.add_argument("--deal", required=True)
    p_ec.add_argument("--norm", required=True)
    p_ec.add_argument("--world-state", required=True,
                      help="Path to JSON file containing supplied_world_state.")
    p_ec.add_argument("--db", default=DEFAULT_DB)
    p_ec.add_argument("--compact", action="store_true")

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
    collapse_group = p_tp.add_mutually_exclusive_group()
    collapse_group.add_argument("--collapse-contributors", action="store_true",
                                dest="collapse", default=True,
                                help="(default) drop top-level norms whose "
                                     "contribution parent is already in the "
                                     "result set.")
    collapse_group.add_argument("--no-collapse-contributors",
                                action="store_false", dest="collapse",
                                help="Preserve every scoping norm including "
                                     "capacity contributors.")
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
            include_annotations=args.annotations,
            collapse_contributors=args.collapse,
            db=args.db)
    elif args.op == "filter_norms":
        try:
            criteria = json.loads(args.criteria)
        except json.JSONDecodeError as e:
            parser.error(f"--criteria must be valid JSON: {e}")
            return 2
        resp = filter_norms(args.deal, criteria, db=args.db)
    elif args.op == "evaluate_feasibility":
        ws_path = Path(args.world_state)
        if not ws_path.exists():
            parser.error(f"--world-state file not found: {ws_path}")
            return 2
        ws = json.loads(ws_path.read_text(encoding="utf-8"))
        resp = evaluate_feasibility(args.deal, args.norm, ws, db=args.db)
    elif args.op == "evaluate_capacity":
        ws_path = Path(args.world_state)
        if not ws_path.exists():
            parser.error(f"--world-state file not found: {ws_path}")
            return 2
        ws = json.loads(ws_path.read_text(encoding="utf-8"))
        resp = evaluate_capacity(args.deal, args.norm, ws, db=args.db)
    else:
        parser.error(f"unknown op: {args.op}")
        return 2

    _print(resp, compact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
