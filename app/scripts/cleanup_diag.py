"""Clean up diagnostic test deals."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

addr = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
if not addr.startswith("http"):
    addr = f"https://{addr}"

d = TypeDB.driver(addr, Credentials(os.getenv("TYPEDB_USERNAME", ""), os.getenv("TYPEDB_PASSWORD", "")), DriverOptions())

for did in ["diag_jcrew_test", "diag_jcrew_full"]:
    pid = f"{did}_rp"
    # Delete answers first (relations)
    tx = d.transaction("valence", TransactionType.WRITE)
    try:
        tx.query(f'match $prov isa rp_provision, has provision_id "{pid}"; (provision: $prov, question: $q) isa $r; delete $r;').resolve()
        tx.commit()
        print(f"Deleted answers for {pid}")
    except Exception as e:
        tx.close()
        print(f"No answers to delete for {pid}: {e}")

    # Delete provision
    tx = d.transaction("valence", TransactionType.WRITE)
    try:
        tx.query(f'match $p isa rp_provision, has provision_id "{pid}"; (deal: $d, provision: $p) isa $r; delete $r;').resolve()
        tx.commit()
        print(f"Deleted provision relation for {pid}")
    except Exception as e:
        tx.close()
        print(f"No provision relation for {pid}: {e}")

    tx = d.transaction("valence", TransactionType.WRITE)
    try:
        tx.query(f'match $p isa rp_provision, has provision_id "{pid}"; delete $p;').resolve()
        tx.commit()
        print(f"Deleted provision {pid}")
    except Exception as e:
        tx.close()
        print(f"No provision {pid}: {e}")

    # Delete deal
    tx = d.transaction("valence", TransactionType.WRITE)
    try:
        tx.query(f'match $d isa deal, has deal_id "{did}"; delete $d;').resolve()
        tx.commit()
        print(f"Deleted deal {did}")
    except Exception as e:
        tx.close()
        print(f"Could not delete deal {did}: {e}")

d.close()
print("Cleanup complete")
