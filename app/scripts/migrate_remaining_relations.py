"""
Migrate remaining 11 entity-bearing relations to sub provision_has_extracted_entity.
provision_has_basket is already migrated.
Run via: python -m app.scripts.migrate_remaining_relations
"""
import os, sys, json
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


# ═══════════════════════════════════════════════════════════════
# STEP 0: Verify current state
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("STEP 0: Verify current state")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    # Check parent exists
    result = list(tx.query('''
        match relation $r; $r label provision_has_extracted_entity;
        select $r;
    ''').resolve().as_concept_rows())
    print(f"Parent relation exists: {len(result) > 0}")

    # Check provision_has_basket already subs it
    result = list(tx.query('''
        match
            relation $r; $r label provision_has_basket;
            $r sub $parent; $parent label provision_has_extracted_entity;
        select $r;
    ''').resolve().as_concept_rows())
    print(f"provision_has_basket already migrated: {len(result) > 0}")

    # Quick test: does polymorphic query with both roles work?
    result = list(tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, extracted: $e) isa provision_has_extracted_entity;
        select $e;
    ''').resolve().as_concept_rows())
    print(f"Polymorphic query (provision + extracted): {len(result)} results")
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 1: Migrate remaining 11 relations
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 1: Migrate remaining relations")
print("=" * 60)

# (relation_name, role_to_alias_as_extracted)
relations_to_migrate = [
    ("provision_has_rdp_basket", "rdp_basket"),
    ("provision_has_blocker", "blocker"),
    ("provision_has_unsub", "designation"),
    ("provision_has_pathway", "pathway"),
    ("provision_has_sweep_tier", "tier"),
    ("provision_has_de_minimis", "threshold"),
    ("provision_has_reallocation", "reallocation"),
    ("provision_has_sweep_exemption", "exemption"),
    ("provision_has_intercompany_permission", "permission"),
    ("provision_has_definition", "definition"),
    ("provision_has_lien_release", "lien_release"),
]

for rel_name, role_name in relations_to_migrate:
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        tx.query(f"""
            define
            relation {rel_name} sub provision_has_extracted_entity,
                relates {role_name} as extracted;
        """).resolve()
        tx.commit()
        print(f"  PASS: {rel_name} (role {role_name} as extracted)")
    except Exception as e:
        err = str(e)
        if tx.is_open():
            tx.close()
        if "already" in err.lower() or "exist" in err.lower():
            print(f"  SKIP: {rel_name} (already migrated)")
        else:
            print(f"  FAIL: {rel_name} -- {err[:200]}")
            print("STOP. Report this error.")
            driver.close()
            sys.exit(1)

print("\nAll relations processed.")


# ═══════════════════════════════════════════════════════════════
# STEP 2: Verify full polymorphic match
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 2: Verify polymorphic match")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    # First: check which relations now sub the parent
    result = list(tx.query('''
        match
            relation $r; $r sub $parent;
            $parent label provision_has_extracted_entity;
        select $r;
    ''').resolve().as_concept_rows())
    rel_labels = set()
    for row in result:
        rel_labels.add(row.get("r").as_relation_type().get_label())
    print(f"Relations subbing parent: {len(rel_labels)}")
    for r in sorted(rel_labels):
        print(f"  {r}")

    # Now: polymorphic entity fetch
    result = list(tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, extracted: $e) isa $rel;
            $rel sub provision_has_extracted_entity;
            $e isa! $etype;
        select $rel, $etype;
    ''').resolve().as_concept_rows())
    print(f"\nPolymorphic match: {len(result)} entities")

    rel_counts = {}
    type_counts = {}
    for row in result:
        rt = row.get("rel").as_relation_type().get_label()
        et = row.get("etype").as_entity_type().get_label()
        rel_counts[rt] = rel_counts.get(rt, 0) + 1
        type_counts[et] = type_counts.get(et, 0) + 1

    print("\nBy relation type:")
    for rt, count in sorted(rel_counts.items()):
        print(f"  {rt}: {count}")

    print("\nBy entity type:")
    for et, count in sorted(type_counts.items()):
        print(f"  {et}: {count}")

    if len(result) > 100:
        print("\nWARNING: Too many rows")
    if len(result) < 5:
        print("\nWARNING: Too few rows")
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 3: Verify get_entity_annotations function
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 3: Verify annotation function")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    result = list(tx.query('''
        match let $an, $qt in get_entity_annotations("ratio_basket");
        select $an, $qt;
    ''').resolve().as_concept_rows())
    print(f"ratio_basket annotations: {len(result)}")
    for row in result:
        an = row.get("an")
        qt = row.get("qt")
        try:
            an_val = an.as_attribute().get_value()
        except Exception:
            an_val = an.as_value().get() if an else None
        try:
            qt_val = qt.as_attribute().get_value()
        except Exception:
            qt_val = qt.as_value().get() if qt else None
        display = f"{qt_val[:60]}..." if qt_val and len(str(qt_val)) > 60 else qt_val
        print(f"  {an_val}: {display}")
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 4: Verify label() + annotations in polymorphic fetch
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 4: Full polymorphic fetch with label() + annotations")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    result = tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, extracted: $e) isa $rel;
            $rel sub provision_has_extracted_entity;
            $e isa! $etype;
            let $type_name = label($etype);
        fetch {
            "entity_type": $type_name,
            "relation": $rel,
            "attributes": { $e.* },
            "annotations": [
                match
                    let $an, $qt in get_entity_annotations($type_name);
                fetch { "attribute": $an, "question": $qt };
            ]
        };
    ''').resolve()
    docs = list(result.as_concept_documents())
    print(f"Total documents: {len(docs)}")

    type_summary = {}
    for doc in docs:
        et = doc.get("entity_type", "unknown")
        ann_count = len(doc.get("annotations", []))
        attr_count = len(doc.get("attributes", {}))
        if et not in type_summary:
            type_summary[et] = {"count": 0, "attrs": attr_count, "annotations": ann_count}
        type_summary[et]["count"] += 1

    print(f"\n{'Entity Type':<40} {'Instances':>10} {'Attrs':>8} {'Annots':>8}")
    print("-" * 70)
    total_ann = 0
    for et, info in sorted(type_summary.items()):
        print(f"{et:<40} {info['count']:>10} {info['attrs']:>8} {info['annotations']:>8}")
        total_ann += info["annotations"] * info["count"]
    print("-" * 70)
    print(f"{'TOTAL':<40} {len(docs):>10} {'':>8} {total_ann:>8}")

    # Show one sample
    if docs:
        print(f"\nSample (first doc):")
        print(json.dumps(docs[0], indent=2, default=str)[:600])
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 5: Verify existing queries still work
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 5: Verify existing queries still work")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    # Old-style query using direct relation type
    result = list(tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, basket: $b) isa provision_has_basket;
            $b has basket_id $bid;
        select $bid;
    ''').resolve().as_concept_rows())
    print(f"Old-style basket query: {len(result)} baskets (should be 8)")

    result = list(tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, blocker: $b) isa provision_has_blocker;
            $b has blocker_id $bid;
        select $bid;
    ''').resolve().as_concept_rows())
    print(f"Old-style blocker query: {len(result)} blockers (should be 1)")

    result = list(tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, pathway: $pw) isa provision_has_pathway;
        select $pw;
    ''').resolve().as_concept_rows())
    print(f"Old-style pathway query: {len(result)} pathways (should be 3)")

    # Test functions still work
    result = list(tx.query('''
        match let $amt in dividend_capacity_components("87852625_rp");
        select $amt;
    ''').resolve().as_concept_rows())
    amt = result[0].get("amt")
    try:
        amt_val = amt.as_value().get()
    except Exception:
        amt_val = amt.as_attribute().get_value()
    print(f"dividend_capacity_components: ${amt_val:,.0f} (should be $130M)")
finally:
    tx.close()


print("\n" + "=" * 60)
print("ALL STEPS COMPLETE")
print("=" * 60)
driver.close()
