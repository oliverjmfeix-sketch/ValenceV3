"""
Valence V3 Backend - Covenant Intelligence Platform

FastAPI application with:
- TypeDB 3.x connection on startup
- Auto-initialization of schema if empty
- CORS configuration for Lovable frontend
- All API routers mounted
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.typedb_client import typedb_client
from app.routers import health, deals, ontology, qa, patterns

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def _ensure_schema_loaded():
    """
    Auto-initialize schema if database is empty.
    
    Checks for ontology_category entities - if none exist, loads schema.
    This runs once on first deployment, then skips on subsequent restarts.
    """
    try:
        # Check if schema already loaded
        results = typedb_client.query_read(
            "match $x isa ontology_category; limit 1; select $x;"
        )
        if results:
            logger.info("✓ Schema already loaded (found ontology_category)")
            return
    except Exception as e:
        logger.info(f"Schema check failed (expected on first run): {e}")
    
    # Schema not found - initialize
    logger.info("=" * 50)
    logger.info("Schema not found - auto-initializing...")
    logger.info("=" * 50)
    
    try:
        from typedb.driver import SessionType, TransactionType
        
        DATA_DIR = Path(__file__).parent / "data"
        
        def load_tql(filepath: Path) -> str:
            """Load and clean TQL file."""
            if not filepath.exists():
                logger.error(f"File not found: {filepath}")
                return ""
            content = filepath.read_text()
            # Remove comment-only lines
            lines = [l for l in content.split('\n') 
                    if l.strip() and not l.strip().startswith('#')]
            return '\n'.join(lines)
        
        # Load schema
        schema_file = DATA_DIR / "schema.tql"
        if schema_file.exists():
            logger.info("Loading schema.tql...")
            schema_tql = load_tql(schema_file)
            typedb_client.query_schema(schema_tql)
            logger.info("✓ Schema loaded")
        else:
            logger.error(f"✗ Schema file not found: {schema_file}")
            return
        
        # Load concepts
        concepts_file = DATA_DIR / "concepts.tql"
        if concepts_file.exists():
            logger.info("Loading concepts.tql...")
            concepts_tql = load_tql(concepts_file)
            typedb_client.query_write(concepts_tql)
            logger.info("✓ Concepts loaded")
        else:
            logger.warning(f"⚠ Concepts file not found: {concepts_file}")
        
        # Load questions
        questions_file = DATA_DIR / "questions.tql"
        if questions_file.exists():
            logger.info("Loading questions.tql...")
            questions_tql = load_tql(questions_file)
            typedb_client.query_write(questions_tql)
            logger.info("✓ Questions loaded")
        else:
            logger.warning(f"⚠ Questions file not found: {questions_file}")
        
        logger.info("=" * 50)
        logger.info("✓ Schema auto-initialization complete!")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.error(f"✗ Schema initialization failed: {e}")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events.
    
    - Connects to TypeDB on startup
    - Auto-initializes schema if empty
    - Closes connection on shutdown
    """
    # Startup
    logger.info("=" * 50)
    logger.info("Starting Valence V3 Backend...")
    logger.info("=" * 50)
    logger.info(f"TypeDB: {settings.typedb_address}/{settings.typedb_database}")
    logger.info(f"CORS origins: {settings.cors_origins}")
    
    # Connect to TypeDB
    try:
        typedb_client.connect()
        logger.info("✓ TypeDB connected")
        
        # Auto-initialize schema if needed
        await _ensure_schema_loaded()
        
    except Exception as e:
        logger.error(f"✗ Startup error: {e}")
        # Don't fail startup - allow health endpoint to report status
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    typedb_client.close()


# Create FastAPI app
app = FastAPI(
    title="Valence V3 API",
    description="Covenant Intelligence Platform - Typed Primitives + Provenance",
    version="3.0.0",
    lifespan=lifespan
)

# CORS middleware - allow Lovable frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(health.router)
app.include_router(deals.router)
app.include_router(ontology.router)
app.include_router(qa.router)
app.include_router(patterns.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Valence V3 API",
        "version": "3.0.0",
        "description": "Covenant Intelligence with Typed Primitives + Provenance",
        "docs": "/docs",
        "health": "/api/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
