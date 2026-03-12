#!/usr/bin/env python3
"""Produce scalar ↔ entity gap report from live TypeDB."""

import os
import sys
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

# Configuration from environment
TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")


def get_driver():
    address = TYPEDB_ADDRESS
    if not address.startswith("http://") and not address.startswith("https://"):
        address = f"https://{address}"
    logger.info(f"Connecting to TypeDB: {address}")
    return TypeDB.driver(
        address,
        Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD),
        DriverOptions()
    )


def _safe_val(row, key):
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_attribute().get_value()
    except Exception:
        return None


def _run_query(driver, query):
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    try:
        return list(tx.query(query).resolve().as_concept_rows())
    finally:
        if tx.is_open():
            tx.close()


def main():
    driver = get_driver()
    lines = []
    lines.append("# Scalar ↔ Entity Gap Report")
    lines.append(f"Generated: {datetime.utcnow().isoformat()}Z")
    lines.append("")

    # ── Query 1: All ontology questions ──────────────────────────
    logger.info("Query 1: All ontology questions...")
    q1 = """
        match
            $q isa ontology_question,
                has question_id $qid,
                has question_text $qt,
                has answer_type $at,
                has covenant_type $ct;
            (category: $cat, question: $q) isa category_has_question;
            $cat has category_id $cid, has name $cname;
        select $qid, $qt, $at, $ct, $cid, $cname;
    """
    all_questions = {}
    for row in _run_query(driver, q1):
        qid = _safe_val(row, "qid")
        if qid:
            all_questions[qid] = {
                "question_text": _safe_val(row, "qt") or "",
                "answer_type": _safe_val(row, "at") or "",
                "covenant_type": _safe_val(row, "ct") or "",
                "category_id": _safe_val(row, "cid") or "",
                "category_name": _safe_val(row, "cname") or "",
            }
    logger.info(f"  Found {len(all_questions)} questions")

    # ── Query 2: All question_annotates_attribute ────────────────
    logger.info("Query 2: All question_annotates_attribute relations...")
    q2 = """
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name $an;
            $q has question_id $qid;
        select $qid, $et, $an;
    """
    annotations = {}  # qid → (entity_type, attr_name)
    annotated_attrs = set()  # (entity_type, attr_name) tuples
    for row in _run_query(driver, q2):
        qid = _safe_val(row, "qid")
        et = _safe_val(row, "et")
        an = _safe_val(row, "an")
        if qid and et and an:
            annotations[qid] = (et, an)
            annotated_attrs.add((et, an))
    logger.info(f"  Found {len(annotations)} annotations")

    # ── Query 3: Channel 3 entity attributes via introspection ───
    logger.info("Query 3: Channel 3 entity attributes...")
    ENTITY_TYPES = [
        "builder_basket", "ratio_basket", "general_rp_basket",
        "management_equity_basket", "tax_distribution_basket",
        "holdco_overhead_basket", "equity_award_basket",
        "unsub_distribution_basket",
        "starter_amount_source", "cni_source", "ecf_source",
        "ebitda_fc_source", "equity_proceeds_source",
        "investment_returns_source", "asset_proceeds_source",
        "debt_conversion_source",
        "jcrew_blocker",
        "unsub_designation",
        "sweep_tier",
        "de_minimis_threshold",
        "basket_reallocation",
        "investment_pathway",
        "general_rdp_basket", "ratio_rdp_basket", "builder_rdp_basket",
        "equity_funded_rdp_basket", "refinancing_rdp_basket",
    ]

    SKIP_ATTRS = {
        "basket_id", "source_id", "tier_id", "blocker_id", "exception_id",
        "threshold_id", "designation_id", "reallocation_id", "pathway_id",
        "qualification_id", "citation_id",
        "display_name", "section_reference", "source_page", "source_text",
        "confidence", "source_section", "source_name", "exception_name",
    }

    all_entity_attrs = {}  # entity_type → [attr_name, ...]
    for et in ENTITY_TYPES:
        try:
            q = f"match entity $t type {et}; $t owns $attr; select $attr;"
            attrs = []
            for row in _run_query(driver, q):
                try:
                    attr_label = row.get("attr").as_attribute_type().get_label()
                    if attr_label not in SKIP_ATTRS:
                        attrs.append(attr_label)
                except Exception:
                    pass
            all_entity_attrs[et] = sorted(attrs)
        except Exception as e:
            logger.warning(f"  Could not introspect {et}: {e}")
            all_entity_attrs[et] = []
    total_attrs = sum(len(v) for v in all_entity_attrs.values())
    logger.info(f"  Found {total_attrs} attributes across {len(ENTITY_TYPES)} entity types")

    # ── Query 4: question_targets_field (Channel 1) ──────────────
    logger.info("Query 4: Channel 1 scalar field mappings...")
    q4 = """
        match
            (question: $q) isa question_targets_field,
                has target_field_name $fn,
                has target_entity_type $et;
            $q has question_id $qid;
        select $qid, $fn, $et;
    """
    scalar_targets = {}
    for row in _run_query(driver, q4):
        qid = _safe_val(row, "qid")
        fn = _safe_val(row, "fn")
        et = _safe_val(row, "et")
        if qid:
            scalar_targets[qid] = {"field_name": fn, "entity_type": et}
    logger.info(f"  Found {len(scalar_targets)} scalar field mappings")

    # ── Compute gaps ─────────────────────────────────────────────
    questions_without_annotation = {
        qid: info for qid, info in all_questions.items()
        if qid not in annotations
    }

    attrs_without_annotation = []
    for et, attrs in sorted(all_entity_attrs.items()):
        for attr in attrs:
            if (et, attr) not in annotated_attrs:
                attrs_without_annotation.append((et, attr))

    # ── Summary stats ────────────────────────────────────────────
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total ontology questions:** {len(all_questions)}")
    lines.append(f"- **Questions with entity annotation:** {len(annotations)}")
    lines.append(f"- **Questions without entity annotation:** {len(questions_without_annotation)}")
    lines.append(f"- **Total Channel 3 entity attributes:** {total_attrs}")
    lines.append(f"- **Attributes with question annotation:** {len(annotated_attrs)}")
    lines.append(f"- **Attributes without question annotation:** {len(attrs_without_annotation)}")
    lines.append(f"- **Channel 1 scalar field mappings:** {len(scalar_targets)}")
    lines.append("")

    # ── Gap 1 ────────────────────────────────────────────────────
    lines.append("## Gap 1: Questions with no entity annotation")
    lines.append("")
    lines.append("These questions exist in TypeDB and produce scalar answers but have")
    lines.append("no `question_annotates_attribute` relation to any Channel 3 entity attribute.")
    lines.append("")
    if questions_without_annotation:
        lines.append("| question_id | category | answer_type | question_text |")
        lines.append("|-------------|----------|-------------|---------------|")
        for qid in sorted(questions_without_annotation.keys()):
            info = questions_without_annotation[qid]
            qt = info["question_text"][:120]
            lines.append(f"| {qid} | {info['category_id']} | {info['answer_type']} | {qt} |")
    else:
        lines.append("None — all questions have entity annotations.")
    lines.append("")

    # ── Gap 2 ────────────────────────────────────────────────────
    lines.append("## Gap 2: Entity attributes with no question annotation")
    lines.append("")
    lines.append("These attributes exist on Channel 3 entities in the schema but have")
    lines.append("no `question_annotates_attribute` relation from any ontology question.")
    lines.append("")
    if attrs_without_annotation:
        lines.append("| entity_type | attribute_name |")
        lines.append("|-------------|---------------|")
        for et, attr in attrs_without_annotation:
            lines.append(f"| {et} | {attr} |")
    else:
        lines.append("None — all entity attributes have question annotations.")
    lines.append("")

    # ── Reference: current annotations ───────────────────────────
    lines.append("## Reference: Current Annotation Map")
    lines.append("")
    lines.append("All `question_annotates_attribute` relations in TypeDB.")
    lines.append("")
    lines.append("| question_id | entity_type | attribute_name | question_text |")
    lines.append("|-------------|-------------|----------------|---------------|")
    for qid in sorted(annotations.keys()):
        et, an = annotations[qid]
        qt = all_questions.get(qid, {}).get("question_text", "")[:120]
        lines.append(f"| {qid} | {et} | {an} | {qt} |")
    lines.append("")

    # ── Reference: Channel 1 ─────────────────────────────────────
    lines.append("## Reference: Channel 1 Scalar Field Map")
    lines.append("")
    lines.append("All `question_targets_field` relations in TypeDB.")
    lines.append("")
    if scalar_targets:
        lines.append("| question_id | target_field_name | target_entity_type |")
        lines.append("|-------------|-------------------|--------------------|")
        for qid in sorted(scalar_targets.keys()):
            st = scalar_targets[qid]
            lines.append(f"| {qid} | {st['field_name']} | {st['entity_type']} |")
    else:
        lines.append("No `question_targets_field` relations found.")
    lines.append("")

    # ── Reference: All entity attributes ──────────────────────────
    lines.append("## Reference: All Channel 3 Entity Attributes")
    lines.append("")
    lines.append("| entity_type | attribute_name | has_annotation |")
    lines.append("|-------------|----------------|----------------|")
    for et in sorted(all_entity_attrs.keys()):
        for attr in all_entity_attrs[et]:
            annotated = "yes" if (et, attr) in annotated_attrs else ""
            lines.append(f"| {et} | {attr} | {annotated} |")
    lines.append("")

    # ── Write report ─────────────────────────────────────────────
    report = "\n".join(lines)

    output_path = "/app/uploads/gap_report.md"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)
    logger.info(f"Report written to {output_path}")

    print(f"\n{report}")


if __name__ == "__main__":
    main()
