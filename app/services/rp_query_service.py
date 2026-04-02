"""RP query service — dumb pipe to TypeDB functions. NO business logic here."""

import logging
from typing import Optional

from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)


class RPQueryService:
    """Calls TypeDB functions. All formulas live in TypeDB, not here."""

    def _read_tx(self):
        return typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )

    async def get_day_one_capacity(self, deal_id: str) -> Optional[float]:
        """Call TypeDB function. Formula is in TypeDB, not here."""
        pid = f"{deal_id}_rp"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = total_day_one_rp_capacity("{pid}");
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(f"total_day_one_rp_capacity failed for {deal_id}: {e}")
            return None
        finally:
            if tx.is_open():
                tx.close()

    async def get_capacity_by_category(
        self, deal_id: str, category: str
    ) -> Optional[float]:
        """Call TypeDB function for category-specific capacity."""
        pid = f"{deal_id}_rp"
        tx = self._read_tx()
        try:
            result = tx.query(f'''
                match let $cap = rp_capacity_by_category("{pid}", "{category}");
                select $cap;
            ''').resolve()
            rows = list(result.as_concept_rows())
            return rows[0].get("cap").as_value().get() if rows else None
        except Exception as e:
            logger.warning(
                f"rp_capacity_by_category failed for {deal_id}/{category}: {e}"
            )
            return None
        finally:
            if tx.is_open():
                tx.close()


rp_query_service = RPQueryService()
