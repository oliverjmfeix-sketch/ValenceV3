"""
Phase D Commit 1 — copy synthesis_guidance from `valence` to `valence_v4`.

The `synthesis_guidance` attribute on `ontology_category` is shared
schema between v3 and v4 but only seeded in `valence`. Phase D's
synthesis service reads it from `valence_v4`, so this script copies
the 18 RP-relevant entries (the intersection of v3 categories with
guidance and v4 categories — 100% overlap; see
`docs/v4_phase_d_lawyer_qa/v3_to_v4_vocab_map.md`) into `valence_v4`.

Idempotent: drops the existing synthesis_guidance attribute on each
v4 category before reinserting. Safe to run multiple times.

Usage:
    C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.migrate_v3_synthesis_guidance
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=False)

from typedb.driver import (  # noqa: E402
    TypeDB, Credentials, DriverOptions, TransactionType,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("migrate_synthesis_guidance")

V3_DB = "valence"
V4_DB = "valence_v4"


def _connect():
    import os
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_v3_guidance(driver) -> dict[str, str]:
    """Read all v3 ontology_category entries with synthesis_guidance.
    Returns {category_id: guidance_string}."""
    out: dict[str, str] = {}
    tx = driver.transaction(V3_DB, TransactionType.READ)
    try:
        r = tx.query(
            "match $c isa ontology_category, has category_id $cid, has synthesis_guidance $sg; "
            "select $cid, $sg;"
        ).resolve()
        for row in r.as_concept_rows():
            cid = row.get("cid").as_attribute().get_value()
            sg = row.get("sg").as_attribute().get_value()
            out[cid] = sg
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def fetch_v4_categories(driver) -> set[str]:
    """Read all v4 category_ids."""
    out: set[str] = set()
    tx = driver.transaction(V4_DB, TransactionType.READ)
    try:
        r = tx.query("match $c isa ontology_category, has category_id $cid; select $cid;").resolve()
        for row in r.as_concept_rows():
            out.add(row.get("cid").as_attribute().get_value())
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return out


def upsert_guidance(driver, category_id: str, guidance: str) -> bool:
    """Drop existing guidance for the category (if any) and insert the new
    value. Single transaction so the entity isn't briefly attribute-less.
    Returns True on success."""
    # Match-delete-insert in one query (per TypeDB 3.x INF4 pattern from
    # docs/v4_phase_c_handover.md). Splitting into two queries fires INF4
    # because the second match doesn't carry the type narrowing.
    delete_q = (
        f'match $c isa ontology_category, has category_id "{category_id}", '
        f'has synthesis_guidance $existing; '
        f'delete has $existing of $c;'
    )
    insert_q = (
        f'match $c isa ontology_category, has category_id "{category_id}"; '
        f'insert $c has synthesis_guidance {_tq_string(guidance)};'
    )
    # Try delete; ignore if no existing guidance to delete
    wtx = driver.transaction(V4_DB, TransactionType.WRITE)
    try:
        try:
            wtx.query(delete_q).resolve()
        except Exception:
            pass  # No existing guidance — fine
        try:
            wtx.query(insert_q).resolve()
            wtx.commit()
            return True
        except Exception as exc:
            if wtx.is_open():
                wtx.close()
            logger.error(f"upsert {category_id}: {str(exc).splitlines()[0][:200]}")
            return False
    except Exception as exc:
        if wtx.is_open():
            wtx.close()
        logger.error(f"upsert {category_id} (outer): {str(exc).splitlines()[0][:200]}")
        return False


def main() -> int:
    driver = _connect()
    try:
        logger.info("Reading v3 synthesis_guidance from %s", V3_DB)
        v3_guidance = fetch_v3_guidance(driver)
        logger.info("  found %d v3 entries with guidance", len(v3_guidance))

        logger.info("Reading v4 ontology_categories from %s", V4_DB)
        v4_cats = fetch_v4_categories(driver)
        logger.info("  found %d v4 categories", len(v4_cats))

        # Intersection: v4 cats that have v3 guidance
        to_migrate = sorted(v4_cats & set(v3_guidance.keys()))
        skipped = sorted(v4_cats - set(v3_guidance.keys()))
        if skipped:
            logger.warning("v4 categories WITHOUT v3 guidance (skipped): %s", skipped)
        logger.info("  migrating %d entries into %s", len(to_migrate), V4_DB)

        applied = 0
        for cid in to_migrate:
            if upsert_guidance(driver, cid, v3_guidance[cid]):
                applied += 1
                logger.info("  upserted: %s (%d chars)", cid, len(v3_guidance[cid]))
            else:
                logger.error("  failed: %s", cid)
        logger.info("Migrated %d/%d", applied, len(to_migrate))

        # Verify
        tx = driver.transaction(V4_DB, TransactionType.READ)
        try:
            r = tx.query(
                "match $c isa ontology_category, has synthesis_guidance $sg; select $c;"
            ).resolve()
            v4_with_guidance = len(list(r.as_concept_rows()))
            logger.info("Post-migration: v4 categories with guidance = %d", v4_with_guidance)
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:
                pass

        return 0 if applied == len(to_migrate) else 1
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
