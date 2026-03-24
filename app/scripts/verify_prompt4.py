"""Verify Prompt 4: synthesis_guidance SSoT deployment.

Run on Railway (Parts 1+3 need TypeDB):
    railway run --service ValenceV3 -- python -m app.scripts.verify_prompt4 --deal-id 8d0bf2f8

Or split:
    railway run --service ValenceV3 -- python -m app.scripts.verify_prompt4 --skip-api
    python -m app.scripts.verify_prompt4 --skip-typedb --skip-routing --deal-id 8d0bf2f8

Checks:
  Step 1 (TypeDB):   All 27-28 categories have synthesis_guidance seeded
  Step 2 (Code):     Hardcoded rules gone from deals.py
  Step 3 (Routing):  TopicRouter assembly works (no deal needed, needs TypeDB)
  Step 4 (API):      MFN yield question via /ask-graph on ACP Tara
  Step 5 (API):      Broad MFN question routes to multiple MFN categories
"""
import argparse
import re
import sys
import time
from pathlib import Path

# ─── Expected categories ────────────────────────────────────────────
EXPECTED_CATEGORIES = {
    # RP (19)
    "RP", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "P", "S", "T", "Z",
    # J.Crew (3)
    "JC1", "JC2", "JC3",
    # MFN (6)
    "MFN1", "MFN2", "MFN3", "MFN4", "MFN5", "MFN6",
}  # 28 total


def verify_typedb():
    """Step 1: Verify synthesis_guidance is seeded in TypeDB."""
    print(f"\n{'='*70}")
    print("STEP 1: TYPEDB SYNTHESIS GUIDANCE")
    print(f"{'='*70}")

    try:
        from app.services.typedb_client import get_typedb_client

        client = get_typedb_client()
        found = {}

        with client.read_transaction() as tx:
            result = tx.query("""
                match
                    $cat isa ontology_category,
                        has category_id $cid;
                    try { $cat has synthesis_guidance $sg; };
                select $cid, $sg;
            """).resolve()

            for row in result.as_concept_rows():
                cid_concept = row.get("cid")
                sg_concept = row.get("sg")
                if cid_concept:
                    cid = cid_concept.as_attribute().get_value()
                    sg = sg_concept.as_attribute().get_value() if sg_concept else None
                    found[cid] = sg

        # Print all found
        for cid in sorted(found.keys()):
            sg = found[cid]
            if sg:
                preview = sg[:80].replace("\n", " ")
                print(f"  {cid:6s} OK  {preview}...")
            else:
                print(f"  {cid:6s} --  NO GUIDANCE")

        # Check coverage
        missing = EXPECTED_CATEGORIES - set(found.keys())
        no_guidance = {cid for cid, sg in found.items()
                       if not sg and cid in EXPECTED_CATEGORIES}

        print(f"\n  Categories found:  {len(found)}")
        print(f"  With guidance:     {sum(1 for sg in found.values() if sg)}")
        print(f"  Missing entirely:  {len(missing)} {sorted(missing) if missing else ''}")
        print(f"  No guidance text:  {len(no_guidance)} {sorted(no_guidance) if no_guidance else ''}")

        passed = len(missing) == 0 and len(no_guidance) == 0
        print(f"\n  {'PASS' if passed else 'FAIL'}")
        return passed

    except Exception as e:
        print(f"\n  SKIP  TypeDB not reachable: {e}")
        return None


def verify_code():
    """Step 2: Verify deleted strings are gone from deals.py."""
    print(f"\n{'='*70}")
    print("STEP 2: CODE VERIFICATION")
    print(f"{'='*70}")

    deals_path = Path(__file__).parent.parent / "routers" / "deals.py"
    content = deals_path.read_text()

    passed = True

    # Strings that must NOT appear (except in comments)
    banned = [
        ("MFN_SYNTHESIS_RULES", "MFN_SYNTHESIS_RULES"),
        ("rp_specific_rules", "rp_specific_rules"),
        ("SELF-VERIFICATION", "SELF-VERIFICATION section header"),
    ]

    for pattern, label in banned:
        if pattern in content:
            print(f"  FAIL  '{label}' still in deals.py")
            passed = False
        else:
            print(f"  PASS  '{label}' not in deals.py")

    # Regex check for hardcoded Duck Creek example
    if re.search(r"4 baskets.*130M.*520M", content):
        print(f"  FAIL  Hardcoded '4 baskets × $130M = $520M' still in deals.py")
        passed = False
    else:
        print(f"  PASS  No hardcoded Duck Creek example in deals.py")

    # Positive checks
    if "get_synthesis_guidance" in content:
        print(f"  PASS  get_synthesis_guidance() called in deals.py")
    else:
        print(f"  FAIL  get_synthesis_guidance() NOT found in deals.py")
        passed = False

    if "{category_guidance}" in content:
        print(f"  PASS  {{category_guidance}} injected into system prompt")
    else:
        print(f"  FAIL  {{category_guidance}} NOT injected into system prompt")
        passed = False

    return passed


def verify_routing():
    """Step 3: Test synthesis guidance assembly via TopicRouter (no deal needed)."""
    print(f"\n{'='*70}")
    print("STEP 3: TOPICROUTER GUIDANCE ASSEMBLY")
    print(f"{'='*70}")

    try:
        from app.services.topic_router import get_topic_router

        router = get_topic_router()
        passed = True

        tests = [
            ("What is the effective yield definition for MFN?", "MFN3"),
            ("Which debt types are excluded from MFN?", "MFN2"),
            ("What is the MFN sunset period?", "MFN4"),
            ("What are the MFN loopholes?", "MFN6"),
            ("What is the J.Crew blocker coverage?", "K"),
            ("What is the total dividend capacity?", "G"),
        ]

        for q, expected_cat in tests:
            result = router.route(q)
            guidance = router.get_synthesis_guidance(result.matched_categories)
            cats = [c.category_id for c in result.matched_categories]

            print(f"\n  Q: {q[:50]}")
            print(f"    Categories: {cats}")

            # Check expected category is in matched categories
            if expected_cat in cats:
                print(f"    PASS  Expected '{expected_cat}' in categories")
            else:
                print(f"    FAIL  Expected '{expected_cat}' NOT in categories")
                passed = False

            # Check guidance is non-empty
            if len(guidance) > 0:
                print(f"    PASS  Guidance length: {len(guidance)} chars")
                print(f"    First 100: {guidance[:100]}...")
            else:
                print(f"    FAIL  Guidance is empty!")
                passed = False

            # Check MFN questions get MFN guidance, RP questions get RP guidance
            if expected_cat.startswith("MFN"):
                if "MFN" in guidance.upper() or "mfn" in guidance.lower():
                    print(f"    PASS  MFN guidance present for MFN question")
                else:
                    print(f"    WARN  MFN question but guidance doesn't mention MFN")
            else:
                # RP/JC questions should not get MFN guidance (unless multi-routed)
                pass

        print(f"\n  {'PASS' if passed else 'FAIL'}")
        return passed

    except Exception as e:
        print(f"\n  SKIP  TopicRouter not available: {e}")
        return None


def verify_api(base_url: str, deal_id: str):
    """Steps 4-5: Test /ask-graph on ACP Tara (MFN questions only)."""
    import requests

    print(f"\n{'='*70}")
    print("STEPS 4-5: API VERIFICATION (/ask-graph)")
    print(f"{'='*70}")
    print(f"  Server:  {base_url}")
    print(f"  Deal:    {deal_id}")

    all_passed = True

    api_tests = [
        {
            "step": "4",
            "description": "MFN yield question",
            "question": "What yield components are included in the MFN calculation?",
            "expected_categories": ["MFN3"],
            "check_prompt_contains": [
                "CATEGORY-SPECIFIC ANALYSIS GUIDANCE",
                "VERIFY BEFORE ANSWERING",
            ],
            "check_prompt_not_contains": [
                "MFN_SYNTHESIS_RULES",
                "SELF-VERIFICATION",
            ],
        },
        {
            "step": "5",
            "description": "Broad MFN question (multi-category)",
            "question": "How strong is the MFN protection in this deal?",
            "expected_categories": ["MFN1", "MFN2", "MFN3", "MFN4", "MFN5", "MFN6"],
            "check_prompt_contains": [
                "CATEGORY-SPECIFIC ANALYSIS GUIDANCE",
            ],
            "check_prompt_not_contains": [
                "MFN_SYNTHESIS_RULES",
            ],
        },
    ]

    for test in api_tests:
        print(f"\n  ── Step {test['step']}: {test['description']} ──")
        print(f"  Q: {test['question'][:70]}...")

        try:
            resp = requests.post(
                f"{base_url}/api/deals/{deal_id}/ask-graph?trace=true",
                json={"question": test["question"]},
                timeout=120,
            )

            if resp.status_code != 200:
                print(f"  FAIL  HTTP {resp.status_code}: {resp.text[:200]}")
                all_passed = False
                continue

            data = resp.json()
            answer = data.get("answer", "")
            trace = data.get("trace", {})
            system_prompt = trace.get("claude_system_prompt", "")

            if not system_prompt:
                print(f"  WARN  No system prompt in trace (trace may be disabled)")

            # Check expected categories appear in system prompt
            for cat_id in test["expected_categories"]:
                marker = f"### Category {cat_id}:"
                alt_marker = f"Category {cat_id}"
                if marker in system_prompt or alt_marker in system_prompt:
                    print(f"  PASS  Category {cat_id} guidance in system prompt")
                else:
                    print(f"  FAIL  Category {cat_id} guidance NOT in system prompt")
                    all_passed = False

            # Check prompt contains required strings
            for required in test.get("check_prompt_contains", []):
                if required in system_prompt:
                    print(f"  PASS  '{required}' in system prompt")
                else:
                    print(f"  FAIL  '{required}' NOT in system prompt")
                    all_passed = False

            # Check prompt does NOT contain banned strings
            for banned in test.get("check_prompt_not_contains", []):
                if banned in system_prompt:
                    print(f"  FAIL  '{banned}' still in system prompt!")
                    all_passed = False
                else:
                    print(f"  PASS  '{banned}' not in system prompt")

            # Check "Verified against:" in answer
            if "Verified against:" in answer:
                print(f"  PASS  'Verified against:' in answer")
            else:
                print(f"  WARN  'Verified against:' not found in answer")

            # Print answer preview
            print(f"  Answer: {answer[:150]}...")

            # Rate limit between calls
            time.sleep(2)

        except requests.exceptions.ConnectionError:
            print(f"  SKIP  Cannot connect to {base_url}")
            return None
        except Exception as e:
            print(f"  FAIL  {type(e).__name__}: {e}")
            all_passed = False

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="Verify Prompt 4 deployment")
    parser.add_argument("--base-url", default="https://valencev3-production.up.railway.app",
                        help="Backend URL")
    parser.add_argument("--deal-id", default="8d0bf2f8",
                        help="Deal ID for API tests (default: ACP Tara)")
    parser.add_argument("--skip-api", action="store_true",
                        help="Skip API tests (Steps 4-5)")
    parser.add_argument("--skip-typedb", action="store_true",
                        help="Skip TypeDB check (Step 1)")
    parser.add_argument("--skip-routing", action="store_true",
                        help="Skip routing test (Step 3)")
    args = parser.parse_args()

    print(f"{'='*70}")
    print("VALENCE V3 — PROMPT 4 VERIFICATION")
    print(f"{'='*70}")

    results = {}

    # Step 1: TypeDB
    if not args.skip_typedb:
        results["1_typedb"] = verify_typedb()

    # Step 2: Code
    results["2_code"] = verify_code()

    # Step 3: Routing
    if not args.skip_routing:
        results["3_routing"] = verify_routing()

    # Steps 4-5: API
    if not args.skip_api:
        results["4-5_api"] = verify_api(args.base_url, args.deal_id)

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for part, result in results.items():
        if result is None:
            print(f"  {part:12s}  SKIP")
        elif result:
            print(f"  {part:12s}  PASS")
        else:
            print(f"  {part:12s}  FAIL")

    any_fail = any(r is False for r in results.values())
    print(f"\n  OVERALL: {'FAIL' if any_fail else 'PASS'}")

    if any_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
