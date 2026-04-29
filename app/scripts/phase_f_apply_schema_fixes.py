"""
Phase F commit 3 — apply schema fixes from the audit.

Idempotent SCHEMA-transaction migration. Two changes:

1. event_governed_by_norm relation (Phase B commit 3 deferral closed).
2. capacity_effect comment annotation in the schema file (no DB change
   for this; documentation only).

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_f_apply_schema_fixes
"""
from __future__ import annotations

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
logger = logging.getLogger("phase_f_schema_fixes")


SCHEMA_DEFINE = """
define
  relation event_governed_by_norm, relates governed_event, relates governing_norm;
  entity event_class, plays event_governed_by_norm:governed_event;
  entity norm, plays event_governed_by_norm:governing_norm;
"""


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)

    driver = TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )
    try:
        tx = driver.transaction(db, TransactionType.SCHEMA)
        try:
            tx.query(SCHEMA_DEFINE).resolve()
            tx.commit()
            logger.info("Defined event_governed_by_norm relation + role players (idempotent).")
        except Exception:
            if tx.is_open():
                tx.close()
            raise

        # Verify
        rtx = driver.transaction(db, TransactionType.READ)
        try:
            r = rtx.query(
                "match $r isa event_governed_by_norm; select $r;"
            ).resolve()
            count = len(list(r.as_concept_rows()))
            logger.info("event_governed_by_norm queryable; %d instances (expected 0 — populated by future phase).",
                        count)
        finally:
            try:
                if rtx.is_open():
                    rtx.close()
            except Exception:
                pass
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
