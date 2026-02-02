"""
Health check endpoints - Simplified
"""
from typing import Dict, Any
from fastapi import APIRouter

from app.services.typedb_client import typedb_client

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Basic health check."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }


@router.get("/api/health")
async def api_health_check() -> Dict[str, Any]:
    """API health check (with /api prefix)."""
    return {
        "status": "ok" if typedb_client.is_connected else "degraded",
        "version": "3.0.0",
        "typedb_connected": typedb_client.is_connected
    }
