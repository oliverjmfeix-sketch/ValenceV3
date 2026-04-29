"""
Phase D2 Commit 1 — add `stage1_picker_guidance` attribute to the schema.

Idempotent schema migration that defines the `stage1_picker_guidance`
string attribute and grants `ontology_category` ownership of it. Runs
inside a single SCHEMA transaction; TypeDB 3.x `define` is idempotent
(re-running on a schema that already contains the additions is a no-op).

The attribute is consumed by `topic_router.get_stage1_picker_guidance()`
to surface category-specific PRIMARY/SUPPLEMENTARY picker-bias
instructions to the Stage 1 classifier in `synthesis_v4`.

Targets `valence_v4` by default (override via TYPEDB_DATABASE env var).
Run against `valence` separately if the v3 DB also needs the attribute
(matches the Phase D1 pattern with synthesis_guidance).

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_d2_add_stage1_picker_guidance_attr
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# override=False so a CLI-supplied TYPEDB_DATABASE wins over .env defaults.
# Per Phase D2 plan: this script targets valence_v4 by default; valence is run
# separately when DB-symmetric schema changes are needed.
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
logger = logging.getLogger("phase_d2_add_attr")


SCHEMA_DEFINE = """
define
  attribute stage1_picker_guidance, value string;
  entity ontology_category, owns stage1_picker_guidance;
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
            logger.info("Defined stage1_picker_guidance + owns on ontology_category (idempotent).")
        except Exception:
            if tx.is_open():
                tx.close()
            raise

        # Verify
        rtx = driver.transaction(db, TransactionType.READ)
        try:
            r = rtx.query(
                "match $c isa ontology_category; "
                "try { $c has stage1_picker_guidance $g; }; "
                "select $c;"
            ).resolve()
            n_categories = len(list(r.as_concept_rows()))
            logger.info("Read-back: %d ontology_category entities resolved (attribute is queryable).",
                        n_categories)
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
