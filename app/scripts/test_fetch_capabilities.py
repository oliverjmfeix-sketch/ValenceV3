"""
Test TypeDB fetch capabilities against live instance.
Run via: python -m app.scripts.test_fetch_capabilities
"""
import json
import sys
import logging

logging.basicConfig(level=logging.WARNING)

from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

PROVISION_ID = "87852625_rp"
DB = settings.typedb_database
driver = typedb_client.driver

if not driver:
    print("FATAL: No TypeDB driver connected")
    sys.exit(1)

results = {}


def run_test(name, query):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    print(f"QUERY:\n{query.strip()}\n")
    tx = driver.transaction(DB, TransactionType.READ)
    try:
        answer = tx.query(query).resolve()

        # Try as concept documents (fetch output)
        try:
            docs = list(answer.as_concept_documents())
            print(f"RESULT: {len(docs)} documents")
            for i, doc in enumerate(docs[:5]):
                print(f"  Doc {i}: {json.dumps(doc, indent=2, default=str)[:500]}")
            if len(docs) > 5:
                print(f"  ... and {len(docs) - 5} more")
            print("STATUS: PASS (concept documents)")
            results[name] = "PASS"
            return "PASS", docs
        except Exception as e1:
            pass

        # Try as concept rows (select output)
        try:
            rows = list(answer.as_concept_rows())
            print(f"RESULT: {len(rows)} rows")
            for i, row in enumerate(rows[:5]):
                print(f"  Row {i}: {row}")
            print("STATUS: PASS (concept rows)")
            results[name] = "PASS"
            return "PASS", rows
        except Exception as e2:
            print(f"FAIL: Neither concept_documents nor concept_rows worked")
            print(f"  Documents error: {e1}")
            print(f"  Rows error: {e2}")
            results[name] = "FAIL"
            return "FAIL", str(e1)

    except Exception as e:
        print(f"FAIL: Query execution error: {e}")
        results[name] = f"FAIL: {str(e)[:200]}"
        return "FAIL", str(e)
    finally:
        if tx.is_open():
            tx.close()


# ── Test 1: Basic fetch ──────────────────────────────────────
run_test("1. Basic fetch", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
    fetch {{
        "provision_id": $p.provision_id
    }};
''')

# ── Test 2: Wildcard $entity.* ───────────────────────────────
run_test("2. Wildcard $entity.*", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa ratio_basket;
    fetch {{
        "basket": {{ $b.* }}
    }};
''')

# ── Test 3: Type variable in fetch ───────────────────────────
run_test("3. Type variable in fetch", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa! $btype;
    fetch {{
        "basket_type": $btype,
        "basket": {{ $b.* }}
    }};
''')

# ── Test 4: Relation type in fetch ───────────────────────────
run_test("4. Relation type in fetch", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa $rel;
        $rel sub provision_has_basket;
        $b isa! $btype;
    fetch {{
        "relation": $rel,
        "basket_type": $btype,
        "basket": {{ $b.* }}
    }};
''')

# ── Test 5: Subquery fetch (blocker → exceptions) ────────────
run_test("5. Subquery fetch (blocker -> exceptions)", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, blocker: $b) isa provision_has_blocker;
    fetch {{
        "blocker": {{ $b.* }},
        "exceptions": [
            match
                (blocker: $b, exception: $ex) isa blocker_has_exception;
                $ex isa! $etype;
            fetch {{
                "exception_type": $etype,
                "exception": {{ $ex.* }}
            }};
        ]
    }};
''')

# ── Test 6: Polymorphic relation match ────────────────────────
run_test("6. Polymorphic relation match", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        ($p, $e) isa $rel;
        $e isa! $etype;
    fetch {{
        "relation": $rel,
        "entity_type": $etype,
        "entity": {{ $e.* }}
    }};
''')

# ── Test 7: Relation hierarchy check ─────────────────────────
run_test("7. Relation hierarchy (provision_has_basket parent)", '''
    match
        relation $r;
        $r sub $parent;
        $r label provision_has_basket;
    select $r, $parent;
''')

# ── Test 8: Function in fetch ─────────────────────────────────
run_test("8. Function in fetch", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa ratio_basket;
    fetch {{
        "basket": {{ $b.* }},
        "capacity_components": [
            match
                let $amt in dividend_capacity_components("{PROVISION_ID}");
            fetch {{ "amount": $amt }};
        ]
    }};
''')

# ── Test 9A: Annotation join (hardcoded type) ────────────────
run_test("9A. Annotation join (hardcoded type)", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa! $btype;
    fetch {{
        "basket_type": $btype,
        "basket": {{ $b.* }},
        "annotations": [
            match
                (question: $q) isa question_annotates_attribute,
                    has target_entity_type "ratio_basket",
                    has target_attribute_name $an;
                $q has question_text $qt;
            fetch {{ "attribute": $an, "text": $qt }};
        ]
    }};
''')

# ── Test 9B: label() function ────────────────────────────────
run_test("9B. Annotation join (label function)", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa! $btype;
        let $type_name = label($btype);
    fetch {{
        "type_name": $type_name,
        "basket": {{ $b.* }}
    }};
''')

# ── Test 9C: Annotation with hardcoded entity type match ─────
run_test("9C. Annotation join (entity type match)", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa ratio_basket;
    fetch {{
        "basket": {{ $b.* }},
        "annotations": [
            match
                (question: $q) isa question_annotates_attribute,
                    has target_entity_type "ratio_basket",
                    has target_attribute_name $an;
                $q has question_text $qt;
            fetch {{ "attribute": $an, "annotation": $qt }};
        ]
    }};
''')

# ── Test 9D: Define + call annotation function ────────────────
print(f"\n{'='*60}")
print("TEST: 9D. Define annotation function")
print(f"{'='*60}")
schema_tx = driver.transaction(DB, TransactionType.SCHEMA)
try:
    schema_tx.query('''
        define
        fun get_entity_annotations($type_name: string) -> { target_attribute_name, question_text }:
            match
                (question: $q) isa question_annotates_attribute,
                    has target_entity_type $et,
                    has target_attribute_name $an;
                $et == $type_name;
                $q has question_text $qt;
            return { $an, $qt };
    ''').resolve()
    schema_tx.commit()
    print("Function get_entity_annotations defined successfully")
except Exception as e:
    print(f"Function definition failed: {e}")
    if schema_tx.is_open():
        schema_tx.close()

run_test("9D. Annotation via function", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa ratio_basket;
    fetch {{
        "basket": {{ $b.* }},
        "annotations": [
            match
                let $an, $qt in get_entity_annotations("ratio_basket");
            fetch {{ "attribute": $an, "annotation": $qt }};
        ]
    }};
''')

# ── Test 10: Full graph fetch ─────────────────────────────────
run_test("10. Full provision graph with annotations", f'''
    match
        $p isa rp_provision, has provision_id "{PROVISION_ID}";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b isa! $btype;
    fetch {{
        "entity_type": $btype,
        "attributes": {{ $b.* }},
        "children": [
            match
                (basket: $b, source: $s) isa basket_has_source;
                $s isa! $stype;
            fetch {{
                "source_type": $stype,
                "source": {{ $s.* }}
            }};
        ],
        "annotations": [
            match
                let $an, $qt in get_entity_annotations("ratio_basket");
            fetch {{ "attribute": $an, "annotation": $qt }};
        ]
    }};
''')

# ── Summary ───────────────────────────────────────────────────
print(f"\n\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
for name, status in results.items():
    flag = "PASS" if status == "PASS" else "FAIL"
    detail = "" if status == "PASS" else f"  ({status[:80]})"
    print(f"  {flag:6s}  {name}{detail}")
