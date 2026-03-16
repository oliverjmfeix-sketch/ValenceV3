"""Diagnostic: test get_attr_value_types query in READ transaction."""
import os
import sys
sys.path.insert(0, '.')

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

address = os.getenv('TYPEDB_ADDRESS', '')
if not address.startswith('http'):
    address = f'https://{address}'
driver = TypeDB.driver(address, Credentials(os.getenv('TYPEDB_USERNAME',''), os.getenv('TYPEDB_PASSWORD','')), DriverOptions())
db = os.getenv('TYPEDB_DATABASE', 'valence')

test_types = ["sweep_tier", "jcrew_blocker", "builder_basket",
              "cni_source", "blocker_exception"]

tx = driver.transaction(db, TransactionType.READ)
try:
    for etype in test_types:
        query = f'match $et label {etype}; $et owns $attr; select $attr;'
        print(f"\n{'='*60}")
        print(f"=== {etype} ===")
        print(f"Query: {query}")
        try:
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            print(f"Rows returned: {len(rows)}")
            for row in rows[:5]:
                attr = row.get("attr")
                print(f"  raw type: {type(attr).__name__}")
                # Try direct methods
                if hasattr(attr, 'get_label'):
                    print(f"  get_label(): {attr.get_label()}")
                if hasattr(attr, 'get_value_type'):
                    vt = attr.get_value_type()
                    print(f"  get_value_type(): {vt} (type={type(vt).__name__})")
                # Try as_attribute_type()
                if hasattr(attr, 'as_attribute_type'):
                    try:
                        at = attr.as_attribute_type()
                        print(f"  as_attribute_type().get_label(): {at.get_label()}")
                        vt2 = at.get_value_type()
                        print(f"  as_attribute_type().get_value_type(): {vt2} (type={type(vt2).__name__})")
                    except Exception as e:
                        print(f"  as_attribute_type() error: {e}")
                # Print all public methods on first row of first type
                if etype == test_types[0] and rows.index(row) == 0:
                    methods = [m for m in dir(attr) if not m.startswith('_')]
                    print(f"  available methods: {methods}")
                    if hasattr(attr, 'as_attribute_type'):
                        at = attr.as_attribute_type()
                        at_methods = [m for m in dir(at) if not m.startswith('_')]
                        print(f"  as_attribute_type methods: {at_methods}")
        except Exception as e:
            print(f"  ERROR: {e}")
finally:
    tx.close()
    driver.close()

print(f"\n{'='*60}")
print("Done.")
