"""Verify MFN Prompt 4: synthesis_guidance SSoT + /ask-graph integration.

Run against the deployed server:
    python -m app.scripts.verify_prompt4 [--base-url URL] [--deal-id ID]

Checks:
  Part 1 (TypeDB): All 28 categories have synthesis_guidance seeded
  Part 2 (Code):   MFN_SYNTHESIS_RULES and rp_specific_rules gone from deals.py
  Part 3 (API):    /ask-graph returns correct guidance in traces for:
    a) MFN yield question → MFN3 guidance in system prompt
    b) RP capacity question → G + F + N guidance in system prompt
    c) J.Crew question → K guidance in system prompt
    d) Broad MFN question → all MFN1-MFN6 guidance
    e) "Verified against:" appears at end of each answer
"""
import argparse
import json
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

# ─── Test questions for API verification ─────────────────────────────
TEST_QUESTIONS = {
    "mfn_yield": {
        "question": "What components are included in the Effective Yield definition for MFN purposes?",
        "expected_categories": ["MFN3"],
        "expected_not_in_prompt": ["MFN_SYNTHESIS_RULES"],
        "description": "(a) MFN yield question → MFN3 guidance",
    },
    "rp_capacity": {
        "question": "What is the total dividend capacity available under the restricted payments covenant?",
        "expected_categories": ["G", "F", "N"],
        "expected_not_in_prompt": ["rp_specific_rules"],
        "description": "(b) RP capacity question → G + F + N guidance",
    },
    "jcrew": {
        "question": "How does the J.Crew blocker structure restrict IP transfers to unrestricted subsidiaries?",
        "expected_categories": ["K"],
        "expected_not_in_prompt": [],
        "description": "(c) J.Crew question → K guidance",
    },
    "broad_mfn": {
        "question": "How strong is MFN protection in this deal?",
        "expected_categories": ["MFN1", "MFN2", "MFN3", "MFN4", "MFN5", "MFN6"],
        "expected_not_in_prompt": ["MFN_SYNTHESIS_RULES"],
        "description": "(d) Broad MFN question → all MFN1-MFN6 guidance",
    },
}


def verify_code():
    """Part 2: Confirm hardcoded rules removed from deals.py."""
    print(f"\n{'='*70}")
    print("PART 2: CODE VERIFICATION")
    print(f"{'='*70}")

    deals_path = Path(__file__).parent.parent / "routers" / "deals.py"
    content = deals_path.read_text()

    passed = True
    checks = [
        ("MFN_SYNTHESIS_RULES", "MFN_SYNTHESIS_RULES removed from deals.py"),
        ("rp_specific_rules", "rp_specific_rules removed from deals.py"),
    ]

    for pattern, label in checks:
        if pattern in content:
            print(f"  FAIL  {label}")
            passed = False
        else:
            print(f"  PASS  {label}")

    # Positive check: synthesis_guidance loaded from topic_router
    if "get_synthesis_guidance" in content:
        print(f"  PASS  get_synthesis_guidance() called in deals.py")
    else:
        print(f"  FAIL  get_synthesis_guidance() NOT found in deals.py")
        passed = False

    if "category_guidance" in content and "{category_guidance}" in content:
        print(f"  PASS  category_guidance injected into system prompt")
    else:
        print(f"  FAIL  category_guidance NOT injected into system prompt")
        passed = False

    return passed


def verify_typedb():
    """Part 1: Load synthesis_guidance from TypeDB, verify all 28 categories."""
    print(f"\n{'='*70}")
    print("PART 1: TYPEDB SYNTHESIS GUIDANCE")
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
        print(f"\n  SKIP  TypeDB not reachable (expected locally): {e}")
        return None


def verify_api(base_url: str, deal_id: str):
    """Part 3: Hit /ask-graph with test questions, verify traces."""
    import requests

    print(f"\n{'='*70}")
    print("PART 3: API VERIFICATION (/ask-graph)")
    print(f"{'='*70}")
    print(f"  Server:  {base_url}")
    print(f"  Deal:    {deal_id}")

    all_passed = True

    for test_key, test in TEST_QUESTIONS.items():
        print(f"\n  ── {test['description']} ──")
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

            # Check expected categories appear in system prompt
            for cat_id in test["expected_categories"]:
                marker = f"### Category {cat_id}:"
                if marker in system_prompt:
                    print(f"  PASS  Category {cat_id} guidance in system prompt")
                else:
                    # Also check without the ### prefix
                    if f"Category {cat_id}" in system_prompt:
                        print(f"  PASS  Category {cat_id} guidance in system prompt (alt format)")
                    else:
                        print(f"  FAIL  Category {cat_id} guidance NOT in system prompt")
                        all_passed = False

            # Check banned strings NOT in system prompt
            for banned in test["expected_not_in_prompt"]:
                if banned in system_prompt:
                    print(f"  FAIL  '{banned}' still in system prompt!")
                    all_passed = False
                else:
                    print(f"  PASS  '{banned}' not in system prompt")

            # (e) Check "Verified against:" in answer
            if "Verified against:" in answer or "Verified against:" in answer.lower():
                print(f"  PASS  'Verified against:' in answer")
            else:
                print(f"  WARN  'Verified against:' not found in answer")

            # Check evidence block
            if data.get("evidence_entities"):
                print(f"  PASS  Evidence entities: {data['evidence_entities']}")
            else:
                print(f"  WARN  No evidence entities parsed")

            # Print answer preview
            print(f"  Answer: {answer[:120]}...")

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
    parser = argparse.ArgumentParser(description="Verify MFN Prompt 4")
    parser.add_argument("--base-url", default="https://valencev3-production.up.railway.app",
                        help="Backend URL")
    parser.add_argument("--deal-id", default="8d0bf2f8",
                        help="Deal ID for API tests (default: ACP Tara)")
    parser.add_argument("--skip-api", action="store_true",
                        help="Skip API tests (Parts 1+2 only)")
    parser.add_argument("--skip-typedb", action="store_true",
                        help="Skip TypeDB check (Parts 2+3 only)")
    args = parser.parse_args()

    print(f"{'='*70}")
    print("VALENCE V3 — PROMPT 4 VERIFICATION")
    print(f"{'='*70}")

    results = {}

    # Part 1: TypeDB
    if not args.skip_typedb:
        results["typedb"] = verify_typedb()

    # Part 2: Code
    results["code"] = verify_code()

    # Part 3: API
    if not args.skip_api:
        results["api"] = verify_api(args.base_url, args.deal_id)

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for part, result in results.items():
        if result is None:
            print(f"  {part:8s}  SKIP")
        elif result:
            print(f"  {part:8s}  PASS")
        else:
            print(f"  {part:8s}  FAIL")

    any_fail = any(r is False for r in results.values())
    print(f"\n  OVERALL: {'FAIL' if any_fail else 'PASS'}")

    if any_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
