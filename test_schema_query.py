"""Test TypeDB 3.x schema query syntax."""
import sys
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

typedb_client.connect()
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.SCHEMA)

queries = [
    'match $et sub sweep_tier; $et owns $attr @key; select $attr;',
    'match $et type sweep_tier; $et owns $attr @key; select $attr;',
    'match entity $et label sweep_tier; $et owns $attr @key; select $attr;',
    'match $et label sweep_tier; $et owns $attr @key; select $attr;',
]
for q in queries:
    try:
        result = tx.query(q).resolve()
        rows = list(result.as_concept_rows())
        print(f"SUCCESS: {q}")
        for r in rows:
            print(f"  attr: {r.get('attr').as_attribute_type().get_label()}")
    except Exception as e:
        err = str(e).split('\n')[0][:120]
        print(f"FAIL: {q}")
        print(f"  err: {err}")

tx.close()
print("Done")
