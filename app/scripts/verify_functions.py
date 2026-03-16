"""Verification script for RP analytical functions infrastructure."""
import os
import sys
sys.path.insert(0, '.')

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

address = os.getenv('TYPEDB_ADDRESS', '')
if not address.startswith('http'):
    address = f'https://{address}'
driver = TypeDB.driver(address, Credentials(os.getenv('TYPEDB_USERNAME',''), os.getenv('TYPEDB_PASSWORD','')), DriverOptions())
db = os.getenv('TYPEDB_DATABASE', 'valence')

print("=" * 60)
print("STEP 1: Verify analysis_* attributes exist in schema")
print("=" * 60)
tx = driver.transaction(db, TransactionType.SCHEMA)
try:
    for attr in ['analysis_category', 'analysis_detail', 'analysis_value', 'analysis_flag']:
        try:
            result = tx.query(f'match $a label {attr}; select $a;').resolve()
            rows = list(result.as_concept_rows())
            status = 'EXISTS' if rows else 'MISSING'
        except Exception as e:
            status = f'ERROR: {e}'
        print(f'  {attr}: {status}')
finally:
    tx.close()

print()
print("=" * 60)
print("STEP 2: Verify bt_at_both dual-mapping")
print("=" * 60)
tx = driver.transaction(db, TransactionType.READ)
try:
    result = tx.query('''
        match
            $c isa blocker_timing, has concept_id "bt_at_both";
            $c has target_entity_attribute $tea;
        select $tea;
    ''').resolve()
    attrs = []
    for row in result.as_concept_rows():
        val = row.get('tea').as_attribute().get_value()
        attrs.append(val)
        print(f'  bt_at_both -> {val}')
    print(f'  Total mappings: {len(attrs)}')
    if len(attrs) == 2 and 'covers_transfer' in attrs and 'covers_designation' in attrs:
        print('  PASS: bt_at_both maps to both attributes')
    else:
        print(f'  FAIL: expected 2 mappings (covers_transfer, covers_designation), got {attrs}')
except Exception as e:
    print(f'  ERROR: {e}')
finally:
    tx.close()

print()
print("=" * 60)
print("STEP 5: Verify jcrew_blocker entity has data")
print("=" * 60)
tx = driver.transaction(db, TransactionType.READ)
try:
    result = tx.query('''
        match
            $p isa rp_provision, has provision_id "87852625_rp";
            (provision: $p, blocker: $b) isa provision_has_blocker;
            $b has blocker_id $bid;
            try { $b has covers_transfer $ct; };
            try { $b has covers_designation $cd; };
            try { $b has covers_ip $cip; };
            try { $b has covers_material_assets $cma; };
            try { $b has binds_loan_parties $blp; };
            try { $b has binds_restricted_subs $brs; };
            try { $b has is_sacred_right $isr; };
        select $bid, $ct, $cd, $cip, $cma, $blp, $brs, $isr;
    ''').resolve()
    rows = list(result.as_concept_rows())
    if not rows:
        print('  NO BLOCKER FOUND')
    else:
        for row in rows:
            for var in ['bid','ct','cd','cip','cma','blp','brs','isr']:
                try:
                    val = row.get(var).as_attribute().get_value()
                    print(f'  {var}: {val}')
                except:
                    print(f'  {var}: NULL')
except Exception as e:
    print(f'  ERROR: {e}')
finally:
    tx.close()

driver.close()
print()
print("Done.")
