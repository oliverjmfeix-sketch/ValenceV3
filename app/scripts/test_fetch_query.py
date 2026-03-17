"""
Test the polymorphic fetch query against live data.
Run via: python -m app.scripts.test_fetch_query
"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

address = TYPEDB_ADDRESS
if not address.startswith("http://") and not address.startswith("https://"):
    address = f"https://{address}"

driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())

PROVISION_ID = "87852625_rp"

FETCH_QUERY = '''
match
    $p isa rp_provision, has provision_id "{pid}";
    (provision: $p, extracted: $e) isa $rel;
    $rel sub provision_has_extracted_entity;
    $e isa! $etype;
    let $type_name = label($etype);
fetch {{
    "relation": $rel,
    "entity_type": $etype,
    "type_name": $type_name,
    "attributes": {{ $e.* }},
    "annotations": [
        match
            let $an, $qt in get_entity_annotations($type_name);
        fetch {{ "attribute": $an, "annotation": $qt }};
    ],
    "children": [
        match
            ($e, $child) isa $child_rel;
            not {{ $child_rel sub provision_has_extracted_entity; }};
            $child isa! $ctype;
            let $child_type_name = label($ctype);
        fetch {{
            "child_relation": $child_rel,
            "child_type": $ctype,
            "child_attributes": {{ $child.* }},
            "child_annotations": [
                match
                    let $can, $cqt in get_entity_annotations($child_type_name);
                fetch {{ "attribute": $can, "annotation": $cqt }};
            ]
        }};
    ]
}};
'''

# ═══════════════════════════════════════════════════════════════
# TEST 1: Full fetch query
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("TEST 1: Full fetch query")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    answer = tx.query(FETCH_QUERY.format(pid=PROVISION_ID)).resolve()
    docs = list(answer.as_concept_documents())
    json_str = json.dumps(docs, indent=2, default=str)
    print(f"Documents returned: {len(docs)}")
    print(f"JSON size: {len(json_str)} chars")

    # Summary
    print(f"\n{'Entity Type':<40} {'Rel':<35} {'Attrs':>6} {'Anns':>6} {'Kids':>6}")
    print("-" * 95)
    for doc in docs:
        rel = doc.get("relation", {}).get("label", "?")
        etype = doc.get("entity_type", {}).get("label", "?")
        attrs = doc.get("attributes", {})
        anns = doc.get("annotations", [])
        children = doc.get("children", [])
        print(f"  {etype:<38} {rel:<35} {len(attrs):>6} {len(anns):>6} {len(children):>6}")
        for child in children:
            crel = child.get("child_relation", {}).get("label", "?")
            ctype = child.get("child_type", {}).get("label", "?")
            cattrs = child.get("child_attributes", {})
            canns = child.get("child_annotations", [])
            print(f"    -> {ctype:<36} {crel:<35} {len(cattrs):>6} {len(canns):>6}")

    # Check for leaked relation types
    rel_types = set(doc.get("relation", {}).get("label", "?") for doc in docs)
    print(f"\nRelation types present: {sorted(rel_types)}")
    bad_rels = {"provision_has_answer", "concept_applicability", "deal_has_provision"}
    leaked = rel_types & bad_rels
    if leaked:
        print(f"  WARNING: Non-entity relations leaked through: {leaked}")
    else:
        print("  OK: No non-entity relations leaked")

    # Check annotation correlation
    print("\nAnnotation correlation check:")
    for doc in docs:
        etype = doc.get("type_name", "?")
        anns = doc.get("annotations", [])
        # All annotations should reference attributes that exist on this entity type
        attr_keys = set(doc.get("attributes", {}).keys())
        ann_attrs = set(a.get("attribute", "") for a in anns)
        # _exists is an annotation but not an attribute in the data
        ann_attrs_no_exists = {a for a in ann_attrs if a != "_exists"}
        extra = ann_attrs_no_exists - attr_keys
        if extra:
            print(f"  {etype}: {len(extra)} annotations for attrs NOT in data: {extra}")

    # Save full output
    with open("/app/uploads/fetch_test_output.json", "w") as f:
        json.dump(docs, f, indent=2, default=str)
    print(f"\nFull output saved to /app/uploads/fetch_test_output.json")
finally:
    tx.close()

# ═══════════════════════════════════════════════════════════════
# TEST 2: Builder basket children specifically
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 2: Builder basket children")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    result = tx.query(f'''
        match
            $p isa rp_provision, has provision_id "{PROVISION_ID}";
            (provision: $p, extracted: $b) isa provision_has_basket;
            $b isa builder_basket;
            ($b, $child) isa $child_rel;
            not {{ $child_rel sub provision_has_extracted_entity; }};
            $child isa! $ctype;
        select $child_rel, $ctype;
    ''').resolve()
    rows = list(result.as_concept_rows())
    print(f"Builder basket child relations: {len(rows)}")
    for row in rows:
        cr = row.get("child_rel").as_relation_type().get_label()
        ct = row.get("ctype").as_entity_type().get_label()
        print(f"  [{cr}] -> {ct}")
finally:
    tx.close()

# ═══════════════════════════════════════════════════════════════
# TEST 3: Check for basket_reallocates_to self-references
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 3: basket_reallocates_to in children")
print("=" * 60)

found_realloc = False
for doc in docs:
    for child in doc.get("children", []):
        crel = child.get("child_relation", {}).get("label", "")
        if "reallocate" in crel.lower():
            etype = doc.get("type_name", "?")
            ctype = child.get("child_type", {}).get("label", "?")
            print(f"  {etype} -> [{crel}] -> {ctype}")
            found_realloc = True
if not found_realloc:
    print("  No basket_reallocates_to found in children (good)")

# ═══════════════════════════════════════════════════════════════
# TEST 4: Check abstract types
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("TEST 4: Check for abstract entity types")
print("=" * 60)

abstract_types = {"rp_basket", "rdp_basket", "builder_basket_source", "blocker_exception", "sweep_exemption"}
found_abstract = False
for doc in docs:
    etype = doc.get("entity_type", {}).get("label", "?")
    if etype in abstract_types:
        print(f"  WARNING: Abstract type {etype} found in results")
        found_abstract = True
if not found_abstract:
    print("  OK: No abstract types in results")

driver.close()
print("\nDone.")
