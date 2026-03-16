"""Check basket data for dividend_capacity_components debugging."""
import os, sys
sys.path.insert(0, '.')
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

address = os.getenv('TYPEDB_ADDRESS', '')
if not address.startswith('http'):
    address = f'https://{address}'
driver = TypeDB.driver(address, Credentials(os.getenv('TYPEDB_USERNAME',''), os.getenv('TYPEDB_PASSWORD','')), DriverOptions())
db = os.getenv('TYPEDB_DATABASE', 'valence')

tx = driver.transaction(db, TransactionType.READ)
try:
    print("=== Baskets linked via provision_has_basket ===")
    q1 = '''match
        $p isa rp_provision, has provision_id "87852625_rp";
        (provision: $p, basket: $b) isa provision_has_basket;
        $b has display_name $dn;
        select $dn;'''
    for row in tx.query(q1).resolve().as_concept_rows():
        print(f"  {row.get('dn').as_attribute().get_value()}")

    print("\n=== All rp_basket with basket_amount_usd ===")
    q2 = 'match $b isa rp_basket, has basket_amount_usd $amt, has display_name $dn; select $dn, $amt;'
    for row in tx.query(q2).resolve().as_concept_rows():
        dn = row.get('dn').as_attribute().get_value()
        amt = row.get('amt').as_attribute().get_value()
        print(f"  {dn}: {amt}")

    print("\n=== general_rp_basket attrs ===")
    q3 = 'match $b isa general_rp_basket, has basket_id $bid; select $bid;'
    for row in tx.query(q3).resolve().as_concept_rows():
        bid = row.get('bid').as_attribute().get_value()
        print(f"  basket_id: {bid}")

    q4 = '''match $b isa general_rp_basket;
        try { $b has basket_amount_usd $amt; };
        try { $b has display_name $dn; };
        select $amt, $dn;'''
    for row in tx.query(q4).resolve().as_concept_rows():
        try: amt = row.get('amt').as_attribute().get_value()
        except: amt = "NULL"
        try: dn = row.get('dn').as_attribute().get_value()
        except: dn = "NULL"
        print(f"  display_name={dn}, basket_amount_usd={amt}")

    print("\n=== jcrew_blocker boolean attrs ===")
    q5 = '''match $b isa jcrew_blocker,
        has covers_transfer $ct,
        has covers_material_assets $cm,
        has covers_designation $cd,
        has covers_ip $ci;
        select $ct, $cm, $cd, $ci;'''
    for row in tx.query(q5).resolve().as_concept_rows():
        print(f"  covers_transfer={row.get('ct').as_attribute().get_value()}")
        print(f"  covers_material_assets={row.get('cm').as_attribute().get_value()}")
        print(f"  covers_designation={row.get('cd').as_attribute().get_value()}")
        print(f"  covers_ip={row.get('ci').as_attribute().get_value()}")
finally:
    tx.close()
    driver.close()
print("\nDone.")
