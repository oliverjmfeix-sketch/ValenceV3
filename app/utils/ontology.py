"""SSoT-compliant category name resolution from TypeDB."""

from typing import Dict
import logging

logger = logging.getLogger(__name__)

_category_name_cache: Dict[str, str] = {}


def get_category_names() -> Dict[str, str]:
    """
    Get category_id -> name mapping from TypeDB.
    Cached after first successful call. Falls back to
    'Category X' if TypeDB is unreachable.
    """
    global _category_name_cache
    if _category_name_cache:
        return _category_name_cache

    try:
        from app.services.typedb_client import typedb_client
        from app.config import settings
        from typedb.driver import TransactionType

        if not typedb_client.driver:
            logger.warning("TypeDB not connected for category names")
            return {}

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            result = tx.query("""
                match
                    $c isa ontology_category,
                        has category_id $cid,
                        has name $cname;
                select $cid, $cname;
            """).resolve()

            names = {}
            for row in result.as_concept_rows():
                cid = row.get("cid").as_attribute().get_value()
                cname = row.get("cname").as_attribute().get_value()
                names[cid] = cname

            _category_name_cache = names
            logger.info(f"Loaded {len(names)} category names from TypeDB")
            return names
        finally:
            tx.close()
    except Exception as e:
        logger.warning(f"Failed to load category names: {e}")
        return {}


def resolve_category_name(category_id: str) -> str:
    """Resolve a single category_id to its name."""
    names = get_category_names()
    return names.get(category_id, f"Category {category_id}")
