"""
Test the deduplicated polymorphic fetch query.
Run via: python -m app.scripts.test_fetch_query_v2
"""
import json, os, sys, time
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

# Fix: use label() to exclude abstract parent
FETCH_QUERY = '''
match
    $p isa rp_provision, has provision_id "{pid}";
    (provision: $p, extracted: $e) isa $rel;
    $rel sub provision_has_extracted_entity;
    let $rel_name = label($rel);
    $rel_name != "provision_has_extracted_entity";
    $e isa! $etype;
    let $type_name = label($etype);
fetch {{
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
            "child_type": $child_type_name,
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

start = time.time()
tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    answer = tx.query(FETCH_QUERY.format(pid=PROVISION_ID)).resolve()
    docs = list(answer.as_concept_documents())
finally:
    tx.close()
duration = time.time() - start

json_str = json.dumps(docs, indent=2, default=str)
print(f"Documents: {len(docs)}")
print(f"JSON size: {len(json_str)} chars")
print(f"Query time: {duration:.2f}s")

# Summary
print(f"\n{'Entity Type':<40} {'Attrs':>6} {'Anns':>6} {'Kids':>6}")
print("-" * 62)
for doc in docs:
    etype = doc.get("type_name", "?")
    attrs = doc.get("attributes", {})
    anns = doc.get("annotations", [])
    children = doc.get("children", [])
    print(f"  {etype:<38} {len(attrs):>6} {len(anns):>6} {len(children):>6}")
    for child in children:
        ctype = child.get("child_type", "?")
        cattrs = child.get("child_attributes", {})
        canns = child.get("child_annotations", [])
        print(f"    -> {ctype:<34} {len(cattrs):>6} {len(canns):>6}")

# Check no duplicates
type_counts = {}
for doc in docs:
    tn = doc.get("type_name", "?")
    # Use attributes to distinguish instances (by ID if present)
    bid = doc.get("attributes", {}).get("basket_id", doc.get("attributes", {}).get("blocker_id", ""))
    key = f"{tn}:{bid}" if bid else tn
    type_counts[key] = type_counts.get(key, 0) + 1

dupes = {k: v for k, v in type_counts.items() if v > 1 and "sweep_tier" not in k and "reallocation" not in k and "pathway" not in k and "rdp_basket" not in k and "exemption" not in k and "de_minimis" not in k}
if dupes:
    print(f"\nWARNING: Unexpected duplicates: {dupes}")
else:
    print(f"\nOK: No unexpected duplicates")

# Save
with open("/app/uploads/fetch_v2_output.json", "w") as f:
    json.dump(docs, f, indent=2, default=str)
print(f"Saved to /app/uploads/fetch_v2_output.json")

driver.close()
