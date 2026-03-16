# Valence V3 — Handoff Document
**Date**: 2026-03-16

## Current State

Startup is clean. Single-instance entity creation works. Entity list stores (sweep_tiers, pathways, exceptions, etc.) all fail due to type coercion bug. RP analytical functions are deployed but return 0 rows because entity data isn't populated.

## Critical Bug: Type Coercion Failure

### Symptom
ALL `entity_list` stores fail. TypeDB rejects values like `"196"` for integer fields, `"5.5"` for double, `"false"` for boolean. Every entity attribute gets wrapped in quotes as a string.

### Root Cause
`get_attr_value_types()` in `app/services/graph_storage.py` (~line 308) queries schema in a READ transaction:
```python
match $et label {entity_type}; $et owns $attr;
```
The match works (returns attribute labels), but `attr_type.get_value_type()` returns nothing in READ transactions on TypeDB Cloud. So the returned dict is empty → `_format_tql_value()` gets `schema_type=None` → treats all values as strings.

### What Was Tried
1. **SCHEMA transactions at startup** — timeout (10s default), TypeDB Cloud holds exclusive lock
2. **SCHEMA transactions at runtime** — also timeout
3. **READ transactions** — don't hang, but `get_value_type()` returns empty
4. **Consolidating into fewer SCHEMA txns** — still timeout
5. **TransactionOptions with 30s timeout** — still timeout

### Recommended Fix: Heuristic coercion in `_format_tql_value()`

Don't rely on schema at all. Detect types from the Python values themselves:
```python
def _format_tql_value(value, schema_type=None):
    # If schema_type is known, use it (existing logic)
    # If not, infer from the value itself:
    if isinstance(value, bool): return "true" if value else "false"
    if isinstance(value, (int, float)): return str(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "false"): return low
        try:
            int(value); return value  # bare integer
        except ValueError: pass
        try:
            float(value); return value  # bare float
        except ValueError: pass
        return f'"{value}"'  # actual string
```
Claude's extraction outputs are typed in JSON — booleans come as `true`/`false`, numbers as numbers. The current code stringifies them then can't convert back because schema_type is missing.

## Secondary Bug: sweep_exemption Subtypes

### Symptom
```
Type-inference was unable to find compatible types for 'entity' & '_anonymous' across a 'has' constraint
```

### Root Cause
Subtypes of `sweep_exemption` (below_threshold_exemption, ratio_basket_exemption, casualty_exemption, non_collateral_exemption, ordinary_course_sale_exemption) don't `owns section_reference` in schema. The store code tries to attach `section_reference` to all entities.

### Fix
Either add `owns section_reference` to sweep_exemption in schema, or skip provenance attrs for types that don't own them.

## Other Issues

| Issue | Severity | Notes |
|---|---|---|
| Missing concept types: builder_source, reallocatable_basket, rdp_basket_type | WARN | "Type label not found" — may need schema additions or extraction prompt fixes |
| Missing metadata for rp_f8 | ERROR | One question has no metadata entry |
| J.Crew Tier 3 prompt too long (212K > 200K tokens) | ERROR | Need to trim context or split extraction |

## What Works

- Startup: clean, no timeouts, no hangs
- TypeDB connection: stable
- Provenance attrs discovered: confidence, section_reference, source_page, source_text
- Entity relation map: 13 types loaded
- Single-instance entities: 12 created (jcrew_blocker, unsub_designation, etc.)
- RP analytical functions: 4 deployed, compile OK (blocker_binding_gap_evidence, blocker_exception_swallow_evidence, unsub_distribution_evidence, pathway_chain_summary)
- bt_at_both dual-mapping: verified (covers_transfer + covers_designation)

## Key Files

| File | Purpose |
|---|---|
| `app/services/graph_storage.py` | Core storage — type coercion, entity creation, schema introspection |
| `app/main.py` | FastAPI startup — no cache warming, lazy init only |
| `app/data/rp_analysis_functions.tql` | 4 analytical functions (Phase 4) |
| `app/data/schema_unified.tql` | Full TypeDB schema |
| `app/scripts/init_schema.py` | DB seeding — loads schema + functions |
| `app/scripts/verify_functions.py` | Temp verification script |
| `logs_2026-03-16_deploy3.txt` | Full extraction log from latest run |

## Key Methods in graph_storage.py

| Method | Line (approx) | Purpose |
|---|---|---|
| `get_attr_value_types()` | ~308 | Returns attr→value_type map. Currently returns empty. |
| `_format_tql_value()` | ~1793 | Coerces Python values to TQL literals. Broken when schema_type=None. |
| `_store_entity_list()` | ~1850+ | Creates entities from Claude's JSON. Calls _format_tql_value for each attr. |
| `get_entity_fields_from_schema()` | ~250 | Returns field list for entity type. Works (READ tx). |
| `_load_provenance_attrs()` | ~180 | Discovers provenance attrs. Fixed (no longer recursive). |

## Deployment

- Platform: Railway
- Trigger: `git push origin main` → auto-deploy
- Reseed: `railway run python -m app.scripts.init_schema --force`
- Re-extract: `curl --max-time 5 "$API/api/deals/87852625/re-extract" &` (fire-and-forget)
- Logs: `railway logs --follow` or Railway dashboard

## Recommended Next Steps (in order)

1. **Fix type coercion** — implement heuristic coercion in `_format_tql_value()`
2. **Deploy + re-extract Duck Creek** (87852625)
3. **Check entity_list stores succeed** in logs
4. **Verify RP analytical functions return data** — run verify_functions.py
5. **Fix sweep_exemption schema** if still erroring
6. **Address remaining warnings** (missing concept types, rp_f8 metadata)
