"""
Valence Backend - Legal Document Analysis Platform

Main FastAPI application with:
- TypeDB connection on startup
- CORS configuration for Lovable frontend
- All API routers mounted
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.services.typedb_client import typedb_client
from app.routers import health, deals, ontology, qa

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup and shutdown events.
    
    Connects to TypeDB on startup, closes on shutdown.
    """
    # Startup
    logger.info("Starting Valence Backend...")
    logger.info(f"TypeDB: {settings.normalized_typedb_address}/{settings.typedb_database}")
    logger.info(f"CORS origins: {settings.cors_origins_list}")
    
    # Connect to TypeDB
    try:
        typedb_client.connect()
        logger.info("TypeDB connected successfully")
    except Exception as e:
        logger.error(f"TypeDB connection failed: {e}")
        # Don't fail startup - allow health endpoint to report status
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    typedb_client.close()


# Create FastAPI app
app = FastAPI(
    title="Valence API",
    description="Legal document analysis with typed primitives and provenance",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware - allow Lovable frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(health.router)
app.include_router(deals.router)
app.include_router(ontology.router)
app.include_router(qa.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Valence API",
        "version": "2.0.0",
        "description": "Legal document analysis with typed primitives and provenance",
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
