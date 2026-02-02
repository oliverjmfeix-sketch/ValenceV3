"""
Deal endpoints - Simplified for V3 launch
"""
import os
import uuid
import logging
from typing import List, Dict, Any

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from app.config import settings
from app.services.typedb_client import typedb_client
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/deals", tags=["Deals"])


@router.get("")
async def list_deals() -> List[Dict[str, Any]]:
    """List all deals."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = """
                match 
                    $d isa deal, 
                    has deal_id $id, 
                    has deal_name $name;
                select $id, $name;
            """
            result = tx.query(query)
            
            deals = []
            for row in result.as_concept_rows():
                deals.append({
                    "deal_id": row.get("id").as_attribute().get_value(),
                    "deal_name": row.get("name").as_attribute().get_value()
                })
            return deals
        finally:
            tx.close()
    except Exception as e:
        logger.error(f"Error listing deals: {e}")
        return []  # Return empty list on error


@router.get("/{deal_id}")
async def get_deal(deal_id: str) -> Dict[str, Any]:
    """Get a single deal."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"""
                match 
                    $d isa deal, 
                    has deal_id "{deal_id}", 
                    has deal_name $name;
                select $name;
            """
            result = tx.query(query)
            rows = list(result.as_concept_rows())
            
            if not rows:
                raise HTTPException(status_code=404, detail="Deal not found")
            
            return {
                "deal_id": deal_id,
                "deal_name": rows[0].get("name").as_attribute().get_value(),
                "answers": {},  # TODO: Fetch from provision_has_answer
                "applicabilities": {}  # TODO: Fetch from concept_applicability
            }
        finally:
            tx.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting deal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_deal(
    deal_name: str = Form(...),
    borrower: str = Form(...)
) -> Dict[str, Any]:
    """Create a new deal (without PDF for now)."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    deal_id = str(uuid.uuid4())[:8]
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            query = f"""
                insert 
                    $d isa deal,
                    has deal_id "{deal_id}",
                    has deal_name "{deal_name}",
                    has borrower_name "{borrower}";
            """
            tx.query(query)
            tx.commit()
            
            return {
                "deal_id": deal_id,
                "deal_name": deal_name,
                "borrower": borrower,
                "status": "created"
            }
        except Exception as e:
            tx.close()
            raise e
    except Exception as e:
        logger.error(f"Error creating deal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{deal_id}")
async def delete_deal(deal_id: str) -> Dict[str, Any]:
    """Delete a deal."""
    if not typedb_client.driver:
        raise HTTPException(status_code=503, detail="Database not connected")
    
    try:
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            query = f"""
                match 
                    $d isa deal, has deal_id "{deal_id}";
                delete 
                    $d isa deal;
            """
            tx.query(query)
            tx.commit()
            
            return {"status": "deleted", "deal_id": deal_id}
        except Exception as e:
            tx.close()
            raise e
    except Exception as e:
        logger.error(f"Error deleting deal: {e}")
        raise HTTPException(status_code=500, detail=str(e))
