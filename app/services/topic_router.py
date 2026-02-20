"""
TopicRouter — SSoT-compliant question routing via TypeDB metadata.

Replaces ALL hardcoded keyword→attribute mappings in qa_engine.py and deals.py.
Category names, descriptions, field names, and covenant types are queried from
TypeDB at runtime and cached with a configurable TTL.

The test: "If I add a new ontology_question to TypeDB with a category_has_question
relation, does the Q&A layer automatically know about it without code changes?"
Answer: YES.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.services.typedb_client import TypeDBClient, get_typedb_client

logger = logging.getLogger(__name__)


@dataclass
class CategoryMetadata:
    """Runtime metadata for a single ontology category, loaded from TypeDB."""
    category_id: str
    name: str
    description: str
    covenant_type: str  # "RP" or "MFN"
    question_ids: List[str] = field(default_factory=list)
    target_fields: List[str] = field(default_factory=list)
    target_concept_types: List[str] = field(default_factory=list)
    # Derived at load time from name + description
    keywords: Set[str] = field(default_factory=set)


@dataclass
class TopicRouteResult:
    """Result of routing a user question to TypeDB categories."""
    matched_categories: List[CategoryMetadata]
    covenant_type: str  # "mfn", "rp", or "both"
    question_ids: List[str]  # All question IDs across matched categories
    all_target_fields: List[str]  # All target field names across matches
    is_specific: bool  # True if matched <= 3 categories (targeted query feasible)


# Stopwords to exclude when building keyword sets from names/descriptions
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "of", "in", "to",
    "for", "with", "on", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "out",
    "off", "over", "under", "again", "further", "then", "once", "that",
    "this", "these", "those", "and", "but", "or", "nor", "not", "so",
    "if", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "any", "only",
})

# Minimum word length to include in keyword matching
_MIN_KEYWORD_LEN = 3


def _tokenize(text: str) -> Set[str]:
    """Tokenize text into lowercase words, excluding stopwords and short words."""
    words = re.findall(r'[a-z][a-z0-9]+', text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) >= _MIN_KEYWORD_LEN}


class TopicRouter:
    """Route user questions to relevant TypeDB categories. SSoT-compliant.

    All routing metadata (category names, descriptions, field names, covenant
    types) is loaded from TypeDB at runtime — zero hardcoded mappings.
    """

    def __init__(
        self,
        client: Optional[TypeDBClient] = None,
        cache_ttl_seconds: int = 300,
    ):
        self._client = client or get_typedb_client()
        self._cache: Optional[Dict[str, CategoryMetadata]] = None
        self._cache_time: float = 0
        self._ttl = cache_ttl_seconds

    # ── Cache management ──────────────────────────────────────────────

    def _get_cached_metadata(self) -> Dict[str, CategoryMetadata]:
        """Return cached category metadata, refreshing if TTL expired."""
        now = time.time()
        if self._cache is None or (now - self._cache_time) > self._ttl:
            try:
                self._cache = self._load_category_metadata()
                self._cache_time = now
                logger.info(
                    "TopicRouter cache refreshed: %d categories loaded",
                    len(self._cache),
                )
            except Exception as e:
                logger.error("TopicRouter cache refresh failed: %s", e)
                if self._cache is not None:
                    # Stale cache is better than no cache
                    return self._cache
                raise
        return self._cache

    def invalidate_cache(self) -> None:
        """Force cache refresh on next access."""
        self._cache = None
        self._cache_time = 0

    # ── TypeDB metadata loading ───────────────────────────────────────

    def _load_category_metadata(self) -> Dict[str, CategoryMetadata]:
        """Query TypeDB for all category → question → field mappings.

        Single method that builds the complete routing lookup from TypeDB.
        """
        categories: Dict[str, CategoryMetadata] = {}

        with self._client.read_transaction() as tx:
            # 1. Load all categories (covenant_type lives on questions, not categories)
            cat_query = """
                match
                    $c isa ontology_category,
                        has category_id $cid,
                        has name $cname;
                    try { $c has description $cdesc; };
                select $cid, $cname, $cdesc;
            """
            cat_result = tx.query(cat_query).resolve()
            for row in cat_result.as_concept_rows():
                cid = _safe_get_value(row, "cid")
                if not cid:
                    continue
                cname = _safe_get_value(row, "cname", "")
                cdesc = _safe_get_value(row, "cdesc", "")

                # Build keyword set from name + description
                keywords = _tokenize(cname) | _tokenize(cdesc)

                categories[cid] = CategoryMetadata(
                    category_id=cid,
                    name=cname,
                    description=cdesc,
                    covenant_type="RP",  # default; derived from questions below
                    keywords=keywords,
                )

            # 2. Load question → category mappings WITH covenant_type from questions
            q_query = """
                match
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid;
                    $q has question_id $qid, has covenant_type $qctype;
                select $cid, $qid, $qctype;
            """
            q_result = tx.query(q_query).resolve()
            # Track covenant types per category to derive category covenant_type
            cat_covenant_types: Dict[str, set] = {}
            for row in q_result.as_concept_rows():
                cid = _safe_get_value(row, "cid")
                qid = _safe_get_value(row, "qid")
                qctype = _safe_get_value(row, "qctype", "RP")
                if cid and qid and cid in categories:
                    categories[cid].question_ids.append(qid)
                    cat_covenant_types.setdefault(cid, set()).add(qctype)

            # Derive covenant_type per category from its questions' covenant_types
            for cid, ctypes in cat_covenant_types.items():
                if cid in categories:
                    if ctypes == {"MFN"}:
                        categories[cid].covenant_type = "MFN"
                    elif ctypes == {"RP"}:
                        categories[cid].covenant_type = "RP"
                    else:
                        # Mixed — keep as RP (most categories are RP)
                        categories[cid].covenant_type = "RP"

            # 3. Load question → target_field mappings (Channel 1: scalar)
            field_query = """
                match
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid;
                    (question: $q) isa question_targets_field,
                        has target_field_name $tfn;
                select $cid, $tfn;
            """
            field_result = tx.query(field_query).resolve()
            for row in field_result.as_concept_rows():
                cid = _safe_get_value(row, "cid")
                tfn = _safe_get_value(row, "tfn")
                if cid and tfn and cid in categories:
                    categories[cid].target_fields.append(tfn)
                    # Also add field name tokens as keywords for matching
                    categories[cid].keywords |= _tokenize(tfn.replace("_", " "))

            # 4. Load question → target_concept_type mappings (Channel 2: multiselect)
            concept_query = """
                match
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid;
                    (question: $q) isa question_targets_concept,
                        has target_concept_type $tct;
                select $cid, $tct;
            """
            concept_result = tx.query(concept_query).resolve()
            for row in concept_result.as_concept_rows():
                cid = _safe_get_value(row, "cid")
                tct = _safe_get_value(row, "tct")
                if cid and tct and cid in categories:
                    categories[cid].target_concept_types.append(tct)
                    # Add concept type tokens as keywords too
                    categories[cid].keywords |= _tokenize(tct.replace("_", " "))

        logger.info(
            "TopicRouter loaded: %d categories, %d total questions, %d target fields",
            len(categories),
            sum(len(c.question_ids) for c in categories.values()),
            sum(len(c.target_fields) for c in categories.values()),
        )
        return categories

    # ── Routing ───────────────────────────────────────────────────────

    def route(self, question: str) -> TopicRouteResult:
        """Route a user question to relevant categories using TypeDB metadata.

        Pass 1 (fast, no LLM): Token-based matching against category keywords.
        Returns a TopicRouteResult with matched categories and aggregated metadata.
        """
        metadata = self._get_cached_metadata()
        question_tokens = _tokenize(question)

        # Also check for multi-word phrases in the original question
        q_lower = question.lower()

        # Score each category by keyword overlap
        scored: List[tuple] = []  # (score, category)
        for cat in metadata.values():
            score = 0

            # Token overlap between question and category keywords
            overlap = question_tokens & cat.keywords
            score += len(overlap) * 2

            # Check if category name appears as a phrase in the question
            cat_name_lower = cat.name.lower()
            if cat_name_lower in q_lower:
                score += 10

            # Check for common domain-specific aliases in the question
            # These are derived from the category data, not hardcoded content
            # E.g., "j.crew" matches because category name contains "j.crew" or "jcrew"
            for token in cat.keywords:
                # Check for partial matches (e.g., "builder" in "builder basket")
                if token in q_lower:
                    score += 1

            if score > 0:
                scored.append((score, cat))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take categories with meaningful scores
        if scored:
            # Include all categories that scored above 0,
            # but apply a threshold: at least 25% of the top score
            top_score = scored[0][0]
            threshold = max(1, top_score * 0.25)
            matched = [cat for score, cat in scored if score >= threshold]
        else:
            matched = []

        # Determine covenant type from matched categories
        covenant_type = self._resolve_covenant_type(matched)

        # If no categories matched, fall back to "both" for broad coverage
        if not matched:
            logger.info(
                "TopicRouter: no category match for '%s', defaulting to 'both'",
                question[:80],
            )
            covenant_type = "both"

        # Aggregate question IDs and target fields
        all_qids: List[str] = []
        all_fields: List[str] = []
        seen_qids: Set[str] = set()
        seen_fields: Set[str] = set()

        for cat in matched:
            for qid in cat.question_ids:
                if qid not in seen_qids:
                    all_qids.append(qid)
                    seen_qids.add(qid)
            for field in cat.target_fields:
                if field not in seen_fields:
                    all_fields.append(field)
                    seen_fields.add(field)

        is_specific = 0 < len(matched) <= 3

        result = TopicRouteResult(
            matched_categories=matched,
            covenant_type=covenant_type,
            question_ids=all_qids,
            all_target_fields=all_fields,
            is_specific=is_specific,
        )

        logger.info(
            "TopicRouter: question='%s' → %d categories [%s], covenant=%s, %d qids, specific=%s",
            question[:60],
            len(matched),
            ", ".join(c.category_id for c in matched[:5]),
            covenant_type,
            len(all_qids),
            is_specific,
        )
        return result

    def _resolve_covenant_type(self, matched: List[CategoryMetadata]) -> str:
        """Determine covenant type from matched categories."""
        if not matched:
            return "both"
        types = {cat.covenant_type.upper() for cat in matched}
        if types == {"RP"}:
            return "rp"
        elif types == {"MFN"}:
            return "mfn"
        else:
            return "both"

    # ── Convenience methods (replace hardcoded helpers) ────────────────

    def detect_covenant_type(self, question: str) -> str:
        """Determine covenant type from matched categories.

        Replaces _detect_covenant_type() in deals.py.
        Returns: "mfn", "rp", or "both"
        """
        result = self.route(question)
        return result.covenant_type

    def get_relevant_field_names(self, question: str) -> List[str]:
        """Get target field names for matched categories.

        Replaces _identify_relevant_attributes() in qa_engine.py.
        """
        result = self.route(question)
        return result.all_target_fields

    def get_all_categories(self) -> Dict[str, CategoryMetadata]:
        """Return all cached category metadata. Useful for diagnostics."""
        return self._get_cached_metadata()


def _safe_get_value(row, key: str, default=None):
    """Safely get attribute value from a TypeDB concept row."""
    try:
        concept = row.get(key)
        if concept is None:
            return default
        return concept.as_attribute().get_value()
    except Exception:
        return default


# ── Module-level singleton ────────────────────────────────────────────

_topic_router: Optional[TopicRouter] = None


def get_topic_router() -> TopicRouter:
    """Get or create the global TopicRouter singleton."""
    global _topic_router
    if _topic_router is None:
        _topic_router = TopicRouter()
    return _topic_router
