"""
Phase C Commit 1.5 — pilot rule end-to-end runner.

Schema-expressibility gate. Loads pilot_rule_general_rp_basket.tql into
valence_v4, runs projection_rule_executor for the rule, compares scalar
attributes between the new rule-emitted norm and the existing python-
projected norm.

Pass criterion: scalar parity (all attributes from the rule subgraph
match the corresponding python-projected attributes).

Usage:
    py -m app.scripts.phase_c_commit_1_5_pilot --deal 6e76ed06
    py -m app.scripts.phase_c_commit_1_5_pilot --deal 6e76ed06 --reload-rule
    py -m app.scripts.phase_c_commit_1_5_pilot --deal 6e76ed06 --cleanup
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
from app.services.projection_rule_executor import execute_rule  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("commit_1_5_pilot")

PILOT_TQL = REPO_ROOT / "app" / "data" / "pilot_rule_general_rp_basket.tql"
PILOT_RULE_ID = "rule_general_rp_basket"
PILOT_NORM_KIND = "general_rp_basket_permission"


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def rule_exists(driver, db: str, rule_id: str) -> bool:
    tx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = tx.query(
                f'match $r isa projection_rule, has projection_rule_id "{rule_id}"; select $r;'
            ).resolve()
            return any(True for _ in r.as_concept_rows())
        except Exception:
            return False
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def load_pilot_rule(driver, db: str) -> None:
    """Load the pilot rule TQL data into the DB."""
    tql = PILOT_TQL.read_text(encoding="utf-8")
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(tql).resolve()
        wtx.commit()
        logger.info(f"loaded pilot rule from {PILOT_TQL.name}")
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def _delete_query(driver, db: str, query: str, label: str) -> bool:
    """Run one delete query in its own transaction. Returns True on success."""
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        try:
            wtx.query(query).resolve()
            wtx.commit()
            logger.info(f"cleanup: {label}")
            return True
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            msg = str(exc).splitlines()[0][:120]
            logger.debug(f"cleanup ({label}): {msg}")
            return False
    except Exception:
        return False


def cleanup_pilot_rule(driver, db: str) -> None:
    """Wipe all pilot-related entities. Safe at this stage because the
    pilot is the ONLY projection_rule in the DB.

    Once Commit 2 lands more rules, cleanup needs to walk just from this
    specific rule (via rule_produces_norm_template, template_emits_attribute,
    attribute_emission_uses_value chains).
    """
    # Pilot-emitted norms (norm_id has "pilot_" prefix)
    _delete_query(
        driver, db,
        'match $n isa norm, has norm_id $nid; $nid like "pilot_.*"; delete $n;',
        "pilot norms",
    )
    # All projection-rule entities (entity types added in Commit 1)
    # Order matters: relations cascade-delete with entities, but we delete
    # in reverse-dependency order for clarity.
    for type_name, label in [
        ("projection_rule", "projection_rule"),
        ("norm_template", "norm_templates"),
        ("defeater_template", "defeater_templates"),
        ("relation_template", "relation_templates"),
        ("role_assignment", "role_assignments"),
        ("role_filler", "role_fillers"),
        ("attribute_emission", "attribute_emissions"),
        ("predicate_specifier", "predicate_specifiers"),
        ("condition_template", "condition_templates"),
        ("match_criterion", "match_criteria"),
        ("value_source", "value_sources"),
    ]:
        _delete_query(
            driver, db,
            f'match $x isa {type_name}; delete $x;',
            f"all {label}",
        )


def fetch_norm_scalars(driver, db: str, norm_id: str) -> dict:
    """Read all scalar attributes of a norm by norm_id."""
    attrs = {}
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = (
            f'match $n isa norm, has norm_id "{norm_id}"; $n has $a; select $a;'
        )
        try:
            r = tx.query(q).resolve()
            for row in r.as_concept_rows():
                a = row.get("a").as_attribute()
                attrs[a.get_type().get_label()] = a.get_value()
        except Exception as exc:
            logger.warning(f"fetch_norm {norm_id}: {str(exc).splitlines()[0][:120]}")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return attrs


def diff_scalars(reference: dict, candidate: dict, ignore: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Compare scalar attribute dicts. Returns (matched, mismatched, missing)."""
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []
    for k, v_ref in reference.items():
        if k in ignore:
            continue
        v_cand = candidate.get(k)
        if v_cand is None:
            missing.append(f"{k}: reference={v_ref!r} | candidate=MISSING")
        elif v_ref != v_cand:
            mismatched.append(f"{k}: reference={v_ref!r} | candidate={v_cand!r}")
        else:
            matched.append(k)
    return matched, mismatched, missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True)
    parser.add_argument("--reload-rule", action="store_true",
                        help="Cleanup then reload the pilot rule before executing")
    parser.add_argument("--cleanup", action="store_true",
                        help="Cleanup pilot rule + emitted pilot_* norms then exit")
    args = parser.parse_args()

    if settings.typedb_database != "valence_v4":
        logger.error("typedb_database must be 'valence_v4' (got %r)", settings.typedb_database)
        return 2

    driver = connect()
    db = "valence_v4"

    try:
        if args.cleanup:
            cleanup_pilot_rule(driver, db)
            return 0

        if args.reload_rule and rule_exists(driver, db, PILOT_RULE_ID):
            cleanup_pilot_rule(driver, db)

        if not rule_exists(driver, db, PILOT_RULE_ID):
            load_pilot_rule(driver, db)
        else:
            logger.info(f"projection_rule {PILOT_RULE_ID} already loaded; reusing")

        # Run the rule
        logger.info("executing projection rule")
        report = execute_rule(driver, db, PILOT_RULE_ID, args.deal)
        logger.info(f"matches={report.matches} norms_emitted={report.norms_emitted} "
                    f"errors={len(report.errors)} warnings={len(report.warnings)}")
        for err in report.errors:
            logger.error(f"  ERROR: {err}")
        for w in report.warnings:
            logger.warning(f"  WARN:  {w}")

        if report.norms_emitted == 0:
            logger.error("no norm emitted; gate FAILED")
            return 1

        # Compare scalars
        reference_id = f"{args.deal}_{PILOT_NORM_KIND}"
        candidate_id = f"pilot_{args.deal}_{PILOT_NORM_KIND}"
        logger.info(f"comparing reference={reference_id} vs candidate={candidate_id}")

        ref = fetch_norm_scalars(driver, db, reference_id)
        cand = fetch_norm_scalars(driver, db, candidate_id)
        if not ref:
            logger.error(f"reference norm {reference_id} not found")
            return 1
        if not cand:
            logger.error(f"candidate norm {candidate_id} not found")
            return 1

        # Ignore norm_id (intentionally prefixed for collision avoidance)
        matched, mismatched, missing = diff_scalars(ref, cand, ignore={"norm_id"})
        logger.info(f"matched: {len(matched)} attrs ({sorted(matched)})")
        if mismatched:
            logger.error(f"mismatched: {len(mismatched)}")
            for m in mismatched:
                logger.error(f"  {m}")
        if missing:
            logger.error(f"missing on candidate: {len(missing)}")
            for m in missing:
                logger.error(f"  {m}")

        # Also list candidate-only attrs for visibility
        extra = set(cand.keys()) - set(ref.keys())
        if extra:
            logger.info(f"candidate-only (not in reference): {sorted(extra)}")

        if mismatched or missing:
            logger.error("PILOT GATE FAILED — schema does not yet faithfully express this rule")
            return 1
        logger.info("PILOT GATE PASSED — scalar parity achieved")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
