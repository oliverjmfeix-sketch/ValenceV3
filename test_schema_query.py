"""Test TypeDB 3.x schema query syntax - round 2."""
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

typedb_client.connect()

# Test subtype queries
queries = [
    # Subtypes of builder_basket_source
    ('subtypes', 'match $sub sub builder_basket_source; not { $sub label builder_basket_source; }; select $sub;'),
    # Label-based owns
    ('owns', 'match $et label builder_basket_source; $et owns $attr; select $attr;'),
    # Subtypes of blocker_exception
    ('sub_blocker', 'match $sub sub blocker_exception; not { $sub label blocker_exception; }; select $sub;'),
    # Can we identify key by convention? Check attr value type
    ('attr_type', 'match $et label sweep_tier; $et owns $attr; select $attr;'),
]

for name, q in queries:
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.SCHEMA)
    try:
        result = tx.query(q).resolve()
        rows = list(result.as_concept_rows())
        print(f"SUCCESS [{name}]: {len(rows)} rows")
        for r in rows:
            for var in ['sub', 'attr']:
                try:
                    c = r.get(var)
                    if hasattr(c, 'as_entity_type'):
                        print(f"  {var}: {c.as_entity_type().get_label()}")
                    elif hasattr(c, 'as_attribute_type'):
                        at = c.as_attribute_type()
                        print(f"  {var}: {at.get_label()} (value_type: {at.get_value_type()})")
                except Exception:
                    pass
    except Exception as e:
        print(f"FAIL [{name}]: {repr(e)[:200]}")
    finally:
        if tx.is_open():
            tx.close()

print("Done")
