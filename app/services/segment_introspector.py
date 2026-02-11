"""SSoT-compliant segment type loader from TypeDB."""

from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

_segment_cache: Optional[List[Dict]] = None


def get_segment_types() -> List[Dict]:
    """
    Load document_segment_type entities from TypeDB.
    Cached after first successful call.

    Returns list of dicts with: segment_type_id, name, find_description,
    display_order, rp_universe_field (nullable).
    """
    global _segment_cache
    if _segment_cache is not None:
        return _segment_cache

    try:
        from app.services.typedb_client import typedb_client
        from app.config import settings
        from typedb.driver import TransactionType

        if not typedb_client.driver:
            logger.warning("TypeDB not connected for segment types")
            return []

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            result = tx.query("""
                match
                    $s isa document_segment_type,
                        has segment_type_id $sid,
                        has name $name,
                        has find_description $desc,
                        has display_order $order;
                    try { $s has rp_universe_field $rpf; };
                select $sid, $name, $desc, $order, $rpf;
            """).resolve()

            segments = []
            for row in result.as_concept_rows():
                sid = row.get("sid").as_attribute().get_value()
                name = row.get("name").as_attribute().get_value()
                desc = row.get("desc").as_attribute().get_value()
                order = row.get("order").as_attribute().get_value()

                rpf_concept = row.get("rpf")
                rpf = rpf_concept.as_attribute().get_value() if rpf_concept else None

                segments.append({
                    "segment_type_id": sid,
                    "name": name,
                    "find_description": desc,
                    "display_order": order,
                    "rp_universe_field": rpf,
                })

            segments.sort(key=lambda x: x["display_order"])
            _segment_cache = segments
            logger.info(f"Loaded {len(segments)} segment types from TypeDB")
            return segments
        finally:
            tx.close()
    except Exception as e:
        logger.warning(f"Failed to load segment types: {e}")
        return []


def get_rp_segment_mapping() -> Dict[str, str]:
    """
    Get segment_type_id -> rp_universe_field mapping.
    Only returns segments that map to RPUniverse fields.
    """
    segments = get_segment_types()
    return {
        s["segment_type_id"]: s["rp_universe_field"]
        for s in segments
        if s.get("rp_universe_field")
    }


def clear_cache():
    """Clear the segment cache (for testing)."""
    global _segment_cache
    _segment_cache = None
