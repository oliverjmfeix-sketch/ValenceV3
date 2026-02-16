"""Temp script to query provision extracted_at timestamps from TypeDB."""
from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
try:
    # RP provisions
    q = 'match $p isa rp_provision, has provision_id $pid; select $pid;'
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"RP provisions: {len(rows)}")
    for row in rows:
        pid = row.get('pid').as_attribute().get_value()
        print(f"  {pid}")

    # Try extracted_at
    try:
        q2 = 'match $p isa rp_provision, has provision_id $pid, has extracted_at $ea; select $pid, $ea;'
        rows2 = list(tx.query(q2).resolve().as_concept_rows())
        print(f"\nRP provisions with extracted_at: {len(rows2)}")
        for row in rows2:
            pid = row.get('pid').as_attribute().get_value()
            ea = row.get('ea').as_attribute().get_value()
            print(f"  {pid}: {ea}")
    except Exception as e:
        print(f"\nextracted_at query error: {e}")

    # MFN provisions
    try:
        q3 = 'match $p isa mfn_provision, has provision_id $pid; select $pid;'
        rows3 = list(tx.query(q3).resolve().as_concept_rows())
        print(f"\nMFN provisions: {len(rows3)}")
        for row in rows3:
            pid = row.get('pid').as_attribute().get_value()
            print(f"  {pid}")
    except Exception as e:
        print(f"\nMFN query error: {e}")

    # Try deal entity
    try:
        q4 = 'match $d isa deal, has deal_id $did; select $did;'
        rows4 = list(tx.query(q4).resolve().as_concept_rows())
        print(f"\nDeals: {len(rows4)}")
        for row in rows4:
            did = row.get('did').as_attribute().get_value()
            print(f"  {did}")
    except Exception as e:
        print(f"\nDeal query error: {e}")

    # Try deal with upload/create date
    try:
        q5 = 'match $d isa deal, has deal_id $did, has created_at $ca; select $did, $ca;'
        rows5 = list(tx.query(q5).resolve().as_concept_rows())
        print(f"\nDeals with created_at: {len(rows5)}")
        for row in rows5:
            did = row.get('did').as_attribute().get_value()
            ca = row.get('ca').as_attribute().get_value()
            print(f"  {did}: {ca}")
    except Exception as e:
        print(f"\nDeal created_at query error: {e}")

finally:
    tx.close()
