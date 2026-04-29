"""
Phase E commit 2 — load the 4 new Q4 carveout extraction questions
into TypeDB.

The seed_loader's existing entry for seed_new_questions.tql probes
for `rp_g8`, which already exists, so the loader skips the file on
re-run. This script loads ONLY the new rp_l24..rp_l27 questions plus
their category_has_question + question_annotates_attribute edges.

Idempotent — checks for question existence before inserting.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_e_load_carveout_questions
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
logger = logging.getLogger("phase_e_load_questions")


# (question_id, question_text, description, answer_type, display_order, extraction_prompt)
QUESTIONS = [
    (
        "rp_l24",
        "Does Section 2.10(c)(iv) provide a product-line / line-of-business sale exemption?",
        "Whether asset sales constituting a product line or line of business are exempt from mandatory prepayment if the leverage ratio test is satisfied",
        "boolean",
        124,
        "Does Section 2.10(c)(iv) (or analogous mandatory prepayment exemption) provide that proceeds from the sale of a product line, line of business, or substantially all of a product line are EXEMPT from the mandatory prepayment sweep, subject to a leverage ratio test (e.g., First Lien Leverage Ratio at or below a stated threshold) OR a pro forma no-worse test? Look in the mandatory prepayment carveouts for 'product line', 'line of business', or 'substantially all assets used in' language coupled with a ratio test. Answer true if such an exemption exists.",
    ),
    (
        "rp_l25",
        "What is the First Lien Leverage Ratio threshold for the Section 2.10(c)(iv) product-line exemption?",
        "Numeric leverage threshold below which the product-line exemption applies",
        "number",
        125,
        "What is the First Lien Leverage Ratio threshold for the Section 2.10(c)(iv) product-line / line-of-business sale exemption from mandatory prepayment? E.g., if 'the First Lien Leverage Ratio is no greater than 6.25 to 1.00 on a Pro Forma Basis', answer 6.25. If the carveout exists but no specific ratio threshold is stated (only no-worse test), answer null. If the carveout does not exist, answer null.",
    ),
    (
        "rp_l26",
        "Does Section 6.05(z) provide an unlimited asset sale basket subject to a leverage ratio test?",
        "Whether the asset sales covenant contains an unlimited dispositions basket conditioned on a First Lien Leverage Ratio test or no-worse pro forma test",
        "boolean",
        126,
        "Does Section 6.05(z) (or the last/highest-numbered clause of the asset sales / dispositions covenant) provide an UNLIMITED basket permitting any disposition subject to a First Lien Leverage Ratio test (e.g., 'so long as the First Lien Leverage Ratio is no greater than 6.00x on a Pro Forma Basis') OR a pro forma no-worse test? Distinguish from Section 2.10 product-line exemption — 6.05(z) is in the negative covenant 6.05 (asset sales prohibition) and grants permissibility, NOT a sweep exemption. Answer true if such an unlimited ratio-conditioned dispositions basket exists.",
    ),
    (
        "rp_l27",
        "What is the First Lien Leverage Ratio threshold for the Section 6.05(z) unlimited basket?",
        "Numeric leverage threshold below which 6.05(z) permits unlimited dispositions",
        "number",
        127,
        "What is the First Lien Leverage Ratio threshold for the Section 6.05(z) unlimited dispositions basket? E.g., if '6.05(z) permits any disposition so long as on a Pro Forma Basis the First Lien Leverage Ratio is no greater than 6.00 to 1.00', answer 6.00. If the basket exists but no specific ratio threshold is stated (only no-worse test), answer null. If the basket does not exist, answer null.",
    ),
]

# (question_id, target_attribute_name)
ANNOTATIONS = [
    ("rp_l24", "permits_product_line_exemption_2_10_c_iv"),
    ("rp_l25", "product_line_2_10_c_iv_threshold"),
    ("rp_l26", "permits_section_6_05_z_unlimited"),
    ("rp_l27", "section_6_05_z_threshold"),
]


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def question_exists(driver, db: str, qid: str) -> bool:
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match $q isa ontology_question, has question_id "{qid}"; select $q;'
        ).resolve()
        return len(list(r.as_concept_rows())) > 0
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def category_link_exists(driver, db: str, qid: str, cat_id: str) -> bool:
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match (category: $c, question: $q) isa category_has_question; '
            f'$q has question_id "{qid}"; '
            f'$c has category_id "{cat_id}"; '
            f'select $q;'
        ).resolve()
        return len(list(r.as_concept_rows())) > 0
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def annotation_exists(driver, db: str, qid: str, attr_name: str) -> bool:
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match (question: $q) isa question_annotates_attribute, '
            f'has target_attribute_name "{attr_name}"; '
            f'$q has question_id "{qid}"; '
            f'select $q;'
        ).resolve()
        return len(list(r.as_concept_rows())) > 0
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def insert_question(driver, db: str, qid: str, text: str, desc: str,
                     atype: str, order: int, prompt: str) -> None:
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        q = (
            f'insert $q isa ontology_question, '
            f'has question_id "{qid}", '
            f'has question_text {_tq_string(text)}, '
            f'has description {_tq_string(desc)}, '
            f'has answer_type "{atype}", '
            f'has display_order {order}, '
            f'has covenant_type "RP", '
            f'has extraction_prompt {_tq_string(prompt)};'
        )
        wtx.query(q).resolve()
        wtx.commit()
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def insert_category_link(driver, db: str, qid: str, cat_id: str) -> None:
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        q = (
            f'match $cat isa ontology_category, has category_id "{cat_id}"; '
            f'$q isa ontology_question, has question_id "{qid}"; '
            f'insert (category: $cat, question: $q) isa category_has_question;'
        )
        wtx.query(q).resolve()
        wtx.commit()
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def insert_annotation(driver, db: str, qid: str, attr_name: str) -> None:
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        q = (
            f'match $q isa ontology_question, has question_id "{qid}"; '
            f'insert (question: $q) isa question_annotates_attribute, '
            f'has target_entity_type "asset_sale_sweep", '
            f'has target_attribute_name "{attr_name}";'
        )
        wtx.query(q).resolve()
        wtx.commit()
    except Exception:
        if wtx.is_open():
            wtx.close()
        raise


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)

    driver = TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )
    try:
        # Step 1: insert questions
        for qid, text, desc, atype, order, prompt in QUESTIONS:
            if question_exists(driver, db, qid):
                logger.info("  question %s already exists — skipping", qid)
                continue
            insert_question(driver, db, qid, text, desc, atype, order, prompt)
            logger.info("  inserted question %s (%s)", qid, atype)

        # Step 2: insert category L links
        for qid, _ in ANNOTATIONS:
            if category_link_exists(driver, db, qid, "L"):
                logger.info("  category link L<->%s already exists — skipping", qid)
                continue
            insert_category_link(driver, db, qid, "L")
            logger.info("  inserted category_has_question L<->%s", qid)

        # Step 3: insert annotations
        for qid, attr in ANNOTATIONS:
            if annotation_exists(driver, db, qid, attr):
                logger.info("  annotation %s -> %s already exists — skipping",
                             qid, attr)
                continue
            insert_annotation(driver, db, qid, attr)
            logger.info("  inserted annotation %s -> %s", qid, attr)

        logger.info("Done.")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
