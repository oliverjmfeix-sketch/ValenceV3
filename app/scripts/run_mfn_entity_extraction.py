"""Re-run MFN entity extraction for a deal using the unified pipeline."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DEAL_ID = sys.argv[1] if len(sys.argv) > 1 else "8d0bf2f8"


async def main():
    mfn_path = f"/app/uploads/{DEAL_ID}_mfn_universe.txt"
    if not os.path.exists(mfn_path):
        print(f"ERROR: MFN universe not found at {mfn_path}")
        sys.exit(1)

    with open(mfn_path) as f:
        mfn_text = f.read()
    print(f"MFN universe: {len(mfn_text)} chars")

    # Ensure deal + provision exist in TypeDB
    from app.services.typedb_client import typedb_client
    from app.config import settings
    from typedb.driver import TransactionType

    typedb_client.connect()

    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        rows = list(tx.query(f'match $d isa deal, has deal_id "{DEAL_ID}"; select $d;').resolve().as_concept_rows())
        print(f"Deal exists: {len(rows) > 0}")
    finally:
        tx.close()

    # Ensure deal exists
    if not rows:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            tx.query(f'insert $d isa deal, has deal_id "{DEAL_ID}", has deal_name "ACP Tara";').resolve()
            tx.commit()
            print("Created deal entity")
        except Exception as e:
            if tx.is_open():
                tx.close()
            print(f"Deal creation: {e}")

    # Ensure MFN provision exists
    provision_id = f"{DEAL_ID}_mfn"
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        rows = list(tx.query(f'match $p isa mfn_provision, has provision_id "{provision_id}"; select $p;').resolve().as_concept_rows())
        prov_exists = len(rows) > 0
    finally:
        tx.close()

    if not prov_exists:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            tx.query(f'''
                match $d isa deal, has deal_id "{DEAL_ID}";
                insert
                    $p isa mfn_provision, has provision_id "{provision_id}", has extracted_at "{now}";
                    (deal: $d, provision: $p) isa deal_has_provision;
            ''').resolve()
            tx.commit()
            print(f"Created mfn_provision: {provision_id}")
        except Exception as e:
            if tx.is_open():
                tx.close()
            print(f"Provision creation: {e}")

    # Run extraction
    from app.services.extraction import get_extraction_service
    svc = get_extraction_service()

    result = await svc.run_mfn_entity_extraction(DEAL_ID, mfn_text)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
