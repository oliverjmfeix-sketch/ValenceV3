"""Find entity types with attribute annotations but no _exists annotation."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

address = TYPEDB_ADDRESS
if not address.startswith("http://") and not address.startswith("https://"):
    address = f"https://{address}"

driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())
tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)

try:
    # 1. Entity types with attribute annotations (excluding _exists and _entity_list)
    q1 = """
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name $an;
            not { $an == "_exists"; };
            not { $an == "_entity_list"; };
        select $et;
    """
    annotated_types = set()
    for row in tx.query(q1).resolve().as_concept_rows():
        try:
            annotated_types.add(row.get("et").as_attribute().get_value())
        except Exception:
            try:
                annotated_types.add(row.get("et").as_value().get())
            except Exception:
                pass

    print(f"\n=== Entity types WITH attribute annotations ({len(annotated_types)}) ===")
    for et in sorted(annotated_types):
        print(f"  {et}")

    # 2. Entity types with _exists annotations
    q2 = """
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name "_exists";
        select $et;
    """
    exists_types = set()
    for row in tx.query(q2).resolve().as_concept_rows():
        try:
            exists_types.add(row.get("et").as_attribute().get_value())
        except Exception:
            try:
                exists_types.add(row.get("et").as_value().get())
            except Exception:
                pass

    print(f"\n=== Entity types WITH _exists annotations ({len(exists_types)}) ===")
    for et in sorted(exists_types):
        print(f"  {et}")

    # 3. Entity list types
    q3 = """
        match
            $q isa ontology_question,
                has answer_type "entity_list",
                has target_entity_type $et;
        select $et;
    """
    entity_list_types = set()
    for row in tx.query(q3).resolve().as_concept_rows():
        try:
            entity_list_types.add(row.get("et").as_attribute().get_value())
        except Exception:
            try:
                entity_list_types.add(row.get("et").as_value().get())
            except Exception:
                pass

    print(f"\n=== Entity list types ({len(entity_list_types)}) ===")
    for et in sorted(entity_list_types):
        print(f"  {et}")

    # 4. Gap analysis
    gap_types = annotated_types - exists_types - entity_list_types
    print(f"\n=== GAP: annotated but NO _exists and NOT entity_list ({len(gap_types)}) ===")
    for et in sorted(gap_types):
        # Show which attributes are annotated for this type
        q4 = f"""
            match
                (question: $q) isa question_annotates_attribute,
                    has target_entity_type "{et}",
                    has target_attribute_name $an;
                $q has question_id $qid;
                not {{ $an == "_exists"; }};
                not {{ $an == "_entity_list"; }};
            select $qid, $an;
        """
        attrs = []
        for row in tx.query(q4).resolve().as_concept_rows():
            try:
                qid = row.get("qid").as_attribute().get_value()
                an = row.get("an").as_attribute().get_value()
                attrs.append((qid, an))
            except Exception:
                pass
        print(f"\n  {et}:")
        for qid, an in sorted(attrs):
            print(f"    {qid} -> {an}")

finally:
    tx.close()
    driver.close()
