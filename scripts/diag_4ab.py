"""Diagnostic script for Prompt 4a+4b schema changes."""
import os
import sys
import json
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

print("=== TEST 1: entity_has_child subtypes ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = "match relation $r sub entity_has_child; select $r;"
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"entity_has_child subtypes: {len(rows)}")
    for row in rows:
        print(f"  {row.get('r').as_relation_type().get_label()}")
finally:
    tx.close()

print("\n=== TEST 2: Polymorphic child match ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $b isa builder_basket;
        $link isa entity_has_child, links (parent: $b, child: $s);
        $s isa! $stype;
        let $sname = label($stype);
    select $sname;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Builder children via abstract: {len(rows)}")
    for row in rows:
        print(f"  {row.get('sname').as_value().get()}")
finally:
    tx.close()

print("\n=== TEST 3: basket_reallocates_to instances ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = "match $r isa basket_reallocates_to; select $r;"
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"basket_reallocates_to instances: {len(rows)}")
finally:
    tx.close()

print("\n=== TEST 4: Full polymorphic fetch ===")
from app.services.graph_traversal import get_rp_entities

context = get_rp_entities("87852625")
print(f"Context length: {len(context)} chars")
print(f"Starts with: {context[:50]}")

if "## ENTITY DATA" in context:
    entities = json.loads(context.split("\n\n", 1)[1])
    print(f"Entities: {len(entities)}")
    print(f"Types: {len(set(e['type_name'] for e in entities))}")

    for e in entities:
        if e.get("children"):
            print(f"  {e['type_name']}: {len(e['children'])} children")

    has_links = False
    for e in entities:
        links = e.get("links", [])
        if links:
            has_links = True
            for lnk in links:
                print(f"  LINK: {e['type_name']} --[{lnk.get('link_relation')}]--> {lnk.get('linked_type')}")

    if not has_links:
        print("  No links on any entity")

    first = entities[0]
    print(f"Keys on first entity: {list(first.keys())}")
else:
    print(f"ERROR: {context}")

print("\n=== TEST 5: Entity-to-entity relations ===")
tx = driver.transaction(db, TransactionType.READ)
try:
    q = """match
        $p isa rp_provision, has provision_id "87852625_rp";
        (provision: $p, extracted: $e) isa provision_has_extracted_entity;
        $e isa! $etype;
        ($e, $other) isa $rel;
        not { $rel sub provision_has_extracted_entity; };
        not { $rel sub entity_has_child; };
        let $rn = label($rel);
        let $en = label($etype);
    select $en, $rn;"""
    rows = list(tx.query(q).resolve().as_concept_rows())
    print(f"Entity-to-entity relations: {len(rows)}")
    for row in rows:
        en = row.get("en").as_value().get()
        rn = row.get("rn").as_value().get()
        print(f"  {en} via {rn}")
    if not rows:
        print("  No cross-references exist — extraction gap, not query gap.")
finally:
    tx.close()

driver.close()
print("\nALL DIAGNOSTICS COMPLETE")
