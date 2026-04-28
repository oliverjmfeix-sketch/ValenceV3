"""
Restore a v3 extraction snapshot into valence_v4.

Use when the cloud database has been deleted / corrupted and the local
TQL snapshot is the only surviving copy of the $12.95 extraction.

Usage:
    # Precondition: valence_v4 exists with schema + seeds loaded, no
    # extraction present (or --force-overwrite passed).
    py -3.12 -m app.scripts.restore_extraction_snapshot --deal 6e76ed06

Works by:
    1. Probing target valence_v4 for existing rp_basket count — if > 0
       and no --force-overwrite, abort (preserves any real extraction)
    2. Reading the snapshot TQL file from app/data/extraction_snapshots/
    3. Splitting into pure-insert and match-insert statements (same
       pattern as app/services/seed_loader._split_statements)
    4. Executing pure inserts first (entities) then match-inserts
       (relations) — each in its own WRITE transaction

After restore, run `python -m app.services.projection_rule_executor
--deal <id>` + harness to verify full round-trip: projection output
should regenerate identically from restored v3 extraction.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=True)

from app.config import settings
from app.services.seed_loader import _split_statements
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("restore_snapshot")

SNAPSHOTS_DIR = REPO_ROOT / "app" / "data" / "extraction_snapshots"


def _extraction_entity_count(driver, db_name: str) -> int:
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        result = tx.query("match $b isa rp_basket; select $b;").resolve()
        return len(list(result.as_concept_rows()))
    except Exception:
        return 0
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def restore(driver, db_name: str, snapshot_path: Path, force: bool = False) -> dict:
    if not snapshot_path.exists():
        raise FileNotFoundError(f"snapshot not found: {snapshot_path}")

    existing = _extraction_entity_count(driver, db_name)
    if existing > 0 and not force:
        raise SystemExit(
            f"REFUSING TO PROCEED — {db_name} already has {existing} rp_basket "
            f"instances. Pass --force-overwrite to restore over existing data "
            f"(existing attributes will conflict on @key; errors will show)."
        )

    content = snapshot_path.read_text(encoding="utf-8")
    inserts, match_inserts = _split_statements(content)
    logger.info(
        "snapshot %s: %d inserts + %d match-inserts",
        snapshot_path.name, len(inserts), len(match_inserts),
    )

    ins_ok = 0
    ins_fail = 0
    mi_ok = 0
    mi_fail = 0
    first_errors: list[str] = []

    t0 = time.perf_counter()

    # Phase 1: entity inserts
    for stmt in inserts:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            ins_ok += 1
        except Exception as exc:  # noqa: BLE001
            if tx.is_open():
                tx.close()
            msg = str(exc).lower()
            if any(k in msg for k in ("already", "duplicate", "unique")):
                continue  # idempotent skip
            ins_fail += 1
            if len(first_errors) < 5:
                first_errors.append(f"insert: {stmt[:80]} ... {str(exc)[:120]}")

    # Phase 2: relation match-inserts
    for stmt in match_inserts:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            mi_ok += 1
        except Exception as exc:  # noqa: BLE001
            if tx.is_open():
                tx.close()
            msg = str(exc).lower()
            if any(k in msg for k in ("already", "duplicate", "unique")):
                continue
            mi_fail += 1
            if len(first_errors) < 5:
                first_errors.append(f"match-insert: {stmt[:80]} ... {str(exc)[:120]}")

    elapsed = time.perf_counter() - t0
    logger.info(
        "restore done in %.1fs: inserts %d/%d, match-inserts %d/%d",
        elapsed, ins_ok, len(inserts), mi_ok, len(match_inserts),
    )
    if first_errors:
        logger.warning("first errors:")
        for e in first_errors:
            logger.warning("  %s", e)

    # Post-restore verification: basket count
    post = _extraction_entity_count(driver, db_name)
    logger.info("post-restore rp_basket count: %d", post)
    return {
        "inserts_ok": ins_ok, "inserts_fail": ins_fail,
        "match_inserts_ok": mi_ok, "match_inserts_fail": mi_fail,
        "post_basket_count": post,
        "elapsed_seconds": elapsed,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Restore a v3 extraction snapshot into valence_v4.")
    p.add_argument("--deal", required=True, help="deal_id (snapshot filename stem)")
    p.add_argument("--database", default=None, help="override target database")
    p.add_argument("--input", default=None, help="override snapshot path")
    p.add_argument(
        "--force-overwrite",
        action="store_true",
        help="Proceed even if extraction data already present in target DB",
    )
    args = p.parse_args()

    db_name = args.database or settings.typedb_database
    snapshot = Path(args.input) if args.input else SNAPSHOTS_DIR / f"{args.deal}.tql"

    driver = TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )
    try:
        report = restore(driver, db_name, snapshot, force=args.force_overwrite)
        print(f"Restore report: {report}")
    finally:
        try:
            driver.close()
        except Exception:
            pass
    return 0 if report.get("inserts_fail", 0) == 0 and report.get("match_inserts_fail", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
