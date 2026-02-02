# Valence V3 - Claude Handoff Context

Read this file at the start of any Claude Code session for project context.

## Repo & Deployment

- **GitHub**: https://github.com/oliverjmfeix-sketch/ValenceV3
- **Backend**: Railway
- **Database**: TypeDB Cloud 3.x
- **Frontend**: Lovable

## Core Architecture Principle

**TypeDB is the Single Source of Truth (SSoT).**

- All field definitions live in TypeDB schema (`app/data/schema.tql`), NOT in Python
- Ontology questions live in `app/data/questions.tql`
- Concept instances (multi-select options) live in `app/data/concepts.tql`
- Adding a new concept type = add to schema + seed data. No Python changes needed.

## TypeDB 3.x Syntax (Critical)

```python
# Queries require .resolve()
result = tx.query(query).resolve()
for row in result.as_concept_rows():
    value = row.get("var").as_attribute().get_value()

# Relations use "links" not parentheses
insert
    $app isa concept_applicability,
        links (provision: $mfn, concept: $c),
        has applicability_status "INCLUDED";
```

## Project Structure

```
ValenceV3/
├── app/
│   ├── main.py                 # FastAPI app + startup
│   ├── config.py               # Settings from env
│   ├── routers/                # API endpoints
│   │   ├── deals.py
│   │   ├── health.py
│   │   ├── ontology.py
│   │   ├── patterns.py
│   │   └── qa.py
│   ├── services/               # Business logic
│   │   ├── typedb_client.py    # TypeDB connection
│   │   ├── extraction.py       # Claude extraction
│   │   ├── pdf_parser.py       # PDF processing
│   │   └── qa_engine.py        # Question answering
│   ├── repositories/           # Data access
│   │   ├── answer_repository.py
│   │   ├── deal_repository.py
│   │   └── ontology_repository.py
│   ├── schemas/
│   │   └── models.py           # Pydantic models
│   ├── scripts/
│   │   ├── init_schema.py      # Initialize TypeDB schema
│   │   └── seed_ontology.py    # Seed ontology questions
│   └── data/
│       ├── schema.tql          # TypeDB schema (concept types here)
│       ├── concepts.tql        # Concept instances (multi-select options)
│       └── questions.tql       # Ontology questions
├── requirements.txt
├── Dockerfile
└── railway.toml
```

## Current State

| Covenant | Questions | Concept Types | Status |
|----------|-----------|---------------|--------|
| MFN | 42 | 12 | Working |
| RP | 429 | ~20 | In Progress |

## RP Concept Types

These consolidate boolean questions into multi-select concept types:

| Concept Type | Purpose |
|--------------|---------|
| `covered_person` | Who can have equity repurchased |
| `repurchase_trigger` | Events that trigger repurchases |
| `dividend_definition_element` | What's included in Dividend definition |
| `builder_source` | What builds Cumulative Amount |
| `builder_reduction_type` | What reduces builder basket |
| `rdp_basket_type` | Baskets available for RDP |
| `intercompany_recipient_type` | Permitted intercompany recipients |
| `tax_group_type` | Tax distribution group types |
| `overhead_cost_type` | Permitted overhead costs |
| `transaction_cost_type` | Permitted transaction costs |
| `equity_award_type` | Equity awards for tax repurchases |
| `rdp_payment_type` | Types of RDP |
| `reallocation_reduction_type` | What reduces general basket |
| `reallocation_target` | Cross-covenant reallocation targets |

## Common Commands

```bash
# Initialize schema
python -m app.scripts.init_schema

# Seed ontology
python -m app.scripts.seed_ontology

# Run locally
uvicorn app.main:app --reload --port 8000

# Deploy to Railway
railway up
```

## Pitfalls to Avoid

1. **Don't hardcode field lists in Python** - introspect from TypeDB
2. **Use TypeDB 3.x syntax** - `links` not parentheses for relations
3. **Call `.resolve()` on queries** - TypeDB 3.x returns Promises
4. **Use tristate for applicabilities** - INCLUDED/EXCLUDED/SILENT (not just boolean)
5. **Paths are in app/data/** - not data/ at the root level
