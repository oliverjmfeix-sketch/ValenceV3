"""
Phase H commit 5 fix #5 — update non-conforming percentage prompts.

Two specific prompts identified by Commit 2 audit don't enforce
decimal Convention 1:
  - rp_f13: "If an EBITDA minus Fixed Charges test exists, what
    multiple of Fixed Charges is subtracted? Common values: 100%,
    125%, 140%, 150%."
  - rp_n2: "If the general RP basket uses a 'greater of' formula,
    what is the EBITDA or Total Assets percentage? For example,
    '100% of EBITDA' or '1.5% of Total Assets'."

Both questions have answer_type=percentage. Per Convention 1
(LOCKED Phase H commit 5 as decimal form), prompts must explicitly
enforce decimal output.

Idempotent match-delete-insert via existing upsert pattern.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_h_update_prompts_decimal_convention
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
logger = logging.getLogger("phase_h_update_prompts")

UPDATES = {
    "rp_f13": (
        "If an EBITDA minus Fixed Charges test exists, what multiple of "
        "Fixed Charges is subtracted? Look in the builder basket / "
        "Cumulative Amount definition for 'Consolidated EBITDA minus N% "
        "of Consolidated Fixed Charges' or similar phrasing. Common "
        "values: 100%, 125%, 140%, 150%. "
        "Return as a decimal fraction (e.g., 1.40 for 140%, 1.0 for 100%). "
        "If no such test exists, answer null."
    ),
    "rp_n2": (
        "If the general RP basket uses a 'greater of' formula combining a "
        "dollar amount with an EBITDA or Total Assets percentage, what is "
        "the percentage component? Look in the general RP basket clause "
        "for language like 'the greater of $X and Y% of Consolidated "
        "EBITDA' or 'the greater of $X and Y% of Total Assets'. "
        "Return as a decimal fraction (e.g., 1.0 for 100% of EBITDA, "
        "0.015 for 1.5% of Total Assets). If no percentage component "
        "exists, answer null."
    ),
}


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def upsert_prompt(driver, db: str, qid: str, new_prompt: str) -> None:
    """Match-delete-insert in two queries (one tx). Per Phase F's
    storage_patterns.md Case B."""
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        # Delete existing prompt (try block — no-op if absent)
        delete_q = (
            f'match $q isa ontology_question, has question_id "{qid}", '
            f'has extraction_prompt $existing; '
            f'delete has $existing of $q;'
        )
        try:
            wtx.query(delete_q).resolve()
        except Exception:
            # No existing prompt — fine
            try:
                if wtx.is_open():
                    wtx.close()
            except Exception:
                pass
            wtx = driver.transaction(db, TransactionType.WRITE)

        insert_q = (
            f'match $q isa ontology_question, has question_id "{qid}"; '
            f'insert $q has extraction_prompt {_tq_string(new_prompt)};'
        )
        wtx.query(insert_q).resolve()
        wtx.commit()
    except Exception:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass
        raise


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)
    driver = _connect()
    try:
        for qid, prompt in UPDATES.items():
            upsert_prompt(driver, db, qid, prompt)
            logger.info("Upserted prompt for %s (%d chars)", qid, len(prompt))
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
