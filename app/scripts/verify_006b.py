"""Verify Prompt 6b: extraction chain methods load correctly."""
from app.services.typedb_client import typedb_client
typedb_client.connect()

from app.services.graph_storage import GraphStorage

# Test 1: basket subtype introspection
subtypes = GraphStorage._get_basket_subtype_names()
print(f'Basket subtypes ({len(subtypes)}): {subtypes}')
assert len(subtypes) >= 10, f'Expected 10+ basket subtypes, got {len(subtypes)}'
assert 'general_rp_basket' in subtypes, 'Missing general_rp_basket'
assert 'general_rdp_basket' in subtypes, 'Missing general_rdp_basket'
assert 'general_investment_basket' in subtypes, 'Missing general_investment_basket'
# Abstract parents should NOT be in the list
assert 'rp_basket' not in subtypes, 'rp_basket is abstract, should not be in list'
assert 'rdp_basket' not in subtypes, 'rdp_basket is abstract, should not be in list'

# Test 2: cross-covenant mapping
ccm = GraphStorage._load_cross_covenant_mappings()
print(f'Cross-covenant mappings: {ccm}')
assert 'general_investment_basket' in ccm, 'Missing general_investment_basket mapping'
assert ccm['general_investment_basket'] == 'investment_provision', 'Wrong provision type'

# Test 3: relation attr introspection
rel_attrs = GraphStorage._get_relation_attr_types('basket_reallocates_to')
print(f'basket_reallocates_to attrs ({len(rel_attrs)}): {sorted(rel_attrs.keys())}')
assert 'reallocation_amount_usd' in rel_attrs, 'Missing reallocation_amount_usd'
assert 'reduces_source_basket' in rel_attrs, 'Missing reduces_source_basket'
assert rel_attrs.get('reduces_source_basket') == 'boolean', (
    f'Wrong type: {rel_attrs.get("reduces_source_basket")}')

# Test 4: template expansion
hint = 'Must be one of: {basket_subtypes}'
basket_types = GraphStorage._get_basket_subtype_names()
expanded = hint.replace('{basket_subtypes}', ', '.join(basket_types))
print(f'Template expansion: {expanded[:100]}...')
assert 'general_investment_basket' in expanded, 'Template expansion missing investment basket'
assert '{basket_subtypes}' not in expanded, 'Template variable not expanded'

print()
print('ALL TESTS PASSED')
