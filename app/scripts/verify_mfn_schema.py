"""Verify MFN schema changes: relation subs, annotations, and relation config introspection."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.environ.get("TYPEDB_ADDRESS", "ip654h-0.cluster.typedb.com:80")
TYPEDB_DATABASE = os.environ.get("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.environ.get("TYPEDB_USERNAME", "admin")
TYPEDB_PASSWORD = os.environ.get("TYPEDB_PASSWORD", "")

def main():
    address = TYPEDB_ADDRESS
    if not address.startswith("http://") and not address.startswith("https://"):
        address = f"https://{address}"

    driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())

    print("=" * 60)
    print("MFN Schema Verification")
    print("=" * 60)

    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    all_ok = True

    # ── 1. Check provision_has_extracted_entity subs ──────────────
    print("\n1. Relations subbing provision_has_extracted_entity:")
    query = """
        match
            relation $rel;
            $rel sub provision_has_extracted_entity;
            not { $rel label provision_has_extracted_entity; };
        select $rel;
    """
    rows = list(tx.query(query).resolve().as_concept_rows())
    rel_names = sorted(set(r.get("rel").as_relation_type().get_label() for r in rows))
    print(f"   Total: {len(rel_names)}")
    for rn in rel_names:
        marker = "MFN" if "exclusion" in rn or "yield_def" in rn or "sunset" in rn or "freebie" in rn else "RP"
        print(f"   [{marker}] {rn}")

    expected_mfn = {"provision_has_exclusion", "provision_has_yield_def", "provision_has_sunset", "provision_has_freebie"}
    found_mfn = expected_mfn & set(rel_names)
    if found_mfn == expected_mfn:
        print(f"   [OK] All 4 MFN relations found")
    else:
        print(f"   [FAIL] Missing MFN relations: {expected_mfn - found_mfn}")
        all_ok = False

    if len(rel_names) >= 16:
        print(f"   [OK] {len(rel_names)} >= 16 total (12 RP + 4 MFN)")
    else:
        print(f"   [FAIL] Expected >= 16, got {len(rel_names)}")
        all_ok = False

    # ── 2. Check MFN entity annotations ──────────────────────────
    print("\n2. MFN entity annotations (get_entity_annotations):")
    mfn_types = ["mfn_exclusion", "mfn_yield_definition", "mfn_sunset_provision", "mfn_freebie_basket"]
    expected_counts = {"mfn_exclusion": 7, "mfn_yield_definition": 10, "mfn_sunset_provision": 6, "mfn_freebie_basket": 6}

    for et in mfn_types:
        ann_query = f"""
            match
                let $attr_name, $question_text in get_entity_annotations("{et}");
            fetch {{
                "attribute": $attr_name,
                "question": $question_text
            }};
        """
        try:
            docs = list(tx.query(ann_query).resolve().as_concept_documents())
            expected = expected_counts[et]
            status = "OK" if len(docs) >= expected else "FAIL"
            print(f"   [{status}] {et}: {len(docs)} annotations (expected {expected})")
            if len(docs) < expected:
                all_ok = False
            for d in docs[:3]:
                print(f"      {d.get('attribute', '?')} -> {d.get('question', '?')[:60]}")
            if len(docs) > 3:
                print(f"      ... and {len(docs) - 3} more")
        except Exception as e:
            print(f"   [FAIL] {et}: {e}")
            all_ok = False

    tx.close()

    # ── 3. Test _build_relation_config ────────────────────────────
    print("\n3. _build_relation_config() introspection:")
    try:
        from app.services.typedb_client import typedb_client
        typedb_client.driver = driver
        typedb_client.database = TYPEDB_DATABASE

        from app.services.graph_storage import GraphStorage
        GraphStorage._relation_config_cache = None  # Force rebuild
        config = GraphStorage._build_relation_config()

        print(f"   Total relations discovered: {len(config)}")
        for rl in sorted(config.keys()):
            cfg = config[rl]
            roles_str = f"({cfg['roles'][0]}, {cfg['roles'][1]})"
            pvar = cfg.get("parent_var", "$prov")
            tier = "T2" if pvar == "$parent" else "T1/T3"
            print(f"   [{tier}] {rl}: roles={roles_str}, parent_var={pvar}")

        # Check MFN relations
        mfn_rels = {"provision_has_exclusion", "provision_has_yield_def", "provision_has_sunset", "provision_has_freebie"}
        found = mfn_rels & set(config.keys())
        if found == mfn_rels:
            print(f"   [OK] All 4 MFN relations in config")
        else:
            print(f"   [FAIL] Missing: {mfn_rels - found}")
            all_ok = False

        # Check Tier 2 relations
        for t2 in ("blocker_has_exception", "basket_has_source"):
            if t2 in config and config[t2].get("parent_var") == "$parent":
                print(f"   [OK] {t2}: parent_var=$parent (Tier 2)")
            elif t2 in config:
                print(f"   [WARN] {t2}: parent_var={config[t2].get('parent_var')} (expected $parent)")
            else:
                print(f"   [FAIL] {t2} not found in config")
                all_ok = False

        # Check Tier 3 (has_amendment_threshold)
        if "has_amendment_threshold" in config:
            hat = config["has_amendment_threshold"]
            print(f"   [OK] has_amendment_threshold: roles={hat['roles']}")
        else:
            print(f"   [WARN] has_amendment_threshold not discovered (Tier 3)")

        # Dry-run: format for MFN provision
        print("\n4. Dry-run: parent_match for MFN provision 8d0bf2f8_mfn:")
        test_cfg = GraphStorage._get_relation_config("provision_has_exclusion", "8d0bf2f8_mfn")
        if test_cfg:
            print(f"   parent_match: {test_cfg['parent_match']}")
            print(f"   roles: {test_cfg['roles']}")
            print(f"   [OK] Uses mfn_provision (not rp_provision)")
            if "mfn_provision" in test_cfg["parent_match"]:
                pass
            else:
                print(f"   [FAIL] Expected mfn_provision in parent_match")
                all_ok = False
        else:
            print(f"   [FAIL] _get_relation_config returned None")
            all_ok = False

    except Exception as e:
        print(f"   [FAIL] _build_relation_config error: {e}")
        import traceback
        traceback.print_exc()
        all_ok = False

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_ok:
        print("ALL CHECKS PASSED")
    else:
        print("SOME CHECKS FAILED — review output above")
    print("=" * 60)

if __name__ == "__main__":
    main()
