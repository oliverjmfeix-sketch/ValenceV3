"""Audit MFN annotation coverage.

Identifies MFN scalar questions WITHOUT question_annotates_attribute mappings.
These 'orphan' scalars are invisible to /ask-graph.

Run on Railway: python -m app.scripts.audit_mfn_annotations
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.environ.get("TYPEDB_ADDRESS", "ip654h-0.cluster.typedb.com:80")
TYPEDB_DATABASE = os.environ.get("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.environ.get("TYPEDB_USERNAME", "admin")
TYPEDB_PASSWORD = os.environ.get("TYPEDB_PASSWORD", "")


def _safe_val(row, var):
    """Extract a value from a TypeDB concept row, handling None."""
    try:
        concept = row.get(var)
        if hasattr(concept, "as_value"):
            return concept.as_value().get()
        if hasattr(concept, "as_attribute"):
            return concept.as_attribute().get_value()
        return str(concept)
    except Exception:
        return None


def main():
    address = TYPEDB_ADDRESS
    if not address.startswith("http://") and not address.startswith("https://"):
        address = f"https://{address}"
    driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)

    print("=" * 70)
    print("MFN ANNOTATION COVERAGE AUDIT")
    print("=" * 70)

    # Query 1: All MFN questions
    all_mfn_query = """
        match
            $q isa ontology_question,
                has covenant_type "MFN",
                has question_id $qid,
                has question_text $qt,
                has answer_type $at;
        select $qid, $qt, $at;
    """
    all_mfn = {}
    for row in tx.query(all_mfn_query).resolve().as_concept_rows():
        qid = _safe_val(row, "qid")
        qt = _safe_val(row, "qt")
        at = _safe_val(row, "at")
        all_mfn[qid] = {"text": qt, "answer_type": at}

    print(f"\nTotal MFN questions: {len(all_mfn)}")

    # Query 2: MFN questions WITH annotations
    annotated_query = """
        match
            $q isa ontology_question,
                has covenant_type "MFN",
                has question_id $qid;
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $tet,
                has target_attribute_name $tan;
        select $qid, $tet, $tan;
    """
    annotated = {}
    for row in tx.query(annotated_query).resolve().as_concept_rows():
        qid = _safe_val(row, "qid")
        tet = _safe_val(row, "tet")
        tan = _safe_val(row, "tan")
        if qid not in annotated:
            annotated[qid] = []
        annotated[qid].append(f"{tet}.{tan}")

    print(f"Annotated MFN questions: {len(annotated)}")

    # Query 3: MFN questions with question_targets_field
    targets_query = """
        match
            $q isa ontology_question,
                has covenant_type "MFN",
                has question_id $qid;
            (question: $q) isa question_targets_field,
                has target_field_name $tfn,
                has target_entity_type $tet;
        select $qid, $tfn, $tet;
    """
    targets = {}
    for row in tx.query(targets_query).resolve().as_concept_rows():
        qid = _safe_val(row, "qid")
        tfn = _safe_val(row, "tfn")
        tet = _safe_val(row, "tet")
        targets[qid] = f"{tet}.{tfn}"

    # Compute orphans
    orphans = {}
    for qid, info in sorted(all_mfn.items()):
        if qid not in annotated:
            orphans[qid] = info

    # Report
    print(f"\n{'─' * 70}")
    print("ANNOTATED QUESTIONS:")
    print(f"{'─' * 70}")
    for qid in sorted(annotated.keys()):
        info = all_mfn.get(qid, {})
        mappings = ", ".join(annotated[qid])
        print(f"  ✅ {qid} ({info.get('answer_type', '?')}): {mappings}")

    print(f"\n{'─' * 70}")
    print("ORPHAN QUESTIONS (no annotation — invisible to /ask-graph):")
    print(f"{'─' * 70}")
    if orphans:
        for qid, info in sorted(orphans.items()):
            target = targets.get(qid, "—")
            text = info["text"][:60] + "..." if len(info["text"]) > 60 else info["text"]
            print(f"  ❌ {qid} ({info['answer_type']}): {text}")
            if target != "—":
                print(f"       targets_field: {target}")
    else:
        print("  🎉 ZERO ORPHANS — full annotation coverage!")

    print(f"\n{'─' * 70}")
    print("QUESTIONS WITH targets_field:")
    print(f"{'─' * 70}")
    for qid in sorted(targets.keys()):
        has_annotation = "✅" if qid in annotated else "❌"
        print(f"  {has_annotation} {qid}: {targets[qid]}")

    print(f"\n{'═' * 70}")
    print(f"SUMMARY: {len(annotated)}/{len(all_mfn)} annotated, {len(orphans)} orphans")
    print(f"{'═' * 70}")

    tx.close()
    driver.close()

    # Exit with non-zero if orphans exist
    sys.exit(1 if orphans else 0)


if __name__ == "__main__":
    main()
