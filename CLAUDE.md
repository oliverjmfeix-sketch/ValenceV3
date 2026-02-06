# CLAUDE.md - Valence Project Instructions

## Model Selection

DEFAULT: Sonnet for all coding tasks.

UPGRADE TO OPUS only when:
- I explicitly ask for architecture review
- Debugging requires reasoning across 3+ files
- The task involves "should we" or "which approach"
- SSoT compliance is in question

USE HAIKU for:
- Docstrings, comments, formatting
- "Rename X to Y"
- Generating test fixtures

## Project Architecture

### SSoT Principle
TypeDB is the single source of truth. Never hardcode:
- Field lists
- Question definitions
- Extraction prompts
- Concept options

If you need a list of fields, query TypeDB. If you need extraction instructions,
load `extraction_metadata` entities.

### V4 Graph Model
We're migrating from flat attributes to graph entities:
- `builder_basket` with `basket_has_source` → source entities
- `jcrew_blocker` with `blocker_has_exception` → exception entities
- `sweep_tier`, `de_minimis_threshold` as separate entities

### Key Patterns

**Storage pattern** (graph_storage.py):
```python
async def _store_X(self, provision_id: str, data: XModel):
    attrs = [f'has x_id "{x_id}"']
    if data.some_field:
        attrs.append(f'has some_field "{self._escape(data.some_field)}"')
    # ... build query, execute
```

**Extraction pattern** (extraction.py):
1. Load metadata from TypeDB
2. Build prompt with metadata + document
3. Call Claude (Sonnet)
4. Parse with Pydantic
5. Store via graph_storage

## TypeDB Syntax (3.x)

- `entity X sub Y` not `X sub entity`
- `relation R, relates A, relates B`
- `owns` not `has` in schema
- `has` in data queries
- Use `@key` for unique identifiers

## File Locations

| Purpose | File |
|---------|------|
| Schema | `app/data/schema.tql` |
| Seed data | `app/data/concepts.tql`, `app/data/questions.tql` |
| V4 models | `app/schemas/extraction_output_v4.py` |
| Storage | `app/services/graph_storage.py` |
| Extraction | `app/services/extraction.py` |

## Testing

Before saying "done":
1. Check TypeQL syntax is valid
2. Verify entity/relation names match schema
3. Ensure provenance fields included
4. Run `pytest tests/` if tests exist

## Cost Awareness

- RP Universe extraction is expensive (~$0.50) - avoid re-running
- V4 extraction is cheap (~$0.10) - fine to iterate
- Store extracted RP Universe so V4 can re-run without re-extracting
