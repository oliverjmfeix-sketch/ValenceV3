"""
Valence V3 Backend - Covenant Intelligence Platform

FastAPI application with:
- TypeDB 3.x connection verification on startup
- CORS configuration for Lovable frontend
- All API routers mounted

DB seeding is handled by init_schema.py (the single source of truth for TQL parsing).
Run `python -m app.scripts.init_schema` to seed a fresh database.
"""
import logging
from contextlib import asynccontextmanager

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


def _ensure_db_ready():
    """Verify TypeDB connection and database exist. Does NOT seed data."""
    driver = typedb_client.driver
    db_name = settings.typedb_database

    if not driver:
        raise RuntimeError("No TypeDB driver available")

    if not driver.databases.contains(db_name):
        logger.error(f"Database '{db_name}' not found. Run: python -m app.scripts.init_schema")
        raise RuntimeError(f"Database '{db_name}' not found")

    logger.info(f"TypeDB database '{db_name}' verified")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("Starting Valence V3 Backend...")
    logger.info("=" * 60)
    logger.info(f"TypeDB: {settings.typedb_address}/{settings.typedb_database}")
    logger.info(f"CORS origins: {settings.cors_origins}")

    # Connect to TypeDB and verify database exists
    try:
        typedb_client.connect()
        logger.info("TypeDB connected")
        _ensure_db_ready()
    except Exception as e:
        logger.error(f"Startup error: {e}")
        # Don't fail startup - allow health endpoint to report status

    yield

    # Shutdown
    logger.info("Shutting down Valence V3...")
    typedb_client.close()
    logger.info("Shutdown complete")


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
