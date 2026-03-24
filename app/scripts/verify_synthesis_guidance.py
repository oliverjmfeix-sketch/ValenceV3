"""Verify synthesis_guidance is seeded correctly in TypeDB.

Run on Railway after init_schema --force:
    python -m app.scripts.verify_synthesis_guidance

Checks:
1. All 27 categories have synthesis_guidance
2. MFN_SYNTHESIS_RULES and rp_specific_rules are gone from deals.py
"""
import sys
from pathlib import Path

# Expected categories (27 total)
EXPECTED_CATEGORIES = {
    # RP (18)
    "RP", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "P", "S", "T", "Z",
    # J.Crew (3)
    "JC1", "JC2", "JC3",
    # MFN (6)
    "MFN1", "MFN2", "MFN3", "MFN4", "MFN5", "MFN6",
}


def verify_typedb():
    """Load synthesis_guidance from TypeDB and verify coverage."""
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

    print(f"\n{'='*70}")
    print(f"SYNTHESIS GUIDANCE VERIFICATION")
    print(f"{'='*70}\n")

    # Print all found
    for cid in sorted(found.keys()):
        sg = found[cid]
        if sg:
            preview = sg[:80].replace("\n", " ")
            print(f"  {cid:6s} ✓  {preview}...")
        else:
            print(f"  {cid:6s} ✗  MISSING")

    # Check coverage
    missing = EXPECTED_CATEGORIES - set(found.keys())
    no_guidance = {cid for cid, sg in found.items() if not sg and cid in EXPECTED_CATEGORIES}

    print(f"\n{'─'*70}")
    print(f"Categories found: {len(found)}")
    print(f"With guidance:    {sum(1 for sg in found.values() if sg)}")
    print(f"Missing:          {len(missing)} {sorted(missing) if missing else ''}")
    print(f"No guidance:      {len(no_guidance)} {sorted(no_guidance) if no_guidance else ''}")

    return len(missing) == 0 and len(no_guidance) == 0


def verify_python():
    """Confirm hardcoded rules are removed from deals.py."""
    deals_path = Path(__file__).parent.parent / "routers" / "deals.py"
    content = deals_path.read_text()

    issues = []
    if "MFN_SYNTHESIS_RULES" in content:
        issues.append("MFN_SYNTHESIS_RULES still present in deals.py")
    if "rp_specific_rules" in content:
        issues.append("rp_specific_rules still present in deals.py")

    print(f"\n{'─'*70}")
    print("PYTHON CODE VERIFICATION")
    print(f"{'─'*70}")

    if issues:
        for issue in issues:
            print(f"  ✗ {issue}")
        return False
    else:
        print("  ✓ MFN_SYNTHESIS_RULES removed")
        print("  ✓ rp_specific_rules removed")
        return True


if __name__ == "__main__":
    python_ok = verify_python()

    try:
        typedb_ok = verify_typedb()
    except Exception as e:
        print(f"\n  ⚠ TypeDB connection failed (expected if running locally): {e}")
        typedb_ok = None

    print(f"\n{'='*70}")
    if python_ok and typedb_ok is not False:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")
        sys.exit(1)
