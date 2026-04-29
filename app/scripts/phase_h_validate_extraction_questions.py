"""
Phase H commit 3 — extraction question validation utility.

Reports authoring conformance for ontology_question entities. Run
manually or in pre-merge checks; flags questions that don't conform
to the documented authoring discipline.

Validation checks (each runnable independently via --check flag):

  - required_attrs: question_text, answer_type, covenant_type are
    populated. extraction_prompt is checked separately because empty
    prompts are a documented design choice for some seed-only
    questions (audit Dimension 1).
  - category_link: every question has at least one category_has_question
    relation, EXCEPT entity_list questions which encode their target
    via question-level attrs (audit Dimension 4).
  - target_traceability: every scalar question has at least one
    question_annotates_attribute or question_targets_field link;
    entity_list questions have target_entity_type + target_relation_type.
  - covenant_type_in_known_set: covenant_type is one of the
    documented codes (RP, MFN, DI, LIENS, INV, AS, EOD, FC, PP, AMD,
    FUND, AFF, PF, CP).

Output: human-readable report to stdout + JSON to
docs/v4_phase_h_extraction_survey/validation_<timestamp>.json.

Per Phase H locked scope: this utility is documentation-driven, not
schema-attribute-driven. Per-question conformance attestation isn't
stored in the graph; the utility computes conformance fresh on each
run from observable schema/data state.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_h_validate_extraction_questions
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
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
logger = logging.getLogger("phase_h_validate")


# Per docs/cowork/skills/valence-ontology/SKILL.md — canonical covenant codes
KNOWN_COVENANT_CODES = {
    "RP", "MFN", "DI", "LIENS", "INV", "AS", "EOD", "FC", "PP",
    "AMD", "FUND", "AFF", "PF", "CP",
}


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def fetch_questions_for_validation(driver, db: str) -> dict:
    """Returns {qid: {required attrs + answer_type + target attrs}}."""
    out = {}
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        q = """
            match $q isa ontology_question, has question_id $qid;
                  try { $q has question_text $qt; };
                  try { $q has answer_type $at; };
                  try { $q has covenant_type $cov; };
                  try { $q has extraction_prompt $ep; };
                  try { $q has target_entity_type $tet; };
                  try { $q has target_relation_type $trt; };
            select $qid, $qt, $at, $cov, $ep, $tet, $trt;
        """
        r = rtx.query(q).resolve()
        for row in r.as_concept_rows():
            def v(key):
                try:
                    c = row.get(key)
                    return c.as_attribute().get_value() if c else None
                except Exception:
                    return None
            qid = v("qid")
            if qid:
                out[qid] = {
                    "question_id": qid,
                    "question_text": v("qt"),
                    "answer_type": v("at"),
                    "covenant_type": v("cov"),
                    "extraction_prompt": v("ep"),
                    "target_entity_type": v("tet"),
                    "target_relation_type": v("trt"),
                }
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return out


def fetch_category_link_qids(driver, db: str) -> set[str]:
    """Returns set of question_ids that have at least one category link."""
    out = set()
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            "match (question: $q) isa category_has_question; "
            "$q has question_id $qid; "
            "select $qid;"
        ).resolve()
        for row in r.as_concept_rows():
            out.add(row.get("qid").as_attribute().get_value())
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return out


def fetch_traced_qids(driver, db: str) -> set[str]:
    """Returns set of question_ids that have at least one annotation OR
    field-target link."""
    out = set()
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        # Annotations
        r1 = rtx.query(
            "match (question: $q) isa question_annotates_attribute; "
            "$q has question_id $qid; "
            "select $qid;"
        ).resolve()
        for row in r1.as_concept_rows():
            out.add(row.get("qid").as_attribute().get_value())
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r2 = rtx.query(
            "match (question: $q) isa question_targets_field; "
            "$q has question_id $qid; "
            "select $qid;"
        ).resolve()
        for row in r2.as_concept_rows():
            out.add(row.get("qid").as_attribute().get_value())
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r3 = rtx.query(
            "match (question: $q) isa question_targets_concept; "
            "$q has question_id $qid; "
            "select $qid;"
        ).resolve()
        for row in r3.as_concept_rows():
            out.add(row.get("qid").as_attribute().get_value())
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return out


def validate(questions: dict, category_qids: set[str],
              traced_qids: set[str]) -> dict:
    """Run conformance checks; return per-question + summary findings."""
    per_q = {}
    issues = defaultdict(list)
    for qid, q in questions.items():
        problems = []
        # required_attrs
        for attr in ("question_text", "answer_type", "covenant_type"):
            if not q.get(attr):
                problems.append(f"missing_required:{attr}")
                issues[f"missing_{attr}"].append(qid)
        # covenant_type_in_known_set
        cov = q.get("covenant_type")
        if cov and cov not in KNOWN_COVENANT_CODES:
            problems.append(f"unknown_covenant_type:{cov}")
            issues["unknown_covenant_type"].append(qid)
        # category_link (entity_list questions are exempt)
        atype = q.get("answer_type")
        if atype != "entity_list" and qid not in category_qids:
            problems.append("missing_category_link")
            issues["missing_category_link"].append(qid)
        # target_traceability
        if atype == "entity_list":
            if not q.get("target_entity_type"):
                problems.append("entity_list_missing_target_entity_type")
                issues["entity_list_missing_target_entity_type"].append(qid)
            if not q.get("target_relation_type"):
                problems.append("entity_list_missing_target_relation_type")
                issues["entity_list_missing_target_relation_type"].append(qid)
        elif atype in ("boolean", "integer", "double", "string", "percentage",
                        "currency", "number"):
            # Scalar questions need at least an annotation or field target
            if qid not in traced_qids:
                problems.append("scalar_no_annotation_or_field_target")
                issues["scalar_no_annotation_or_field_target"].append(qid)
        per_q[qid] = problems
    return {"per_question": per_q, "issues": dict(issues)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args()

    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if args.out_json is None:
        out_json = (REPO_ROOT / "docs" / "v4_phase_h_extraction_survey"
                     / f"validation_{timestamp}.json")
    else:
        out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Validating ontology_questions in %s", db)
    driver = _connect()
    try:
        questions = fetch_questions_for_validation(driver, db)
        logger.info("  %d questions", len(questions))
        category_qids = fetch_category_link_qids(driver, db)
        logger.info("  %d questions with category links", len(category_qids))
        traced_qids = fetch_traced_qids(driver, db)
        logger.info("  %d questions with annotation/target traceability", len(traced_qids))

        results = validate(questions, category_qids, traced_qids)

        # Summary
        total_with_issues = sum(1 for problems in results["per_question"].values() if problems)
        print()
        print("PHASE H VALIDATION SUMMARY")
        print("=" * 60)
        print(f"  total questions: {len(questions)}")
        print(f"  questions with issues: {total_with_issues}")
        print(f"  conformance rate: {(len(questions)-total_with_issues)/len(questions)*100:.1f}%")
        print()
        print("  Issues by category:")
        for cat, qids in sorted(results["issues"].items()):
            print(f"    {cat}: {len(qids)}")
            if len(qids) <= 8:
                for qid in qids:
                    print(f"      - {qid}")
            else:
                for qid in qids[:5]:
                    print(f"      - {qid}")
                print(f"      ... and {len(qids) - 5} more")

        out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        logger.info("Wrote validation results to %s", out_json)
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
