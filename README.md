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
│   ├── eval/
│   │   ├── cc_questions.py              # Cross-covenant eval questions
│   │   └── duck_creek_ablation.py       # Duck Creek ablation test
│   ├── prompts/
│   │   └── reasoning.py                 # Reasoning prompt templates
│   ├── routers/
│   │   ├── ablation.py                  # Ablation testing endpoints
│   │   ├── deals.py                     # Deal CRUD + upload + extraction + /ask-graph
│   │   ├── eval.py                      # Legacy eval endpoints
│   │   ├── graph_eval.py                # Gold standard eval runner
│   │   ├── health.py                    # Health checks + admin endpoints
│   │   ├── mfn_eval.py                  # MFN eval (legacy, use graph_eval)
│   │   ├── ontology.py                  # Ontology query endpoints
│   │   └── qa.py                        # Q&A endpoints
│   ├── services/
│   │   ├── typedb_client.py             # TypeDB connection
│   │   ├── extraction.py                # Claude extraction pipeline
│   │   ├── graph_storage.py             # TypeDB write (all 3 channels)
│   │   ├── graph_reader.py              # TypeDB read (legacy fetchers)
│   │   ├── graph_traversal.py           # Polymorphic entity fetch + cross-covenant walk
│   │   ├── topic_router.py              # Question → category routing (SSoT)
│   │   ├── segment_introspector.py      # Schema introspection
│   │   ├── trace_collector.py           # Trace/debug collector
│   │   ├── cost_tracker.py              # Claude API cost tracking
│   │   └── pdf_parser.py                # PDF text extraction
│   ├── schemas/
│   │   ├── models.py                    # Pydantic API models
│   │   └── extraction_output_v4.py      # V4 extraction Pydantic models
│   ├── scripts/
│   │   ├── init_schema.py               # DB seeding (single entry point)
│   │   └── (various verify_*.py)        # Verification scripts
│   ├── data/
│   │   ├── schema_unified.tql           # THE schema (single file)
│   │   ├── questions.tql                # Base ontology (Categories A-K)
│   │   ├── categories.tql               # Category definitions + links
│   │   ├── mfn_ontology_questions.tql   # MFN questions (43 across 6 categories)
│   │   ├── seed_synthesis_guidance.tql  # Per-category synthesis guidance
│   │   ├── seed_mfn_annotations.tql     # MFN attribute annotations
│   │   ├── seed_mfn_entity_list_questions.tql  # MFN entity-list extraction
│   │   ├── rp_functions.tql             # RP analytical functions
│   │   ├── mfn_functions.tql            # MFN pattern detection functions
│   │   ├── annotation_functions.tql     # Entity annotation function
│   │   └── gold_standard/               # Gold standard eval data (JSON)
│   └── utils/
│       └── ontology.py                  # Ontology utilities
├── src/
│   └── types/
│       └── mfn.generated.ts             # Generated TypeScript types
├── requirements.txt
├── Dockerfile
├── railway.toml
└── CLAUDE.md
```
