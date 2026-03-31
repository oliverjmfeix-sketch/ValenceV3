"""DI query service — Python wrapper for TypeDB DI functions.

SSoT: All calculations happen in TypeDB functions.
Python only calls functions and formats results.
Returns None when data is missing (no hardcoded defaults).
"""

import logging
from typing import Optional, List

from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)


class DIQueryService:
    """Service for querying DI TypeDB functions."""

    def _read_tx(self):
        return typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)

    async def get_total_incremental_capacity(self, deal_id: str) -> Optional[float]:
        """Get total incremental facility freebie capacity."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = total_incremental_capacity("{pid}");
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"total_incremental_capacity failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def get_total_capped_capacity(self, deal_id: str) -> Optional[float]:
        """Get total capacity from all capped baskets."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = total_capped_basket_capacity("{pid}");
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"total_capped_basket_capacity failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def get_basket_capacity(self, deal_id: str, basket_type: str) -> Optional[float]:
        """Get capacity for a specific basket type."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = basket_capacity("{pid}", "{basket_type}");
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"basket_capacity failed for {deal_id}/{basket_type}: {e}")
            return None
        finally:
            tx.close()

    async def get_projected_grower_capacity(self, deal_id: str, ebitda: float) -> Optional[float]:
        """Get projected grower basket capacity at given EBITDA."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = projected_grower_capacity("{pid}", {ebitda});
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"projected_grower_capacity failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def check_ied_priming_risk(self, deal_id: str) -> Optional[bool]:
        """Check if IED priming risk exists. Returns None on error."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match true == has_ied_priming_risk("{pid}");
                select;
            ''').resolve()
            return len(list(result.as_concept_rows())) > 0
        except Exception as e:
            logger.warning(f"has_ied_priming_risk check failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def check_trapdoor_basket(self, deal_id: str) -> Optional[bool]:
        """Check if trapdoor basket exists. Returns None on error."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match true == has_trapdoor_basket("{pid}");
                select;
            ''').resolve()
            return len(list(result.as_concept_rows())) > 0
        except Exception as e:
            logger.warning(f"has_trapdoor_basket check failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def get_vulnerability_report(self, deal_id: str) -> dict:
        """Get vulnerability analysis for DI covenant.

        Returns dict with vulnerability flags. Values are None if check failed.
        """
        return {
            "ied_priming_risk": await self.check_ied_priming_risk(deal_id),
            "trapdoor_basket": await self.check_trapdoor_basket(deal_id),
        }

    async def get_basket_count(self, deal_id: str) -> Optional[int]:
        """Get count of permitted debt baskets."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $count = count_permitted_baskets("{pid}");
                select $count;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("count").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"count_permitted_baskets failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def get_grower_basket_count(self, deal_id: str) -> Optional[int]:
        """Get count of grower baskets."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $count = count_grower_baskets("{pid}");
                select $count;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("count").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"count_grower_baskets failed for {deal_id}: {e}")
            return None
        finally:
            tx.close()

    async def get_basket_types(self, deal_id: str) -> List[str]:
        """Get all basket type labels for a provision."""
        pid = f"{deal_id}_di"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $type in get_basket_types("{pid}");
                select $type;
            ''').resolve()
            return [row.get("type").as_value().get() for row in result.as_concept_rows()]
        except Exception as e:
            logger.warning(f"get_basket_types failed for {deal_id}: {e}")
            return []
        finally:
            tx.close()


di_query_service = DIQueryService()
