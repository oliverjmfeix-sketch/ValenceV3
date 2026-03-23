"""Verify Prompt 3: parameterized graph traversal + metadata entity filter."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DEAL_ID = sys.argv[1] if len(sys.argv) > 1 else "8d0bf2f8"


def main():
    from app.services.typedb_client import typedb_client
    typedb_client.connect()

    print("=" * 60)
    print(f"Prompt 3 Verification — deal {DEAL_ID}")
    print("=" * 60)
    all_ok = True

    # ── 1. Test get_provision_entities for MFN ────────────────
    print("\n1. get_provision_entities (mfn_provision):")
    from app.services.graph_traversal import get_provision_entities
    mfn_docs, mfn_ctx = get_provision_entities(DEAL_ID, "mfn_provision")
    print(f"   MFN entities: {len(mfn_docs)}")
    if mfn_docs:
        types = sorted(set(d.get("type_name", "?") for d in mfn_docs))
        print(f"   Entity types: {types}")
        print(f"   Context length: {len(mfn_ctx)} chars")
        print(f"   [OK] MFN entities fetched via polymorphic query")
    else:
        print(f"   [FAIL] No MFN entities returned")
        all_ok = False

    # ── 2. Test get_provision_entities for RP ─────────────────
    print("\n2. get_provision_entities (rp_provision):")
    rp_docs, rp_ctx = get_provision_entities(DEAL_ID, "rp_provision")
    print(f"   RP entities: {len(rp_docs)}")
    if rp_docs:
        types = sorted(set(d.get("type_name", "?") for d in rp_docs))
        print(f"   Entity types: {types[:5]}{'...' if len(types) > 5 else ''}")
        print(f"   [OK] RP entities still work")
    else:
        print(f"   [WARN] No RP entities (may not have been extracted for this deal)")

    # ── 3. Test TopicRouter entity type filtering ─────────────
    print("\n3. TopicRouter.get_relevant_entity_types():")
    from app.services.topic_router import get_topic_router
    router = get_topic_router()

    test_questions = [
        ("What is the effective yield definition for MFN?", {"mfn_yield_definition"}),
        ("Which debt types are excluded from MFN?", {"mfn_exclusion"}),
        ("What is the MFN sunset period?", {"mfn_sunset_provision"}),
        ("What is the MFN freebie basket size?", {"mfn_freebie_basket"}),
    ]

    for question, expected_types in test_questions:
        result = router.route(question)
        relevant = router.get_relevant_entity_types(result.matched_categories)
        matched_cats = [c.category_id for c in result.matched_categories]
        has_expected = expected_types & relevant
        status = "OK" if has_expected else "MISS"
        print(f"   [{status}] \"{question[:50]}...\"")
        print(f"         covenant={result.covenant_type}, cats={matched_cats}")
        print(f"         relevant_types={sorted(relevant)[:6]}")
        if not has_expected:
            print(f"         Expected at least one of: {expected_types}")
            all_ok = False

    # ── 4. Test filtering on MFN docs ─────────────────────────
    print("\n4. Filter MFN docs by relevant types:")
    if mfn_docs:
        result = router.route("What is the effective yield definition?")
        relevant = router.get_relevant_entity_types(result.matched_categories)
        if relevant:
            filtered = [d for d in mfn_docs if d.get("type_name") in relevant]
            print(f"   Total MFN entities: {len(mfn_docs)}")
            print(f"   Relevant types: {sorted(relevant)}")
            print(f"   Filtered to: {len(filtered)} entities")
            filtered_types = sorted(set(d.get("type_name", "?") for d in filtered))
            print(f"   Filtered types: {filtered_types}")
            if len(filtered) < len(mfn_docs):
                print(f"   [OK] Filter reduced entities from {len(mfn_docs)} to {len(filtered)}")
            else:
                print(f"   [INFO] Filter kept all entities (types overlap)")
        else:
            print(f"   [INFO] No relevant types found — would use all entities (fallback)")

    # ── Summary ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_ok:
        print("ALL CHECKS PASSED")
    else:
        print("SOME CHECKS FAILED — review output above")
    print("=" * 60)


if __name__ == "__main__":
    main()
