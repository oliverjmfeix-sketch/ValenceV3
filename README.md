# Valence Backend

Legal document analysis platform that extracts typed primitives from credit agreements and enables 100% accurate Q&A against structured data in TypeDB.

## Architecture

- **FastAPI** - API server
- **TypeDB Cloud 3.x** - Graph database with typed primitives + inference rules
- **Claude API** - Document extraction (used once at upload time)
- **PyMuPDF** - PDF text extraction

## Key Principles

1. **Typed Primitives** - No JSON blobs. Every extracted value is a typed attribute.
2. **Provenance** - Every primitive links to source_text, source_page, source_section.
3. **SSoT** - TypeDB is single source of truth. Questions come from schema, not hardcoded lists.
4. **Pattern Functions** — J.Crew vulnerability detection via TypeDB 3.x `fun` functions. Logic lives in the database schema, called on the fly from queries.

## Setup

### 1. Clone and Install

```bash
cd ValenceV3
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Variables

Create `.env`:

```bash
# TypeDB Cloud - IMPORTANT: No https://, just host:port
TYPEDB_ADDRESS=your-cluster.typedb.cloud:1729
TYPEDB_DATABASE=valence
TYPEDB_USERNAME=admin
TYPEDB_PASSWORD=your-password
TYPEDB_TLS_ENABLED=true

# Claude API
ANTHROPIC_API_KEY=sk-ant-xxx

# Server
PORT=8000
UPLOADS_DIR=/app/uploads

# CORS - Add your Lovable frontend URL
CORS_ORIGINS=http://localhost:5173,https://your-app.lovable.app

# Debug endpoints (disabled by default in production)
DEBUG_ENDPOINTS_ENABLED=false
```

### 3. Initialize TypeDB Schema + Seed Data

```bash
python -m app.scripts.init_schema
```

This single command loads the unified schema, all ontology questions, concepts,
extraction metadata, and TypeDB functions.

### 4. Run Locally

```bash
uvicorn app.main:app --reload --port 8000
```

## Deploy to Railway

Railway auto-deploys from GitHub push to `main`. **Never use `railway up`.**

### Reseed TypeDB (after schema changes)

```bash
railway ssh --service ValenceV3 -- python -m app.scripts.init_schema --force
```

## Project Structure

```
ValenceV3/
├── app/
│   ├── __init__.py
│   ├── main.py                          # FastAPI app + startup
│   ├── config.py                        # Settings from env
│   ├── routers/
│   │   ├── health.py                    # Health checks + admin endpoints
│   │   └── deals.py                     # Deal CRUD + upload + extraction
│   ├── services/
│   │   ├── typedb_client.py             # TypeDB connection
│   │   ├── extraction.py                # Claude extraction pipeline
│   │   ├── graph_storage.py             # TypeDB write (all 3 channels)
│   │   ├── graph_reader.py              # TypeDB read (entity context for Q&A)
│   │   ├── topic_router.py              # Question → attribute routing
│   │   ├── segment_introspector.py      # Schema introspection
│   │   ├── cost_tracker.py              # Claude API cost tracking
│   │   └── pdf_parser.py                # PDF text extraction
│   ├── schemas/
│   │   ├── models.py                    # Pydantic API models
│   │   └── extraction_output_v4.py      # V4 extraction Pydantic models
│   ├── scripts/
│   │   ├── init_schema.py               # DB seeding (single entry point)
│   │   └── test_functions.py            # TypeDB function tests
│   └── data/
│       ├── schema_unified.tql           # THE schema (single file)
│       ├── questions.tql                # Base ontology (Categories A-K)
│       ├── ontology_expanded.tql        # Expanded questions (F9+, G5+, I, L, N)
│       ├── ontology_category_m.tql      # Category M: Unsub distributions
│       ├── concepts.tql                 # Concept type seed instances
│       ├── rp_basket_metadata.tql       # RP basket extraction metadata
│       ├── rdp_basket_metadata.tql      # RDP basket extraction metadata
│       ├── investment_pathway_metadata.tql  # Pathway extraction metadata
│       ├── rp_functions.tql             # RP analytical functions
│       ├── rp_analysis_functions.tql    # RP analysis functions (blocker gaps, etc.)
│       ├── mfn_functions.tql            # MFN pattern detection functions
│       └── gold_standard/               # Gold standard eval data
├── src/
│   └── types/
│       └── mfn.generated.ts             # Generated TypeScript types
├── requirements.txt
├── Dockerfile
├── railway.toml
└── CLAUDE.md
```
