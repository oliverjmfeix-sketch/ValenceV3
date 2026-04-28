"""
Phase D Commit 1 — v4 norm-context fetch for synthesis.

Walks every v4 norm for a deal in `valence_v4` and assembles structured
context dicts the synthesis service (D2) feeds to the two-stage Claude
pipeline. Each norm dict includes:

  - The norm's own scalar attributes (cap_usd, modality, etc.)
  - Scope edges (subject/action/object/instrument)
  - Conditions tree (root + recursive children + predicate references)
  - Defeats edges (which defeaters override this norm)
  - norm_contributes_to_capacity edges (with child_index + aggregation_function)
  - norm_extracted_from → v3 entity (with full v3 attribute payload, so
    synthesis can cite source_text / source_section / source_page and
    surface v3 attributes like capacity_category that v3
    synthesis_guidance references)
  - produced_by_rule provenance (which projection_rule emitted the norm)

Plus a separate `defeaters_for_deal` aggregate (list of defeater dicts
+ which norms each defeats) and `proceeds_flows_for_deal` aggregate
(event_provides_proceeds_to_norm edges from the asset_sale_proceeds
seed).

Smoke test (when run as `__main__`): prints structured fetch for Duck
Creek deal `6e76ed06`, verifies v3 entity attributes are reachable
via extracted_from (downstream of the v3→v4 vocabulary audit at
docs/v4_phase_d_lawyer_qa/v3_to_v4_vocab_map.md).

Usage as library:
    from app.services.synthesis_v4_fetch import fetch_norm_context
    norms = fetch_norm_context(driver, "valence_v4", "6e76ed06")

Usage as CLI:
    C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.services.synthesis_v4_fetch --deal 6e76ed06
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=False)

from typedb.driver import (  # noqa: E402
    TypeDB, Credentials, DriverOptions, TransactionType,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Connection helper
# ═══════════════════════════════════════════════════════════════════════════════


def connect_typedb():
    import os
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Atomic queries (one round-trip each; assembled in Python below)
# ═══════════════════════════════════════════════════════════════════════════════


def _all_attrs_of(tx, owner_iid: str) -> dict[str, Any]:
    """Read every attribute owned by a single entity, return as
    {attr_label: value}. Skips internal/empty values. Used for both
    norm scalars and the `extracted_from` v3 entity payload.
    """
    attrs: dict[str, Any] = {}
    try:
        r = tx.query(f"match $x iid {owner_iid}; $x has $a; select $a;").resolve()
        for row in r.as_concept_rows():
            a = row.get("a").as_attribute()
            label = a.get_type().get_label()
            value = a.get_value()
            if value is None:
                continue
            # Cast TypeDB values to JSON-safe primitives
            if isinstance(value, (str, int, float, bool)):
                attrs[label] = value
            else:
                attrs[label] = str(value)
    except Exception as exc:
        logger.debug("_all_attrs_of(%s): %s", owner_iid,
                     str(exc).splitlines()[0][:120])
    return attrs


def fetch_norms(driver, db: str, deal_id: str) -> list[dict]:
    """Returns one dict per v4 norm for the deal:
        {norm_iid, norm_id, norm_kind (concrete entity-type label), scalars}
    Norm IDs scoped via `nid contains "{deal_id}"`."""
    out: list[dict] = []
    tx = driver.transaction(db, TransactionType.READ)
    try:
        # Fetch norm iids + ids + types
        r = tx.query(
            f'match $n isa! norm, has norm_id $nid; '
            f'$nid contains "{deal_id}"; '
            f'select $n, $nid;'
        ).resolve()
        rows = list(r.as_concept_rows())
        for row in rows:
            iid = row.get("n").get_iid()
            norm_id = row.get("nid").as_attribute().get_value()
            scalars = _all_attrs_of(tx, iid)
            out.append({
                "norm_iid": iid,
                "norm_id": norm_id,
                "scalars": scalars,
            })
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_scope_edges(driver, db: str, deal_id: str
                       ) -> dict[str, list[dict]]:
    """Returns {norm_id: [{relation, target_type, target_iid, target_id}, ...]}.
    Covers norm_binds_subject, norm_scopes_action, norm_scopes_object,
    norm_scopes_instrument.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    queries = {
        "norm_binds_subject": ("subject", "$tgt"),
        "norm_scopes_action": ("action", "$tgt"),
        "norm_scopes_object": ("object", "$tgt"),
        "norm_scopes_instrument": ("instrument", "$tgt"),
    }
    tx = driver.transaction(db, TransactionType.READ)
    try:
        for relation, (role, tgt_var) in queries.items():
            q = (
                f'match $n isa norm, has norm_id $nid; '
                f'$nid contains "{deal_id}"; '
                f'(norm: $n, {role}: $tgt) isa {relation}; '
                f'$tgt isa! $type; '
                f'select $nid, $tgt, $type;'
            )
            try:
                r = tx.query(q).resolve()
                for row in r.as_concept_rows():
                    nid = row.get("nid").as_attribute().get_value()
                    target_label = row.get("type").get_label()
                    out[nid].append({
                        "relation": relation,
                        "target_type": target_label,
                    })
            except Exception as exc:
                logger.warning("scope %s: %s", relation,
                               str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return dict(out)


def fetch_contributes_to(driver, db: str, deal_id: str) -> list[dict]:
    """Returns list of {source_norm_id, target_norm_id, child_index,
    aggregation_function} for norm_contributes_to_capacity edges scoped
    to deal."""
    out: list[dict] = []
    q = (
        f'match $src isa norm, has norm_id $sid; '
        f'$tgt isa norm, has norm_id $tid; '
        f'$sid contains "{deal_id}"; '
        f'$tid contains "{deal_id}"; '
        f'(contributor: $src, pool: $tgt) isa norm_contributes_to_capacity, '
        f'    has child_index $cidx, '
        f'    has aggregation_function $agg; '
        f'select $sid, $tid, $cidx, $agg;'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                out.append({
                    "source_norm_id": row.get("sid").as_attribute().get_value(),
                    "target_norm_id": row.get("tid").as_attribute().get_value(),
                    "child_index": row.get("cidx").as_attribute().get_value(),
                    "aggregation_function": row.get("agg").as_attribute().get_value(),
                })
        except Exception as exc:
            logger.warning("contributes_to: %s", str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_defeats_edges(driver, db: str, deal_id: str) -> list[dict]:
    """Returns list of {defeater_id, norm_id} for `defeats` edges scoped
    to deal."""
    out: list[dict] = []
    q = (
        f'match $d isa defeater, has defeater_id $did; '
        f'$n isa norm, has norm_id $nid; '
        f'$did contains "{deal_id}"; '
        f'(defeater: $d, defeated: $n) isa defeats; '
        f'select $did, $nid;'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                out.append({
                    "defeater_id": row.get("did").as_attribute().get_value(),
                    "norm_id": row.get("nid").as_attribute().get_value(),
                })
        except Exception as exc:
            logger.warning("defeats: %s", str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_defeaters(driver, db: str, deal_id: str) -> list[dict]:
    """Returns one dict per defeater for the deal: {defeater_iid,
    defeater_id, scalars}. Same shape as fetch_norms."""
    out: list[dict] = []
    tx = driver.transaction(db, TransactionType.READ)
    try:
        r = tx.query(
            f'match $d isa! defeater, has defeater_id $did; '
            f'$did contains "{deal_id}"; '
            f'select $d, $did;'
        ).resolve()
        for row in r.as_concept_rows():
            iid = row.get("d").get_iid()
            defeater_id = row.get("did").as_attribute().get_value()
            scalars = _all_attrs_of(tx, iid)
            out.append({
                "defeater_iid": iid,
                "defeater_id": defeater_id,
                "scalars": scalars,
            })
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_root_conditions(driver, db: str, deal_id: str
                            ) -> dict[str, str]:
    """Returns {norm_id: root_condition_id}."""
    out: dict[str, str] = {}
    q = (
        f'match $n isa norm, has norm_id $nid; '
        f'$c isa condition, has condition_id $cid; '
        f'$nid contains "{deal_id}"; '
        f'(norm: $n, root: $c) isa norm_has_condition; '
        f'select $nid, $cid;'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                out[row.get("nid").as_attribute().get_value()] = (
                    row.get("cid").as_attribute().get_value()
                )
        except Exception as exc:
            logger.warning("root_conditions: %s", str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_condition_tree(driver, db: str, root_condition_id: str
                           ) -> dict | None:
    """Returns the condition tree rooted at root_condition_id as a
    nested dict: {condition_id, scalars, children: [tree, tree, ...]}
    sorted by child_index. Walks `condition_has_child` recursively.
    Predicate references via `condition_references_predicate` (the
    state_predicate_id) included on each leaf.
    """
    tx = driver.transaction(db, TransactionType.READ)
    try:
        # Fetch all conditions reachable from root by traversing
        # condition_has_child. Build a flat dict, then recurse.
        all_conds: dict[str, dict] = {}
        # Pre-load the root
        r = tx.query(
            f'match $c isa! condition, has condition_id "{root_condition_id}"; '
            f'select $c;'
        ).resolve()
        rows = list(r.as_concept_rows())
        if not rows:
            return None
        root_iid = rows[0].get("c").get_iid()
        all_conds[root_condition_id] = {
            "condition_id": root_condition_id,
            "scalars": _all_attrs_of(tx, root_iid),
            "children": [],
        }
        # BFS to collect all descendants
        queue = [root_condition_id]
        seen = {root_condition_id}
        while queue:
            current = queue.pop(0)
            q = (
                f'match $p isa condition, has condition_id "{current}"; '
                f'$ch isa condition, has condition_id $chid; '
                f'(parent: $p, child: $ch) isa condition_has_child, has child_index $cidx; '
                f'select $ch, $chid, $cidx;'
            )
            try:
                r = tx.query(q).resolve()
                children = []
                for row in r.as_concept_rows():
                    ch_iid = row.get("ch").get_iid()
                    chid = row.get("chid").as_attribute().get_value()
                    cidx = row.get("cidx").as_attribute().get_value()
                    if chid not in seen:
                        seen.add(chid)
                        all_conds[chid] = {
                            "condition_id": chid,
                            "scalars": _all_attrs_of(tx, ch_iid),
                            "children": [],
                        }
                        queue.append(chid)
                    children.append((cidx, chid))
                # Wire up the parent's children list (sorted)
                children.sort(key=lambda t: t[0])
                all_conds[current]["children"] = [chid for _, chid in children]
            except Exception as exc:
                logger.warning("condition tree (%s): %s", current,
                               str(exc).splitlines()[0][:120])

        # Predicate references for each condition (leaves typically)
        for cid in seen:
            try:
                r = tx.query(
                    f'match $c isa condition, has condition_id "{cid}"; '
                    f'(condition: $c, predicate: $p) isa condition_references_predicate; '
                    f'$p has state_predicate_id $pid; '
                    f'select $pid;'
                ).resolve()
                pids = [row.get("pid").as_attribute().get_value()
                        for row in r.as_concept_rows()]
                if pids:
                    all_conds[cid]["predicate_ids"] = pids
            except Exception:
                pass

        # Build nested tree from flat dict
        def _build_tree(cid: str) -> dict:
            node = dict(all_conds[cid])  # shallow copy
            child_ids = node.get("children", [])
            node["children"] = [_build_tree(ch) for ch in child_ids]
            return node

        return _build_tree(root_condition_id)
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def fetch_norm_extracted_from(driver, db: str, deal_id: str) -> dict[str, dict]:
    """Returns {norm_id: {v3_entity_type, v3_entity_iid, v3_attrs}} for
    norms with norm_extracted_from edges. The v3 entity payload is the
    bridge to v3-vocabulary attributes (capacity_category, etc.) that
    v3 synthesis_guidance references.
    """
    out: dict[str, dict] = {}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        r = tx.query(
            f'match $n isa norm, has norm_id $nid; '
            f'$nid contains "{deal_id}"; '
            f'$v isa! $vtype; '
            f'(norm: $n, fact: $v) isa norm_extracted_from; '
            f'select $nid, $v, $vtype;'
        ).resolve()
        for row in r.as_concept_rows():
            nid = row.get("nid").as_attribute().get_value()
            v_iid = row.get("v").get_iid()
            v_type = row.get("vtype").get_label()
            v_attrs = _all_attrs_of(tx, v_iid)
            out[nid] = {
                "v3_entity_type": v_type,
                "v3_entity_iid": v_iid,
                "v3_attrs": v_attrs,
            }
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_produced_by_rule(driver, db: str, deal_id: str) -> dict[str, str]:
    """Returns {norm_or_defeater_id: rule_id} for produced_by_rule edges
    on the deal's emitted entities."""
    out: dict[str, str] = {}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        for kind, id_attr in (("norm", "norm_id"), ("defeater", "defeater_id")):
            try:
                r = tx.query(
                    f'match $e isa {kind}, has {id_attr} $eid; '
                    f'$eid contains "{deal_id}"; '
                    f'$r isa projection_rule, has projection_rule_id $rid; '
                    f'(produced_entity: $e, owning_rule: $r) isa produced_by_rule; '
                    f'select $eid, $rid;'
                ).resolve()
                for row in r.as_concept_rows():
                    out[row.get("eid").as_attribute().get_value()] = (
                        row.get("rid").as_attribute().get_value()
                    )
            except Exception as exc:
                logger.warning("produced_by_rule (%s): %s", kind,
                               str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_proceeds_flows(driver, db: str, deal_id: str) -> list[dict]:
    """Returns list of {event_class_id, target_norm_id,
    proceeds_flow_kind, proceeds_flow_conditions} for
    event_provides_proceeds_to_norm edges to deal-scoped norms."""
    out: list[dict] = []
    q = (
        f'match $e isa event_class, has event_class_id $ecid; '
        f'$n isa norm, has norm_id $nid; '
        f'$nid contains "{deal_id}"; '
        f'(proceeds_event: $e, proceeds_target_norm: $n) '
        f'    isa event_provides_proceeds_to_norm, '
        f'    has proceeds_flow_kind $kind, '
        f'    has proceeds_flow_conditions $conds; '
        f'select $ecid, $nid, $kind, $conds;'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                out.append({
                    "event_class_id": row.get("ecid").as_attribute().get_value(),
                    "target_norm_id": row.get("nid").as_attribute().get_value(),
                    "proceeds_flow_kind": row.get("kind").as_attribute().get_value(),
                    "proceeds_flow_conditions": row.get("conds").as_attribute().get_value(),
                })
        except Exception as exc:
            logger.warning("proceeds_flows: %s", str(exc).splitlines()[0][:120])
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Public API: assemble the per-deal context
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_norm_context(driver, db: str, deal_id: str) -> dict[str, Any]:
    """Top-level fetch. Returns a structured dict:
        {
          "deal_id": "...",
          "norms": [<one dict per norm with all relations resolved>, ...],
          "defeaters": [<one dict per defeater>, ...],
          "proceeds_flows": [...],
          "summary": {<aggregate counts for sanity>}
        }
    """
    norms_raw = fetch_norms(driver, db, deal_id)
    scope = fetch_scope_edges(driver, db, deal_id)
    contributes = fetch_contributes_to(driver, db, deal_id)
    defeats = fetch_defeats_edges(driver, db, deal_id)
    defeaters_raw = fetch_defeaters(driver, db, deal_id)
    root_conds = fetch_root_conditions(driver, db, deal_id)
    extracted = fetch_norm_extracted_from(driver, db, deal_id)
    provenance = fetch_produced_by_rule(driver, db, deal_id)

    # Map contributes_to edges to their source norm
    contributes_by_source: dict[str, list[dict]] = defaultdict(list)
    for edge in contributes:
        contributes_by_source[edge["source_norm_id"]].append({
            "target_norm_id": edge["target_norm_id"],
            "child_index": edge["child_index"],
            "aggregation_function": edge["aggregation_function"],
        })

    # Map defeats edges to defeated norm
    defeats_by_norm: dict[str, list[str]] = defaultdict(list)
    for edge in defeats:
        defeats_by_norm[edge["norm_id"]].append(edge["defeater_id"])

    # Build per-norm dicts
    norms_out: list[dict] = []
    for n in norms_raw:
        nid = n["norm_id"]
        norm_dict: dict[str, Any] = {
            "norm_id": nid,
            "scalars": n["scalars"],
            "scope_edges": scope.get(nid, []),
            "contributes_to": contributes_by_source.get(nid, []),
            "defeated_by": defeats_by_norm.get(nid, []),
            "produced_by_rule": provenance.get(nid),
        }
        # Conditions tree (if any)
        root_cond_id = root_conds.get(nid)
        if root_cond_id:
            tree = fetch_condition_tree(driver, db, root_cond_id)
            if tree:
                norm_dict["condition_tree"] = tree
        # Extracted-from v3 entity
        if nid in extracted:
            norm_dict["extracted_from"] = extracted[nid]
        norms_out.append(norm_dict)

    # Per-defeater dicts (no conditions tree per Phase C — defeaters
    # don't carry conditions in current rule corpus)
    defeaters_out: list[dict] = []
    for d in defeaters_raw:
        defeaters_out.append({
            "defeater_id": d["defeater_id"],
            "scalars": d["scalars"],
            "produced_by_rule": provenance.get(d["defeater_id"]),
        })

    proceeds = fetch_proceeds_flows(driver, db, deal_id)

    summary = {
        "norm_count": len(norms_out),
        "defeater_count": len(defeaters_out),
        "norms_with_conditions": sum(1 for n in norms_out if "condition_tree" in n),
        "norms_with_extracted_from": sum(1 for n in norms_out if "extracted_from" in n),
        "contributes_to_edges": len(contributes),
        "defeats_edges": len(defeats),
        "proceeds_flow_edges": len(proceeds),
    }
    return {
        "deal_id": deal_id,
        "norms": norms_out,
        "defeaters": defeaters_out,
        "proceeds_flows": proceeds,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI / smoke test
# ═══════════════════════════════════════════════════════════════════════════════


def _smoke_audit_v3_attrs(context: dict) -> dict[str, Any]:
    """Audit verification per docs/v4_phase_d_lawyer_qa/v3_to_v4_vocab_map.md:
    confirm v3-vocabulary attributes referenced by synthesis_guidance are
    present in the fetched extracted_from payloads. Returns per-attribute
    presence counts.
    """
    targets = [
        # capacity-related (categories F, G, N)
        "capacity_category",
        "capacity_composition",
        "basket_amount_usd",
        "basket_grower_pct",
        "annual_cap_usd",
        "annual_cap_pct_ebitda",
        # builder-source flags (category F)
        "has_cni_source",
        "has_ecf_source",
        "has_ebitda_fc_source",
        "has_starter_amount_source",
        # ratio (category G)
        "ratio_threshold",
        "has_no_worse_test",
        # source citations
        "source_text",
        "source_section",
        "source_page",
    ]
    presence: dict[str, int] = {t: 0 for t in targets}
    for n in context["norms"]:
        ef = n.get("extracted_from")
        if not ef:
            continue
        for attr in targets:
            if attr in ef.get("v3_attrs", {}):
                presence[attr] += 1
    return presence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True, help="deal_id")
    parser.add_argument("--db", default="valence_v4")
    parser.add_argument("--json", action="store_true",
                        help="Print full context as JSON")
    parser.add_argument("--norm",
                        help="Print one specific norm by id (substring match)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")

    driver = connect_typedb()
    try:
        context = fetch_norm_context(driver, args.db, args.deal)
    finally:
        driver.close()

    if args.json:
        print(json.dumps(context, indent=2, default=str))
        return 0

    print(f"# fetch_norm_context summary — deal {args.deal} ({args.db})")
    print()
    print("## Aggregate counts")
    for k, v in context["summary"].items():
        print(f"  {k:32s} = {v}")
    print()

    print("## Norm kinds present")
    norm_kinds_count: dict[str, int] = defaultdict(int)
    for n in context["norms"]:
        kind = n["scalars"].get("norm_kind") or "<unknown>"
        norm_kinds_count[kind] += 1
    for kind in sorted(norm_kinds_count):
        print(f"  {kind:48s} = {norm_kinds_count[kind]}")
    print()

    print("## v3-vocabulary attribute audit (downstream of v3_to_v4_vocab_map.md)")
    audit = _smoke_audit_v3_attrs(context)
    for attr, count in audit.items():
        print(f"  {attr:32s} present in {count}/{context['summary']['norms_with_extracted_from']} norms' extracted_from")
    print()

    if args.norm:
        candidates = [n for n in context["norms"] if args.norm in n["norm_id"]]
        if not candidates:
            print(f"## No norm matches --norm '{args.norm}'")
            return 1
        norm = candidates[0]
        print(f"## Sample norm: {norm['norm_id']}")
        print(json.dumps(norm, indent=2, default=str))
    else:
        # Default: first norm
        if context["norms"]:
            norm = context["norms"][0]
            print(f"## Sample norm (first in list): {norm['norm_id']}")
            print(json.dumps(norm, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
