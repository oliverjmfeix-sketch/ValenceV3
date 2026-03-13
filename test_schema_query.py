"""Test TypeDB 3.x schema query syntax."""
import traceback
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

typedb_client.connect()

queries = [
    'match $et sub sweep_tier; $et owns $attr @key; select $attr;',
    'match $et type sweep_tier; $et owns $attr @key; select $attr;',
    'match entity $et label sweep_tier; $et owns $attr @key; select $attr;',
    'match $et label sweep_tier; $et owns $attr @key; select $attr;',
    # Maybe the issue is @key in match - try without
    'match $et sub sweep_tier; $et owns $attr; select $attr;',
    'match $et type sweep_tier; $et owns $attr; select $attr;',
    # Try define-style
    'match entity $et, owns $attr @key; $et type sweep_tier; select $attr;',
    'match entity $et owns $attr @key; $et type sweep_tier; select $attr;',
    # Maybe just sub entity
    'match $et sub entity; $et type sweep_tier; $et owns $attr; select $attr;',
    # Try with label
    'match $et label sweep_tier; $et owns $attr; select $attr;',
]
for q in queries:
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.SCHEMA)
    try:
        result = tx.query(q).resolve()
        rows = list(result.as_concept_rows())
        print(f"SUCCESS: {q}")
        for r in rows:
            print(f"  attr: {r.get('attr').as_attribute_type().get_label()}")
    except Exception as e:
        print(f"FAIL: {q}")
        print(f"  err: {repr(e)}")
    finally:
        if tx.is_open():
            tx.close()

print("Done")
