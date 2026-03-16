"""Test RP analytical functions against Duck Creek extraction."""
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

queries = {
    "blocker_binding_gap": 'match let $cat, $detail in blocker_binding_gap_evidence("87852625_rp"); select $cat, $detail;',
    "blocker_exception_swallow": 'match let $ename in blocker_exception_swallow_evidence("87852625_rp"); select $ename;',
    "unsub_distribution": 'match let $an, $val in unsub_distribution_evidence("87852625_rp"); select $an, $val;',
    "pathway_chain": 'match let $src, $tgt, $unc in pathway_chain_summary("87852625_rp"); select $src, $tgt, $unc;',
    "dividend_capacity": 'match let $dn, $amt in dividend_capacity_components("87852625_rp"); select $dn, $amt;',
}

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    for name, query in queries.items():
        try:
            rows = list(tx.query(query).resolve().as_concept_rows())
            print(f"\n=== {name}: {len(rows)} rows ===")
            for r in rows[:10]:
                vals = []
                for col in r.column_names():
                    v = r.get(col)
                    if hasattr(v, "as_attribute"):
                        vals.append(f"{col}={v.as_attribute().get_value()}")
                    elif hasattr(v, "get_value"):
                        vals.append(f"{col}={v.get_value()}")
                    else:
                        vals.append(f"{col}={v}")
                print("  " + ", ".join(vals))
        except Exception as e:
            print(f"\n=== {name}: ERROR ===\n  {e}")
finally:
    tx.close()
    driver.close()
