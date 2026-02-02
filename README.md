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
4. **Inference Rules** - Pattern detection (J.Crew, Serta, yield exclusion) happens in TypeDB, not Python.

## Setup

### 1. Clone and Install

```bash
cd valence-backend
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
```

### 3. Initialize TypeDB Schema

```bash
python -m app.scripts.init_schema
```

### 4. Seed Ontology Questions

```bash
python -m app.scripts.seed_ontology
```

### 5. Run Locally

```bash
uvicorn app.main:app --reload --port 8000
```

## Deploy to Railway

### 1. Create Railway Project

```bash
railway login
railway init
```

### 2. Add Volume for PDF Storage

In Railway Dashboard:
1. Go to Service → Settings → Volumes
2. Add Volume: name=`pdf-storage`, mount=`/app/uploads`

### 3. Set Environment Variables

```bash
railway variables set TYPEDB_ADDRESS=your-cluster.typedb.cloud:1729
railway variables set TYPEDB_DATABASE=valence
railway variables set TYPEDB_USERNAME=admin
railway variables set TYPEDB_PASSWORD=xxx
railway variables set ANTHROPIC_API_KEY=sk-ant-xxx
railway variables set CORS_ORIGINS=https://your-app.lovable.app
```

### 4. Deploy

```bash
railway up
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/health/typedb` | GET | TypeDB connection check |
| `/api/deals` | GET | List all deals |
| `/api/deals/{id}` | GET | Get deal with primitives |
| `/api/deals/{id}` | DELETE | Delete deal |
| `/api/deals/upload` | POST | Upload PDF and extract |
| `/api/deals/{id}/pdf` | GET | Serve stored PDF |
| `/api/ontology/questions` | GET | All questions by category |
| `/api/deals/{id}/answers` | GET | All answers for a deal |
| `/api/deals/{id}/provenance/{attribute}` | GET | Source for specific primitive |
| `/api/deals/{id}/qa` | POST | Ask natural language question |
| `/api/qa/cross-deal` | POST | Query across all deals |

## Project Structure

```
valence-backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app + startup
│   ├── config.py               # Settings from env
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health.py           # Health checks
│   │   ├── deals.py            # Deal CRUD + upload
│   │   ├── ontology.py         # Ontology questions
│   │   └── qa.py               # Q&A interface
│   ├── services/
│   │   ├── __init__.py
│   │   ├── typedb_client.py    # TypeDB connection
│   │   ├── extraction.py       # Claude extraction
│   │   ├── pdf_parser.py       # PDF text extraction
│   │   └── qa_engine.py        # Question answering
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── deal_repository.py
│   │   ├── answer_repository.py
│   │   └── ontology_repository.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── models.py           # Pydantic models
│   └── scripts/
│       ├── init_schema.py      # Initialize TypeDB schema
│       └── seed_ontology.py    # Seed ontology questions
├── data/
│   ├── schema.tql              # TypeDB schema
│   ├── ontology_mfn.tql        # MFN ontology questions
│   └── ontology_rp.tql         # RP ontology questions
├── requirements.txt
├── Dockerfile
├── railway.toml
└── .env.example
```
