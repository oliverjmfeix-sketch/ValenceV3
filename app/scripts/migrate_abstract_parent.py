"""
Schema migration: Add abstract parent relation for polymorphic fetch.
Run via: python -m app.scripts.migrate_abstract_parent
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
# STEP 0: Test feasibility
# ═══════════════════════════════════════════════════════════════
print("=" * 60)
print("STEP 0: Test schema migration feasibility")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
try:
    tx.query("""
        define
        relation provision_has_extracted_entity @abstract,
            relates provision,
            relates extracted;
    """).resolve()

    tx.query("""
        redefine
        relation provision_has_basket sub provision_has_extracted_entity,
            relates basket as extracted;
    """).resolve()

    tx.commit()
    print("PASS: provision_has_basket now subs provision_has_extracted_entity")
except Exception as e:
    print(f"FAIL: {e}")
    print("STOP. Do not proceed. Report this error.")
    if tx.is_open():
        tx.close()
    driver.close()
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# STEP 1: Migrate remaining 11 relations
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 1: Migrate all entity-bearing relations")
print("=" * 60)

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
            redefine
            relation {rel_name} sub provision_has_extracted_entity,
                relates {role_name} as extracted;
        """).resolve()
        tx.commit()
        print(f"  PASS: {rel_name}")
    except Exception as e:
        print(f"  FAIL: {rel_name} -- {e}")
        print("STOP. Do not proceed. Report this error.")
        if tx.is_open():
            tx.close()
        driver.close()
        sys.exit(1)

print("\nAll 12 relations migrated successfully.")


# ═══════════════════════════════════════════════════════════════
# STEP 2: Verify polymorphic match
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 2: Verify polymorphic match")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    result = tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, extracted: $e) isa $rel;
            $rel sub provision_has_extracted_entity;
            $e isa! $etype;
        select $rel, $etype;
    ''').resolve()
    rows = list(result.as_concept_rows())
    print(f"Polymorphic match: {len(rows)} entities")

    rel_types = set()
    entity_types = set()
    for row in rows:
        rel_types.add(row.get("rel").as_relation_type().get_label())
        entity_types.add(row.get("etype").as_entity_type().get_label())
    print(f"Relation types: {sorted(rel_types)}")
    print(f"Entity types: {sorted(entity_types)}")

    if len(rows) > 100:
        print("WARNING: Too many rows. Non-entity relations may be included.")
    if len(rows) < 5:
        print("WARNING: Too few rows. Migration may have broken existing data.")
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 3: Define get_entity_annotations function
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 3: Define get_entity_annotations function")
print("=" * 60)

func_tql = '''
    define
    fun get_entity_annotations($type_name: string) -> { target_attribute_name, question_text }:
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name $an;
            $et == $type_name;
            $q has question_text $qt;
        return { $an, $qt };
'''

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
try:
    tx.query(func_tql).resolve()
    tx.commit()
    print("PASS: get_entity_annotations function defined")
except Exception as e:
    err = str(e)
    print(f"Initial define failed: {err}")
    if tx.is_open():
        tx.close()
    # May already exist from test script — try redefine approach
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
    try:
        tx.query(func_tql.replace("define", "redefine")).resolve()
        tx.commit()
        print("PASS: get_entity_annotations function redefined")
    except Exception as e2:
        print(f"Redefine also failed: {e2}")
        if tx.is_open():
            tx.close()
        # Already exists and is identical — that's fine
        if "already" in str(e).lower() or "exists" in str(e).lower():
            print("PASS: function already exists (from test script)")
        else:
            print("FAIL: Could not define function. STOP.")
            driver.close()
            sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# STEP 4: Verify annotation function
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 4: Verify annotation function")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    result = tx.query('''
        match
            let $an, $qt in get_entity_annotations("ratio_basket");
        select $an, $qt;
    ''').resolve()
    rows = list(result.as_concept_rows())
    print(f"ratio_basket annotations: {len(rows)}")
    for row in rows:
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
# STEP 5: Verify label() with polymorphic match
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 5: Verify label() with polymorphic match")
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
        select $type_name;
    ''').resolve()
    rows = list(result.as_concept_rows())
    type_names = set()
    for row in rows:
        tn = row.get("type_name")
        try:
            type_names.add(tn.as_value().get())
        except Exception:
            type_names.add(tn.as_attribute().get_value() if tn else None)
    print(f"Entity type names via label(): {sorted(type_names)}")
    print(f"Count: {len(type_names)} distinct types, {len(rows)} total entities")
finally:
    tx.close()


# ═══════════════════════════════════════════════════════════════
# STEP 5b: Full fetch test with polymorphic match + annotations
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("STEP 5b: Full polymorphic fetch with annotations")
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

    # Summary by entity type
    type_counts = {}
    for doc in docs:
        et = doc.get("entity_type", "unknown")
        type_counts[et] = type_counts.get(et, 0) + 1

    for et, count in sorted(type_counts.items()):
        # Find one doc of this type to count annotations
        sample = next(d for d in docs if d.get("entity_type") == et)
        ann_count = len(sample.get("annotations", []))
        attr_count = len(sample.get("attributes", {}))
        print(f"  {et}: {count} instance(s), {attr_count} attrs, {ann_count} annotations")

    # Show one full example
    print(f"\nSample document (first):")
    print(json.dumps(docs[0], indent=2, default=str)[:800])

finally:
    tx.close()

print("\n" + "=" * 60)
print("ALL STEPS COMPLETE")
print("=" * 60)

driver.close()
