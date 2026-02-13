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
                    try { $s has mfn_universe_field $mfnf; };
                select $sid, $name, $desc, $order, $rpf, $mfnf;
            """).resolve()

            segments = []
            for row in result.as_concept_rows():
                sid = row.get("sid").as_attribute().get_value()
                name = row.get("name").as_attribute().get_value()
                desc = row.get("desc").as_attribute().get_value()
                order = row.get("order").as_attribute().get_value()

                rpf_concept = row.get("rpf")
                rpf = rpf_concept.as_attribute().get_value() if rpf_concept else None

                mfnf_concept = row.get("mfnf")
                mfnf = mfnf_concept.as_attribute().get_value() if mfnf_concept else None

                segments.append({
                    "segment_type_id": sid,
                    "name": name,
                    "find_description": desc,
                    "display_order": order,
                    "rp_universe_field": rpf,
                    "mfn_universe_field": mfnf,
                })

            segments.sort(key=lambda x: x["display_order"])
            _segment_cache = segments
            logger.info(f"Loaded {len(segments)} segment types from TypeDB")
            validate_segment_references()
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


def get_mfn_segment_mapping() -> Dict[str, str]:
    """
    Get segment_type_id -> mfn_universe_field mapping.
    Only returns segments that map to MFN universe fields.
    SSoT: loaded from TypeDB mfn_universe_field attribute.
    """
    segments = get_segment_types()
    return {
        s["segment_type_id"]: s["mfn_universe_field"]
        for s in segments
        if s.get("mfn_universe_field")
    }


def validate_segment_references():
    """
    Validate that all segment_type_id strings referenced in mappings
    actually exist in TypeDB. Call at startup to catch drift.
    """
    known_ids = {s["segment_type_id"] for s in get_segment_types()}
    if not known_ids:
        logger.warning("No segment types loaded — skipping validation")
        return

    # Check RP mapping
    referenced_ids = set()
    rp_mapping = get_rp_segment_mapping()
    referenced_ids.update(rp_mapping.keys())

    # Check MFN mapping
    mfn_mapping = get_mfn_segment_mapping()
    referenced_ids.update(mfn_mapping.keys())

    missing = referenced_ids - known_ids
    if missing:
        logger.error(
            f"SEGMENT ID MISMATCH: Mappings reference {missing} "
            f"but TypeDB only has {known_ids}. "
            f"Update TypeDB seed or fix mapping references."
        )
    else:
        logger.info(
            f"Segment ID validation passed: {len(referenced_ids)} "
            f"referenced IDs all exist in TypeDB"
        )


def clear_cache():
    """Clear the segment cache (for testing)."""
    global _segment_cache
    _segment_cache = None
