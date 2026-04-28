"""
Phase C Commit 3 — parallel run + benchmark gate.

Purpose: verify full structural parity between python projection and
rule-based projection, and benchmark runtime, before Commit 4 deletes
the python helpers.

Strategy: reuse `valence_v4` with prefix discipline. Both populations
already coexist today — python emits non-prefixed IDs (e.g.
`6e76ed06_general_rp_basket_permission`), rule-based emits `conv_`-prefixed
IDs. Snapshot both, strip prefix from rule-based snapshot, diff.

Phases:
  A. Run python projection (clears all output for deal first), snapshot
  B. Clear conv_ output only (preserves rule subgraphs), run rule-based
     execution, snapshot
  C. Diff snapshots, benchmark, scope-cliff inventory
  D. Final cleanup (clear conv_ output) + harness baseline check

Snapshot includes:
  - norms (scalar attributes)
  - defeaters (scalar attributes)
  - conditions (scalar attributes)
  - scope edges (subject/action/object/instrument)
  - norm_contributes_to_capacity (source/pool/aggregation_function/child_index)
  - defeats edges (defeater -> bound norm)
  - norm_has_root_condition (norm -> condition)
  - condition_has_child (parent -> child + child_index)
  - atomic_condition_references_predicate (condition -> predicate_specifier_id)

Excluded by design:
  - produced_by_rule (rule-based-only by design)

Documented scope cliffs (rule-based does NOT emit; python does):
  - event_provides_proceeds_to_norm (deontic_projection._project_proceeds_flows)
  - norm_reallocates_capacity_from (deontic_projection._project_reallocations;
    Duck Creek has zero reallocation v3 entities → expect zero on both sides)
  - norm_provides_carryforward_to / norm_provides_carryback_to (sourced
    from load_ground_truth.py, NOT deontic_projection.py — verified
    excluded)

Benchmark gate: rule-based wall-clock / python wall-clock. >10× regression
triggers denormalization (precomputed match_query strings cached on
projection_rule entities) before Commit 4.

Pre-run note (orphan accumulation):
  Prior converter re-runs leave orphaned `attribute_emission` (~3204),
  `role_assignment` (~2012), `predicate_specifier` (~18) entities. These
  are NOT walked during rule execution (the executor walks top-down
  from rules → templates → emissions; orphans have no inbound from
  any rule). So orphans do not affect the benchmark numbers in this
  script. Sweeping is recommended as a follow-up patch to
  `cleanup_converted_rules` in `phase_c_commit_2_converter.py`, but is
  out of scope for Commit 3.

Usage:
    cd C:/Users/olive/ValenceV3/.claude/worktrees/v4-deontic
    TYPEDB_DATABASE=valence_v4 \\
      C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
      -m app.scripts.phase_c_commit_3_parallel_run --deal 6e76ed06

Output:
  - console: human-readable diff + benchmark + pass/fail
  - JSON file: full snapshots + diff + timings
    (`docs/v4_phase_c_commit_3/parallel_run_<timestamp>.json`)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from app.config import settings  # noqa: E402
from app.services.deontic_projection import project_deal  # noqa: E402
from app.services.projection_rule_executor import execute_rule  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("commit_3_parallel")

NORM_ID_PREFIX = "conv_"
PILOT_ID_PREFIX = "pilot_"
RULE_BASED_PREFIXES = (NORM_ID_PREFIX, PILOT_ID_PREFIX)
BENCHMARK_THRESHOLD = 10.0  # rule-based / python wall-clock ratio
HARNESS_BASELINE = {
    "A1": "pass",
    "A4_missing": 45,
    "A4_spurious": 6,
    "A4_mismatched": 0,
    "A5": "pass",
    "A6": "pass",
}

OUTPUT_DIR = REPO_ROOT / "docs" / "v4_phase_c_commit_3"


# ═══════════════════════════════════════════════════════════════════════════════
# Connection helpers
# ═══════════════════════════════════════════════════════════════════════════════


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def _execute_write(driver, db: str, q: str) -> bool:
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        try:
            wtx.query(q).resolve()
            wtx.commit()
            return True
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            logger.warning(f"write failed: {str(exc).splitlines()[0][:120]}")
            return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════════════


def clear_rule_based_output_only(driver, db: str, deal_id: str) -> dict[str, int]:
    """Delete rule-based emissions (conv_*-prefixed AND pilot_*-prefixed)
    norms / defeaters / conditions for a deal. Preserves
    projection_rule subgraphs (templates / emissions / value_sources)
    and python projection output (non-prefixed).
    """
    counts = {"norms": 0, "defeaters": 0, "conditions": 0}
    patterns = [p + deal_id for p in RULE_BASED_PREFIXES]
    queries = []
    for pattern in patterns:
        queries.extend([
            ("conditions",
             f'match $c isa condition, has condition_id $cid; '
             f'$cid contains "{pattern}"; delete $c;'),
            ("norms",
             f'match $n isa norm, has norm_id $nid; '
             f'$nid contains "{pattern}"; delete $n;'),
            ("defeaters",
             f'match $d isa defeater, has defeater_id $did; '
             f'$did contains "{pattern}"; delete $d;'),
        ])
    # Pre-count (sum across all rule-based prefixes)
    tx = driver.transaction(db, TransactionType.READ)
    try:
        for pattern in patterns:
            for label, kind, id_attr in (
                ("conditions", "condition", "condition_id"),
                ("norms", "norm", "norm_id"),
                ("defeaters", "defeater", "defeater_id"),
            ):
                try:
                    r = tx.query(
                        f'match $x isa {kind}, has {id_attr} $id; '
                        f'$id contains "{pattern}"; select $x;'
                    ).resolve()
                    counts[label] += len(list(r.as_concept_rows()))
                except Exception:
                    pass
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    for label, q in queries:
        _execute_write(driver, db, q)
    return counts




def clear_python_and_conv_output(driver, db: str, deal_id: str) -> dict[str, int]:
    """Delete both python (non-prefixed) and conv_-prefixed output for a
    deal. Mirrors deontic_projection.clear_v4_projection_for_deal but
    callable from this script without circular reasoning.
    """
    counts = {"norms": 0, "defeaters": 0, "conditions": 0}
    queries = [
        ("conditions",
         f'match $c isa condition, has condition_id $cid; '
         f'$cid contains "{deal_id}"; delete $c;'),
        ("norms",
         f'match $n isa norm, has norm_id $nid; '
         f'$nid contains "{deal_id}"; delete $n;'),
        ("defeaters",
         f'match $d isa defeater, has defeater_id $did; '
         f'$did contains "{deal_id}"; delete $d;'),
    ]
    for label, q in queries:
        _execute_write(driver, db, q)
    return counts


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot
# ═══════════════════════════════════════════════════════════════════════════════


def _attr_dict(driver, db: str, owner_iid: str) -> dict[str, Any]:
    """Read all attributes owned by a single entity, return as
    {attr_label: value}. Skips internal-typed values not representable
    in JSON; uses string conversion as fallback."""
    attrs: dict[str, Any] = {}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(
                f'match $x iid {owner_iid}; $x has $a; select $a;'
            ).resolve()
            for row in r.as_concept_rows():
                a = row.get("a").as_attribute()
                label = a.get_type().get_label()
                value = a.get_value()
                # Cast TypeDB-specific values to JSON-safe primitives
                if isinstance(value, (str, int, float, bool)) or value is None:
                    attrs[label] = value
                else:
                    attrs[label] = str(value)
        except Exception as exc:
            logger.debug(f"_attr_dict({owner_iid}): {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return attrs


def _id_attr_for(kind: str) -> str:
    return {"norm": "norm_id", "defeater": "defeater_id", "condition": "condition_id"}[kind]


def snapshot_entities(driver, db: str, kind: str, deal_id: str,
                      prefix: str | None) -> dict[str, dict[str, Any]]:
    """For each entity of `kind` whose id contains deal_id (and optionally
    starts with `prefix` — pass None for python output, "conv_" for
    rule-based), return {id: {attribute_label: value}}.

    For rule-based snapshots, the prefix is stripped from the keys so
    diff aligns by canonical id.
    """
    id_attr = _id_attr_for(kind)
    snap: dict[str, dict[str, Any]] = {}
    tx = driver.transaction(db, TransactionType.READ)
    iids: list[tuple[str, str]] = []
    try:
        try:
            r = tx.query(
                f'match $x isa {kind}, has {id_attr} $id; '
                f'$id contains "{deal_id}"; select $x, $id;'
            ).resolve()
            for row in r.as_concept_rows():
                iid = row.get("x").get_iid()
                eid = row.get("id").as_attribute().get_value()
                iids.append((iid, eid))
        except Exception as exc:
            logger.warning(f"snapshot {kind}: {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass

    for iid, eid in iids:
        # Filter by mode: prefix=None → python (no rule-based prefix);
        # prefix="rule_based" or any non-None → rule-based (conv_/pilot_)
        if prefix is None:
            if _has_rule_based_prefix(eid):
                continue
            canonical_id = eid
        else:
            if not _has_rule_based_prefix(eid):
                continue
            canonical_id = _strip_rule_prefix(eid)
        # For conditions, also canonicalize __c0 / __cond_root to __root
        if kind == "condition":
            canonical_id = canonicalize_condition_id(canonical_id)
        snap[canonical_id] = _attr_dict(driver, db, iid)
        # Replace id field with canonical (drop conv_/pilot_ prefix
        # AND canonicalize condition id naming if applicable)
        if id_attr in snap[canonical_id]:
            snap[canonical_id][id_attr] = canonical_id
    return snap


# Edge snapshots — each returns a list of canonicalized tuples.
# For rule-based snapshots, the conv_/pilot_ prefix is stripped from
# id-bearing fields so the tuple keys align across both populations.

def _has_rule_based_prefix(eid: str) -> bool:
    return any(eid.startswith(p) for p in RULE_BASED_PREFIXES)


def _strip_rule_prefix(eid: str) -> str:
    """Strip whichever rule-based prefix is present (conv_ or pilot_).
    Returns the canonicalized id."""
    for p in RULE_BASED_PREFIXES:
        if eid.startswith(p):
            return eid[len(p):]
    return eid


def _strip(eid: str, prefix: str | None) -> str:
    """Backwards-compatible: when prefix is None, return eid unchanged.
    When prefix is the sentinel "rule_based", strip whichever rule-based
    prefix is present. When prefix is a literal string, strip it if
    present.
    """
    if prefix is None:
        return eid
    if prefix == "rule_based":
        return _strip_rule_prefix(eid)
    if eid.startswith(prefix):
        return eid[len(prefix):]
    return eid


# Condition IDs use different conventions across populations:
#   python:     <norm_id>__c0           / __c0_0 / __c0_1
#   rule-based: <norm_id>__cond_root    / __cond_root_0 / __cond_root_1
# Structurally identical (root + N indexed children). For the diff,
# canonicalize both to <norm_id>__root[_<idx>].

import re as _re

_CANON_PATTERNS = (
    _re.compile(r"__cond_root(_\d+)?$"),
    _re.compile(r"__c0(_\d+)?$"),
)


def canonicalize_condition_id(cid: str) -> str:
    for pat in _CANON_PATTERNS:
        m = pat.search(cid)
        if m:
            base = cid[: m.start()]
            suffix = m.group(1) or ""
            return f"{base}__root{suffix}"
    return cid


def snapshot_scope_edges(driver, db: str, deal_id: str,
                          prefix: str | None) -> dict[str, list[dict]]:
    """Capture norm scope edges. Returns one entry per relation type:
        norm_binds_subject (subject role), norm_scopes_action (action role),
        norm_scopes_object (object role), norm_scopes_instrument (instrument role).
    Each entry is a list of dicts: {norm_id, target_kind, target_label,
    edge_attrs?}. target_kind is the concrete subtype of the bound entity.
    """
    out: dict[str, list[dict]] = {
        "norm_binds_subject": [],
        "norm_scopes_action": [],
        "norm_scopes_object": [],
        "norm_scopes_instrument": [],
    }

    queries = {
        "norm_binds_subject": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (norm: $n, subject: $tgt) isa norm_binds_subject;\n'
            f'    $tgt isa! $type;\n'
            f'select $nid, $tgt, $type;\n'
        ),
        "norm_scopes_action": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (norm: $n, action: $tgt) isa norm_scopes_action;\n'
            f'    $tgt isa! $type;\n'
            f'select $nid, $tgt, $type;\n'
        ),
        "norm_scopes_object": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (norm: $n, object: $tgt) isa norm_scopes_object;\n'
            f'    $tgt isa! $type;\n'
            f'select $nid, $tgt, $type;\n'
        ),
        "norm_scopes_instrument": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (norm: $n, instrument: $tgt) isa norm_scopes_instrument;\n'
            f'    $tgt isa! $type;\n'
            f'select $nid, $tgt, $type;\n'
        ),
    }

    for rel, q in queries.items():
        tx = driver.transaction(db, TransactionType.READ)
        try:
            try:
                r = tx.query(q).resolve()
                for row in r.as_concept_rows():
                    nid = row.get("nid").as_attribute().get_value()
                    if prefix is None and _has_rule_based_prefix(nid):
                        continue
                    if prefix is not None and not _has_rule_based_prefix(nid):
                        continue
                    canonical_nid = _strip_rule_prefix(nid) if prefix is not None else nid
                    target_label = row.get("type").get_label()
                    out[rel].append({
                        "norm_id": canonical_nid,
                        "target_type": target_label,
                    })
            except Exception as exc:
                logger.warning(f"snapshot {rel}: {str(exc).splitlines()[0][:120]}")
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass
    return out


def snapshot_contributes_to(driver, db: str, deal_id: str,
                             prefix: str | None) -> list[dict]:
    """Capture norm_contributes_to_capacity edges with their attributes."""
    rows_out: list[dict] = []
    # Required attrs (always present on python emissions): child_index,
    # aggregation_function. Optional: aggregation_direction.
    q = (
        f'match\n'
        f'    $src isa norm, has norm_id $sid;\n'
        f'    $tgt isa norm, has norm_id $tid;\n'
        f'    $sid contains "{deal_id}";\n'
        f'    $tid contains "{deal_id}";\n'
        f'    (contributor: $src, pool: $tgt) isa norm_contributes_to_capacity,\n'
        f'        has child_index $cidx,\n'
        f'        has aggregation_function $agg;\n'
        f'select $sid, $tid, $cidx, $agg;\n'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                sid = row.get("sid").as_attribute().get_value()
                tid = row.get("tid").as_attribute().get_value()
                if prefix is None and (_has_rule_based_prefix(sid) or _has_rule_based_prefix(tid)):
                    continue
                if prefix is not None and (not _has_rule_based_prefix(sid) or not _has_rule_based_prefix(tid)):
                    continue
                csid = _strip_rule_prefix(sid) if prefix is not None else sid
                ctid = _strip_rule_prefix(tid) if prefix is not None else tid
                attrs = {
                    "child_index": row.get("cidx").as_attribute().get_value(),
                    "aggregation_function": row.get("agg").as_attribute().get_value(),
                }
                rows_out.append({
                    "source_norm_id": csid,
                    "target_norm_id": ctid,
                    "attrs": attrs,
                })
        except Exception as exc:
            logger.warning(f"snapshot contributes_to: {type(exc).__name__}: {exc!r}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return rows_out


def snapshot_defeats(driver, db: str, deal_id: str,
                      prefix: str | None) -> list[dict]:
    """Capture defeats edges (defeater_id, bound_norm_id)."""
    out: list[dict] = []
    q = (
        f'match\n'
        f'    $d isa defeater, has defeater_id $did;\n'
        f'    $n isa norm, has norm_id $nid;\n'
        f'    $did contains "{deal_id}";\n'
        f'    (defeater: $d, defeated: $n) isa defeats;\n'
        f'select $did, $nid;\n'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                did = row.get("did").as_attribute().get_value()
                nid = row.get("nid").as_attribute().get_value()
                if prefix is None and (_has_rule_based_prefix(did) or _has_rule_based_prefix(nid)):
                    continue
                if prefix is not None and (not _has_rule_based_prefix(did) or not _has_rule_based_prefix(nid)):
                    continue
                out.append({
                    "defeater_id": _strip_rule_prefix(did) if prefix is not None else did,
                    "norm_id": _strip_rule_prefix(nid) if prefix is not None else nid,
                })
        except Exception as exc:
            logger.warning(f"snapshot defeats: {str(exc).splitlines()[0][:200]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def snapshot_norm_has_root_condition(driver, db: str, deal_id: str,
                                      prefix: str | None) -> list[dict]:
    """Schema relation is `norm_has_condition` (norm, root)."""
    out: list[dict] = []
    q = (
        f'match\n'
        f'    $n isa norm, has norm_id $nid;\n'
        f'    $c isa condition, has condition_id $cid;\n'
        f'    $nid contains "{deal_id}";\n'
        f'    (norm: $n, root: $c) isa norm_has_condition;\n'
        f'select $nid, $cid;\n'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                nid = row.get("nid").as_attribute().get_value()
                cid = row.get("cid").as_attribute().get_value()
                if prefix is None and (_has_rule_based_prefix(nid) or _has_rule_based_prefix(cid)):
                    continue
                if prefix is not None and (not _has_rule_based_prefix(nid) or not _has_rule_based_prefix(cid)):
                    continue
                # Canonicalize condition_id (strip __c0 / __cond_root variants)
                canon_cid = _strip_rule_prefix(cid) if prefix is not None else cid
                out.append({
                    "norm_id": _strip_rule_prefix(nid) if prefix is not None else nid,
                    "condition_id": canonicalize_condition_id(canon_cid),
                })
        except Exception as exc:
            logger.warning(f"snapshot norm_has_root_condition: {str(exc).splitlines()[0][:200]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def snapshot_condition_has_child(driver, db: str, deal_id: str,
                                  prefix: str | None) -> list[dict]:
    """condition_has_child owns child_index only."""
    out: list[dict] = []
    q = (
        f'match\n'
        f'    $p isa condition, has condition_id $pid;\n'
        f'    $c isa condition, has condition_id $cid;\n'
        f'    $pid contains "{deal_id}";\n'
        f'    (parent: $p, child: $c) isa condition_has_child,\n'
        f'        has child_index $cidx;\n'
        f'select $pid, $cid, $cidx;\n'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                pid = row.get("pid").as_attribute().get_value()
                cid = row.get("cid").as_attribute().get_value()
                if prefix is None and (_has_rule_based_prefix(pid) or _has_rule_based_prefix(cid)):
                    continue
                if prefix is not None and (not _has_rule_based_prefix(pid) or not _has_rule_based_prefix(cid)):
                    continue
                cpid = _strip_rule_prefix(pid) if prefix is not None else pid
                ccid = _strip_rule_prefix(cid) if prefix is not None else cid
                # Canonicalize condition IDs to align __c0 / __cond_root
                cpid = canonicalize_condition_id(cpid)
                ccid = canonicalize_condition_id(ccid)
                attrs = {"child_index": row.get("cidx").as_attribute().get_value()}
                out.append({
                    "parent_condition_id": cpid,
                    "child_condition_id": ccid,
                    "attrs": attrs,
                })
        except Exception as exc:
            logger.warning(f"snapshot condition_has_child: {type(exc).__name__}: {exc!r}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def snapshot_condition_predicate(driver, db: str, deal_id: str,
                                  prefix: str | None) -> list[dict]:
    """For each condition, capture the state_predicate_id it references
    (if any). Compares predicate-binding shape across populations.
    """
    out: list[dict] = []
    q = (
        f'match\n'
        f'    $c isa condition, has condition_id $cid;\n'
        f'    $cid contains "{deal_id}";\n'
        f'    (condition: $c, predicate: $p)\n'
        f'        isa condition_references_predicate;\n'
        f'    $p has state_predicate_id $pid;\n'
        f'select $cid, $pid;\n'
    )
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                cid = row.get("cid").as_attribute().get_value()
                pid = row.get("pid").as_attribute().get_value()
                if prefix is None and _has_rule_based_prefix(cid):
                    continue
                if prefix is not None and not _has_rule_based_prefix(cid):
                    continue
                canon_cid = _strip_rule_prefix(cid) if prefix is not None else cid
                out.append({
                    "condition_id": canonicalize_condition_id(canon_cid),
                    "predicate_id": pid,
                })
        except Exception as exc:
            logger.debug(f"snapshot condition_predicate: {str(exc).splitlines()[0][:200]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def snapshot_scope_cliffs(driver, db: str, deal_id: str) -> dict[str, int]:
    """Inventory edges that python emits but rule-based does not. Numbers
    surface in the diff report as 'rule-based-pending' (not failures).
    """
    cliffs = {}
    cliff_queries = {
        "event_provides_proceeds_to_norm": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (proceeds_target_norm: $n) isa event_provides_proceeds_to_norm;\n'
            f'select $n;\n'
        ),
        "norm_reallocates_capacity_from": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (reallocation_receiver: $n) isa norm_reallocates_capacity_from;\n'
            f'select $n;\n'
        ),
        "norm_provides_carryforward_to": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (carryforward_source: $n) isa norm_provides_carryforward_to;\n'
            f'select $n;\n'
        ),
        "norm_provides_carryback_to": (
            f'match\n'
            f'    $n isa norm, has norm_id $nid;\n'
            f'    $nid contains "{deal_id}";\n'
            f'    (carryback_source: $n) isa norm_provides_carryback_to;\n'
            f'select $n;\n'
        ),
    }
    for rel, q in cliff_queries.items():
        tx = driver.transaction(db, TransactionType.READ)
        try:
            try:
                r = tx.query(q).resolve()
                cliffs[rel] = len(list(r.as_concept_rows()))
            except Exception:
                cliffs[rel] = -1
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass
    return cliffs


def take_snapshot(driver, db: str, deal_id: str,
                   prefix: str | None) -> dict[str, Any]:
    """Take full snapshot of v4 emissions for a deal under the given
    prefix discipline (None = python's non-prefixed; 'conv_' = rule-based)."""
    return {
        "norms": snapshot_entities(driver, db, "norm", deal_id, prefix),
        "defeaters": snapshot_entities(driver, db, "defeater", deal_id, prefix),
        "conditions": snapshot_entities(driver, db, "condition", deal_id, prefix),
        "scope_edges": snapshot_scope_edges(driver, db, deal_id, prefix),
        "contributes_to": snapshot_contributes_to(driver, db, deal_id, prefix),
        "defeats": snapshot_defeats(driver, db, deal_id, prefix),
        "norm_has_root_condition":
            snapshot_norm_has_root_condition(driver, db, deal_id, prefix),
        "condition_has_child":
            snapshot_condition_has_child(driver, db, deal_id, prefix),
        "condition_predicate":
            snapshot_condition_predicate(driver, db, deal_id, prefix),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Rule-based execution
# ═══════════════════════════════════════════════════════════════════════════════


def list_rule_ids(driver, db: str) -> list[str]:
    """Return all projection_rule IDs (conv_* + pilot)."""
    ids: list[str] = []
    tx = driver.transaction(db, TransactionType.READ)
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
    """Return rule IDs in the execution order the converter uses:
    1. Mapping-derived rules (rule_conv_<source_entity_type>) — including the
       basket-level builder rules and rule_conv_jcrew_blocker
    2. b_aggregate (rule_conv_builder_b_aggregate)
    3. Builder sub-source rules (rule_conv_builder_builder_source_*)
    4. Defeater rules (rule_conv_*_defeater)
    5. Pilot (rule_general_rp_basket) — slot anywhere; place last for
       cleanest report ordering
    """
    mapping = []
    b_agg = []
    sub_source = []
    defeaters = []
    pilot = []
    for rid in rule_ids:
        if rid == "rule_general_rp_basket":
            pilot.append(rid)
        elif rid == "rule_conv_builder_b_aggregate":
            b_agg.append(rid)
        elif rid.startswith("rule_conv_builder_builder_source_"):
            sub_source.append(rid)
        elif rid.endswith("_defeater"):
            defeaters.append(rid)
        else:
            mapping.append(rid)
    return mapping + b_agg + sub_source + defeaters + pilot


def run_rule_based(driver, db: str, deal_id: str) -> tuple[list[dict], float]:
    """Execute every projection_rule. Returns (per-rule reports, wall-clock seconds)."""
    rule_ids = order_rule_ids(list_rule_ids(driver, db))
    logger.info(f"rule-based: executing {len(rule_ids)} rules")

    reports: list[dict] = []
    start = time.perf_counter()
    for rid in rule_ids:
        t0 = time.perf_counter()
        report = execute_rule(driver, db, rid, deal_id)
        elapsed = time.perf_counter() - t0
        reports.append({
            "rule_id": rid,
            "matches": report.matches,
            "norms_emitted": report.norms_emitted,
            "relations_emitted": report.relations_emitted,
            "conditions_emitted": report.conditions_emitted,
            "provenance_emitted": report.provenance_emitted,
            "errors": report.errors[:5],
            "warnings": report.warnings[:5],
            "elapsed_s": round(elapsed, 3),
        })
    total_elapsed = time.perf_counter() - start
    return reports, total_elapsed


# ═══════════════════════════════════════════════════════════════════════════════
# Diff
# ═══════════════════════════════════════════════════════════════════════════════


def diff_entity_dicts(a: dict[str, dict], b: dict[str, dict],
                       ignore_keys: set[str] | None = None
                       ) -> dict[str, Any]:
    """Diff two snapshots keyed by entity id. Returns a dict with
    only_in_a, only_in_b, attribute_diffs (per id with mismatching keys).
    """
    ignore = ignore_keys or set()
    only_in_a = sorted(set(a) - set(b))
    only_in_b = sorted(set(b) - set(a))
    attribute_diffs: dict[str, dict[str, Any]] = {}
    for k in sorted(set(a) & set(b)):
        a_attrs = a[k]
        b_attrs = b[k]
        keys_a = set(a_attrs) - ignore
        keys_b = set(b_attrs) - ignore
        per_id: dict[str, Any] = {}
        for ak in sorted(keys_a | keys_b):
            va = a_attrs.get(ak)
            vb = b_attrs.get(ak)
            if va != vb:
                per_id[ak] = {"python": va, "rule_based": vb}
        if per_id:
            attribute_diffs[k] = per_id
    return {
        "only_in_python": only_in_a,
        "only_in_rule_based": only_in_b,
        "attribute_diffs": attribute_diffs,
        "shared_count": len(set(a) & set(b)),
    }


def diff_edge_lists(a: list[dict], b: list[dict],
                     key_fields: list[str]) -> dict[str, Any]:
    """Diff two lists of edge dicts by tuple-key on `key_fields`. Returns
    only_in_a, only_in_b, attribute_diffs for shared keys.
    """
    def make_key(d: dict) -> tuple:
        return tuple(d.get(f) for f in key_fields)

    a_by_key = {make_key(d): d for d in a}
    b_by_key = {make_key(d): d for d in b}
    only_a = sorted([str(k) for k in (set(a_by_key) - set(b_by_key))])
    only_b = sorted([str(k) for k in (set(b_by_key) - set(a_by_key))])
    attribute_diffs: dict[str, Any] = {}
    for k in (set(a_by_key) & set(b_by_key)):
        da = a_by_key[k]
        db = b_by_key[k]
        # Compare 'attrs' sub-dict if present, else direct compare
        if "attrs" in da or "attrs" in db:
            aa = da.get("attrs", {})
            ba = db.get("attrs", {})
            differing = {
                ak: {"python": aa.get(ak), "rule_based": ba.get(ak)}
                for ak in (set(aa) | set(ba))
                if aa.get(ak) != ba.get(ak)
            }
            if differing:
                attribute_diffs[str(k)] = differing
    return {
        "only_in_python": only_a,
        "only_in_rule_based": only_b,
        "attribute_diffs": attribute_diffs,
        "shared_count": len(set(a_by_key) & set(b_by_key)),
    }


def diff_snapshots(snap_python: dict, snap_rule: dict) -> dict[str, Any]:
    """Top-level diff. Each section returns a structured diff."""
    diff = {}
    diff["norms"] = diff_entity_dicts(
        snap_python["norms"], snap_rule["norms"], ignore_keys={"norm_id"},
    )
    diff["defeaters"] = diff_entity_dicts(
        snap_python["defeaters"], snap_rule["defeaters"],
        ignore_keys={"defeater_id"},
    )
    diff["conditions"] = diff_entity_dicts(
        snap_python["conditions"], snap_rule["conditions"],
        ignore_keys={"condition_id"},
    )
    # Scope edges per relation type
    diff["scope_edges"] = {}
    for rel in ("norm_binds_subject", "norm_scopes_action",
                "norm_scopes_object", "norm_scopes_instrument"):
        diff["scope_edges"][rel] = diff_edge_lists(
            snap_python["scope_edges"][rel],
            snap_rule["scope_edges"][rel],
            key_fields=["norm_id", "target_type"],
        )
    diff["contributes_to"] = diff_edge_lists(
        snap_python["contributes_to"], snap_rule["contributes_to"],
        key_fields=["source_norm_id", "target_norm_id"],
    )
    diff["defeats"] = diff_edge_lists(
        snap_python["defeats"], snap_rule["defeats"],
        key_fields=["defeater_id", "norm_id"],
    )
    diff["norm_has_root_condition"] = diff_edge_lists(
        snap_python["norm_has_root_condition"],
        snap_rule["norm_has_root_condition"],
        key_fields=["norm_id", "condition_id"],
    )
    diff["condition_has_child"] = diff_edge_lists(
        snap_python["condition_has_child"],
        snap_rule["condition_has_child"],
        key_fields=["parent_condition_id", "child_condition_id"],
    )
    diff["condition_predicate"] = diff_edge_lists(
        snap_python["condition_predicate"],
        snap_rule["condition_predicate"],
        key_fields=["condition_id"],
    )
    return diff


def summarize_diff(diff: dict) -> dict[str, Any]:
    """Compact summary suitable for console + JSON header."""
    summary = {}
    for section in ("norms", "defeaters", "conditions"):
        d = diff[section]
        summary[section] = {
            "shared": d["shared_count"],
            "only_python": len(d["only_in_python"]),
            "only_rule_based": len(d["only_in_rule_based"]),
            "attr_diffs": len(d["attribute_diffs"]),
        }
    for section in ("contributes_to", "defeats", "norm_has_root_condition",
                    "condition_has_child", "condition_predicate"):
        d = diff[section]
        summary[section] = {
            "shared": d["shared_count"],
            "only_python": len(d["only_in_python"]),
            "only_rule_based": len(d["only_in_rule_based"]),
            "attr_diffs": len(d["attribute_diffs"]),
        }
    summary["scope_edges"] = {}
    for rel, d in diff["scope_edges"].items():
        summary["scope_edges"][rel] = {
            "shared": d["shared_count"],
            "only_python": len(d["only_in_python"]),
            "only_rule_based": len(d["only_in_rule_based"]),
        }
    return summary


def diff_is_zero(summary: dict) -> bool:
    """A 'zero diff' run has no only_python / only_rule_based / attr_diffs
    in any section. Shared counts may be any positive number."""
    for section, stats in summary.items():
        if section == "scope_edges":
            for rel, sub in stats.items():
                if sub["only_python"] or sub["only_rule_based"]:
                    return False
        else:
            if stats.get("only_python") or stats.get("only_rule_based"):
                return False
            if stats.get("attr_diffs"):
                return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True)
    parser.add_argument(
        "--skip-python", action="store_true",
        help="skip rerunning python projection (use existing valence_v4 "
             "non-prefixed output); rule-based timing only"
    )
    parser.add_argument(
        "--snapshot-only", action="store_true",
        help="snapshot current state (both populations) and diff WITHOUT "
             "re-running either projection. Useful for quick debugging."
    )
    args = parser.parse_args()

    if settings.typedb_database != "valence_v4":
        logger.error("typedb_database must be 'valence_v4' (override TYPEDB_DATABASE)")
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = OUTPUT_DIR / f"parallel_run_{timestamp}.json"

    driver = connect()
    db = "valence_v4"

    try:
        # ── Phase A: Python projection ─────────────────────────────────────
        logger.info("=" * 60)
        logger.info("Phase A: python projection")
        if args.snapshot_only:
            logger.info("--snapshot-only: skipping python re-run; using existing")
            python_elapsed = -1.0
        elif args.skip_python:
            logger.info("--skip-python: assuming existing python output is valid")
            python_elapsed = -1.0
        else:
            # Wipe everything for the deal first (both python + conv_)
            cleared = clear_python_and_conv_output(driver, db, args.deal)
            logger.info(f"  wiped output for deal {args.deal}: {cleared}")
            t0 = time.perf_counter()
            report = project_deal(driver, db, args.deal, dry_run=False)
            python_elapsed = time.perf_counter() - t0
            logger.info(f"  python project_deal: {python_elapsed:.2f}s")
            logger.info(
                f"  norms_created={report.norms_created} "
                f"conditions_created={report.conditions_created} "
                f"scope_edges_created={report.scope_edges_created}"
            )

        snap_python = take_snapshot(driver, db, args.deal, prefix=None)
        logger.info(
            f"  python snapshot: norms={len(snap_python['norms'])} "
            f"defeaters={len(snap_python['defeaters'])} "
            f"conditions={len(snap_python['conditions'])} "
            f"contributes_to={len(snap_python['contributes_to'])} "
            f"defeats={len(snap_python['defeats'])}"
        )

        # ── Phase B: Rule-based projection ─────────────────────────────────
        logger.info("=" * 60)
        logger.info("Phase B: rule-based projection")
        if args.snapshot_only:
            logger.info("--snapshot-only: skipping rule-based re-run; using existing")
            rule_elapsed = -1.0
            rule_reports: list[dict] = []
        else:
            cleared_conv = clear_rule_based_output_only(driver, db, args.deal)
            logger.info(f"  cleared prior conv_ output: {cleared_conv}")
            rule_reports, rule_elapsed = run_rule_based(driver, db, args.deal)
            logger.info(f"  rule-based total: {rule_elapsed:.2f}s")
            total_norms = sum(r["norms_emitted"] for r in rule_reports)
            total_relations = sum(r["relations_emitted"] for r in rule_reports)
            logger.info(
                f"  rule-based emitted: norms={total_norms} "
                f"relations={total_relations}"
            )

        snap_rule = take_snapshot(driver, db, args.deal, prefix=NORM_ID_PREFIX)
        logger.info(
            f"  rule-based snapshot: norms={len(snap_rule['norms'])} "
            f"defeaters={len(snap_rule['defeaters'])} "
            f"conditions={len(snap_rule['conditions'])} "
            f"contributes_to={len(snap_rule['contributes_to'])} "
            f"defeats={len(snap_rule['defeats'])}"
        )

        # ── Phase C: Diff ──────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("Phase C: structural diff")
        diff = diff_snapshots(snap_python, snap_rule)
        summary = summarize_diff(diff)
        is_zero = diff_is_zero(summary)
        for section, stats in summary.items():
            if section == "scope_edges":
                for rel, sub in stats.items():
                    logger.info(
                        f"  {rel:30s} shared={sub['shared']:3d} "
                        f"only_python={sub['only_python']:3d} "
                        f"only_rule_based={sub['only_rule_based']:3d}"
                    )
            else:
                logger.info(
                    f"  {section:30s} shared={stats['shared']:3d} "
                    f"only_python={stats['only_python']:3d} "
                    f"only_rule_based={stats['only_rule_based']:3d} "
                    f"attr_diffs={stats.get('attr_diffs', 0):3d}"
                )

        # Scope cliffs
        cliffs = snapshot_scope_cliffs(driver, db, args.deal)
        logger.info("=" * 60)
        logger.info("Scope cliffs (python emits, rule-based does not):")
        for rel, n in cliffs.items():
            logger.info(f"  {rel:42s} python={n}")

        # Benchmark
        logger.info("=" * 60)
        if python_elapsed > 0 and rule_elapsed > 0:
            ratio = rule_elapsed / python_elapsed
            logger.info(
                f"Benchmark: python={python_elapsed:.2f}s "
                f"rule_based={rule_elapsed:.2f}s "
                f"ratio={ratio:.2f}x "
                f"threshold={BENCHMARK_THRESHOLD}x"
            )
            benchmark_pass = ratio <= BENCHMARK_THRESHOLD
            logger.info(
                f"Benchmark gate: {'PASS' if benchmark_pass else 'FAIL'}"
            )
        else:
            logger.info("Benchmark: skipped (one or both runs not timed)")
            benchmark_pass = None

        # Final cleanup: wipe conv_ output, restore harness baseline
        logger.info("=" * 60)
        logger.info("Final cleanup: clearing conv_ output to restore harness baseline")
        if not args.snapshot_only:
            cleared_final = clear_rule_based_output_only(driver, db, args.deal)
            logger.info(f"  cleared: {cleared_final}")

        # Optional: post-run harness check
        if not args.snapshot_only and not args.skip_python:
            logger.info("=" * 60)
            logger.info("Post-run validation_harness baseline check")
            try:
                from app.services.validation_harness import (
                    connect as harness_connect,  # noqa: F401
                    EXPECTED_DB,  # noqa: F401
                )
                # The harness uses its own driver; we can't share ours easily.
                # Instead, log a hint to run it manually after the script.
                logger.info(
                    "  to verify baseline preserved, run:"
                )
                logger.info(
                    f'  TYPEDB_DATABASE={db} '
                    f'C:/Users/olive/ValenceV3/.venv/Scripts/python.exe '
                    f'-m app.services.validation_harness --deal {args.deal}'
                )
                logger.info(
                    f"  expected: A1=pass, A4 missing={HARNESS_BASELINE['A4_missing']} "
                    f"spurious={HARNESS_BASELINE['A4_spurious']} "
                    f"mismatched={HARNESS_BASELINE['A4_mismatched']}, A5=pass, A6=pass"
                )
            except Exception as exc:
                logger.warning(f"harness check skipped: {exc}")

        # ── Persist results ────────────────────────────────────────────────
        result_doc = {
            "timestamp_utc": timestamp,
            "deal_id": args.deal,
            "summary": summary,
            "is_zero_diff": is_zero,
            "scope_cliffs": cliffs,
            "benchmark": {
                "python_seconds": python_elapsed,
                "rule_based_seconds": rule_elapsed,
                "ratio": (
                    rule_elapsed / python_elapsed
                    if python_elapsed > 0 and rule_elapsed > 0 else None
                ),
                "threshold_x": BENCHMARK_THRESHOLD,
                "passed": benchmark_pass,
            },
            "rule_reports": rule_reports if not args.snapshot_only else [],
            "diff": diff,
        }
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(result_doc, fh, indent=2, default=str)
        logger.info(f"results written to {output_path}")

        # Exit code: 0 = parity zero AND benchmark pass; 1 = anything failed
        gate_pass = is_zero and (benchmark_pass is None or benchmark_pass)
        return 0 if gate_pass else 1
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
