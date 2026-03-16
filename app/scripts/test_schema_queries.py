"""Test schema introspection queries individually to diagnose timeout."""
import os
import sys
import time
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
tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)

def run_test(name, query):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"QUERY: {query.strip()}")
    print(f"{'='*60}")
    start = time.time()
    try:
        rows = list(tx.query(query).resolve().as_concept_rows())
        elapsed_ms = (time.time() - start) * 1000
        print(f"  ROWS: {len(rows)}")
        print(f"  DURATION: {elapsed_ms:.1f} ms")
        # Log first 20 rows
        for i, row in enumerate(rows[:20]):
            parts = []
            for col in row.column_names():
                try:
                    concept = row.get(col)
                    # Try to get label for type concepts
                    try:
                        parts.append(f"{col}={concept.get_label()}")
                    except Exception:
                        try:
                            parts.append(f"{col}={concept.as_attribute().get_value()}")
                        except Exception:
                            try:
                                parts.append(f"{col}={concept.as_value().get()}")
                            except Exception:
                                parts.append(f"{col}={concept}")
                except Exception as e:
                    parts.append(f"{col}=ERR:{e}")
            print(f"    [{i}] {', '.join(parts)}")
        if len(rows) > 20:
            print(f"    ... ({len(rows) - 20} more rows)")
        return len(rows), elapsed_ms
    except Exception as e:
        elapsed_ms = (time.time() - start) * 1000
        print(f"  ERROR after {elapsed_ms:.1f} ms: {e}")
        return -1, elapsed_ms

try:
    # Test 1: Does plays work at all?
    run_test(
        "1 — plays on ratio_basket",
        "match $et label ratio_basket; $et plays $role; select $role;"
    )

    # Test 2: Does plays work for rp_provision?
    run_test(
        "2 — plays on rp_provision",
        "match $et label rp_provision; $et plays $role; select $role;"
    )

    # Test 3: Does relates work (all relations)?
    run_test(
        "3 — all relations and roles",
        "match relation $rt; $rt relates $role; select $rt, $role;"
    )

    # Test 4: Constrained relates
    run_test(
        "4 — relates on provision_has_basket",
        "match relation $rt label provision_has_basket; $rt relates $role; select $role;"
    )

    # Test 5: Minimal cross-reference
    run_test(
        "5 — ratio_basket plays role in provision_has_basket",
        """match
            $et1 label ratio_basket; $et1 plays $role1;
            $rt label provision_has_basket; $rt relates $role1;
        select $role1;"""
    )

    # Test 6: Full cross-reference for ONE entity type
    run_test(
        "6 — full cross-ref ratio_basket <-> rp_provision via relation",
        """match
            $et1 label ratio_basket; $et1 plays $role1;
            $et2 label rp_provision; $et2 plays $role2;
            relation $rt; $rt relates $role1; $rt relates $role2;
        select $rt, $role1, $role2;"""
    )

    print(f"\n{'='*60}")
    print("ALL TESTS COMPLETE")
    print(f"{'='*60}")

finally:
    tx.close()
    driver.close()
