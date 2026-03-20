"""Verify migration 006 applied correctly."""
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

typedb_client.connect()
tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
try:
    # 1. investment_provision type exists
    r1 = list(tx.query(
        'match entity $e label investment_provision; select $e;'
    ).resolve().as_concept_rows())
    print(f'investment_provision type exists: {len(r1) > 0}')

    # 2. basket_reallocates_to owns expected attrs
    r2 = list(tx.query(
        'match relation $r label basket_reallocates_to; $r owns $a; select $a;'
    ).resolve().as_concept_rows())
    attrs = {row.get('a').as_attribute_type().get_label() for row in r2}
    expected = {
        'reallocation_amount_usd', 'reallocation_grower_pct', 'reduces_source_basket',
        'reduction_is_dollar_for_dollar', 'reduction_while_outstanding_only',
        'section_reference', 'source_text', 'source_page'
    }
    missing = expected - attrs
    extra = attrs - expected
    print(f'basket_reallocates_to owns {len(attrs)} attrs')
    print(f'Expected present: {not missing}')
    if missing:
        print(f'  MISSING: {missing}')
    if extra:
        print(f'  Extra (ok): {extra}')

    # 3. cross_covenant_mapping seed data
    r3 = list(tx.query(
        'match $m isa cross_covenant_mapping, '
        'has basket_type_name $bt, has provision_type_name $pt; '
        'select $bt, $pt;'
    ).resolve().as_concept_rows())
    print(f'cross_covenant_mappings: {len(r3)}')
    for row in r3:
        bt = row.get('bt').as_attribute().get_value()
        pt = row.get('pt').as_attribute().get_value()
        print(f'  {bt} -> {pt}')

    # 4. extraction_prompt updated
    r4 = list(tx.query(
        'match $q isa ontology_question, '
        'has question_id "rp_el_reallocations", has extraction_prompt $ep; '
        'select $ep;'
    ).resolve().as_concept_rows())
    if r4:
        ep = r4[0].get('ep').as_attribute().get_value()
        has_template = '{basket_subtypes}' in ep
        has_routing = 'source_basket_type' in ep
        has_direction = 'EACH DIRECTION' in ep
        print(f'extraction_prompt updated: template_var={has_template}, routing_fields={has_routing}, direction_instruction={has_direction}')
    else:
        print('WARNING: no extraction_prompt found on rp_el_reallocations')

    # 5. is_bidirectional NOT on basket_reallocates_to
    has_bidir = 'is_bidirectional' in attrs
    print(f'is_bidirectional on relation: {has_bidir} (should be False)')

finally:
    tx.close()
