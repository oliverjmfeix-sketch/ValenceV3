"""
Test multiple TypeDB 3.x redefine syntax variants for:
  - Making provision_has_basket sub provision_has_extracted_entity
  - Aliasing its `basket` role as `extracted`

The abstract parent provision_has_extracted_entity already exists with:
  relates provision, relates extracted

Run via:
  railway ssh --service ValenceV3 -- "cd /app && python -m app.scripts.test_redefine_syntax"
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

print(f"Connecting to {address} / db={TYPEDB_DATABASE}")
driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())
print("Connected.\n")

# ---------------------------------------------------------------------------
# PRE-FLIGHT: Check whether provision_has_extracted_entity actually exists
# ---------------------------------------------------------------------------
print("=" * 60)
print("PRE-FLIGHT: Schema state check")
print("=" * 60)

def schema_type_exists(type_name: str) -> bool:
    """Return True if the given type label exists in the schema."""
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    try:
        # TypeDB 3.x: use fetch to introspect type labels
        q = f"match $r sub relation; fetch {{ \"label\": $r; }};"
        rows = list(tx.query(q).resolve())
        for row in rows:
            try:
                label = str(row)
                if type_name in label:
                    return True
            except Exception:
                pass
        return False
    except Exception as e:
        print(f"  [WARN] schema_type_exists query failed: {e}")
        return False
    finally:
        tx.close()

# Direct approach: try a trivial read query referencing the type
def type_exists_direct(type_name: str) -> bool:
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        q = f"match $r sub {type_name};"
        rows = list(tx.query(q).resolve())
        print(f"  {type_name}: EXISTS (sub-types query returned {len(rows)} rows)")
        return True
    except Exception as e:
        print(f"  {type_name}: NOT FOUND - {str(e)[:150]}")
        return False
    finally:
        tx.close()

parent_exists = type_exists_direct("provision_has_extracted_entity")
basket_exists = type_exists_direct("provision_has_basket")

if not parent_exists:
    print()
    print("  *** Abstract parent does NOT exist. Attempting to create it now... ***")
    create_parent_q = [
        "define relation provision_has_extracted_entity @abstract, relates provision, relates extracted;",
    ]
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        tx.query(create_parent_q[0]).resolve()
        tx.commit()
        print("  [OK] Created provision_has_extracted_entity")
        parent_exists = True
    except Exception as e:
        try:
            tx.close()
        except Exception:
            pass
        print(f"  [ERROR] Could not create abstract parent: {e}")

print()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_schema(queries: list[str], label: str) -> bool:
    """Run one or more schema statements in a single write transaction. Returns True on success."""
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        for q in queries:
            tx.query(q).resolve()
        tx.commit()
        print(f"  [PASS] {label}")
        return True
    except Exception as e:
        tx.close()
        print(f"  [FAIL] {label}")
        print(f"         Error: {e}")
        return False


def undo_sub() -> None:
    """
    Attempt to revert provision_has_basket back to a standalone relation
    (no parent, just relates provision + basket).
    We try redefining sub back to `relation` (base type).
    """
    # In TypeDB 3.x the base type for relations is `relation`.
    # We also need to drop the role alias if it was added.
    revert_queries = [
        # Remove the alias first (if it exists)
        "redefine relation provision_has_basket relates basket;",
        # Remove the sub (revert to base relation type)
        "redefine relation provision_has_basket sub relation;",
    ]
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        for q in revert_queries:
            try:
                tx.query(q).resolve()
            except Exception:
                pass  # best-effort
        tx.commit()
    except Exception:
        try:
            tx.close()
        except Exception:
            pass


def check_current_state() -> None:
    """Print the current supertype of provision_has_basket for visibility."""
    q = """
    match
      $r label provision_has_basket;
    fetch {
      "type": $r
    };
    """
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    try:
        rows = list(tx.query(q).resolve())
        print(f"  [INFO] Current state query returned {len(rows)} rows")
    except Exception as e:
        print(f"  [INFO] State check query failed (expected): {e}")
    finally:
        tx.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

results = {}

print("=" * 60)
print("TEST 1: Two separate redefine statements in one transaction")
print("=" * 60)
# Approach: two statements, one after the other in the same schema tx
queries_t1 = [
    "redefine relation provision_has_basket sub provision_has_extracted_entity;",
    "redefine relation provision_has_basket relates basket as extracted;",
]
results["T1_separate_statements"] = run_schema(queries_t1, "T1: separate statements")
undo_sub()

print()
print("=" * 60)
print("TEST 2: Comma-separated on one line (original failing form)")
print("=" * 60)
queries_t2 = [
    "redefine relation provision_has_basket sub provision_has_extracted_entity, relates basket as extracted;",
]
results["T2_comma_one_line"] = run_schema(queries_t2, "T2: comma-separated one line")
undo_sub()

print()
print("=" * 60)
print("TEST 3: Comma-separated with explicit newline (multi-line string)")
print("=" * 60)
queries_t3 = [
    "redefine\nrelation provision_has_basket sub provision_has_extracted_entity,\n    relates basket as extracted;",
]
results["T3_comma_multiline"] = run_schema(queries_t3, "T3: comma-separated multiline")
undo_sub()

print()
print("=" * 60)
print("TEST 4a: Use `define` for sub change, then `redefine` for role alias")
print("=" * 60)
queries_t4a = [
    "define relation provision_has_basket sub provision_has_extracted_entity;",
    "redefine relation provision_has_basket relates basket as extracted;",
]
results["T4a_define_sub_redefine_alias"] = run_schema(queries_t4a, "T4a: define sub + redefine alias")
undo_sub()

print()
print("=" * 60)
print("TEST 4b: Use `define` for both sub and alias in one block")
print("=" * 60)
queries_t4b = [
    "define relation provision_has_basket sub provision_has_extracted_entity, relates basket as extracted;",
]
results["T4b_define_both"] = run_schema(queries_t4b, "T4b: define both together")
undo_sub()

print()
print("=" * 60)
print("TEST 5: redefine sub only (no role alias)")
print("=" * 60)
queries_t5 = [
    "redefine relation provision_has_basket sub provision_has_extracted_entity;",
]
results["T5_sub_only"] = run_schema(queries_t5, "T5: redefine sub only")

if results["T5_sub_only"]:
    print()
    print("=" * 60)
    print("TEST 6: (T5 succeeded) Now redefine role alias separately")
    print("=" * 60)
    # provision_has_basket is now subbing provision_has_extracted_entity
    queries_t6 = [
        "redefine relation provision_has_basket relates basket as extracted;",
    ]
    results["T6_alias_after_sub"] = run_schema(queries_t6, "T6: role alias after sub already set")
    undo_sub()
else:
    undo_sub()
    results["T6_alias_after_sub"] = None
    print("  [SKIP] T6 skipped because T5 failed")

print()
print("=" * 60)
print("TEST 7: redefine with `relates basket as extracted` first, then sub")
print("=" * 60)
# Some TypeDB versions may need the alias before the inheritance
queries_t7 = [
    "redefine relation provision_has_basket relates basket as extracted;",
    "redefine relation provision_has_basket sub provision_has_extracted_entity;",
]
results["T7_alias_first_then_sub"] = run_schema(queries_t7, "T7: alias first, sub second")
undo_sub()

print()
print("=" * 60)
print("TEST 8: Single redefine block with semicolons inside")
print("=" * 60)
# Try putting both in one redefine keyword block separated by semicolons
queries_t8 = [
    "redefine relation provision_has_basket sub provision_has_extracted_entity; relation provision_has_basket relates basket as extracted;",
]
results["T8_single_redefine_two_stmts"] = run_schema(queries_t8, "T8: single redefine, two relation declarations")
undo_sub()

print()
print("=" * 60)
print("TEST 9: `redefine` keyword once, two relation clauses comma-sep (no type keyword repeat)")
print("=" * 60)
queries_t9 = [
    "redefine relation provision_has_basket sub provision_has_extracted_entity, relates basket as extracted;",
]
# This is the same as T2 — only included explicitly so output is clear
results["T9_same_as_T2_explicit"] = run_schema(queries_t9, "T9: same as T2 (explicit re-run)")
undo_sub()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
for name, passed in results.items():
    if passed is None:
        status = "SKIP"
    elif passed:
        status = "PASS"
    else:
        status = "FAIL"
    print(f"  {status:4s}  {name}")

driver.close()
print("\nDone.")
