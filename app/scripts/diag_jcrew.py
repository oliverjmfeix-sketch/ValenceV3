"""
Diagnostic: J.Crew extraction pipeline end-to-end test.

Tests the full pipeline: question loading → Claude call → JSON parsing → TypeDB storage.
Uses a minimal synthetic credit agreement to keep Claude costs ~$0.02.
"""
import asyncio
import os
import sys
import logging
import traceback
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

addr = TYPEDB_ADDRESS
if not addr.startswith("http"):
    addr = f"https://{addr}"

driver = TypeDB.driver(addr, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())

# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: Verify JC data in TypeDB
# ═══════════════════════════════════════════════════════════════════════════

print("=" * 60)
print("STEP 1: Check JC data in TypeDB")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
rows = list(tx.query(
    'match $cat isa ontology_category, has category_id $cid; '
    '$cid like "^JC"; '
    '(category: $cat, question: $q) isa category_has_question; '
    '$q has question_id $qid; select $cid, $qid;'
).resolve().as_concept_rows())
from collections import Counter
c = Counter()
for r in rows:
    c[r.get("cid").as_attribute().get_value()] += 1
print(f"JC relations: {len(rows)} (JC1={c.get('JC1',0)}, JC2={c.get('JC2',0)}, JC3={c.get('JC3',0)})")
tx.close()

if len(rows) != 69:
    print("FAIL: Expected 69 JC relations!")
    driver.close()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 2: Verify Python question loading
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("STEP 2: Verify Python question loading")
print("=" * 60)

from app.services.typedb_client import typedb_client
typedb_client.connect()

from app.services.extraction import ExtractionService, RPUniverse
svc = ExtractionService()
all_cats = svc.load_questions_by_category("RP")
jc1 = all_cats.get("JC1", [])
jc2 = all_cats.get("JC2", [])
jc3 = all_cats.get("JC3", [])
print(f"JC1={len(jc1)}, JC2={len(jc2)}, JC3={len(jc3)}")

if len(jc1) + len(jc2) + len(jc3) == 0:
    print("FAIL: No JC questions loaded!")
    driver.close()
    typedb_client.close()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 3: Test _answer_category_questions with JC1 (small subset)
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("STEP 3: Test _answer_category_questions (JC1, first 5 questions only)")
print("=" * 60)

# Minimal synthetic credit agreement text for testing
MINI_AGREEMENT = """[PAGE 1]
CREDIT AGREEMENT dated as of March 1, 2024 among ACME CORP ("Borrower"),
the Lenders, and BANK OF TESTING as Administrative Agent.

[PAGE 15]
SECTION 1.01. Defined Terms.

"Intellectual Property" shall have the meaning assigned to such term in the Security Agreement.

"Material Intellectual Property" means any Intellectual Property that, as determined by the
Borrower in good faith, is material to the business of the Borrower and its Restricted Subsidiaries,
taken as a whole. For avoidance of doubt, this includes patents, trademarks, and copyrights
but shall not include trade secrets or know-how.

"Transfer" means any sale, assignment, transfer, conveyance or other disposition of assets.
For the avoidance of doubt, the granting of a non-exclusive license shall not constitute a Transfer.

"Unrestricted Subsidiary" means any Subsidiary designated as such by the Board of Directors.
The Borrower may designate any Restricted Subsidiary as an Unrestricted Subsidiary if the
aggregate fair market value of all Unrestricted Subsidiaries shall not exceed the greater of
$50,000,000 and 10% of Total Assets. Such designation may be made at any time without
the consent of any Lender.

[PAGE 89]
SECTION 6.03. Investments.
(a) The Borrower and its Restricted Subsidiaries may make Investments in Unrestricted Subsidiaries
in an aggregate amount not to exceed the greater of $25,000,000 and 5% of Consolidated EBITDA.

(b) Investments by Loan Parties in non-Guarantor Restricted Subsidiaries in an aggregate
amount not to exceed $75,000,000.

[PAGE 120]
SECTION 6.06. Restriction on Transfer of Material IP.
No Loan Party shall sell, assign, transfer, or otherwise dispose of any Material Intellectual Property
to any Unrestricted Subsidiary or any Person that is not a Loan Party; provided that Loan Parties
may (i) license Intellectual Property in the ordinary course of business, (ii) transfer non-Material
assets, and (iii) make Permitted Investments subject to Section 6.03.

[PAGE 150]
SECTION 9.08. Amendments; Waivers.
Any amendment to Section 6.06 (IP Transfer Restriction) shall require the consent of the
Required Lenders. No super-majority or all-lender consent is required.
"""

# Test with only first 5 JC1 questions to keep costs low
test_questions = jc1[:5]
print(f"Testing with {len(test_questions)} questions...")
for q in test_questions:
    print(f"  {q['question_id']}: {q['question_text'][:60]}...")

try:
    answers = svc._answer_category_questions(
        MINI_AGREEMENT,
        test_questions,
        "J.Crew Tier 1 — Structural Vulnerability (TEST)",
    )
    print(f"\nClaude returned {len(answers)} answers:")
    for a in answers:
        print(f"  {a.question_id}: value={a.value} confidence={a.confidence} type={a.answer_type}")
except Exception as e:
    print(f"\nFAIL: _answer_category_questions raised: {e}")
    traceback.print_exc()
    answers = []

if not answers:
    print("\nFAIL: No answers returned from Claude!")
    driver.close()
    typedb_client.close()
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: Test storage (create test deal + provision, store answers)
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("STEP 4: Test storage (create deal, provision, store answers)")
print("=" * 60)

TEST_DEAL_ID = "diag_jcrew_test"
TEST_PROVISION_ID = f"{TEST_DEAL_ID}_rp"

from datetime import datetime, timezone
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

# Create test deal
tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
try:
    tx.query(f'''
        insert $d isa deal,
            has deal_id "{TEST_DEAL_ID}",
            has deal_name "JC Diagnostic Test",
            has created_at {now_iso};
    ''').resolve()
    tx.commit()
    print(f"Created test deal: {TEST_DEAL_ID}")
except Exception as e:
    tx.close()
    if "unique" in str(e).lower() or "duplicate" in str(e).lower() or "already" in str(e).lower():
        print(f"Test deal already exists (OK)")
    else:
        print(f"WARN: Could not create test deal: {e}")

# Create test provision
svc._ensure_provision_exists(TEST_DEAL_ID, TEST_PROVISION_ID)

# Store answers
from app.services.extraction import CategoryAnswers
test_cat_answers = [CategoryAnswers(
    category_id="JC1",
    category_name="J.Crew Tier 1 — Test",
    answers=answers,
)]

try:
    success = svc.store_extraction_result(TEST_DEAL_ID, test_cat_answers)
    print(f"store_extraction_result returned: {success}")
except Exception as e:
    print(f"FAIL: store_extraction_result raised: {e}")
    traceback.print_exc()
    success = False

# ═══════════════════════════════════════════════════════════════════════════
# STEP 5: Verify answers in TypeDB
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("STEP 5: Verify JC answers in TypeDB")
print("=" * 60)

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
try:
    stored = list(tx.query(f'''
        match
            $prov isa rp_provision, has provision_id "{TEST_PROVISION_ID}";
            (provision: $prov, question: $q) isa provision_has_answer,
                has answer_id $aid;
            $q has question_id $qid;
            $qid like "^jc_";
        select $qid, $aid;
    ''').resolve().as_concept_rows())
    print(f"JC answers stored in TypeDB: {len(stored)}")
    for r in stored:
        qid = r.get("qid").as_attribute().get_value()
        aid = r.get("aid").as_attribute().get_value()
        print(f"  {qid} -> {aid}")
except Exception as e:
    print(f"FAIL: Could not query answers: {e}")
    traceback.print_exc()
finally:
    tx.close()

# ═══════════════════════════════════════════════════════════════════════════
# STEP 6: Run full run_jcrew_deep_analysis (all 3 tiers)
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("STEP 6: Run full run_jcrew_deep_analysis (all 3 tiers)")
print("=" * 60)

# Create RPUniverse with synthetic text
rp_universe = RPUniverse()
rp_universe.raw_text = MINI_AGREEMENT
rp_universe.definitions = MINI_AGREEMENT[MINI_AGREEMENT.index("[PAGE 15]"):MINI_AGREEMENT.index("[PAGE 89]")]

# Use a different deal_id to avoid conflicts
FULL_DEAL_ID = "diag_jcrew_full"
FULL_PROVISION_ID = f"{FULL_DEAL_ID}_rp"

tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
try:
    tx.query(f'''
        insert $d isa deal,
            has deal_id "{FULL_DEAL_ID}",
            has deal_name "JC Full Diagnostic Test",
            has created_at {now_iso};
    ''').resolve()
    tx.commit()
    print(f"Created test deal: {FULL_DEAL_ID}")
except Exception as e:
    tx.close()
    if "unique" in str(e).lower() or "duplicate" in str(e).lower() or "already" in str(e).lower():
        print(f"Test deal already exists (OK)")
    else:
        print(f"WARN: Could not create deal: {e}")

print("Running full 3-tier analysis (this calls Claude 3 times)...")
try:
    result = asyncio.run(svc.run_jcrew_deep_analysis(
        deal_id=FULL_DEAL_ID,
        rp_universe=rp_universe,
        document_text=MINI_AGREEMENT,
    ))
    print(f"\nResult: {result}")
except Exception as e:
    print(f"\nFAIL: run_jcrew_deep_analysis raised: {e}")
    traceback.print_exc()
    result = None

# Verify full answers stored
if result and not result.get("skipped"):
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
    try:
        stored = list(tx.query(f'''
            match
                $prov isa rp_provision, has provision_id "{FULL_PROVISION_ID}";
                (provision: $prov, question: $q) isa provision_has_answer,
                    has answer_id $aid;
                $q has question_id $qid;
                $qid like "^jc_";
            select $qid, $aid;
        ''').resolve().as_concept_rows())
        print(f"\nFull JC answers stored: {len(stored)}")
        # Group by tier
        t1 = sum(1 for r in stored if r.get("qid").as_attribute().get_value().startswith("jc_t1"))
        t2 = sum(1 for r in stored if r.get("qid").as_attribute().get_value().startswith("jc_t2"))
        t3 = sum(1 for r in stored if r.get("qid").as_attribute().get_value().startswith("jc_t3"))
        print(f"  T1: {t1}, T2: {t2}, T3: {t3}")
    except Exception as e:
        print(f"FAIL: Could not query answers: {e}")
        traceback.print_exc()
    finally:
        tx.close()

# ═══════════════════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════════════════

driver.close()
typedb_client.close()

print("\n" + "=" * 60)
print("DIAGNOSIS COMPLETE")
print("=" * 60)
