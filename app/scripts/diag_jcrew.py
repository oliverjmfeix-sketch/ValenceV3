"""Diagnostic: Check J.Crew extraction pipeline end-to-end."""
import os, sys, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

addr = TYPEDB_ADDRESS
if not addr.startswith("http"): addr = f"https://{addr}"

driver = TypeDB.driver(addr, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())

print("=" * 60)
print("STEP 1: Check JC data in TypeDB")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)

# Check category_has_question relations for JC
rows = list(tx.query(
    'match $cat isa ontology_category, has category_id $cid; '
    '$cid like "^JC"; '
    '(category: $cat, question: $q) isa category_has_question; '
    '$q has question_id $qid; select $cid, $qid;'
).resolve().as_concept_rows())
print(f"\nJC category_has_question relations: {len(rows)}")
from collections import Counter
c = Counter()
for r in rows:
    c[r.get("cid").as_attribute().get_value()] += 1
for k in sorted(c):
    print(f"  {k}: {c[k]}")

# Check JC questions exist
rows2 = list(tx.query(
    'match $q isa ontology_question, has question_id $qid; $qid like "^jc_"; select $qid;'
).resolve().as_concept_rows())
print(f"\nJC questions total: {len(rows2)}")

# Check JC categories exist
rows3 = list(tx.query(
    'match $c isa ontology_category, has category_id $cid; $cid like "^JC"; select $cid;'
).resolve().as_concept_rows())
print(f"JC categories: {len(rows3)}")
for r in rows3:
    print(f"  {r.get('cid').as_attribute().get_value()}")

# Check a sample question has all required attrs
print("\nSample question jc_t1_01 attributes:")
sample = list(tx.query(
    'match $q isa ontology_question, has question_id "jc_t1_01", '
    'has covenant_type $ct, has answer_type $at, has display_order $do; '
    'select $ct, $at, $do;'
).resolve().as_concept_rows())
if sample:
    r = sample[0]
    print(f"  covenant_type: {r.get('ct').as_attribute().get_value()}")
    print(f"  answer_type: {r.get('at').as_attribute().get_value()}")
    print(f"  display_order: {r.get('do').as_attribute().get_value()}")
else:
    print("  NOT FOUND or missing attributes!")

tx.close()

print("\n" + "=" * 60)
print("STEP 2: Check load_questions_by_category('RP') returns JC categories")
print("=" * 60)

# Need to init the typedb_client singleton
from app.services.typedb_client import typedb_client
typedb_client.connect()

from app.services.extraction import ExtractionService
svc = ExtractionService()
all_cats = svc.load_questions_by_category("RP")
print(f"\nAll RP categories: {sorted(all_cats.keys())}")
print(f"JC1: {len(all_cats.get('JC1', []))}")
print(f"JC2: {len(all_cats.get('JC2', []))}")
print(f"JC3: {len(all_cats.get('JC3', []))}")
jc_total = len(all_cats.get('JC1', [])) + len(all_cats.get('JC2', [])) + len(all_cats.get('JC3', []))
print(f"JC total: {jc_total}")

if jc_total == 0:
    print("\n*** JC QUESTIONS NOT LOADING — checking query details ***")
    # Try the raw query to see what's happening
    tx2 = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    raw = list(tx2.query(
        'match $cat isa ontology_category, has category_id $cid, has name $cname; '
        '(category: $cat, question: $q) isa category_has_question; '
        '$q has question_id $qid, has question_text $qt, has answer_type $at, '
        'has covenant_type "RP", has display_order $order; '
        '$cid like "^JC"; '
        'select $cid, $qid, $at, $order;'
    ).resolve().as_concept_rows())
    print(f"  Raw query with all attrs + covenant_type filter: {len(raw)} rows")
    tx2.close()

driver.close()
typedb_client.close()

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
