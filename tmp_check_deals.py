"""Quick check: what deals exist in TypeDB?"""
from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

typedb_client.connect()
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
rows = list(tx.query("match $d isa deal, has deal_id $did, has deal_name $name; select $did, $name;").resolve().as_concept_rows())
tx.close()
for r in rows:
    did = r.get("did").as_attribute().get_value()
    name = r.get("name").as_attribute().get_value()
    print(f"  {did} = {name}")
if not rows:
    print("  NO DEALS FOUND")

# Check uploads
import os
uploads = os.listdir("/app/uploads") if os.path.exists("/app/uploads") else []
print(f"\nUploads dir: {uploads}")

# Check extraction status
try:
    from app.routers.deals import extraction_status
    for did, status in extraction_status.items():
        print(f"\nExtraction status for {did}:")
        print(f"  status={status.status}, progress={status.progress}")
        print(f"  step={status.current_step}")
except Exception as e:
    print(f"Could not check extraction status: {e}")
