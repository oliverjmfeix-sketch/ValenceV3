"""
Valence V3 Backend - Covenant Intelligence Platform

FastAPI application with:
- TypeDB 3.x connection on startup
- Auto-schema initialization if database is empty
- CORS configuration for Lovable frontend
- All API routers mounted
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client
from app.routers import health, deals, ontology, qa, patterns

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _load_tql_file(filepath: Path) -> str:
    """Load and clean TQL file - remove comments."""
    if not filepath.exists():
        logger.warning(f"TQL file not found: {filepath}")
        return ""
    content = filepath.read_text()
    lines = [l for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
    return '\n'.join(lines)


async def _ensure_schema_loaded():
    """
    Auto-initialize schema if database is empty.
    
    Uses TypeDB 3.x API directly via the driver.
    """
    driver = typedb_client.driver
    db_name = settings.typedb_database
    
    if not driver:
        logger.error("No TypeDB driver available")
        return
    
    # Check if database exists
    if not driver.databases.contains(db_name):
        logger.info(f"Creating database: {db_name}")
        driver.databases.create(db_name)
    
    # Check if schema already loaded by looking for ontology_category
    try:
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            result = list(tx.query("match $x isa ontology_category; limit 1;").as_concept_rows())
            if result:
                logger.info("✓ Schema already loaded")
                return
        finally:
            tx.close()
    except Exception as e:
        logger.info(f"Schema check: {e} - will initialize")
    
    # Load schema files
    DATA_DIR = Path(__file__).parent / "data"
    
    # 1. Load schema (define statements)
    schema_file = DATA_DIR / "schema.tql"
    if schema_file.exists():
        logger.info("Loading schema.tql...")
        schema_tql = _load_tql_file(schema_file)
        if schema_tql:
            tx = driver.transaction(db_name, TransactionType.SCHEMA)
            try:
                tx.query(schema_tql)
                tx.commit()
                logger.info("✓ Schema loaded")
            except Exception as e:
                logger.error(f"Schema load failed: {e}")
                tx.close()
                return
    else:
        logger.error(f"Schema file not found: {schema_file}")
        return
    
    # 2. Load concepts (insert statements)
    concepts_file = DATA_DIR / "concepts.tql"
    if concepts_file.exists():
        logger.info("Loading concepts.tql...")
        concepts_tql = _load_tql_file(concepts_file)
        if concepts_tql:
            tx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                tx.query(concepts_tql)
                tx.commit()
                logger.info("✓ Concepts loaded")
            except Exception as e:
                logger.error(f"Concepts load failed: {e}")
                tx.close()
    
    # 3. Load questions (insert statements)
    questions_file = DATA_DIR / "questions.tql"
    if questions_file.exists():
        logger.info("Loading questions.tql...")
        questions_tql = _load_tql_file(questions_file)
        if questions_tql:
            tx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                tx.query(questions_tql)
                tx.commit()
                logger.info("✓ Questions loaded")
            except Exception as e:
                logger.error(f"Questions load failed: {e}")
                tx.close()
    
    logger.info("✓ Schema initialization complete!")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("Starting Valence V3 Backend...")
    logger.info("=" * 60)
    logger.info(f"TypeDB: {settings.typedb_address}/{settings.typedb_database}")
    logger.info(f"CORS origins: {settings.cors_origins}")
    
    # Connect to TypeDB
    try:
        typedb_client.connect()
        logger.info("✓ TypeDB connected")
        
        # Auto-initialize schema if database is empty
        await _ensure_schema_loaded()
        
    except Exception as e:
        logger.error(f"✗ Startup error: {e}")
        # Don't fail startup - allow health endpoint to report status
    
    yield
    
    # Shutdown
    logger.info("Shutting down Valence V3...")
    typedb_client.close()
    logger.info("✓ Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Valence V3 API",
    description="Covenant Intelligence Platform - Typed Primitives + Provenance",
    version="3.0.0",
    lifespan=lifespan
)

# CORS middleware
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
