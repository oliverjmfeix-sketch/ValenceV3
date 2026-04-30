"""
Phase I.4 — apply category guidance updates from the seed TQL files.

Loads from app/data/seed_synthesis_guidance.tql and
app/data/seed_stage1_picker_guidance.tql, picks the strings for the
categories I.4 rewrites (L, N, I), and upserts via match-delete-insert.

Idempotent. Re-running produces no changes when DB already matches seed.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_i4_apply_guidance
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# IMPORTANT: load_dotenv with override=False so a caller's
# `TYPEDB_DATABASE=valence_v4` environment is NOT overwritten by the
# main .env's `TYPEDB_DATABASE="valence"` setting. (Earlier I.4 attempt
# used override=True and silently wrote to v3 — root caused, fixed.)
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from typedb.driver import (  # noqa: E402
    TypeDB, Credentials, DriverOptions, TransactionType,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("phase_i4_apply")


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def _read_existing(driver, db: str, category_id: str, attr: str) -> str:
    tx = driver.transaction(db, TransactionType.READ)
    try:
        q = (f'match $c isa ontology_category, has category_id "{category_id}"; '
             f'try {{ $c has {attr} $v; }}; select $v;')
        r = tx.query(q).resolve()
        for row in r.as_concept_rows():
            try:
                v = row.get("v")
                if v is None:
                    continue
                return v.as_attribute().get_value()
            except Exception:
                continue
        return ""
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass


def _upsert(driver, db: str, category_id: str, attr: str, value: str) -> None:
    """Match-delete-insert; safe when no existing value."""
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        delete_q = (
            f'match $c isa ontology_category, has category_id "{category_id}", '
            f'has {attr} $existing; delete has $existing of $c;'
        )
        try:
            wtx.query(delete_q).resolve()
        except Exception:
            pass
        # _tq_string-style escape
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        insert_q = (
            f'match $c isa ontology_category, has category_id "{category_id}"; '
            f'insert $c has {attr} "{escaped}";'
        )
        wtx.query(insert_q).resolve()
        wtx.commit()
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def _extract_block(text: str, category_id: str, attr_name: str) -> str:
    """Find the `match $cat isa ontology_category, has category_id "X";` line
    in the seed file and capture the following insert string for `attr_name`.

    Pattern:
      match $cat isa ontology_category, has category_id "X";
      insert $cat has <attr_name> "...";
    """
    # Find the match line for this category, then a fenced quote string
    # following the next "has <attr_name>".
    pattern = re.compile(
        r'match\s+\$cat\s+isa\s+ontology_category,\s+has\s+category_id\s+"' +
        re.escape(category_id) + r'";\s*\n'
        r'insert\s+\$cat\s+has\s+' + re.escape(attr_name) + r'\s+"((?:\\.|[^"\\])*)";',
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        raise RuntimeError(
            f"Could not find {attr_name} insert for category {category_id} "
            f"in seed text"
        )
    raw = m.group(1)
    # Unescape: \\ -> \ ; \" -> "
    return raw.replace('\\"', '"').replace('\\\\', '\\')


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)

    syn_seed = (REPO_ROOT / "app" / "data" / "seed_synthesis_guidance.tql").read_text(encoding="utf-8")
    pick_seed = (REPO_ROOT / "app" / "data" / "seed_stage1_picker_guidance.tql").read_text(encoding="utf-8")

    # Categories whose synthesis_guidance is rewritten this commit:
    syn_targets = ["L", "N", "I"]
    # Categories whose stage1_picker_guidance is rewritten/added this commit:
    pick_targets = ["L"]  # N already has one from D2; we updated synthesis_guidance only for N

    driver = _connect()
    changed = 0
    try:
        for cid in syn_targets:
            new_value = _extract_block(syn_seed, cid, "synthesis_guidance")
            existing = _read_existing(driver, db, cid, "synthesis_guidance")
            if existing == new_value:
                logger.info("synthesis_guidance[%s]: unchanged (%d chars)", cid, len(new_value))
                continue
            _upsert(driver, db, cid, "synthesis_guidance", new_value)
            readback = _read_existing(driver, db, cid, "synthesis_guidance")
            if readback != new_value:
                logger.error("synthesis_guidance[%s] readback mismatch! exp %d got %d",
                             cid, len(new_value), len(readback))
                return 1
            logger.info("synthesis_guidance[%s] upserted: %d chars (was %d)",
                        cid, len(new_value), len(existing))
            changed += 1

        for cid in pick_targets:
            new_value = _extract_block(pick_seed, cid, "stage1_picker_guidance")
            existing = _read_existing(driver, db, cid, "stage1_picker_guidance")
            if existing == new_value:
                logger.info("stage1_picker_guidance[%s]: unchanged (%d chars)", cid, len(new_value))
                continue
            _upsert(driver, db, cid, "stage1_picker_guidance", new_value)
            readback = _read_existing(driver, db, cid, "stage1_picker_guidance")
            if readback != new_value:
                logger.error("stage1_picker_guidance[%s] readback mismatch!", cid)
                return 1
            logger.info("stage1_picker_guidance[%s] upserted: %d chars (was %d)",
                        cid, len(new_value), len(existing))
            changed += 1

        logger.info("Done. %d categories changed.", changed)
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
