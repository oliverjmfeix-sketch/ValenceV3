"""
Graph Reader — TypeDB query utilities and annotation validation.

Provides:
  - Annotation loading and validation (used by main.py startup, deals.py)
  - Generic query execution (run_query) with tracing
  - Value extraction helpers (safe_val, safe_type)
  - Formatting utilities (fmt_dollar, fmt_pct)

Entity fetching is handled by graph_traversal.py via a single polymorphic fetch.
"""
import logging
import re as _re
import time as _time
from typing import Dict, List, Optional

from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client
from app.services.trace_collector import TraceCollector

logger = logging.getLogger(__name__)

# ── Lazy-loaded question text cache ─────────────────────────────────────────
_question_texts: Optional[dict] = None

# ── Lazy-loaded attribute annotation cache (replaces attribute_glossary.py) ──
_annotation_cache: Optional[Dict[str, Dict[str, str]]] = None
_annotation_cache_time: float = 0
_ANNOTATION_CACHE_TTL = 600  # 10 minutes


def _get_question_texts() -> dict:
    """Return question_id → question_text mapping, loading once from TypeDB."""
    global _question_texts
    if _question_texts is None:
        _question_texts = _load_question_texts()
    return _question_texts


def _load_question_texts() -> dict:
    """Load all ontology question texts from TypeDB."""
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        query = """
            match $q isa ontology_question,
                has question_id $qid,
                has question_text $qt;
            select $qid, $qt;
        """
        results = list(tx.query(query).resolve().as_concept_rows())
        texts = {}
        for row in results:
            qid = row.get("qid").as_attribute().get_value()
            qt = row.get("qt").as_attribute().get_value()
            texts[qid] = qt
        logger.info(f"Loaded {len(texts)} question texts for entity annotation")
        return texts
    except Exception as e:
        logger.warning(f"Failed to load question texts: {e}")
        return {}
    finally:
        if tx.is_open():
            tx.close()


def _get_annotation_map() -> Dict[str, Dict[str, str]]:
    """Load attribute → question_id mapping from TypeDB.

    Returns dict[entity_type][attribute_name] → question_id.
    Replaces ATTRIBUTE_GLOSSARY and REALLOCATION_ANNOTATIONS from attribute_glossary.py.
    """
    global _annotation_cache, _annotation_cache_time
    now = _time.time()
    if _annotation_cache is not None and (now - _annotation_cache_time) < _ANNOTATION_CACHE_TTL:
        return _annotation_cache

    query = """
        match
            (question: $q) isa question_annotates_attribute,
                has target_entity_type $et,
                has target_attribute_name $an;
            $q has question_id $qid;
        select $qid, $et, $an;
    """
    result_map: Dict[str, Dict[str, str]] = {}
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        results = list(tx.query(query).resolve().as_concept_rows())
        for row in results:
            qid = row.get("qid").as_attribute().get_value()
            et = row.get("et").as_attribute().get_value()
            an = row.get("an").as_attribute().get_value()
            if qid and et and an:
                result_map.setdefault(et, {})[an] = qid
        logger.info(f"Loaded {sum(len(v) for v in result_map.values())} attribute annotations from TypeDB")
    except Exception as e:
        logger.error(f"Failed to load attribute annotations from TypeDB: {e}")
        result_map = {}
    finally:
        if tx.is_open():
            tx.close()

    _annotation_cache = result_map
    _annotation_cache_time = now
    return result_map


def validate_annotations() -> bool:
    """Validate that question_annotates_attribute data in TypeDB is consistent."""
    annotations = _get_annotation_map()
    question_texts = _get_question_texts()

    total = sum(len(v) for v in annotations.values())
    missing = []
    for et, attrs in annotations.items():
        for attr_name, qid in attrs.items():
            if qid not in question_texts:
                missing.append(f"  {et}.{attr_name} -> {qid}")

    if missing:
        logger.warning(f"Annotation references {len(missing)} missing question_ids:\n" + "\n".join(missing))
    else:
        logger.info(f"Annotation validation passed: {total} annotations, all question_ids found")

    return len(missing) == 0


def fmt_dollar(val) -> str:
    """Format a numeric value as $X,XXX,XXX."""
    if val is None:
        return ""
    try:
        return f"${val:,.0f}"
    except (TypeError, ValueError):
        return str(val)


def fmt_pct(val) -> str:
    """Format a numeric value as X%.

    Values stored as decimals (e.g., 1.0 = 100%, 0.5 = 50%) are converted.
    Values > 1 are assumed to already be percentages.
    """
    if val is None:
        return ""
    try:
        v = float(val)
        if v <= 1.0:
            return f"{v * 100:.0f}%"
        return f"{v:.1f}%"
    except (TypeError, ValueError):
        return str(val)


def safe_val(row, key: str):
    """Extract attribute or value from a TypeDB row, returning None on failure.

    Handles both has-bound variables (Attribute) and let-bound variables (Value).
    """
    try:
        concept = row.get(key)
        if concept is None:
            return None
        try:
            return concept.as_attribute().get_value()
        except Exception:
            return concept.as_value().get()
    except Exception:
        return None


def safe_type(row, key: str) -> Optional[str]:
    """Get the type label of an entity variable."""
    try:
        concept = row.get(key)
        if concept is None:
            return None
        return concept.as_entity().get_type().get_label()
    except Exception:
        return None


def _get_variable_names(query: str) -> List[str]:
    """Extract $variable names from the select clause of a TQL query."""
    select_match = _re.search(r'select\s+(.+?);', query, _re.IGNORECASE | _re.DOTALL)
    if select_match:
        return _re.findall(r'\$(\w+)', select_match.group(1))
    return []


def run_query(query: str, trace: TraceCollector = None, trace_name: str = "") -> list:
    """Execute a read query and return rows. Optionally trace."""
    start = _time.time()
    tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
    try:
        result = list(tx.query(query).resolve().as_concept_rows())
        duration_ms = (_time.time() - start) * 1000

        if trace and trace_name:
            sample = []
            for row in result[:5]:
                row_dict = {}
                for var_name in _get_variable_names(query):
                    val = safe_val(row, var_name)
                    if val is not None:
                        row_dict[var_name] = val
                sample.append(row_dict)
            trace.add_query(trace_name, query, len(result), duration_ms, sample)

        return result
    except Exception as e:
        logger.debug(f"Query failed: {e}")
        if trace and trace_name:
            trace.add_query(trace_name, query, 0, (_time.time() - start) * 1000)
        return []
    finally:
        tx.close()
