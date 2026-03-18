"""Diagnostic v2: test role specialization behavior."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

addr = os.getenv("TYPEDB_ADDRESS", "")
if not addr.startswith("http"):
    addr = f"https://{addr}"
driver = TypeDB.driver(
    addr,
    Credentials(os.getenv("TYPEDB_USERNAME", ""), os.getenv("TYPEDB_PASSWORD", "")),
    DriverOptions(),
)
db = os.getenv("TYPEDB_DATABASE", "valence")

print("=== A: Concrete roles (old way) ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        $link (basket: $b, source: $s) isa basket_has_source;
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Concrete roles: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== B: Abstract roles via entity_has_child ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        $link isa entity_has_child, links (parent: $b, child: $s);
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Abstract roles: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== C: Abstract relation, concrete roles ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        $link isa entity_has_child, links (basket: $b, source: $s);
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Abstract rel + concrete roles: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== D: No relation type, abstract roles ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        (parent: $b, child: $s);
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Untyped + abstract roles: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== E: $child_rel sub entity_has_child pattern ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        ($b, $s) isa $child_rel;
        $child_rel sub entity_has_child;
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Positional + sub filter: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== F: links keyword, positional (no role names) ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        $link isa $child_rel, links ($b, $s);
        $child_rel sub entity_has_child;
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"links + positional + sub: {len(rows)}")
    for r in rows:
        print(f"  {r.get('sname').as_value().get()}")
finally:
    tx.close()

driver.close()
print("\nDONE")
