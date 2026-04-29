"""
Phase D2 — upsert ontology_category guidance attributes (synthesis_guidance
or stage1_picker_guidance).

Used by Commits 2, 3, 5 to author per-category prompt content for the
synthesis_v4 pipeline. Idempotent match-delete-insert pattern (cloned
from migrate_v3_synthesis_guidance.py — TypeDB 3.x INF4 pattern).

Operations:
  --append   read existing value, append " " + new value, write back
  --replace  unconditionally overwrite (use sparingly; default off)
  --read     print current value, no write

Usage examples:
    # Append capacity-arithmetic guidance to category N
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_d2_upsert_category_guidance \\
        --category N --attr synthesis_guidance --append \\
        --value "<new content here>"

    # Read current stage1_picker_guidance for category N
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_d2_upsert_category_guidance \\
        --category N --attr stage1_picker_guidance --read
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

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
logger = logging.getLogger("phase_d2_upsert")

VALID_ATTRS = {"synthesis_guidance", "stage1_picker_guidance"}


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def read_existing(driver, db: str, category_id: str, attr: str) -> str:
    """Read current attribute value; "" if unset."""
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


def upsert(driver, db: str, category_id: str, attr: str, value: str) -> None:
    """Match-delete-insert in two queries (one transaction). Per TypeDB 3.x
    INF4 pattern: split is required because match-delete and match-insert
    can't share a single match clause for attribute-replace.
    """
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        # Try delete; ignore if no existing value (transaction stays usable)
        delete_q = (
            f'match $c isa ontology_category, has category_id "{category_id}", '
            f'has {attr} $existing; delete has $existing of $c;'
        )
        try:
            wtx.query(delete_q).resolve()
        except Exception:
            # No existing value — fine, fall through to insert
            pass

        insert_q = (
            f'match $c isa ontology_category, has category_id "{category_id}"; '
            f'insert $c has {attr} {_tq_string(value)};'
        )
        wtx.query(insert_q).resolve()
        wtx.commit()
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--category", required=True, help="ontology_category.category_id (e.g. N, L)")
    p.add_argument("--attr", required=True, choices=sorted(VALID_ATTRS),
                   help="Which attribute to upsert")
    op = p.add_mutually_exclusive_group(required=True)
    op.add_argument("--append", action="store_true",
                    help="Append --value to existing (separator: '\\n\\n')")
    op.add_argument("--replace", action="store_true",
                    help="Overwrite existing with --value")
    op.add_argument("--read", action="store_true",
                    help="Print current value, no write")
    p.add_argument("--value", help="New content (required for --append / --replace)")
    p.add_argument("--db", default=os.environ.get("TYPEDB_DATABASE", "valence_v4"))
    args = p.parse_args()

    if (args.append or args.replace) and not args.value:
        p.error("--value is required for --append / --replace")

    logger.info("Target DB: %s", args.db)
    driver = _connect()
    try:
        existing = read_existing(driver, args.db, args.category, args.attr)
        logger.info("Existing %s on category %s: %d chars",
                     args.attr, args.category, len(existing))

        if args.read:
            print(f"=== {args.attr} for category {args.category} ===")
            print(existing if existing else "(empty)")
            return 0

        if args.append:
            new_value = (existing + "\n\n" + args.value).strip() if existing else args.value
        else:  # replace
            new_value = args.value

        if new_value == existing:
            logger.info("No change (new value equals existing). Skipping write.")
            return 0

        upsert(driver, args.db, args.category, args.attr, new_value)
        logger.info("Upserted %s on category %s: %d chars (was %d)",
                     args.attr, args.category, len(new_value), len(existing))

        # Read back to confirm
        readback = read_existing(driver, args.db, args.category, args.attr)
        if readback != new_value:
            logger.error("Read-back mismatch! expected %d chars, got %d",
                          len(new_value), len(readback))
            return 1
        logger.info("Read-back matches.")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
