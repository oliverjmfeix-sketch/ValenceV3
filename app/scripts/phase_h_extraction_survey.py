"""
Phase H commit 1 — extraction methodology survey.

Read-only enumeration of ontology_question entities and their
linkages, used as the input to the Phase H commit 2 audit.

Coverage (8 audit dimensions per the plan):
  1. Question authoring discipline — every question's full attribute payload
  2. Prompt content structure — extraction_prompt length + convention-keyword presence
  3. Value convention enforcement — per attribute family, do prompts mention decimal/numeric/USD/etc.
  4. Question-to-target traceability — which questions annotate which attributes
  5. Versioning discipline — surface as audit finding (no version attrs in schema)
  6. Universe handling — surface as audit finding (universe scope is not on the question)
  7. Storage/extraction interface coherence — answer_type vs storage_value_type alignment
  8. Question category alignment — every question's category_has_question linkages

Output: docs/v4_phase_h_extraction_survey/snapshot_<timestamp>.json

Idempotent. Re-runnable. Deal-agnostic — the survey covers schema
commitments, not Duck Creek specifically (instance counts of stored
answers are sampled per question_id for traceability).

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_h_extraction_survey
"""
from __future__ import annotations

import json
import logging
import os
import re
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
logger = logging.getLogger("phase_h_survey")


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def fetch_questions(driver, db: str) -> dict:
    """Returns {question_id: {full attr payload}}."""
    out = {}
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        q = """
            match $q isa ontology_question, has question_id $qid;
                  try { $q has question_text $qt; };
                  try { $q has description $desc; };
                  try { $q has answer_type $at; };
                  try { $q has extraction_prompt $ep; };
                  try { $q has target_entity_type $tet; };
                  try { $q has target_relation_type $trt; };
                  try { $q has covenant_type $cov; };
                  try { $q has display_order $do; };
                  try { $q has storage_value_type $svt; };
                  try { $q has is_required $req; };
            select $qid, $qt, $desc, $at, $ep, $tet, $trt, $cov, $do, $svt, $req;
        """
        r = rtx.query(q).resolve()
        for row in r.as_concept_rows():
            def v(key):
                try:
                    c = row.get(key)
                    if c is None:
                        return None
                    return c.as_attribute().get_value()
                except Exception:
                    return None
            qid = v("qid")
            if not qid:
                continue
            out[qid] = {
                "question_id": qid,
                "question_text": v("qt"),
                "description": v("desc"),
                "answer_type": v("at"),
                "extraction_prompt": v("ep"),
                "target_entity_type": v("tet"),
                "target_relation_type": v("trt"),
                "covenant_type": v("cov"),
                "display_order": v("do"),
                "storage_value_type": v("svt"),
                "is_required": v("req"),
            }
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return out


def fetch_category_links(driver, db: str) -> dict:
    """Returns {question_id: [category_id, ...]}."""
    out = defaultdict(list)
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            "match (category: $c, question: $q) isa category_has_question; "
            "$c has category_id $cid; "
            "$q has question_id $qid; "
            "select $cid, $qid;"
        ).resolve()
        for row in r.as_concept_rows():
            cid = row.get("cid").as_attribute().get_value()
            qid = row.get("qid").as_attribute().get_value()
            out[qid].append(cid)
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return dict(out)


def fetch_attribute_annotations(driver, db: str) -> dict:
    """Returns {question_id: [{target_entity_type, target_attribute_name}, ...]}."""
    out = defaultdict(list)
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            "match $rel (question: $q) isa question_annotates_attribute, "
            "  has target_entity_type $tet, has target_attribute_name $tan; "
            "$q has question_id $qid; "
            "select $qid, $tet, $tan;"
        ).resolve()
        for row in r.as_concept_rows():
            qid = row.get("qid").as_attribute().get_value()
            tet = row.get("tet").as_attribute().get_value()
            tan = row.get("tan").as_attribute().get_value()
            out[qid].append({"target_entity_type": tet, "target_attribute_name": tan})
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return dict(out)


def fetch_field_targets(driver, db: str) -> dict:
    """Returns {question_id: [target_field_name, ...]}."""
    out = defaultdict(list)
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            "match $rel (question: $q) isa question_targets_field, has target_field_name $tfn; "
            "$q has question_id $qid; "
            "select $qid, $tfn;"
        ).resolve()
        for row in r.as_concept_rows():
            qid = row.get("qid").as_attribute().get_value()
            tfn = row.get("tfn").as_attribute().get_value()
            out[qid].append(tfn)
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return dict(out)


# ─────────────────────────────────────────────────────────────────────────
# Convention-keyword scanning
# ─────────────────────────────────────────────────────────────────────────

# Regex patterns to detect explicit convention enforcement in prompts
_PERCENTAGE_DECIMAL_PATTERNS = [
    r"\b(?:as\s+)?(?:a\s+)?decimal\b",
    r"\bfraction\b",
    r"\b0\.\d+\s*(?:for|=|means|representing)\s*\d+%",
    r"\b15%\s*(?:as|=)\s*0\.15\b",
    r"e\.g\.,?\s*0\.\d+\b",
]
_PERCENTAGE_NUMERIC_PATTERNS = [
    r"\b(?:as\s+)?(?:a\s+)?(?:whole\s+)?number\b.*(?:percent|%)",
    r"\b\d+\.0\s*(?:for|=|means|representing)\s*\d+%",
    r"e\.g\.,?\s*15\.0\b",
]
_USD_RAW_PATTERNS = [
    r"\bUSD\s+raw\b",
    r"\bdollar\s+amount\b",
    r"\b\$\d{1,3}(,\d{3})+",  # $130,000,000
    r"e\.g\.,?\s*\$?\s*130000000\b",
]
_USD_MILLIONS_PATTERNS = [
    r"\bin\s+millions\b",
    r"\$\d+M\b(?!\.\d)",
    r"e\.g\.,?\s*\$?\s*130\b",
]


def scan_prompt_conventions(prompt: str | None) -> dict:
    """Detect convention-enforcement keywords in an extraction prompt."""
    if not prompt:
        return {"empty": True}
    out = {"empty": False, "length_chars": len(prompt)}
    if any(re.search(p, prompt, re.IGNORECASE) for p in _PERCENTAGE_DECIMAL_PATTERNS):
        out["enforces_percentage_decimal"] = True
    if any(re.search(p, prompt, re.IGNORECASE) for p in _PERCENTAGE_NUMERIC_PATTERNS):
        out["enforces_percentage_numeric"] = True
    if any(re.search(p, prompt, re.IGNORECASE) for p in _USD_RAW_PATTERNS):
        out["enforces_usd_raw"] = True
    if any(re.search(p, prompt, re.IGNORECASE) for p in _USD_MILLIONS_PATTERNS):
        out["enforces_usd_millions"] = True
    out["mentions_json"] = bool(re.search(r"\bjson\b|\b\{.*?\}\b", prompt, re.IGNORECASE))
    out["mentions_return"] = bool(re.search(r"\breturn\b|\boutput\b", prompt, re.IGNORECASE))
    out["mentions_null"] = "null" in prompt.lower()
    return out


# ─────────────────────────────────────────────────────────────────────────
# Conformance checks
# ─────────────────────────────────────────────────────────────────────────

REQUIRED_ATTRS = ["question_text", "answer_type", "extraction_prompt", "covenant_type"]


def conformance_check(q: dict) -> dict:
    """Per-question conformance against authoring discipline expectations."""
    out = {"missing_required_attrs": []}
    for attr in REQUIRED_ATTRS:
        if not q.get(attr):
            out["missing_required_attrs"].append(attr)

    answer_type = q.get("answer_type") or ""
    storage_value_type = q.get("storage_value_type") or ""
    target_entity_type = q.get("target_entity_type") or ""
    target_relation_type = q.get("target_relation_type") or ""

    # Storage / answer-type coherence
    if answer_type == "entity_list":
        out["expects_target_entity_type"] = bool(target_entity_type)
        out["expects_target_relation_type"] = bool(target_relation_type)
    elif answer_type in ("boolean", "integer", "double", "string", "percentage",
                         "currency", "number"):
        # Scalars don't need target_entity_type at the question level — they
        # use question_annotates_attribute instead. So this is fine if missing.
        pass
    elif answer_type == "multiselect":
        # Multiselects use question_targets_concept
        pass
    elif answer_type == "entity":
        # Single-instance entity (rare)
        pass
    return out


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "docs" / "v4_phase_h_extraction_survey"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"snapshot_{timestamp}.json"

    logger.info("Surveying %s", db)
    driver = _connect()
    try:
        logger.info("Fetching ontology_questions...")
        questions = fetch_questions(driver, db)
        logger.info("  %d questions", len(questions))

        logger.info("Fetching category links...")
        cat_links = fetch_category_links(driver, db)
        logger.info("  %d questions linked to categories",
                    sum(1 for v in cat_links.values() if v))

        logger.info("Fetching attribute annotations...")
        attr_anns = fetch_attribute_annotations(driver, db)
        logger.info("  %d annotation entries (sum across questions)",
                    sum(len(v) for v in attr_anns.values()))

        logger.info("Fetching field targets...")
        field_targets = fetch_field_targets(driver, db)
        logger.info("  %d field-target entries",
                    sum(len(v) for v in field_targets.values()))

        # Combine and analyze
        combined = {}
        for qid, q in questions.items():
            entry = dict(q)
            entry["categories"] = cat_links.get(qid, [])
            entry["annotated_attributes"] = attr_anns.get(qid, [])
            entry["target_fields"] = field_targets.get(qid, [])
            entry["prompt_scan"] = scan_prompt_conventions(q.get("extraction_prompt"))
            entry["conformance"] = conformance_check(q)
            combined[qid] = entry

        # Aggregate stats
        by_covenant = defaultdict(int)
        by_answer_type = defaultdict(int)
        prompts_with_decimal = 0
        prompts_with_numeric = 0
        prompts_with_usd_raw = 0
        prompts_empty = 0
        questions_no_category = 0
        questions_missing_required = []
        for qid, e in combined.items():
            cov = e.get("covenant_type") or "?"
            by_covenant[cov] += 1
            atype = e.get("answer_type") or "?"
            by_answer_type[atype] += 1
            ps = e.get("prompt_scan", {})
            if ps.get("empty"):
                prompts_empty += 1
            if ps.get("enforces_percentage_decimal"):
                prompts_with_decimal += 1
            if ps.get("enforces_percentage_numeric"):
                prompts_with_numeric += 1
            if ps.get("enforces_usd_raw"):
                prompts_with_usd_raw += 1
            if not e.get("categories"):
                questions_no_category += 1
            if e.get("conformance", {}).get("missing_required_attrs"):
                questions_missing_required.append(qid)

        summary = {
            "db": db,
            "timestamp": timestamp,
            "total_questions": len(combined),
            "by_covenant_type": dict(by_covenant),
            "by_answer_type": dict(by_answer_type),
            "questions_with_empty_prompt": prompts_empty,
            "questions_with_decimal_pct_enforcement": prompts_with_decimal,
            "questions_with_numeric_pct_enforcement": prompts_with_numeric,
            "questions_with_usd_raw_enforcement": prompts_with_usd_raw,
            "questions_with_no_category": questions_no_category,
            "questions_missing_required_attrs": questions_missing_required,
        }

        snapshot = {
            "summary": summary,
            "questions": combined,
        }
        out_path.write_text(json.dumps(snapshot, indent=2, default=str),
                              encoding="utf-8")
        logger.info("Wrote snapshot: %s (%d bytes)",
                    out_path, len(out_path.read_text(encoding="utf-8")))

        # Print summary
        print()
        print("PHASE H SURVEY SUMMARY")
        print("=" * 50)
        for k, v in summary.items():
            print(f"  {k}: {v}")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
