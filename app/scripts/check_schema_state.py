"""
Quick schema state check: verify provision_has_basket sub provision_has_extracted_entity
and test that both role names (basket + extracted) work in queries.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

address = TYPEDB_ADDRESS
if not address.startswith("http://") and not address.startswith("https://"):
    address = f"https://{address}"

driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())

# 1. Check sub-types of the parent
tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    q = "match $r sub provision_has_extracted_entity;"
    rows = list(tx.query(q).resolve())
    print(f"subtypes of provision_has_extracted_entity: {len(rows)}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"sub query error: {e}")
finally:
    tx.close()

# 2. Verify `extracted` role works on the parent relation
tx2 = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    q2 = "match $p isa rp_provision; (provision: $p, extracted: $b) isa provision_has_extracted_entity; select $b; limit 3;"
    rows2 = list(tx2.query(q2).resolve())
    print(f"provision_has_extracted_entity via `extracted` role: {len(rows2)} rows")
except Exception as e:
    print(f"extracted role query error: {e}")
finally:
    tx2.close()

# 3. Verify `basket` role still works on provision_has_basket
tx3 = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    q3 = "match $p isa rp_provision; (provision: $p, basket: $b) isa provision_has_basket; select $b; limit 3;"
    rows3 = list(tx3.query(q3).resolve())
    print(f"provision_has_basket via `basket` role: {len(rows3)} rows")
except Exception as e:
    print(f"basket role query error: {e}")
finally:
    tx3.close()

# 4. Verify inherited `extracted` role works on provision_has_basket directly
tx4 = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    q4 = "match $p isa rp_provision; (provision: $p, extracted: $b) isa provision_has_basket; select $b; limit 3;"
    rows4 = list(tx4.query(q4).resolve())
    print(f"provision_has_basket via inherited `extracted` role: {len(rows4)} rows")
except Exception as e:
    print(f"inherited extracted role query error: {e}")
finally:
    tx4.close()

# 5. Check provision role on parent
tx5 = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    q5 = "match $r sub provision_has_extracted_entity;"
    rows5 = list(tx5.query(q5).resolve())
    print(f"sub query confirmed: {len(rows5)} rows")
except Exception as e:
    print(f"sub check error: {e}")
finally:
    tx5.close()

driver.close()
print("Done.")
