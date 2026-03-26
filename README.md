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
│   │   ├── eval.py                      # Auto-eval pipeline
│   │   ├── graph_eval.py                # Gold standard eval runner
│   │   ├── health.py                    # Health checks + admin endpoints
│   │   ├── mfn_eval.py                  # MFN eval (legacy, use graph_eval)
│   │   └── ontology.py                  # Ontology query endpoints
│   ├── services/
│   │   ├── typedb_client.py             # TypeDB connection
│   │   ├── extraction.py                # Claude extraction pipeline
│   │   ├── graph_storage.py             # TypeDB write (all 3 channels)
│   │   ├── graph_reader.py              # TypeDB read + annotation cache
│   │   ├── graph_traversal.py           # Polymorphic entity fetch + cross-covenant walk
│   │   ├── graph_queries.py             # Reusable TypeDB query helpers
│   │   ├── topic_router.py              # Question → category routing (SSoT)
│   │   ├── segment_introspector.py      # Schema introspection
│   │   ├── trace_collector.py           # Trace/debug collector
│   │   ├── cost_tracker.py              # Claude API cost tracking
│   │   └── pdf_parser.py                # PDF text extraction
│   ├── schemas/
│   │   ├── models.py                    # Pydantic API models
│   │   └── extraction_response.py       # Extraction response Pydantic models
│   ├── scripts/
│   │   ├── init_schema.py               # DB seeding (single entry point, 26 steps)
│   │   ├── gap_report.py                # Reusable diagnostic
│   │   └── run_mfn_eval.py              # Standalone MFN eval runner
│   ├── data/
│   │   ├── schema_unified.tql           # THE schema (single file, ~1800 lines)
│   │   ├── concepts.tql                 # ~170 concept instances
│   │   ├── jcrew_concepts_seed.tql      # 72 J.Crew concept instances
│   │   ├── questions.tql                # Base ontology (Categories A-K)
│   │   ├── categories.tql               # Category definitions + links
│   │   ├── ontology_expanded.tql        # Extended questions
│   │   ├── ontology_category_m.tql      # Category M questions
│   │   ├── ontology_category_p.tql      # Category P questions
│   │   ├── jcrew_questions_seed.tql     # J.Crew questions (69)
│   │   ├── mfn_concepts_extended.tql    # MFN-specific concepts
│   │   ├── mfn_ontology_questions.tql   # MFN questions (43)
│   │   ├── seed_*.tql                   # Seed data (annotations, mappings, etc.)
│   │   ├── mfn_functions.tql            # MFN pattern detection functions
│   │   ├── segment_types_seed.tql       # Document segment type definitions
│   │   ├── annotation_functions.tql     # Entity annotation function
│   │   ├── gold_standard/               # Gold standard eval data (JSON)
│   │   └── eval_results/               # Local copies of eval output files
│   └── utils/
│       └── ontology.py                  # Ontology utilities
├── tests/
│   ├── test_extraction_response.py      # Extraction response schema tests
│   └── test_topic_router.py             # TopicRouter SSoT compliance tests
├── src/
│   └── types/
│       └── mfn.generated.ts             # Generated TypeScript types
├── requirements.txt
├── Dockerfile
├── railway.toml
└── CLAUDE.md
```
