#!/usr/bin/env python3
"""
TopicRouter SSoT Compliance Test Suite

Tests the new TopicRouter against the deployed Railway backend to verify:
1. Metadata loads correctly from TypeDB
2. Question routing is accurate
3. Covenant type detection works
4. Zero hardcoded mappings (file-based grep checks)
5. Integration with /ask endpoint

Usage:
    python test_topic_router.py                          # all tests
    python test_topic_router.py --grep-only              # only file grep checks (no backend needed)
    python test_topic_router.py --url http://localhost:8000  # custom backend URL
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Configuration ─────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://valencev3-production.up.railway.app"
APP_DIR = Path(__file__).parent / "app"
TOPIC_ROUTER_PATH = APP_DIR / "services" / "topic_router.py"
DEALS_PATH = APP_DIR / "routers" / "deals.py"

# ── Test result tracking ──────────────────────────────────────────────

class TestResults:
    def __init__(self):
        self.categories_loaded = 0
        self.questions_mapped = 0
        self.target_fields_mapped = 0

        self.rp_correct = 0
        self.rp_total = 0
        self.mfn_correct = 0
        self.mfn_total = 0
        self.cross_correct = 0
        self.cross_total = 0
        self.edge_correct = 0
        self.edge_total = 0

        self.no_hardcoded_fields = None     # True/False
        self.no_hardcoded_categories = None
        self.no_hardcoded_keywords = None

        self.integration_tested = 0
        self.integration_passed = 0

        self.failures: List[str] = []
        self.typedb_connected = False
        self.backend_reachable = False

results = TestResults()


def fail(msg: str):
    results.failures.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str):
    print(f"  OK:   {msg}")

# ── HTTP helpers ──────────────────────────────────────────────────────

def http_get(url: str, timeout: int = 15) -> Optional[dict]:
    """GET request using httpx or fallback to urllib."""
    try:
        import httpx
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  HTTP GET {url} failed: {e}")
        return None


def http_post(url: str, body: dict, timeout: int = 60) -> Optional[dict]:
    """POST JSON request using httpx or fallback to urllib."""
    try:
        import httpx
        resp = httpx.post(url, json=body, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except ImportError:
        pass
    try:
        import urllib.request
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  HTTP POST {url} failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# STEP 4: SSoT compliance grep checks (file-based, no backend needed)
# ══════════════════════════════════════════════════════════════════════

def run_step4_grep_checks():
    print("\n" + "=" * 60)
    print("STEP 4: SSoT Compliance — Grep Checks")
    print("=" * 60)

    # Check 4a: No hardcoded field names in topic_router.py
    print("\n4a. No hardcoded field names in topic_router.py")
    patterns_fields = [
        "builder_basket", "jcrew_blocker", "mfn_threshold",
        "ratio_basket", "dividend_prohibition", "tax_distribution",
        "management_equity",
    ]
    field_hits = _grep_file(TOPIC_ROUTER_PATH, patterns_fields)
    if field_hits:
        results.no_hardcoded_fields = False
        for line_num, line in field_hits:
            fail(f"  topic_router.py:{line_num}: {line.strip()}")
    else:
        results.no_hardcoded_fields = True
        ok("Zero hardcoded field names found")

    # Check 4b: No hardcoded category IDs in topic_router.py
    print("\n4b. No hardcoded category IDs in topic_router.py")
    # Look for quoted category IDs like "F", "G", "JC1", "MFN1" etc.
    cat_hits = _grep_file_regex(
        TOPIC_ROUTER_PATH,
        r'''["'](F|G|H|I|J|K|L|M|N|S|T|Z|JC1|JC2|JC3|MFN\d)["']''',
    )
    if cat_hits:
        results.no_hardcoded_categories = False
        for line_num, line in cat_hits:
            fail(f"  topic_router.py:{line_num}: {line.strip()}")
    else:
        results.no_hardcoded_categories = True
        ok("Zero hardcoded category IDs found")

    # Check 4c: No hardcoded keyword lists in deals.py production paths
    print("\n4c. No hardcoded keyword lists in deals.py production paths")
    keyword_patterns = ["mfn_signals", "rp_signals", "keyword_map", "_identify_relevant"]
    kw_hits = _grep_file(DEALS_PATH, keyword_patterns)
    # Filter: allow matches in comments/docstrings
    active_hits = []
    for line_num, line in kw_hits:
        stripped = line.strip()
        # Skip comments and docstrings
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Skip lines that are inside a docstring context (heuristic: contains "DEPRECATED" or "Replaces")
        if "DEPRECATED" in line or "Replaces" in line or "SSoT" in line:
            continue
        active_hits.append((line_num, line))

    if active_hits:
        results.no_hardcoded_keywords = False
        for line_num, line in active_hits:
            fail(f"  deals.py:{line_num}: {line.strip()}")
    else:
        results.no_hardcoded_keywords = True
        ok("Zero hardcoded keyword lists in production paths")


def _grep_file(filepath: Path, patterns: List[str]) -> List[Tuple[int, str]]:
    """Search a file for any of the given substring patterns."""
    hits = []
    if not filepath.exists():
        fail(f"File not found: {filepath}")
        return hits
    content = filepath.read_text(encoding="utf-8")
    for i, line in enumerate(content.splitlines(), 1):
        for pat in patterns:
            if pat in line.lower():
                hits.append((i, line))
                break  # one hit per line
    return hits


def _grep_file_regex(filepath: Path, pattern: str) -> List[Tuple[int, str]]:
    """Search a file for a regex pattern."""
    hits = []
    if not filepath.exists():
        fail(f"File not found: {filepath}")
        return hits
    content = filepath.read_text(encoding="utf-8")
    regex = re.compile(pattern)
    for i, line in enumerate(content.splitlines(), 1):
        # Skip comments and strings used in examples/docs
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if regex.search(line):
            hits.append((i, line))
    return hits


# ══════════════════════════════════════════════════════════════════════
# STEP 1: Verify TopicRouter loads metadata from TypeDB
# ══════════════════════════════════════════════════════════════════════

def run_step1_metadata(base_url: str):
    print("\n" + "=" * 60)
    print("STEP 1: Verify TopicRouter Loads Metadata from TypeDB")
    print("=" * 60)

    # Use the SSoT status endpoint to verify TypeDB is alive
    print("\nChecking backend health...")
    health = http_get(f"{base_url}/api/health")
    if not health:
        fail("Backend not reachable")
        return
    results.backend_reachable = True
    print(f"  Backend status: {health.get('status')}")
    print(f"  TypeDB connected: {health.get('typedb_connected')}")

    if not health.get("typedb_connected"):
        fail("TypeDB not connected on backend")
        return
    results.typedb_connected = True

    # Use ssot-status to get counts
    ssot = http_get(f"{base_url}/api/admin/ssot-status")
    if ssot:
        print(f"\n  SSoT Status:")
        print(f"    Segment types:       {ssot.get('segment_types', '?')}")
        print(f"    Extraction metadata: {ssot.get('extraction_metadata', '?')}")
        print(f"    Ontology questions:  {ssot.get('ontology_questions', '?')}")
        print(f"    Ontology categories: {ssot.get('ontology_categories', '?')}")
        print(f"    Legal concepts:      {ssot.get('legal_concepts', '?')}")
        print(f"    SSoT compliant:      {ssot.get('ssot_compliant', '?')}")

        results.categories_loaded = ssot.get("ontology_categories", 0)
        results.questions_mapped = ssot.get("ontology_questions", 0)
    else:
        fail("Could not fetch SSoT status")

    # Use debug/schema-check for more detail
    schema = http_get(f"{base_url}/api/debug/schema-check")
    if schema:
        rp_q = schema.get("rp_question_count", 0)
        with_cat = schema.get("questions_with_category_count", 0)
        print(f"\n  Schema Check:")
        print(f"    RP questions:        {rp_q}")
        print(f"    Questions w/category:{with_cat}")
        for key in sorted(schema.keys()):
            if key.startswith("category_") and key.endswith("_question_count"):
                cat = key.replace("category_", "").replace("_question_count", "")
                print(f"    Category {cat} questions: {schema[key]}")

    # Now test the TopicRouter indirectly by asking a question and inspecting
    # the routed_categories in the response.
    # We need a deal for the /ask endpoint — we'll check in Step 5.
    # For now, we just log what we can from the SSoT counts.

    if results.categories_loaded < 10:
        fail(f"Only {results.categories_loaded} categories loaded (expected >= 10)")
    else:
        ok(f"{results.categories_loaded} categories loaded from TypeDB")

    if results.questions_mapped < 50:
        fail(f"Only {results.questions_mapped} questions mapped (expected >= 50)")
    else:
        ok(f"{results.questions_mapped} questions mapped from TypeDB")


# ══════════════════════════════════════════════════════════════════════
# STEPS 2 & 3: Test routing accuracy + covenant type detection
# ══════════════════════════════════════════════════════════════════════

# Test case format: (question, expected_covenant_type, expected_category_ids, description)
# expected_category_ids: list of category IDs where at least ONE must appear in results

# NOTE on category IDs (from TypeDB seed data, NOT hardcoded in topic_router.py):
# A=Dividend Restrictions, B=Intercompany Dividends, C=Management Equity Basket,
# D=Tax Distribution Basket, E=Equity Awards, F=Builder Basket, G=Ratio Dividend,
# H=Holding Company Overhead, I=Basket Reallocation, J=Unrestricted Subsidiaries,
# K=J.Crew Blocker, L/M/N=Expanded categories, S=RDP General, T=RDP Baskets,
# Z=Pattern Detection, JC1-3=J.Crew Tiers, MFN1-6=MFN categories

RP_TEST_CASES = [
    (
        "What is the builder basket starter amount?",
        "rp",
        ["F"],
        "Builder basket → category F (Builder Basket / Cumulative Amount)",
    ),
    (
        "Does the deal have a J.Crew blocker?",
        "rp",
        ["K", "JC1", "JC2", "JC3"],
        "J.Crew blocker → K or JC categories",
    ),
    (
        "What is the tax distribution basket?",
        "rp",
        ["D"],
        "Tax distribution → category D (Tax Distribution Basket)",
    ),
    (
        "Is there a ratio basket for dividends?",
        "rp",
        ["G"],
        "Ratio basket → category G (Ratio-Based Dividend Basket)",
    ),
    (
        "How does basket reallocation work?",
        "rp",
        ["I"],
        "Reallocation → category I (Basket Reallocation)",
    ),
    (
        "What is the management equity basket cap?",
        "rp",
        ["C"],
        "Management equity → category C (Management Equity Basket)",
    ),
    (
        "What is the total quantifiable dividend capacity?",
        "rp",
        ["N", "F", "G", "A"],
        "Dividend capacity → multiple RP categories",
    ),
]

MFN_TEST_CASES = [
    (
        "What is the MFN threshold in basis points?",
        None,  # may be "mfn" or "both" since "threshold" appears in RP fields too
        ["MFN1"],
        "MFN threshold → MFN1 (may include RP cats due to 'threshold' keyword overlap)",
    ),
    (
        "Does the MFN have a sunset provision?",
        "mfn",
        ["MFN4"],
        "MFN sunset → MFN4 (Sunset & Timing)",
    ),
    (
        "Is OID included in the MFN yield calculation?",
        None,  # may be "mfn" or "both"
        ["MFN3"],
        "OID yield → MFN3 (Yield Mechanics)",
    ),
    (
        "Which facility types are excluded from MFN?",
        "mfn",
        ["MFN2"],
        "MFN facility scope → MFN2 (Facility & Debt Scope)",
    ),
]

CROSS_TEST_CASES = [
    (
        "Summarize the restricted payments and MFN provisions",
        "both",
        [],  # broad match, no specific category required
        "Cross-covenant question → 'both'",
    ),
    (
        "What are the most borrower-friendly provisions?",
        "both",
        [],
        "Broad question → 'both'",
    ),
]

EDGE_TEST_CASES = [
    (
        "hello",
        "both",
        [],
        "Gibberish → fallback 'both'",
    ),
    (
        "What is EBITDA?",
        None,  # any result is fine
        [],
        "Generic financial term → graceful handling",
    ),
    (
        "",
        "both",
        [],
        "Empty string → fallback 'both'",
    ),
]


def run_steps2_3_routing(base_url: str, deal_id: Optional[str]):
    """Test routing accuracy by calling /ask and inspecting routed_categories."""
    print("\n" + "=" * 60)
    print("STEPS 2 & 3: Routing Accuracy + Covenant Type Detection")
    print("=" * 60)

    if not deal_id:
        print("\n  SKIP: No deal available to test routing via /ask endpoint.")
        print("  Routing tests require an extracted deal to call POST /ask.")
        # Still count as tested so we can report partial results
        results.rp_total = len(RP_TEST_CASES)
        results.mfn_total = len(MFN_TEST_CASES)
        results.cross_total = len(CROSS_TEST_CASES)
        results.edge_total = len(EDGE_TEST_CASES)
        return

    ask_url = f"{base_url}/api/deals/{deal_id}/ask"

    # RP questions
    print(f"\n--- RP Questions (expected covenant_type='rp') ---")
    results.rp_total = len(RP_TEST_CASES)
    for question, exp_type, exp_cats, desc in RP_TEST_CASES:
        _test_routing(ask_url, question, exp_type, exp_cats, desc, "rp")

    # MFN questions
    print(f"\n--- MFN Questions (expected covenant_type='mfn') ---")
    results.mfn_total = len(MFN_TEST_CASES)
    for question, exp_type, exp_cats, desc in MFN_TEST_CASES:
        _test_routing(ask_url, question, exp_type, exp_cats, desc, "mfn")

    # Cross-covenant questions
    print(f"\n--- Cross-Covenant Questions (expected covenant_type='both') ---")
    results.cross_total = len(CROSS_TEST_CASES)
    for question, exp_type, exp_cats, desc in CROSS_TEST_CASES:
        _test_routing(ask_url, question, exp_type, exp_cats, desc, "cross")

    # Edge cases
    print(f"\n--- Edge Cases ---")
    results.edge_total = len(EDGE_TEST_CASES)
    for question, exp_type, exp_cats, desc in EDGE_TEST_CASES:
        _test_routing(ask_url, question, exp_type, exp_cats, desc, "edge")


def _test_routing(
    ask_url: str,
    question: str,
    exp_type: Optional[str],
    exp_cats: List[str],
    desc: str,
    group: str,
):
    """Call /ask with a question and check routed_categories + covenant_type."""
    display_q = question[:60] if question else "(empty)"
    print(f"\n  Q: \"{display_q}\"")
    print(f"     Expect: type={exp_type or 'any'}, cats={exp_cats or 'any'}")

    data = http_post(ask_url, {"question": question}, timeout=60)
    if data is None:
        # /ask may reject empty strings with 400
        if question == "":
            # Expected — empty question should be handled gracefully
            print(f"     Got: HTTP error (expected for empty string)")
            _increment(group, True)
            return
        fail(f"HTTP request failed for: {display_q}")
        _increment(group, False)
        return

    actual_type = data.get("covenant_type", "?")
    actual_cats = data.get("routed_categories", [])
    answer_len = len(data.get("answer", ""))

    print(f"     Got:    type={actual_type}, cats={actual_cats}")
    print(f"     Answer: {answer_len} chars")

    passed = True

    # Check covenant type
    if exp_type is not None and actual_type != exp_type:
        fail(f"Expected covenant_type='{exp_type}', got '{actual_type}' for: {display_q}")
        passed = False

    # Check expected categories (at least one must be present)
    if exp_cats:
        if actual_cats:
            if any(ec in actual_cats for ec in exp_cats):
                ok(f"Category match: {[c for c in exp_cats if c in actual_cats]}")
            else:
                fail(f"Expected one of {exp_cats} in {actual_cats} for: {display_q}")
                passed = False
        else:
            fail(f"No routed_categories in response for: {display_q}")
            passed = False

    if passed:
        _increment(group, True)
    else:
        _increment(group, False)


def _increment(group: str, passed: bool):
    if group == "rp":
        if passed:
            results.rp_correct += 1
    elif group == "mfn":
        if passed:
            results.mfn_correct += 1
    elif group == "cross":
        if passed:
            results.cross_correct += 1
    elif group == "edge":
        if passed:
            results.edge_correct += 1


# ══════════════════════════════════════════════════════════════════════
# STEP 5: Integration test with /ask endpoint
# ══════════════════════════════════════════════════════════════════════

def run_step5_integration(base_url: str, deal_id: Optional[str]):
    print("\n" + "=" * 60)
    print("STEP 5: Integration Test — /ask Endpoint")
    print("=" * 60)

    if not deal_id:
        print("\n  SKIP: No deal with extracted data found.")
        return

    ask_url = f"{base_url}/api/deals/{deal_id}/ask"

    integration_questions = [
        "What is the builder basket starter amount?",
        "Does this deal have a J.Crew blocker?",
        "What is the MFN threshold?",
        "Summarize the restricted payment provisions",
    ]

    for q in integration_questions:
        print(f"\n  Q: \"{q}\"")
        results.integration_tested += 1

        data = http_post(ask_url, {"question": q}, timeout=60)
        if data is None:
            fail(f"Integration test failed for: {q}")
            continue

        ctype = data.get("covenant_type", "?")
        cats = data.get("routed_categories", "NOT IN RESPONSE")
        answer = data.get("answer", "")
        citations = data.get("citations", [])

        print(f"     Covenant type: {ctype}")
        print(f"     Routed categories: {cats}")
        print(f"     Answer length: {len(answer)}")
        print(f"     Has citations: {bool(citations)}")
        print(f"     Answer preview: {answer[:150]}...")

        if answer and len(answer) > 20:
            results.integration_passed += 1
            ok(f"Got substantive answer ({len(answer)} chars)")
        else:
            fail(f"Empty or trivial answer for: {q}")


# ══════════════════════════════════════════════════════════════════════
# Find a deal with extracted data
# ══════════════════════════════════════════════════════════════════════

def find_test_deal(base_url: str) -> Optional[str]:
    """Find a deal with extracted data to test against."""
    print("\nLooking for a deal with extracted data...")
    deals = http_get(f"{base_url}/api/deals")
    if not deals or not isinstance(deals, list) or len(deals) == 0:
        print("  No deals found in backend.")
        return None

    print(f"  Found {len(deals)} deal(s)")

    # Try each deal to find one with extraction data
    for deal in deals[:5]:
        did = deal.get("deal_id")
        dname = deal.get("deal_name", "?")
        print(f"  Checking deal {did} ({dname})...")

        # Check if RP provision exists
        rp = http_get(f"{base_url}/api/deals/{did}/rp-provision")
        if rp and rp.get("scalar_count", 0) > 0:
            print(f"  Using deal {did} ({dname}) — {rp['scalar_count']} scalar answers")
            return did

    print("  No deal with extracted RP data found.")
    return None


# ══════════════════════════════════════════════════════════════════════
# Summary table
# ══════════════════════════════════════════════════════════════════════

def print_summary():
    print("\n")
    print("+" + "=" * 58 + "+")
    print("|{:^58s}|".format("TopicRouter SSoT Test Results"))
    print("+" + "=" * 58 + "+")

    print("|{:<58s}|".format(" Metadata Loading"))
    print("|{:<58s}|".format(f"   Categories loaded:        {results.categories_loaded}"))
    print("|{:<58s}|".format(f"   Questions mapped:         {results.questions_mapped}"))
    print("|{:<58s}|".format(f"   Target fields mapped:     {results.target_fields_mapped}"))
    print("|{:<58s}|".format(""))

    print("|{:<58s}|".format(" Routing Accuracy"))
    print("|{:<58s}|".format(
        f"   RP questions correct:     {results.rp_correct}/{results.rp_total}"
    ))
    print("|{:<58s}|".format(
        f"   MFN questions correct:    {results.mfn_correct}/{results.mfn_total}"
    ))
    print("|{:<58s}|".format(
        f"   Cross-covenant correct:   {results.cross_correct}/{results.cross_total}"
    ))
    print("|{:<58s}|".format(
        f"   Edge cases handled:       {results.edge_correct}/{results.edge_total}"
    ))
    print("|{:<58s}|".format(""))

    print("|{:<58s}|".format(" SSoT Compliance"))
    print("|{:<58s}|".format(
        f"   No hardcoded fields:      {_pf(results.no_hardcoded_fields)}"
    ))
    print("|{:<58s}|".format(
        f"   No hardcoded categories:  {_pf(results.no_hardcoded_categories)}"
    ))
    print("|{:<58s}|".format(
        f"   No hardcoded keywords:    {_pf(results.no_hardcoded_keywords)}"
    ))
    print("|{:<58s}|".format(""))

    print("|{:<58s}|".format(" Integration (/ask endpoint)"))
    print("|{:<58s}|".format(
        f"   Tested:                   {results.integration_tested} questions"
    ))
    print("|{:<58s}|".format(
        f"   All returned answers:     {_pf(results.integration_tested > 0 and results.integration_passed == results.integration_tested)}"
    ))
    print("+" + "=" * 58 + "+")

    if results.failures:
        print(f"\n{len(results.failures)} failure(s):")
        for i, f in enumerate(results.failures, 1):
            print(f"  {i}. {f}")
    else:
        print("\nAll checks passed!")

    # Exit code
    critical_failures = (
        results.no_hardcoded_fields is False
        or results.no_hardcoded_categories is False
        or results.no_hardcoded_keywords is False
    )
    return 1 if critical_failures else 0


def _pf(val):
    if val is None:
        return "SKIP"
    return "PASS" if val else "FAIL"


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="TopicRouter SSoT compliance tests")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="Backend base URL")
    parser.add_argument("--grep-only", action="store_true", help="Only run file grep checks")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print("=" * 60)
    print("TopicRouter SSoT Compliance Test Suite")
    print("=" * 60)
    print(f"Backend URL: {base_url}")
    print(f"App dir:     {APP_DIR}")

    # Step 4 always runs (file-based)
    run_step4_grep_checks()

    if args.grep_only:
        print("\n--grep-only mode: skipping Steps 1, 2, 3, 5 (require backend)")
        exit_code = print_summary()
        sys.exit(exit_code)

    # Check backend health before proceeding
    print("\nChecking backend connectivity...")
    health = http_get(f"{base_url}/api/health")
    if not health:
        print("Backend not reachable. Skipping Steps 1, 2, 3, 5.")
        print("Run with deployed backend or use --grep-only for local checks.")
        exit_code = print_summary()
        sys.exit(exit_code)

    results.backend_reachable = True
    if not health.get("typedb_connected"):
        print("TypeDB not connected. Skipping Steps 1, 2, 3, 5.")
        exit_code = print_summary()
        sys.exit(exit_code)

    results.typedb_connected = True

    # Step 1: Metadata
    run_step1_metadata(base_url)

    # Find a deal for testing
    deal_id = find_test_deal(base_url)

    # Steps 2 & 3: Routing
    run_steps2_3_routing(base_url, deal_id)

    # Step 5: Integration
    run_step5_integration(base_url, deal_id)

    # Summary
    exit_code = print_summary()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
