"""Verify reallocation graph edges after re-extraction."""
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

typedb_client.connect()
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
try:
    # 1. Count basket_reallocates_to instances
    r1 = list(tx.query(
        'match $r isa basket_reallocates_to; select $r;'
    ).resolve().as_concept_rows())
    print(f'basket_reallocates_to instances: {len(r1)}')

    # 2. Show connections with types
    q2 = '''match
        $link isa basket_reallocates_to,
            links (source_basket: $src, target_basket: $tgt);
        $src isa! $stype; $tgt isa! $ttype;
        let $sn = label($stype); let $tn = label($ttype);
    select $sn, $tn;'''
    edges = []
    for row in tx.query(q2).resolve().as_concept_rows():
        sn = row.get('sn').as_value().get()
        tn = row.get('tn').as_value().get()
        edges.append((sn, tn))
        print(f'  {sn} --> {tn}')

    # 3. Check investment entities
    r3 = list(tx.query(
        'match $p isa investment_provision; select $p;'
    ).resolve().as_concept_rows())
    print(f'investment_provision instances: {len(r3)}')

    r4 = list(tx.query(
        'match $b isa general_investment_basket; select $b;'
    ).resolve().as_concept_rows())
    print(f'general_investment_basket instances: {len(r4)}')

    # 4. Check investment basket dollar amount
    r5 = list(tx.query('''match
        $b isa general_investment_basket, has basket_amount_usd $amt;
    select $amt;''').resolve().as_concept_rows())
    if r5:
        amt = r5[0].get('amt').as_attribute().get_value()
        print(f'general_investment_basket amount: {amt}')
    else:
        print('WARNING: general_investment_basket has no basket_amount_usd')

    # 5. Verify relation attributes on edges
    r6 = list(tx.query('''match
        $link isa basket_reallocates_to,
            links (source_basket: $src, target_basket: $tgt);
        $src isa! $stype; let $sn = label($stype);
        $tgt isa! $ttype; let $tn = label($ttype);
        try { $link has section_reference $sr; };
        try { $link has reallocation_amount_usd $amt; };
    select $sn, $tn, $sr, $amt;''').resolve().as_concept_rows())
    for row in r6:
        sn = row.get('sn').as_value().get()
        tn = row.get('tn').as_value().get()
        sr = row.get('sr')
        sr_val = sr.as_attribute().get_value() if sr else '?'
        amt = row.get('amt')
        amt_val = amt.as_attribute().get_value() if amt else '?'
        print(f'  {sn} --> {tn} [section: {sr_val}, amount: {amt_val}]')

    # 6. All baskets on RP provision via polymorphic query
    print()
    print('All baskets on 87852625_rp via provision_has_extracted_entity:')
    r7 = list(tx.query('''match
        $prov isa provision, has provision_id "87852625_rp";
        $b has basket_id $bid;
        $rel isa provision_has_extracted_entity, links ($prov, $b);
        $b isa! $actual;
        let $tn = label($actual);
    select $tn, $bid;''').resolve().as_concept_rows())
    for row in r7:
        tn = row.get('tn').as_value().get()
        bid = row.get('bid').as_attribute().get_value()
        print(f'  {tn}: {bid}')

    print()
    print('VERIFICATION COMPLETE')

finally:
    tx.close()
