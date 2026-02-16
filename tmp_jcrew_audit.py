"""
J.Crew Data Source Audit — Read-only discovery.
Run on Railway: railway ssh --service ValenceV3 -- python tmp_jcrew_audit.py
"""
import json
import sys

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
from app.config import settings


def connect():
    addr = settings.normalized_typedb_address
    cred = Credentials(settings.typedb_username, settings.typedb_password)
    opts = DriverOptions()
    driver = TypeDB.driver(addr, cred, opts)
    return driver


def run_query(driver, query, label=""):
    """Run a read query, return list of dicts."""
    tx = driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        result = tx.query(query).resolve()
        rows = []
        for row in result.as_concept_rows():
            d = {}
            for var in row.column_names():
                try:
                    concept = row.get(var)
                    if concept is None:
                        d[var] = None
                    elif concept.is_attribute():
                        d[var] = concept.as_attribute().get_value()
                    elif concept.is_entity():
                        d[var] = f"<entity:{concept.as_entity().get_type().get_label()}>"
                    else:
                        d[var] = str(concept)
                except Exception:
                    d[var] = None
            rows.append(d)
        return rows
    except Exception as e:
        print(f"  ERROR [{label}]: {e}")
        return []
    finally:
        tx.close()


def main():
    driver = connect()
    print("Connected to TypeDB\n")

    # ── 0a: Deal exists? ─────────────────────────────────────────────────
    print("=" * 70)
    print("0a. Deal exists?")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000", has deal_name $name;
        select $name;
    ''', "0a")
    if not rows:
        print("  DEAL NOT FOUND. Stopping.")
        sys.exit(1)
    for r in rows:
        print(f"  deal_name: {r.get('name')}")

    # ── 0b: Has rp_provision? ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("0b. Has rp_provision?")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision, has provision_id $pid;
        select $pid;
    ''', "0b")
    if not rows:
        print("  NO RP PROVISION. Need to run extraction first.")
        sys.exit(1)
    for r in rows:
        print(f"  provision_id: {r.get('pid')}")

    # ── 0c: Has jc_* answers? ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("0c. Has jc_* answers?")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $a) isa provision_has_answer;
            $a has question_id $qid;
            { $qid like "jc_%"; };
        select $qid;
    ''', "0c")
    print(f"  Count: {len(rows)} jc_* answers found")
    if not rows:
        print("  NO JC ANSWERS. Checking prerequisites...")
        # Check categories
        cat_rows = run_query(driver, '''
            match $c isa ontology_category, has category_id $cid;
                { $cid == "JC1"; } or { $cid == "JC2"; } or { $cid == "JC3"; };
            select $cid;
        ''', "0c-cats")
        print(f"  JC categories seeded: {[r.get('cid') for r in cat_rows]}")
        # Check questions
        q_rows = run_query(driver, '''
            match $q isa ontology_question, has question_id $qid;
                $qid like "jc_%";
            select $qid;
        ''', "0c-qs")
        print(f"  JC questions seeded: {len(q_rows)}")
        print("  JC extraction needs to be run for this deal.")
        # Don't exit — continue checking other paths
    else:
        jc_qids = sorted([r.get('qid') for r in rows])
        for qid in jc_qids:
            print(f"    {qid}")

    # ── 1: Full JC Question Catalog ──────────────────────────────────────
    print("\n" + "=" * 70)
    print("1. Full JC Question Catalog (JC1, JC2, JC3)")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $cat isa ontology_category, has category_id $cid;
            ($cat, $q) isa category_has_question;
            $q has question_id $qid, has question_text $qt, has answer_type $at;
            { $cid == "JC1"; } or { $cid == "JC2"; } or { $cid == "JC3"; };
        select $cid, $qid, $qt, $at;
    ''', "1")
    rows.sort(key=lambda r: (r.get('cid', ''), r.get('qid', '')))
    print(f"  Total: {len(rows)} questions")
    for r in rows:
        qt = (r.get('qt') or '')[:80]
        print(f"  {r.get('cid'):4s} | {r.get('qid'):12s} | {r.get('at'):12s} | {qt}")

    # ── 2: Which JC Answers Exist ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("2. Which JC Answers Exist for Duck Creek?")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision, has provision_id $pid;
            ($p, $a) isa provision_has_answer;
            $a has question_id $qid;
            { $qid like "jc_%"; };
        select $pid, $qid;
    ''', "2")
    print(f"  Populated: {len(rows)} answers")
    answered_qids = sorted([r.get('qid') for r in rows])
    for qid in answered_qids:
        print(f"    {qid}")

    # ── 3: Sample Answer Values ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("3. Answer Values for Duck Creek JC Questions")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $a) isa provision_has_answer;
            $a has question_id $qid;
            { $qid like "jc_%"; };
            try { $a has answer_boolean $ab; };
            try { $a has answer_string $as; };
            try { $a has answer_double $ad; };
            try { $a has answer_integer $ai; };
        select $qid, $ab, $as, $ad, $ai;
    ''', "3")
    rows.sort(key=lambda r: r.get('qid', ''))
    for r in rows:
        val = r.get('ab') if r.get('ab') is not None else r.get('as') if r.get('as') is not None else r.get('ad') if r.get('ad') is not None else r.get('ai')
        val_type = 'bool' if r.get('ab') is not None else 'str' if r.get('as') is not None else 'dbl' if r.get('ad') is not None else 'int' if r.get('ai') is not None else 'null'
        val_display = str(val)[:100] if val is not None else 'NULL'
        print(f"  {r.get('qid'):12s} [{val_type:4s}] = {val_display}")

    # ── 4: Concept Applicability ─────────────────────────────────────────
    print("\n" + "=" * 70)
    print("4. Concept Applicability for Duck Creek")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $concept) isa concept_applicability, has applicability_status $status;
            $concept has concept_id $cid, has concept_name $cname;
        select $cid, $cname, $status;
    ''', "4")
    rows.sort(key=lambda r: r.get('cid', ''))
    print(f"  Total: {len(rows)} concept_applicability entries")
    for r in rows:
        print(f"  {r.get('cid'):30s} | {r.get('status'):10s} | {r.get('cname')}")

    # ── 5a: rp_k1 answer ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("5. Blocker Existence Resolution")
    print("=" * 70)

    print("\n  5a. rp_k1 (blocker exists boolean):")
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $a) isa provision_has_answer;
            $a has question_id "rp_k1";
            try { $a has answer_boolean $val; };
        select $val;
    ''', "5a")
    if rows:
        for r in rows:
            print(f"    rp_k1 = {r.get('val')}")
    else:
        print("    EMPTY — no rp_k1 answer")

    print("\n  5b. blocker_prohibition_type concepts:")
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $concept) isa concept_applicability;
            $concept isa blocker_prohibition_type, has concept_id $cid;
        select $cid;
    ''', "5b")
    if rows:
        for r in rows:
            print(f"    {r.get('cid')}")
    else:
        print("    EMPTY — no blocker_prohibition_type concepts")

    print("\n  5c. jc_t1_33 (verbatim blocker text):")
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $a) isa provision_has_answer;
            $a has question_id "jc_t1_33";
            try { $a has answer_string $text; };
        select $text;
    ''', "5c")
    if rows:
        for r in rows:
            txt = str(r.get('text', ''))[:200]
            print(f"    jc_t1_33 = {txt}...")
    else:
        print("    EMPTY — no jc_t1_33 answer")

    print("\n  5d. jcrew_blocker V4 entity:")
    rows = run_query(driver, '''
        match
            $d isa deal, has deal_id "b6209000";
            ($d, $p) isa deal_has_provision;
            $p isa rp_provision;
            ($p, $b) isa provision_has_blocker;
            $b isa jcrew_blocker;
            try { $b has covers_transfer $ct; };
            try { $b has covers_designation $cd; };
        select $ct, $cd;
    ''', "5d")
    if rows:
        for r in rows:
            print(f"    covers_transfer={r.get('ct')}, covers_designation={r.get('cd')}")
    else:
        print("    EMPTY — no jcrew_blocker entity")

    # ── 6: Named Relations ───────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("6. Named Multiselect Relations — Live or Dead?")
    print("=" * 70)

    for rel_name, query in [
        ("blocker_prohibits", '''
            match
                (provision: $p, prohibition_type: $t) isa blocker_prohibits;
                $t has concept_id $cid;
            select $cid;
        '''),
        ("blocker_binds", '''
            match
                (provision: $p, bound_entity_type: $t) isa blocker_binds;
                $t has concept_id $cid;
            select $cid;
        '''),
        ("blocker_protects", '''
            match
                (provision: $p, protected_asset: $t) isa blocker_protects;
                $t has concept_id $cid;
            select $cid;
        '''),
    ]:
        rows = run_query(driver, query, f"6-{rel_name}")
        if rows:
            print(f"  {rel_name}: LIVE ({len(rows)} entries)")
            for r in rows:
                print(f"    {r.get('cid')}")
        else:
            print(f"  {rel_name}: DEAD (empty)")

    # ── 7: Function Composition ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("7. Function Composition Test")
    print("=" * 70)
    rows = run_query(driver, '''
        match
            true == has_pattern_no_blocker("b6209000");
        select;
    ''', "7-composition")
    print(f"  Result: {'matched (true)' if rows else 'empty (false or no data)'}")
    print("  (No error = function composition works in TypeDB 3.x)")

    # ── 8: Flat Entities ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("8. Flat Entity Confirmation — Should All Be Empty")
    print("=" * 70)
    for entity_type in [
        "jcrew_provision_analysis",
        "definition_quality_analysis",
        "interaction_risk_analysis",
        "protection_analysis",
    ]:
        rows = run_query(driver, f"match $a isa {entity_type}; select $a;", f"8-{entity_type}")
        status = "EMPTY (confirmed)" if not rows else f"HAS DATA ({len(rows)} rows)"
        print(f"  {entity_type}: {status}")

    print("\n" + "=" * 70)
    print("AUDIT COMPLETE")
    print("=" * 70)

    driver.close()


if __name__ == "__main__":
    main()
