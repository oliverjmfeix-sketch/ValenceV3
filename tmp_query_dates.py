"""Temp script to query provision/deal attributes from TypeDB."""
from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

typedb_client.connect()
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
try:
    # All attributes on each deal
    for did in ["41efd282", "6297ebfa", "cbe30932", "ecc5be02"]:
        print(f"\n=== Deal {did} ===")
        q = f'match $d isa deal, has deal_id "{did}", has $attr; select $attr;'
        rows = list(tx.query(q).resolve().as_concept_rows())
        for row in rows:
            a = row.get('attr').as_attribute()
            print(f"  {a.get_type().get_label()}: {a.get_value()}")

    # All attributes on each RP provision
    for did in ["41efd282", "6297ebfa", "cbe30932", "ecc5be02"]:
        pid = f"{did}_rp"
        print(f"\n=== RP Provision {pid} ===")
        q = f'match $p isa rp_provision, has provision_id "{pid}", has $attr; select $attr;'
        rows = list(tx.query(q).resolve().as_concept_rows())
        for row in rows:
            a = row.get('attr').as_attribute()
            print(f"  {a.get_type().get_label()}: {a.get_value()}")

finally:
    tx.close()
