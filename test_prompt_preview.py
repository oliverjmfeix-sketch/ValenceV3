"""Preview the entity_list prompt section."""
from app.services.typedb_client import typedb_client
from app.services.graph_storage import GraphStorage
from app.config import settings

typedb_client.connect()

# Load entity_list questions
from typedb.driver import TransactionType
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
query = """
    match
        $q isa ontology_question,
            has question_id $qid,
            has question_text $qt,
            has answer_type "entity_list",
            has covenant_type "RP",
            has target_entity_type $tet,
            has target_relation_type $trt,
            has display_order $order;
    select $qid, $qt, $tet, $trt, $order;
"""
result = tx.query(query).resolve()
questions = []
for row in result.as_concept_rows():
    qid = row.get("qid").as_attribute().get_value()
    q = {
        "question_id": qid,
        "question_text": row.get("qt").as_attribute().get_value(),
        "target_entity_type": row.get("tet").as_attribute().get_value(),
        "target_relation_type": row.get("trt").as_attribute().get_value(),
        "display_order": row.get("order").as_attribute().get_value(),
    }
    questions.append(q)
tx.close()

print(f"Loaded {len(questions)} entity_list questions\n")
for q in sorted(questions, key=lambda x: x["display_order"]):
    print(f"  {q['question_id']}: entity={q['target_entity_type']}, rel={q['target_relation_type']}")

print("\n--- FORMATTED PROMPT SECTIONS ---\n")
for q in sorted(questions, key=lambda x: x["display_order"]):
    section = GraphStorage._format_entity_list_question(q)
    print(section)
