"""
Health check endpoints.
"""
from fastapi import APIRouter, Depends

from app.services.typedb_client import TypeDBClient, get_typedb_client
from app.schemas.models import HealthCheck, TypeDBHealth

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthCheck)
async def health_check():
    """Basic health check."""
    return HealthCheck(status="ok", version="2.0.0")


@router.get("/health/typedb", response_model=TypeDBHealth)
async def typedb_health(
    client: TypeDBClient = Depends(get_typedb_client)
):
    """
    Deep TypeDB connection health check.
    
    Verifies:
    - Connection to TypeDB Cloud
    - Database exists
    - Can execute queries
    """
    result = client.health_check()
    return TypeDBHealth(**result)
