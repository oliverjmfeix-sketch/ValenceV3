"""
Valence v4 — ground-truth-to-graph loader.

Loads app/data/duck_creek_rp_ground_truth.yaml into a dedicated TypeDB database
(`valence_v4_ground_truth`). Harness comparisons are then graph-to-graph:
extracted data in `valence_v4`, ground truth in `valence_v4_ground_truth`.

YAML is an authoring convenience. Graph is the source of truth.

CLI:
    py -3.12 -m app.scripts.load_ground_truth            # create + load (errors if DB already has norm data)
    py -3.12 -m app.scripts.load_ground_truth --force    # drop + recreate + load

Preflight: refuses to target `valence_v4` (the extraction DB) regardless of
.env setting. This script always targets `valence_v4_ground_truth`.

Scope (pilot): loads
    - schema_unified.tql + schema_v4_deontic.tql
    - all seeds (primitives, state_predicates, segment_types, segment_expectations,
      expected_norm_kinds, gold_questions)
    - post-seed integrity check (predicate_id composite-key contract)
    - per-deal party instances (one per party_role the ground truth uses)
    - norm entities with scalar attributes
    - norm_scopes_action edges — one per entry in scoped_actions (all, not first)
    - norm_scopes_instrument / norm_scopes_object edges — one per entry in
      scoped_objects, dispatching on whether the object_class_label names an
      instrument_class subtype
    - norm_binds_subject edges — one per entry in subject_role (all, not first)
    - condition entities — full tree recursion: root + every compound child +
      every atomic leaf each becomes its own condition entity. condition_topology
      populated on root only; operator on every node
    - condition_has_child relations with child_index preserving sibling order
    - norm_has_condition relation
    - condition_references_predicate edges on every atomic leaf (resolved via
      predicate_id composite lookup)
    - norm_serves_question edges per serves_questions list
    - norm_contributes_to_capacity edges per contributes_to_norm_id
    - norm_provides_carryforward_to / _carryback_to per YAML field

Deferred (populated only when Prompt 07 projection lands):
    - Defeater / violation_consequent structures
    - norm_in_segment edges
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

import yaml  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.predicate_id import construct_state_predicate_id  # noqa: E402
from app.services.seed_loader import load_seeds  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("load_ground_truth")

DATA_DIR = REPO_ROOT / "app" / "data"
GROUND_TRUTH = DATA_DIR / "duck_creek_rp_ground_truth.yaml"
TARGET_DB = "valence_v4_ground_truth"


def _tq_string(s: str) -> str:
    """Escape a string for TypeQL double-quoted literal."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _first(lst):
    return lst[0] if lst else None


def load_instrument_labels(driver, db_name: str) -> set[str]:
    """Query the schema for concrete subtypes of instrument_class.

    Used at scope-edge creation time to dispatch between
    `norm_scopes_instrument` (for instrument objects) and
    `norm_scopes_object` (for non-instrument objects). SSoT: the set
    is sourced from the live schema, not a hardcoded list in Python.
    """
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query("match $t sub instrument_class; select $t;").resolve()
        labels = {row.get("t").get_label() for row in r.as_concept_rows()}
        labels.discard("instrument_class")   # drop the abstract parent
        return labels
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def load_schema_file(driver, db_name: str, filepath: Path) -> None:
    tx = driver.transaction(db_name, TransactionType.SCHEMA)
    try:
        tx.query(filepath.read_text(encoding="utf-8")).resolve()
        tx.commit()
        logger.info("  loaded schema: %s", filepath.name)
    except Exception:
        if tx.is_open():
            tx.close()
        raise


def execute_write(driver, db_name: str, tql: str) -> None:
    tx = driver.transaction(db_name, TransactionType.WRITE)
    try:
        tx.query(tql).resolve()
        tx.commit()
    except Exception:
        if tx.is_open():
            tx.close()
        raise


def count_norms(driver, db_name: str) -> int:
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query("match $n isa norm; select $n;").resolve()
        return len(list(r.as_concept_rows()))
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def count_of(driver, db_name: str, isa: str) -> int:
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        r = tx.query(f"match $x isa {isa}; select $x;").resolve()
        return len(list(r.as_concept_rows()))
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


# ─── Phase 1: schema + seeds ──────────────────────────────────────────────────


def build_db(driver, force: bool) -> None:
    if driver.databases.contains(TARGET_DB):
        if force:
            driver.databases.get(TARGET_DB).delete()
            logger.info("dropped existing %s", TARGET_DB)
        else:
            existing = count_norms(driver, TARGET_DB)
            if existing > 0:
                logger.error("%s exists with %d norms. Pass --force to rebuild.", TARGET_DB, existing)
                raise SystemExit(2)
            driver.databases.get(TARGET_DB).delete()
    driver.databases.create(TARGET_DB)
    logger.info("created %s", TARGET_DB)

    load_schema_file(driver, TARGET_DB, DATA_DIR / "schema_unified.tql")
    load_schema_file(driver, TARGET_DB, DATA_DIR / "schema_v4_deontic.tql")

    # Functions needed for validation harness queries over ground-truth db
    for fn in ("deontic_condition_functions.tql", "deontic_norm_functions.tql",
               "deontic_capacity_functions.tql", "deontic_pathway_functions.tql",
               "deontic_validation_functions.tql", "deontic_pattern_functions.tql"):
        load_schema_file(driver, TARGET_DB, DATA_DIR / fn)

    # Seed via the shared loader — kind="ground_truth" loads SHARED_SEEDS only
    # (primitives, state_predicates, segment_types, gold_questions). The
    # harness-only seeds (segment_norm_expectations, expected_norm_kinds) are
    # intentionally excluded from this database; they are consumed against
    # valence_v4, not against ground truth. load_seeds runs the post-seed
    # state_predicate_id integrity check.
    logger.info("loading seeds (kind=ground_truth)")
    seed_counts = load_seeds(driver, TARGET_DB, kind="ground_truth")
    logger.info("seed counts: %s", seed_counts)


# ─── Phase 2: per-deal party instances ────────────────────────────────────────

PARTY_ROLE_TO_ENTITY = {
    "borrower": "borrower_party",
    "loan_party": "loan_party",
    "restricted_sub": "restricted_sub_party",
    "unrestricted_sub": "unrestricted_sub_party",
    "holdings": "holdings_party",
    "agent": "agent_party",
    "required_lenders": "required_lenders_party",
}


def seed_deal_parties(driver, deal_id: str, roles_used: set[str]) -> None:
    """Create one party per role the ground truth uses, scoped to this deal."""
    lines = ["insert"]
    for role in sorted(roles_used):
        entity = PARTY_ROLE_TO_ENTITY.get(role)
        if not entity:
            logger.warning("unknown party_role %r — skipping party creation", role)
            continue
        pid = f"{deal_id}__{role}"
        lines.append(
            f'    $p_{role} isa {entity}, has party_id {_tq_string(pid)}, has party_role {_tq_string(role)};'
        )
    tql = "\n".join(lines) + ";" if len(lines) > 1 else ""
    if tql:
        # Strip the unintentional trailing ";;" if present by re-joining cleanly
        tql = "\n".join(lines)
        execute_write(driver, TARGET_DB, tql)
        logger.info("  seeded %d per-deal party instances", len(lines) - 1)


# ─── Phase 3: norm inserts ────────────────────────────────────────────────────


def _opt_owns(name: str, val) -> str:
    """Render an optional `has <name> <val>` clause; skip if val is None."""
    if val is None:
        return ""
    if isinstance(val, bool):
        v = "true" if val else "false"
    elif isinstance(val, (int, float)):
        v = str(val)
    else:
        v = _tq_string(str(val))
    return f", has {name} {v}"


def insert_norm(driver, norm: dict) -> None:
    nid = norm["norm_id"]
    q = ["insert"]
    attrs = [f'has norm_id {_tq_string(nid)}']
    # capacity_aggregation_function is an authoring cue at the NORM level in YAML,
    # but the schema carries aggregation_function on the norm_contributes_to_capacity
    # EDGE, not on the norm entity. Skip here; read elsewhere when building edges.
    for field in ("norm_kind", "modality", "capacity_composition", "action_scope",
                  "cap_grower_reference", "source_text", "source_section"):
        if field in norm and norm[field] is not None:
            attrs.append(f'has {field} {_tq_string(str(norm[field]))}')
    for field in ("cap_usd", "cap_grower_pct", "floor_value"):
        if field in norm and norm[field] is not None:
            attrs.append(f'has {field} {norm[field]}')
    if "cap_uses_greater_of" in norm and norm["cap_uses_greater_of"] is not None:
        attrs.append(f'has cap_uses_greater_of {"true" if norm["cap_uses_greater_of"] else "false"}')
    if "source_page" in norm:
        sp = norm["source_page"]
        if isinstance(sp, int):
            attrs.append(f"has source_page {sp}")
        # placeholder strings like "<page_unknown>" skipped — type is integer

    q.append(f"    $n isa norm, " + ",\n        ".join(attrs) + ";")
    execute_write(driver, TARGET_DB, "\n".join(q))


def bind_norm_scope(driver, norm: dict, instrument_labels: set[str]) -> None:
    """Emit one edge per entry in subject_role, scoped_actions, scoped_objects.

    - subject_role → norm_binds_subject (one edge per role)
    - scoped_actions → norm_scopes_action (one edge per action label)
    - scoped_objects → norm_scopes_instrument when label ∈ instrument_labels,
                       else norm_scopes_object (one edge per object label)
    """
    nid = norm["norm_id"]

    # norm_binds_subject — iterate all subject_roles
    for sr in (norm.get("subject_role") or []):
        q = f"""
match
    $n isa norm, has norm_id {_tq_string(nid)};
    $p isa party, has party_role {_tq_string(sr)};
insert
    (norm: $n, subject: $p) isa norm_binds_subject;
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.debug("bind_subject skipped for %s (%s): %s", nid, sr, str(e)[:120])

    # norm_scopes_action — iterate all scoped_actions
    for sa in (norm.get("scoped_actions") or []):
        q = f"""
match
    $n isa norm, has norm_id {_tq_string(nid)};
    $ac isa action_class, has action_class_label {_tq_string(sa)};
insert
    (norm: $n, action: $ac) isa norm_scopes_action;
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.warning("norm_scopes_action skipped for %s (%s): %s", nid, sa, str(e)[:120])

    # scoped_objects — dispatch on whether the label names an instrument subtype
    for so in (norm.get("scoped_objects") or []):
        if so in instrument_labels:
            relation_type = "norm_scopes_instrument"
            role_b = "instrument"
            match_type = "instrument_class"
            match_attr = "instrument_class_label"
        else:
            relation_type = "norm_scopes_object"
            role_b = "object"
            match_type = "object_class"
            match_attr = "object_class_label"
        q = f"""
match
    $n isa norm, has norm_id {_tq_string(nid)};
    $oc isa {match_type}, has {match_attr} {_tq_string(so)};
insert
    (norm: $n, {role_b}: $oc) isa {relation_type};
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.warning("%s skipped for %s (%s): %s", relation_type, nid, so, str(e)[:120])


# ─── Phase 4: conditions ──────────────────────────────────────────────────────


_OPERATOR_MAP = {"atomic": "atomic", "OR": "or", "AND": "and", "NOT": "not"}


def construct_condition_id(norm_id: str, path: str) -> str:
    """Deterministic id for a condition node from norm_id + tree path.

    Root is always path="c0"; children suffix depth+index. Example:
    norm `6e76ed06_ratio_rp_basket_permission` → root
    `6e76ed06_ratio_rp_basket_permission__c0`, first child `__c0_0`,
    second `__c0_1`, grandchild `__c0_0_0`, etc.

    Deterministic ids aid debugging and make reloads idempotent (though
    the loader drops + recreates the DB via --force, so idempotency
    isn't strictly required).
    """
    return f"{norm_id}__{path}"


def _insert_condition_node(driver, norm_id: str, cond: dict, path: str, is_root: bool) -> int:
    """Recursively create a condition entity for this node and all descendants.

    - Creates the condition entity with condition_id + condition_operator.
    - On root only, sets condition_topology.
    - For atomic nodes, resolves the state_predicate via composite id and
      creates a condition_references_predicate edge.
    - For compound nodes, recurses into each child, then creates a
      condition_has_child relation with child_index preserving YAML order.

    Returns the total number of condition entities created (self + descendants).
    """
    op = _OPERATOR_MAP.get(cond.get("type"), cond.get("type", "atomic"))
    cid = construct_condition_id(norm_id, path)

    owns = [
        f'has condition_id {_tq_string(cid)}',
        f'has condition_operator {_tq_string(op)}',
    ]
    if is_root and cond.get("topology"):
        owns.append(f'has condition_topology {_tq_string(cond["topology"])}')

    q_node = f"""
insert
    $c isa condition, { ", ".join(owns) };
"""
    execute_write(driver, TARGET_DB, q_node)
    count = 1

    if op == "atomic":
        pred_label = cond.get("predicate")
        if pred_label:
            thr = cond.get("threshold_value_double")
            op_cmp = cond.get("operator_comparison")
            ref = cond.get("reference_predicate_label")
            pred_id = construct_state_predicate_id(pred_label, thr, op_cmp, ref)
            q_pred = f"""
match
    $c isa condition, has condition_id {_tq_string(cid)};
    $p isa state_predicate, has state_predicate_id {_tq_string(pred_id)};
insert
    (condition: $c, predicate: $p) isa condition_references_predicate;
"""
            try:
                execute_write(driver, TARGET_DB, q_pred)
            except Exception as e:
                logger.warning("condition_references_predicate skipped for %s at %s (pred_id=%r): %s",
                               norm_id, path, pred_id, str(e)[:160])
        return count

    # Compound: recurse into ordered children and link parent → child
    for idx, child in enumerate(cond.get("children") or []):
        child_path = f"{path}_{idx}"
        count += _insert_condition_node(driver, norm_id, child, child_path, is_root=False)
        child_cid = construct_condition_id(norm_id, child_path)
        q_link = f"""
match
    $parent isa condition, has condition_id {_tq_string(cid)};
    $child isa condition, has condition_id {_tq_string(child_cid)};
insert
    (parent: $parent, child: $child) isa condition_has_child,
        has child_index {idx};
"""
        execute_write(driver, TARGET_DB, q_link)
    return count


def insert_condition_tree(driver, norm: dict) -> int:
    """Build the full condition tree for a norm and link it via norm_has_condition.

    Walks the YAML condition tree recursively, emitting one condition entity
    per node, condition_has_child edges with child_index, and
    condition_references_predicate edges on every atomic leaf.

    Returns the number of condition entities created for this norm (0 if the
    norm has no condition block).
    """
    cond = norm.get("condition")
    if not cond or not isinstance(cond, dict):
        return 0

    nid = norm["norm_id"]
    count = _insert_condition_node(driver, nid, cond, path="c0", is_root=True)

    root_cid = construct_condition_id(nid, "c0")
    q_link = f"""
match
    $n isa norm, has norm_id {_tq_string(nid)};
    $c isa condition, has condition_id {_tq_string(root_cid)};
insert
    (norm: $n, root: $c) isa norm_has_condition;
"""
    execute_write(driver, TARGET_DB, q_link)
    return count


# ─── Phase 5: other relations ─────────────────────────────────────────────────


def link_serves_questions(driver, norm: dict) -> None:
    nid = norm["norm_id"]
    for entry in norm.get("serves_questions") or []:
        if not isinstance(entry, dict):
            continue
        qid = entry.get("question_id")
        role = entry.get("role", "primary")
        q = f"""
match
    $n isa norm, has norm_id {_tq_string(nid)};
    $q isa gold_question, has question_id {_tq_string(qid)};
insert
    (norm: $n, question: $q) isa norm_serves_question,
        has serves_role {_tq_string(role)};
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.warning("norm_serves_question skipped for %s → %s: %s", nid, qid, str(e)[:120])


def link_contributes_to(driver, norm: dict) -> None:
    nid = norm["norm_id"]
    parent = norm.get("contributes_to_norm_id")
    if not parent:
        return
    agg_fn = norm.get("capacity_aggregation_function") or "sum"
    direction = norm.get("aggregation_direction") or "add"
    q = f"""
match
    $contrib isa norm, has norm_id {_tq_string(nid)};
    $pool isa norm, has norm_id {_tq_string(parent)};
insert
    (contributor: $contrib, pool: $pool) isa norm_contributes_to_capacity,
        has aggregation_function {_tq_string(agg_fn)},
        has aggregation_direction {_tq_string(direction)};
"""
    try:
        execute_write(driver, TARGET_DB, q)
    except Exception as e:
        logger.warning("norm_contributes_to_capacity skipped for %s → %s: %s", nid, parent, str(e)[:120])


def link_carryforward_carryback(driver, norm: dict) -> None:
    nid = norm["norm_id"]
    fwd = norm.get("provides_carryforward_to")
    if fwd:
        years = norm.get("carryforward_years", 1)
        q = f"""
match
    $src isa norm, has norm_id {_tq_string(nid)};
    $recip isa norm, has norm_id {_tq_string(fwd)};
insert
    (carryforward_source: $src, carryforward_recipient: $recip) isa norm_provides_carryforward_to,
        has carryforward_years {years};
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.warning("carryforward edge skipped for %s: %s", nid, str(e)[:120])
    back = norm.get("provides_carryback_to")
    if back:
        years = norm.get("carryback_years", 1)
        q = f"""
match
    $src isa norm, has norm_id {_tq_string(nid)};
    $recip isa norm, has norm_id {_tq_string(back)};
insert
    (carryback_source: $src, carryback_recipient: $recip) isa norm_provides_carryback_to,
        has carryback_years {years};
"""
        try:
            execute_write(driver, TARGET_DB, q)
        except Exception as e:
            logger.warning("carryback edge skipped for %s: %s", nid, str(e)[:120])


# ─── Main ─────────────────────────────────────────────────────────────────────


def insert_defeater(driver, defeater: dict) -> None:
    """Insert a defeater entity + defeats edge + optional defeater_has_condition.

    YAML shape (see duck_creek_rp_ground_truth.yaml § Defeaters):
      defeater_id, defeats_norm_id, defeater_type, defeater_name,
      source_section, source_page, source_text, condition (optional).

    Condition follows the same tree shape as norm.condition; we reuse
    _insert_condition_node with a defeater-scoped path prefix so
    condition_ids don't collide with norm-sourced conditions.
    """
    did = defeater["defeater_id"]
    norm_id = defeater["defeats_norm_id"]

    owns = [
        f'has defeater_id {_tq_string(did)}',
        f'has defeater_type {_tq_string(defeater.get("defeater_type","exception"))}',
    ]
    if defeater.get("defeater_name"):
        owns.append(f'has defeater_name {_tq_string(defeater["defeater_name"])}')
    if defeater.get("source_text"):
        owns.append(f'has source_text {_tq_string(defeater["source_text"])}')
    if defeater.get("source_section"):
        owns.append(f'has source_section {_tq_string(defeater["source_section"])}')
    if isinstance(defeater.get("source_page"), int):
        owns.append(f"has source_page {defeater['source_page']}")

    q_def = f"insert $d isa defeater, {', '.join(owns)};"
    execute_write(driver, TARGET_DB, q_def)

    # defeats edge — defeater to norm
    q_edge = f"""
match
    $d isa defeater, has defeater_id {_tq_string(did)};
    $n isa norm, has norm_id {_tq_string(norm_id)};
insert
    (defeater: $d, defeated: $n) isa defeats;
"""
    execute_write(driver, TARGET_DB, q_edge)

    # Optional condition tree
    cond = defeater.get("condition")
    if not cond or not isinstance(cond, dict):
        return

    # Reuse norm condition-tree insertion; use the defeater_id as the
    # "norm_id" anchor for condition_id construction so ids are unique
    # across norm vs defeater trees.
    _insert_condition_node(driver, did, cond, path="c0", is_root=True)
    root_cid = construct_condition_id(did, "c0")
    q_has_cond = f"""
match
    $d isa defeater, has defeater_id {_tq_string(did)};
    $c isa condition, has condition_id {_tq_string(root_cid)};
insert
    (defeater: $d, root: $c) isa defeater_has_condition;
"""
    execute_write(driver, TARGET_DB, q_has_cond)


def main() -> int:
    p = argparse.ArgumentParser(description="Load Duck Creek RP ground truth YAML into valence_v4_ground_truth.")
    p.add_argument("--force", action="store_true", help="Drop existing target DB and recreate.")
    args = p.parse_args()

    # Hardcoded target — preflight safeguard
    if settings.typedb_database == TARGET_DB:
        logger.warning("settings.typedb_database is %r; loader hardcodes that too, proceeding.", TARGET_DB)
    if settings.typedb_database == "valence_v4":
        logger.info("settings.typedb_database is valence_v4; loader targets %s (separate db) regardless.", TARGET_DB)

    driver = connect()
    try:
        # Load YAML
        gt = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
        deal_id = gt["deal_id"]
        norms = gt.get("norms", [])
        logger.info("YAML: %d norms from deal %s", len(norms), deal_id)

        # Phase 1: build schema + seeds
        build_db(driver, args.force)

        # Phase 2: per-deal party instances
        roles_used = set()
        for n in norms:
            for r in n.get("subject_role") or []:
                roles_used.add(r)
        logger.info("distinct subject roles in use: %s", sorted(roles_used))
        seed_deal_parties(driver, deal_id, roles_used)

        # Phase 3: insert norms (scalars)
        logger.info("inserting %d norms (scalars)", len(norms))
        inserted = 0
        for n in norms:
            try:
                insert_norm(driver, n)
                inserted += 1
            except Exception as e:
                logger.error("  insert failed for %s: %s", n.get("norm_id"), str(e)[:160])
        logger.info("  %d/%d norms inserted", inserted, len(norms))

        # Phase 3b: scope relations — iterate all list entries (not just first)
        instrument_labels = load_instrument_labels(driver, TARGET_DB)
        logger.info("binding subjects / scopes (instrument subtypes: %d)", len(instrument_labels))
        for n in norms:
            bind_norm_scope(driver, n, instrument_labels)

        # Phase 4: conditions — full tree recursion
        logger.info("inserting condition trees")
        cond_entity_total = 0
        cond_tree_count = 0
        for n in norms:
            if n.get("condition"):
                cond_entity_total += insert_condition_tree(driver, n)
                cond_tree_count += 1
        logger.info("  %d condition trees inserted (%d total condition entities)",
                    cond_tree_count, cond_entity_total)

        # Phase 5: other relations
        logger.info("linking serves_questions")
        for n in norms:
            link_serves_questions(driver, n)
        logger.info("linking contributes_to")
        for n in norms:
            link_contributes_to(driver, n)
        logger.info("linking carryforward/carryback")
        for n in norms:
            link_carryforward_carryback(driver, n)

        # Phase 6: defeaters (Prompt 13 / Commit 0 backfill)
        defeaters = gt.get("defeaters", []) or []
        if defeaters:
            logger.info("inserting %d defeaters", len(defeaters))
            def_inserted = 0
            for d in defeaters:
                try:
                    insert_defeater(driver, d)
                    def_inserted += 1
                except Exception as e:
                    logger.error("  defeater insert failed for %s: %s",
                                 d.get("defeater_id"), str(e)[:160])
            logger.info("  %d/%d defeaters inserted", def_inserted, len(defeaters))

        # Verify counts
        logger.info("=== verification ===")
        for isa in ("norm", "condition", "norm_has_condition", "condition_has_child",
                    "condition_references_predicate",
                    "norm_binds_subject", "norm_scopes_action", "norm_scopes_object",
                    "norm_scopes_instrument",
                    "norm_serves_question", "norm_contributes_to_capacity",
                    "norm_provides_carryforward_to", "norm_provides_carryback_to",
                    "gold_question", "state_predicate",
                    "defeater", "defeats", "defeater_has_condition"):
            try:
                n = count_of(driver, TARGET_DB, isa)
                logger.info("  %-35s: %d", isa, n)
            except Exception:
                logger.info("  %-35s: query failed", isa)

        return 0
    finally:
        try:
            driver.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
