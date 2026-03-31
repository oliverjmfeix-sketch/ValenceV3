"""
Format-Agnostic RP Universe Extraction for Covenant Analysis.

Flow:
1. Parse PDF → raw text with page markers
2. Extract RP-Relevant Universe (focused ~150-200k chars):
   - All relevant definitions (by term pattern, not section number)
   - Complete dividend/RP covenant with ALL baskets
   - Investment, Asset Sale, RDP covenants
   - Unrestricted Sub mechanics, Pro forma provisions
3. Load questions from TypeDB (SSoT)
4. Answer ALL questions against the focused RP universe
5. Store results to TypeDB

Key insight: Extract the complete RP-relevant "mini-agreement" by CONTENT PATTERN
(not section numbers) to preserve cross-references, then answer all questions
against this focused context.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional

from anthropic import Anthropic

from app.config import settings
from app.services.pdf_parser import PDFParser, get_pdf_parser
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


def _safe_get_value(row, key: str, default=None):
    """Safely get attribute value from a TypeDB row with null check."""
    try:
        concept = row.get(key)
        if concept is None:
            return default
        return concept.as_attribute().get_value()
    except Exception:
        return default


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class ExtractedContent:
    """Verbatim extracted content with page references."""
    section_type: str
    text: str
    pages: List[int]
    section_reference: Optional[str] = None


@dataclass
class CovenantUniverse:
    """Unified universe for all covenant types (RP, MFN, DEBT_INCURRENCE, etc.)."""
    covenant_type: str                      # "RP", "MFN", "DEBT_INCURRENCE"
    deal_id: str
    sections: Dict[str, str] = field(default_factory=dict)    # section_name → extracted text
    segment_map: Dict[str, dict] = field(default_factory=dict)  # segment_id → {start_page, end_page, section_ref}
    raw_text: str = ""                      # Combined text for Claude
    created_at: datetime = field(default_factory=datetime.utcnow)
    validated: bool = False

    @property
    def cache_path(self) -> str:
        return f"/app/uploads/{self.deal_id}_{self.covenant_type.lower()}_universe.json"

    def to_cache_dict(self) -> dict:
        """Serialize for JSON caching."""
        return {
            "covenant_type": self.covenant_type,
            "deal_id": self.deal_id,
            "sections": self.sections,
            "segment_map": self.segment_map,
            "raw_text": self.raw_text,
            "created_at": self.created_at.isoformat(),
            "validated": self.validated,
        }

    @classmethod
    def from_cache_dict(cls, data: dict) -> "CovenantUniverse":
        """Deserialize from JSON cache."""
        return cls(
            covenant_type=data["covenant_type"],
            deal_id=data["deal_id"],
            sections=data["sections"],
            segment_map=data["segment_map"],
            raw_text=data["raw_text"],
            created_at=datetime.fromisoformat(data["created_at"]),
            validated=data.get("validated", False),
        )


@dataclass
class AnsweredQuestion:
    """A question answered by Claude with provenance."""
    question_id: str
    attribute_name: str
    answer_type: str
    value: Any
    source_text: str
    source_pages: List[int]
    confidence: str
    reasoning: Optional[str] = None
    chunk_index: int = 0
    section_reference: Optional[str] = None


@dataclass
class CategoryAnswers:
    """All answers for a category."""
    category_id: str
    category_name: str
    answers: List[AnsweredQuestion]


class AnswerTracker:
    """Track best answer per question across extraction passes."""

    CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "not_found": 0}

    def __init__(self):
        self.answers: Dict[str, AnsweredQuestion] = {}

    def update(self, answer: AnsweredQuestion):
        """Keep answer if better than existing."""
        existing = self.answers.get(answer.question_id)
        if not existing or self._is_better(answer, existing):
            self.answers[answer.question_id] = answer

    def _is_better(self, new: AnsweredQuestion, old: AnsweredQuestion) -> bool:
        """Check if new answer is better than old based on confidence."""
        new_rank = self.CONFIDENCE_RANK.get(new.confidence, 0)
        old_rank = self.CONFIDENCE_RANK.get(old.confidence, 0)
        return new_rank > old_rank

    def get_unanswered_questions(self, all_questions: List[Dict]) -> List[Dict]:
        """Return questions without high-confidence answers."""
        return [
            q for q in all_questions
            if q["question_id"] not in self.answers
            or self.answers[q["question_id"]].confidence != "high"
        ]

    def get_high_confidence_count(self) -> int:
        """Count questions with high confidence answers."""
        return sum(1 for a in self.answers.values() if a.confidence == "high")

    def get_all_answers(self) -> List[AnsweredQuestion]:
        """Get all tracked answers."""
        return list(self.answers.values())


@dataclass
class ExtractionResult:
    """Unified extraction result for any covenant type."""
    deal_id: str
    covenant_type: str
    provision_id: str
    answers_stored: int
    entities_created: int
    extraction_time_seconds: float
    universe_chars: int
    model_used: str
    total_cost_usd: float
    cost_breakdown: dict = field(default_factory=dict)
    validated: bool = False
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "covenant_type": self.covenant_type,
            "provision_id": self.provision_id,
            "answers_stored": self.answers_stored,
            "entities_created": self.entities_created,
            "extraction_time_seconds": self.extraction_time_seconds,
            "universe_chars": self.universe_chars,
            "model_used": self.model_used,
            "total_cost_usd": self.total_cost_usd,
            "cost_breakdown": self.cost_breakdown,
            "validated": self.validated,
            "errors": self.errors,
        }


# =============================================================================
# EXTRACTION SERVICE
# =============================================================================

class ExtractionService:
    """
    Format-agnostic RP Universe extraction pipeline.

    Extracts the complete RP-relevant "mini-agreement" by CONTENT PATTERN
    (not section numbers), then answers all questions against this focused context.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        parser: Optional[PDFParser] = None
    ):
        self.client = Anthropic(api_key=api_key or settings.anthropic_api_key)
        self.model = model or settings.claude_model
        self.parser = parser or get_pdf_parser()

    # =========================================================================
    # VALIDATION PROMPTS (per covenant type)
    # =========================================================================

    _VALIDATION_PROMPTS = {
        "RP": """You are validating whether extracted text contains the ACTUAL Restricted Payments covenant.

The RP covenant MUST contain:
- Restriction language ("shall not declare or pay any dividend", "shall not make any Restricted Payment")
- Permitted baskets (labeled exceptions like (a), (b), (c) through (z) or numbered clauses)
- Capacity mechanics (dollar amounts, percentage of Consolidated Net Income, leverage tests, etc.)

This is NOT the RP section if it only contains:
- Cross-references TO the RP covenant
- Definitions that mention Restricted Payments
- Other covenants that reference RP""",

        "MFN": """You are validating whether extracted text contains the ACTUAL MFN (Most Favored Nation) provision.

The MFN provision MUST contain:
- Yield comparison language ("Effective Yield shall not exceed...", "if the yield exceeds...")
- A threshold (typically in basis points: 25, 50, 75, 100 bps)
- What happens when triggered (spread adjustment, lender option, etc.)

This is NOT the MFN section if it only contains:
- Cross-references TO the MFN provision (e.g., "subject to Section 2.20(f)")
- Definitions of Effective Yield without the comparison mechanics
- Defaulting Lender provisions or other unrelated sections""",

        "DEBT_INCURRENCE": """You are validating whether extracted text contains the ACTUAL Debt Incurrence covenant (typically Section 6.01 or 7.01).

The Debt covenant MUST contain:
- Restriction language ("shall not create, incur, assume or permit to exist any Indebtedness")
- Permitted debt baskets (labeled exceptions with dollar amounts or ratio tests)
- Ratio-based capacity (Consolidated Total Debt to EBITDA, First Lien Leverage Ratio, Secured Leverage Ratio)

This is NOT the Debt section if it only contains:
- Cross-references TO the debt covenant
- Definitions of Indebtedness without the restriction mechanics
- Lien covenants or other negative covenants""",
    }

    # =========================================================================
    # UNIFIED UNIVERSE: get_or_build_universe (single entry point)
    # =========================================================================

    def get_or_build_universe(
        self,
        deal_id: str,
        covenant_type: str,
        document_text: Optional[str] = None,
        segment_map: Optional[dict] = None,
        force_rebuild: bool = False,
        validate: bool = True,
    ) -> Optional[CovenantUniverse]:
        """
        Single entry point for getting a covenant universe.

        1. Check cache (unless force_rebuild)
        2. Validate cached content (if validate=True)
        3. Build from document if needed
        4. Validate new content
        5. Cache if valid
        6. Return universe or None if validation fails
        """
        covenant_type = covenant_type.upper()
        cache_path = f"/app/uploads/{deal_id}_{covenant_type.lower()}_universe.json"

        # Try cache first (unless force_rebuild)
        if not force_rebuild:
            cached = self._load_cached_universe(cache_path)
            if cached:
                if validate and not cached.validated:
                    if self._validate_universe(cached):
                        cached.validated = True
                        self._cache_universe(cached)
                        return cached
                    else:
                        logger.warning(f"Cached {covenant_type} universe failed validation, rebuilding...")
                else:
                    return cached

        # Need to build from document
        if not document_text:
            pdf_path = f"/app/uploads/{deal_id}.pdf"
            if not os.path.exists(pdf_path):
                logger.error(f"No document text provided and no PDF at {pdf_path}")
                return None
            document_text = self.parse_document(pdf_path)

        if not segment_map:
            segment_map = self.segment_document(document_text)

        universe = self._build_covenant_universe(
            deal_id, covenant_type, document_text, segment_map
        )

        if not universe:
            logger.error(f"Failed to build {covenant_type} universe for {deal_id}")
            return None

        if validate:
            if not self._validate_universe(universe):
                logger.error(
                    f"{covenant_type} universe validation failed for {deal_id}. "
                    f"NOT caching. Check segmenter results."
                )
                return None
            universe.validated = True

        self._cache_universe(universe)
        return universe

    def _load_cached_universe(self, cache_path: str) -> Optional[CovenantUniverse]:
        """Load universe from JSON cache file."""
        try:
            if not os.path.exists(cache_path):
                return None

            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            universe = CovenantUniverse.from_cache_dict(data)
            logger.info(f"Loaded cached universe: {cache_path} ({len(universe.raw_text)} chars)")
            return universe

        except Exception as e:
            logger.warning(f"Failed to load cached universe from {cache_path}: {e}")
            return None

    def _cache_universe(self, universe: CovenantUniverse) -> bool:
        """Cache universe to JSON file."""
        try:
            cache_path = universe.cache_path
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(universe.to_cache_dict(), f, indent=2)

            logger.info(f"Cached universe: {cache_path} ({len(universe.raw_text)} chars)")
            return True

        except Exception as e:
            logger.warning(f"Failed to cache universe: {e}")
            return False

    def _validate_universe(self, universe: CovenantUniverse) -> bool:
        """
        Validate universe contains expected content using Sonnet.

        Returns True if valid, False if validation fails.
        Cost: ~$0.10 per validation (30K input tokens on Sonnet).
        """
        validation_prompt = self._VALIDATION_PROMPTS.get(
            universe.covenant_type,
            f"Validate this contains actual {universe.covenant_type} covenant language with restriction mechanics and permitted baskets/exceptions."
        )

        sample_text = universe.raw_text[:30000]

        prompt = f"""{validation_prompt}

## EXTRACTED TEXT (first 30K chars)

{sample_text}

## QUESTION

Does this text contain the ACTUAL {universe.covenant_type} provision mechanics (the clause itself with restrictions and exceptions), not just cross-references or definitions?

Answer ONLY: YES or NO"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}]
            )

            answer = response.content[0].text.strip().upper()
            is_valid = answer.startswith("YES")

            if is_valid:
                logger.info(f"{universe.covenant_type} universe validation PASSED for {universe.deal_id}")
            else:
                logger.warning(
                    f"{universe.covenant_type} universe validation FAILED for {universe.deal_id}. "
                    f"Sonnet response: {answer}. Segmenter likely captured wrong section."
                )

            return is_valid

        except Exception as e:
            logger.error(f"Universe validation failed with error: {e}. Assuming invalid.")
            return False

    def _build_covenant_universe(
        self,
        deal_id: str,
        covenant_type: str,
        document_text: str,
        segment_map: dict,
    ) -> Optional[CovenantUniverse]:
        """
        Build universe by slicing document at segment boundaries.
        Which segments to include comes from TypeDB (SSoT).
        """
        from app.services.segment_introspector import get_segment_mapping_for_covenant

        segment_mapping = get_segment_mapping_for_covenant(covenant_type)

        if not segment_mapping:
            logger.error(f"No segment mapping found for covenant type {covenant_type}")
            return None

        segments_by_id = {
            s["segment_type_id"]: s
            for s in segment_map.get("segments", [])
            if s.get("found", True)
        }

        sections = {}
        included_segments = {}

        for seg_id, section_name in segment_mapping.items():
            seg = segments_by_id.get(seg_id)
            if seg:
                sliced = self._slice_by_pages(
                    document_text, seg["start_page"], seg["end_page"]
                )
                if sliced:
                    sections[section_name] = sliced
                    included_segments[seg_id] = {
                        "start_page": seg["start_page"],
                        "end_page": seg["end_page"],
                        "section_ref": seg.get("section_ref", ""),
                    }

        if not sections:
            logger.warning(f"No sections found for {covenant_type} universe")
            return None

        raw_parts = []
        for section_name, content in sections.items():
            raw_parts.append(f"=== {section_name.upper()} ===\n{content}")
        raw_text = "\n\n".join(raw_parts)

        logger.info(
            f"Built {covenant_type} universe for {deal_id}: "
            f"{len(sections)} sections, {len(raw_text)} chars"
        )

        return CovenantUniverse(
            covenant_type=covenant_type,
            deal_id=deal_id,
            sections=sections,
            segment_map=included_segments,
            raw_text=raw_text,
        )

    # =========================================================================
    # STEP 1: Parse PDF
    # =========================================================================

    def parse_document(self, pdf_path: str) -> str:
        """Parse PDF to text with [PAGE X] markers for provenance."""
        logger.info(f"Parsing PDF: {pdf_path}")
        pages = self.parser.extract_pages(pdf_path)

        text_parts = []
        for page in pages:
            text_parts.append(f"\n[PAGE {page.page_number}]\n")
            text_parts.append(page.text)

        full_text = ''.join(text_parts)
        logger.info(f"Parsed {len(pages)} pages, {len(full_text)} chars")
        return full_text

    # =========================================================================
    # STEP 2: Extract RP-Relevant Universe (Segmenter-Based)
    # =========================================================================

    def extract_rp_universe(self, document_text: str) -> CovenantUniverse:
        """
        Extract RP-relevant universe using document segmentation.

        Segment definitions loaded from TypeDB (SSoT).
        Claude identifies section locations (page numbers),
        Python slices the original text.
        """
        doc_len = len(document_text)
        logger.info(f"Segmenting {doc_len} char document for RP universe")

        segment_map = self.segment_document(document_text)
        self._last_segment_map = segment_map  # Retained for MFN universe reuse

        found_count = sum(
            1 for s in segment_map.get("segments", [])
            if s.get("found", True)
        )
        logger.info(f"Segmentation complete: {found_count} sections found")

        universe = self._build_rp_universe_from_segments(document_text, segment_map)

        logger.info(
            f"RP Universe built: definitions={len(universe.definitions)}, "
            f"dividend={len(universe.dividend_covenant)}, "
            f"investment={len(universe.investment_covenant)}, "
            f"rdp={len(universe.rdp_covenant)} chars"
        )

        return universe

    def segment_document(self, document_text: str) -> dict:
        """
        Send full document to Claude. Get back JSON with page numbers for each section.
        Segment definitions loaded from TypeDB (SSoT).
        Uses N-way split for documents > 400K chars.
        """
        import math

        doc_len = len(document_text)
        max_chunk = 400000

        if doc_len <= max_chunk:
            # Single call
            prompt = self._build_segmentation_prompt(document_text)
            response_text = self._call_claude_streaming(
                prompt, max_tokens=4096, step="segmentation"
            )
            if response_text:
                return self._parse_segmentation_response(response_text)
            return {"segments": []}

        # N-way split for large documents
        num_chunks = math.ceil(doc_len / max_chunk)
        chunk_size = doc_len // num_chunks

        # Find page boundaries for splits
        boundaries = [0]
        for i in range(1, num_chunks):
            target = chunk_size * i
            pb = document_text.rfind(
                "[PAGE ", max(0, target - 5000), min(doc_len, target + 5000)
            )
            if pb == -1:
                pb = target
            boundaries.append(pb)
        boundaries.append(doc_len)

        logger.info(f"Large doc ({doc_len} chars): splitting into {num_chunks} chunks")

        chunk_maps = []
        for ci in range(len(boundaries) - 1):
            chunk = document_text[boundaries[ci]:boundaries[ci + 1]]
            if ci == 0:
                part_hint = f"\nNOTE: This is PART 1 of {num_chunks} of a large document. Definitions and early articles are usually here.\n"
            elif ci == num_chunks - 1:
                part_hint = f"\nNOTE: This is PART {ci + 1} of {num_chunks} (LAST PART). Negative covenants and events of default are usually here.\n"
            else:
                part_hint = f"\nNOTE: This is PART {ci + 1} of {num_chunks} (MIDDLE PART). Report whatever sections you find.\n"

            prompt = self._build_segmentation_prompt(chunk, part_hint=part_hint)
            response_text = self._call_claude_streaming(
                prompt, max_tokens=4096, step="segmentation"
            )
            if response_text:
                chunk_maps.append(self._parse_segmentation_response(response_text))

        # Merge: prefer larger page spans
        return self._merge_segment_maps(chunk_maps)

    def _build_segmentation_prompt(
        self, document_text: str, part_hint: str = ""
    ) -> str:
        """Build segmentation prompt dynamically from TypeDB segment types."""
        from app.services.segment_introspector import get_segment_types

        segments = get_segment_types()

        section_lines = ""
        for seg in segments:
            section_lines += (
                f'\n- **{seg["segment_type_id"]}** ({seg["name"]}): '
                f'{seg["find_description"]}'
            )

        return f"""You are analyzing a credit agreement to identify the location of each major section.
{part_hint}
## SECTIONS TO FIND
{section_lines}

## DOCUMENT

{document_text}

## YOUR TASK

For each section, find it in the document and report WHERE it is.
Do NOT copy or extract any text. Just report locations.

Return ONLY valid JSON, no markdown fences, no explanation:

{{
  "segments": [
    {{"segment_type_id": "definitions", "found": true, "section_ref": "as found in document", "start_page": 3, "end_page": 58}},
    {{"segment_type_id": "repricing_protection", "found": false}}
  ]
}}

CRITICAL:
1. start_page / end_page are from the [PAGE X] markers in the document
2. end_page is the LAST page of this section (before the next section starts)
3. If a section doesn't exist, set found=false"""

    def _parse_segmentation_response(self, response_text: str) -> dict:
        """Parse Claude's segmentation JSON response."""
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = re.sub(r'^```(?:json)?\s*', '', clean)
            clean = re.sub(r'\s*```$', '', clean)
        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            logger.error(f"Segmentation JSON parse failed: {e}")
            return {"segments": []}

    def _merge_segment_maps(self, maps: list) -> dict:
        """Merge segment maps from N chunks. Prefer larger page spans."""
        best = {}
        not_found = {}

        for seg_map in maps:
            for seg in seg_map.get("segments", []):
                sid = seg["segment_type_id"]
                if not seg.get("found", True):
                    if sid not in best:
                        not_found[sid] = seg
                    continue

                span = seg.get("end_page", 0) - seg.get("start_page", 0)
                if sid not in best:
                    best[sid] = (seg, span)
                else:
                    if span >= best[sid][1]:
                        best[sid] = (seg, span)

        merged = [seg for seg, _ in best.values()]
        for sid, seg in not_found.items():
            if sid not in best:
                merged.append(seg)

        return {"segments": merged}

    def _slice_by_pages(
        self, document_text: str, start_page: int, end_page: int
    ) -> str:
        """Slice document text between [PAGE X] markers."""
        start_marker = f"[PAGE {start_page}]"
        end_marker = f"[PAGE {end_page + 1}]"

        start_pos = document_text.find(start_marker)
        if start_pos == -1:
            return ""

        end_pos = document_text.find(end_marker, start_pos)
        if end_pos == -1:
            # Section runs to end of document (or cap at 200K)
            return document_text[start_pos:start_pos + 200000]

        return document_text[start_pos:end_pos]

    def _call_claude_streaming(self, prompt: str, max_tokens: int = 16000,
                               step: str = "extraction",
                               deal_id: str = None) -> str:
        """Call Claude with streaming to handle long operations."""
        from app.services.cost_tracker import extract_usage
        try:
            collected_text = []
            chunk_count = 0
            start = time.time()
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    collected_text.append(text)
                    chunk_count += 1
                final_message = stream.get_final_message()
            duration = time.time() - start
            logger.info(f"Streaming complete: {chunk_count} chunks received")
            result = "".join(collected_text)
            logger.info(f"Response assembled: {len(result)} chars")
            usage = extract_usage(
                final_message, self.model, step, deal_id, duration
            )
            self._last_streaming_usage = usage
            # Accumulate all streaming usages for pipeline cost summaries
            if not hasattr(self, '_streaming_usages'):
                self._streaming_usages = []
            self._streaming_usages.append(usage)
            return result
        except Exception as e:
            logger.error(f"Claude streaming error: {e}")
            self._last_streaming_usage = None
            return ""

    # =========================================================================
    # STEP 3: Load Questions from TypeDB (SSoT)
    # =========================================================================

    def load_questions_by_category(self, covenant_type: str) -> Dict[str, List[Dict]]:
        """Load questions from TypeDB grouped by category via category_has_question."""
        if not typedb_client.driver:
            logger.warning("TypeDB not connected")
            return {}

        try:
            from typedb.driver import TransactionType
            tx = typedb_client.driver.transaction(
                settings.typedb_database, TransactionType.READ
            )
            try:
                query = f"""
                    match
                        $cat isa ontology_category, has category_id $cid, has name $cname;
                        (category: $cat, question: $q) isa category_has_question;
                        $q has question_id $qid, has question_text $qt, has answer_type $at,
                           has covenant_type "{covenant_type}", has display_order $order;
                    select $cid, $cname, $qid, $qt, $at, $order;
                """

                result = tx.query(query).resolve()
                questions_by_cat: Dict[str, List[Dict]] = {}

                for row in result.as_concept_rows():
                    cat_id = _safe_get_value(row, "cid")
                    cat_name = _safe_get_value(row, "cname", "")
                    qid = _safe_get_value(row, "qid")
                    if not qid or not cat_id:
                        continue

                    if cat_id not in questions_by_cat:
                        questions_by_cat[cat_id] = []

                    questions_by_cat[cat_id].append({
                        "question_id": qid,
                        "question_text": _safe_get_value(row, "qt", ""),
                        "answer_type": _safe_get_value(row, "at", "string"),
                        "display_order": _safe_get_value(row, "order", 0),
                        "category_id": cat_id,
                        "category_name": cat_name,
                        "extraction_prompt": None  # Will be loaded separately
                    })

                # Load extraction_prompt for each question (optional attribute)
                for cat_id, questions in questions_by_cat.items():
                    for q in questions:
                        prompt = self._get_extraction_prompt(tx, q["question_id"])
                        if prompt:
                            q["extraction_prompt"] = prompt

                # Load target field/concept mappings
                for cat_id, questions in questions_by_cat.items():
                    for q in questions:
                        target_info = self._get_question_target(tx, q["question_id"])
                        q["target_type"] = target_info["type"]
                        q["target_field_name"] = target_info["name"]
                        q["target_concept_type"] = target_info.get("concept_type")
                        q["concept_options"] = target_info.get("options", [])

                logger.info(f"Loaded {sum(len(qs) for qs in questions_by_cat.values())} {covenant_type} questions")
                return questions_by_cat

            finally:
                tx.close()
        except Exception as e:
            logger.error(f"Error loading questions: {e}")
            return {}

    def _get_extraction_prompt(self, tx, question_id: str) -> Optional[str]:
        """Get extraction_prompt hint for a question (if exists)."""
        try:
            query = f"""
                match
                    $q isa ontology_question,
                        has question_id "{question_id}",
                        has extraction_prompt $ep;
                select $ep;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            if result:
                return _safe_get_value(result[0], "ep")
        except Exception:
            pass
        return None

    def _get_question_target(self, tx, question_id: str) -> Dict[str, Any]:
        """Get target field or concept type for a question.

        Gracefully handles questions without question_targets_field relations.
        """
        try:
            # Try field target first
            query = f"""
                match
                    $q isa ontology_question, has question_id "{question_id}";
                    (question: $q) isa question_targets_field, has target_field_name $fn;
                select $fn;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            if result:
                field_name = _safe_get_value(result[0], "fn")
                if field_name:
                    return {
                        "type": "field",
                        "name": field_name
                    }

            # Try concept target (multiselect)
            query = f"""
                match
                    $q isa ontology_question, has question_id "{question_id}";
                    (question: $q) isa question_targets_concept, has target_concept_type $ct;
                select $ct;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            if result:
                concept_type = _safe_get_value(result[0], "ct")
                if concept_type:
                    options = self._load_concept_options(tx, concept_type)
                    return {
                        "type": "concept",
                        "name": concept_type,
                        "concept_type": concept_type,
                        "options": options
                    }

        except Exception as e:
            logger.debug(f"No target relation found for {question_id}: {e}")

        # Default: derive field name from question_id (e.g., rp_a1 -> a1_answer)
        # This allows extraction to work even without explicit target relations
        return {"type": "field", "name": f"{question_id}_answer"}

    def _load_concept_options(self, tx, concept_type: str) -> List[Dict[str, str]]:
        """Load all concept instances for a given concept type."""
        options = []
        try:
            query = f"""
                match
                    $c isa {concept_type},
                        has concept_id $cid,
                        has name $name;
                select $cid, $name;
            """
            result = tx.query(query).resolve()
            for row in result.as_concept_rows():
                cid = _safe_get_value(row, "cid")
                name = _safe_get_value(row, "name")
                if cid:  # Skip rows with missing required fields
                    options.append({
                        "id": cid,
                        "name": name or ""
                    })
        except Exception as e:
            logger.warning(f"Error loading concept options for {concept_type}: {e}")
        return options

    # =========================================================================
    # STEP 4: Answer Questions Against RP Universe
    # =========================================================================
    def _answer_category_questions(
        self,
        context: str,
        questions: List[Dict],
        category_name: str,
        system_instruction: str = "",
    ) -> List[AnsweredQuestion]:
        """Answer a category's questions against the RP universe context."""
        questions_text = self._format_questions_for_prompt(questions)

        system_block = ""
        if system_instruction:
            system_block = f"\n## SYSTEM INSTRUCTION\n\n{system_instruction}\n"

        prompt = f"""Answer covenant analysis questions using the extracted RP-relevant content.
{system_block}
## RP-RELEVANT CONTEXT

{context}

## CATEGORY: {category_name}

## QUESTIONS

{questions_text}

## INSTRUCTIONS

For EACH question, answer based ONLY on the extracted content above:
- question_id: The ID in brackets
- value: The answer (true/false for boolean, number for numeric, array for multiselect)
- source_text: EXACT verbatim quote from the document text that supports your answer (max 500 chars). This MUST be actual contract language copied from the context above. NEVER write "See page X" or "See Section X" or any other reference — always paste the actual text. If no supporting text exists, use an empty string "".
- source_pages: Page numbers from [PAGE X] markers
- section_reference: The specific agreement provision reference where this appears, including the paragraph/subsection letter or number (e.g., "6.06(p)", "6.09(a)(I)", "Definition of Cumulative Amount, clause (h)"). Be as specific as possible.
- reasoning: Brief explanation (1 sentence)

## OUTPUT

Return JSON array:
```json
[
  {{"question_id": "rp_a1", "value": true, "source_text": "...", "source_pages": [89], "section_reference": "6.06(p)", "reasoning": "..."}}
]
```

Return ONLY the JSON array."""

        from app.services.cost_tracker import extract_usage
        try:
            start = time.time()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            duration = time.time() - start
            self._last_qa_usage = extract_usage(
                response, self.model, "rp_qa", deal_id=None, duration=duration
            )
            return self._parse_qa_response(response.content[0].text, questions)
        except Exception as e:
            logger.error(f"QA error for {category_name}: {e}")
            self._last_qa_usage = None
            return []

    def _format_questions_for_prompt(self, questions: List[Dict]) -> str:
        """Format questions for the QA prompt, including multiselect options and extraction hints."""
        lines = []
        for i, q in enumerate(questions, 1):
            answer_type = q.get("answer_type", "boolean")
            target = q.get("target_field_name", "")
            target_type = q.get("target_type", "field")
            concept_options = q.get("concept_options", [])
            extraction_prompt = q.get("extraction_prompt")

            type_hint = {
                "boolean": "(yes/no)",
                "integer": "(number)",
                "double": "(decimal)",
                "percentage": "(decimal 0-1)",
                "currency": "(dollar amount)",
                "string": "(text)",
                "number": "(numeric)",
                "multiselect": "(select from options below)"
            }.get(answer_type, "")

            lines.append(f"{i}. [{q['question_id']}] {q['question_text']} {type_hint}")

            # Add extraction hint if available
            if extraction_prompt:
                lines.append(f"   → Hint: {extraction_prompt}")

            if target_type == "concept" and concept_options:
                lines.append(f"   → Concept: {target}")
                option_strs = [f"{opt['id']} ({opt['name']})" for opt in concept_options]
                lines.append(f"   → Valid options: {', '.join(option_strs)}")
            elif target:
                lines.append(f"   → Field: {target}")

        return "\n".join(lines)

    def _parse_qa_response(self, response_text: str, questions: List[Dict]) -> List[AnsweredQuestion]:
        """Parse QA response into AnsweredQuestion objects."""
        try:
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start == -1 or end == 0:
                logger.warning(f"No JSON array in QA response")
                return []

            json_str = response_text[start:end]
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)

            data = json.loads(json_str)
            q_lookup = {q['question_id']: q for q in questions}
            answers = []

            for item in data:
                qid = item.get("question_id")
                if not qid or qid not in q_lookup:
                    continue

                q = q_lookup[qid]
                target_type = q.get("target_type", "field")

                if target_type == "concept":
                    attr_name = q.get("target_concept_type", q.get("target_field_name", ""))
                else:
                    attr_name = q.get("target_field_name", "")

                # Validate multiselect values
                value = item.get("value")
                if target_type == "concept" and isinstance(value, list):
                    valid_ids = {opt["id"] for opt in q.get("concept_options", [])}
                    if valid_ids:
                        value = [v for v in value if v in valid_ids]

                answers.append(AnsweredQuestion(
                    question_id=qid,
                    attribute_name=attr_name,
                    answer_type=q.get("answer_type", "string"),
                    value=value,
                    source_text=item.get("source_text") or "",
                    source_pages=(item.get("source_pages") or []),
                    confidence=item.get("confidence") or "high",
                    reasoning=item.get("reasoning"),
                    section_reference=item.get("section_reference"),
                ))

            logger.info(f"Parsed {len(answers)} answers")
            return answers

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in QA: {e}")
            return []

    # =========================================================================
    # STEP 5: Store Results to TypeDB
    # =========================================================================

    def store_extraction_result(
        self,
        deal_id: str,
        category_answers: List[CategoryAnswers]
    ) -> bool:
        """Store extraction results to TypeDB via provision_has_answer."""
        if not typedb_client.driver:
            logger.error("TypeDB not connected, cannot store results")
            return False

        provision_id = f"{deal_id}_rp"

        try:
            from app.services.graph_storage import GraphStorage

            self._ensure_provision_exists_unified(deal_id, provision_id, "RP")
            storage = GraphStorage(deal_id)

            scalar_count = 0
            scalar_failed = 0
            multiselect_count = 0
            multiselect_failed = 0

            for cat_answers in category_answers:
                for answer in cat_answers.answers:
                    if answer.value is None:
                        continue
                    if answer.confidence == "not_found":
                        continue

                    if isinstance(answer.value, list):
                        for concept_id in answer.value:
                            success = self._store_concept_applicability_safe(
                                provision_id, answer.attribute_name,
                                concept_id, answer.source_text,
                                answer.source_pages[0] if answer.source_pages else 0
                            )
                            if success:
                                multiselect_count += 1
                            else:
                                multiselect_failed += 1
                    else:
                        try:
                            coerced = self._coerce_answer_value(
                                answer.value, answer.answer_type
                            )
                            if coerced is not None:
                                storage.store_scalar_answer(
                                    provision_id=provision_id,
                                    question_id=answer.question_id,
                                    value=coerced,
                                    source_text=answer.source_text or None,
                                    source_page=(
                                        answer.source_pages[0]
                                        if answer.source_pages else None
                                    ),
                                    source_section=answer.section_reference,
                                )
                                scalar_count += 1
                            else:
                                scalar_failed += 1
                        except Exception as e:
                            logger.warning(
                                f"Could not store answer for {answer.question_id}: {e}"
                            )
                            scalar_failed += 1

            logger.info(
                f"Stored {scalar_count} scalar ({scalar_failed} failed), "
                f"{multiselect_count} multiselect ({multiselect_failed} failed)"
            )
            return True

        except Exception as e:
            logger.error(f"Storage error: {e}")
            return False

    def _coerce_answer_value(self, value: Any, answer_type: str) -> Any:
        """Coerce an answer value to the correct Python type for store_scalar_answer."""
        if value is None:
            return None
        if isinstance(value, str) and value.lower() in ("not_found", "n/a", "none", "null"):
            return None

        if answer_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1")
            return bool(value)

        if answer_type in ("double", "percentage", "currency", "float", "decimal"):
            try:
                return float(str(value).strip("'\""))
            except (ValueError, TypeError):
                return None

        if answer_type in ("integer", "int", "number"):
            try:
                return int(float(str(value).strip("'\"")))
            except (ValueError, TypeError):
                return None

        # Default to string
        return str(value)

    def _store_concept_applicability_safe(
        self,
        provision_id: str,
        concept_type: str,
        concept_id: str,
        source_text: str,
        source_page: int
    ) -> bool:
        """Store a concept applicability with its own transaction."""
        from typedb.driver import TransactionType

        escaped_text = source_text.replace('\\', '\\\\').replace('"', '\\"')[:500]

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match
                    $p isa rp_provision, has provision_id "{provision_id}";
                    $c isa {concept_type}, has concept_id "{concept_id}";
                insert
                    (provision: $p, concept: $c) isa concept_applicability,
                        has applicability_status "INCLUDED",
                        has source_text "{escaped_text}",
                        has source_page {source_page};
            """
            tx.query(query).resolve()
            tx.commit()
            logger.debug(f"Stored applicability: {concept_type}/{concept_id}")
            return True
        except Exception as e:
            tx.close()
            logger.warning(f"Could not store applicability for {concept_id}: {e}")
            return False

    # =========================================================================
    # J.CREW DEEP ANALYSIS PIPELINE
    # =========================================================================

    @staticmethod
    def _find_page_number(text: str, position: int) -> Optional[int]:
        """Find the most recent [PAGE X] marker before the given position."""
        preceding_text = text[:position]
        matches = list(re.finditer(r'\[PAGE\s+(\d+)\]', preceding_text))
        if matches:
            return int(matches[-1].group(1))
        return None

    def extract_definitions_section(self, document_text: str) -> str:
        """Extract definitions relevant to J.Crew deep analysis from document text.

        Searches for defined terms critical to Tier 2 definition quality analysis:
        IP, Transfer, Material, Unrestricted Subsidiary, Collateral, etc.

        Uses regex to find "Term" means ... paragraphs in the definitions section.
        Returns concatenated definition text for use as Tier 2 context.
        """
        target_terms = [
            "Intellectual Property", "Material Intellectual Property", "Material IP",
            "IP", "Transfer", "Disposition",
            "Material", "Materiality",
            "Unrestricted Subsidiary", "Excluded Subsidiary",
            "Restricted Subsidiary", "Non-Guarantor Restricted Subsidiary",
            "Loan Party", "Credit Party",
            "Guarantor", "Subsidiary Guarantor",
            "Permitted Investment", "Investment",
            "Permitted Lien", "Permitted Encumbrance",
            "Collateral", "Pledged Assets",
            "Exclusive License", "License",
            "Trade Secret", "Know-How", "Patent", "Trademark", "Copyright",
            "Principal Property", "Material Asset",
        ]

        found = []
        seen_starts = set()  # Deduplicate overlapping matches

        for term in target_terms:
            # Match "Term" means/shall mean patterns (smart and straight quotes)
            pattern = (
                rf'["\u201c]{re.escape(term)}["\u201d]'
                rf'\s*(?:means?|shall mean|is defined as|has the meaning)'
            )
            for match in re.finditer(pattern, document_text, re.IGNORECASE):
                start = match.start()

                # Deduplicate: skip if we already captured near this position
                bucket = start // 200
                if bucket in seen_starts:
                    continue
                seen_starts.add(bucket)

                # Walk back to paragraph/line start
                line_start = document_text.rfind('\n', max(0, start - 500), start)
                line_start = line_start + 1 if line_start != -1 else max(0, start - 500)

                # Walk forward to find end of definition
                search_end = min(start + 4000, len(document_text))
                # Look for next quoted-term definition pattern
                next_def = re.search(
                    r'\n\s*["\u201c][A-Z][a-zA-Z\s]+["\u201d]\s*'
                    r'(?:means?|shall mean|is defined|has the meaning)',
                    document_text[start + 50:search_end]
                )
                if next_def:
                    end = start + 50 + next_def.start()
                else:
                    # Fall back to double-newline paragraph break
                    double_nl = document_text.find('\n\n', start + 50, search_end)
                    end = double_nl if double_nl != -1 else search_end

                page = self._find_page_number(document_text, start)
                header = f"=== {term}"
                if page:
                    header += f" [page {page}]"
                header += " ==="
                found.append(f"{header}\n{document_text[line_start:end].strip()}")

        if found:
            result = "\n\n---\n\n".join(found)
            logger.info(f"Extracted {len(found)} definitions ({len(result)} chars) for J.Crew analysis")
            return result

        logger.warning("No definitions found for J.Crew analysis — will fall back to RP universe definitions")
        return ""

    # =========================================================================
    # UNIFIED COVENANT EXTRACTION
    # =========================================================================

    async def extract_covenant(
        self,
        deal_id: str,
        covenant_type: str,
        universe: CovenantUniverse,
        model: Optional[str] = None,
    ) -> 'ExtractionResult':
        """
        Unified extraction for any covenant type.

        1. Load questions from TypeDB (SSoT)
        2. Ensure provision exists
        3. Extract entities (Call 0)
        4. Extract scalars (dynamic batching by token budget)
        5. Store everything to TypeDB
        6. Return unified result
        """
        from app.services.graph_storage import GraphStorage
        from app.services.cost_tracker import ExtractionCostSummary
        from app.schemas.extraction_response import ExtractionResponse

        covenant_type = covenant_type.upper()
        model = model or settings.claude_model
        start_time = time.time()
        provision_id = f"{deal_id}_{covenant_type.lower()}"

        logger.info(
            f"Unified extraction starting: deal={deal_id}, covenant={covenant_type}, "
            f"universe={len(universe.raw_text)} chars, model={model}"
        )

        cost_summary = ExtractionCostSummary(deal_id=deal_id)

        # Load questions from TypeDB (SSoT)
        scalar_questions = self.load_questions_by_category(covenant_type)
        entity_questions = self._load_entity_list_questions(covenant_type)

        total_scalar = sum(len(qs) for qs in scalar_questions.values())
        total_entity = len(entity_questions)
        logger.info(f"Loaded questions: {total_scalar} scalar, {total_entity} entity_list")

        # Ensure provision exists
        self._ensure_provision_exists_unified(deal_id, provision_id, covenant_type)

        all_answers = []

        # ── STEP 1: Entity extraction (Call 0) ──────────────────────────
        if entity_questions:
            logger.info(f"Call 0: Entity extraction ({len(entity_questions)} questions)")
            entity_answers = await self._extract_entities_unified(
                universe=universe,
                questions=entity_questions,
                model=model,
                deal_id=deal_id,
                cost_summary=cost_summary,
            )
            all_answers.extend(entity_answers)
            logger.info(f"Call 0 complete: {len(entity_answers)} entity answers")

        # ── STEP 2: Scalar extraction (dynamic batching) ────────────────
        if scalar_questions:
            logger.info("Scalar extraction: dynamic batching")
            scalar_answers = await self._extract_scalars_dynamic(
                universe=universe,
                questions_by_cat=scalar_questions,
                model=model,
                deal_id=deal_id,
                cost_summary=cost_summary,
            )
            all_answers.extend(scalar_answers)
            logger.info(f"Scalar extraction complete: {len(scalar_answers)} answers")

        # ── STEP 3: Store everything to TypeDB ──────────────────────────
        logger.info(f"Storing {len(all_answers)} total answers to TypeDB")

        storage = GraphStorage(deal_id)
        extraction = ExtractionResponse(answers=all_answers)
        storage_result = storage.store_extraction(deal_id, provision_id, extraction)

        # ── STEP 4: Build and return result ─────────────────────────────
        extraction_time = time.time() - start_time
        cost_summary.log_summary()

        result = ExtractionResult(
            deal_id=deal_id,
            covenant_type=covenant_type,
            provision_id=provision_id,
            answers_stored=storage_result.get("answers_stored", 0),
            entities_created=storage_result.get("entities_created", 0),
            extraction_time_seconds=round(extraction_time, 2),
            universe_chars=len(universe.raw_text),
            model_used=model,
            total_cost_usd=round(cost_summary.total_cost_usd, 4),
            cost_breakdown={
                "num_api_calls": len(cost_summary.steps),
                "total_input_tokens": cost_summary.total_input_tokens,
                "total_output_tokens": cost_summary.total_output_tokens,
            },
            validated=universe.validated,
        )

        logger.info(
            f"Unified extraction complete: {result.answers_stored} answers, "
            f"{result.entities_created} entities in {extraction_time:.1f}s "
            f"(${result.total_cost_usd:.4f})"
        )

        return result

    def _ensure_provision_exists_unified(
        self, deal_id: str, provision_id: str, covenant_type: str
    ):
        """Ensure provision entity exists for any covenant type."""
        from typedb.driver import TransactionType

        provision_type = f"{covenant_type.lower()}_provision"

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            check = f'match $p isa {provision_type}, has provision_id "{provision_id}"; select $p;'
            rows = list(tx.query(check).resolve().as_concept_rows())
            exists = len(rows) > 0
        finally:
            tx.close()

        if exists:
            logger.debug(f"Provision {provision_id} already exists")
            return

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            tx.query(f'''
                match $d isa deal, has deal_id "{deal_id}";
                insert
                    $p isa {provision_type}, has provision_id "{provision_id}";
                    (deal: $d, provision: $p) isa deal_has_provision;
            ''').resolve()
            tx.commit()
            logger.info(f"Created {provision_type}: {provision_id}")
        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.error(f"Failed to create provision {provision_id}: {e}")
            raise

    async def _extract_entities_unified(
        self,
        universe: CovenantUniverse,
        questions: List[dict],
        model: str,
        deal_id: str,
        cost_summary: 'ExtractionCostSummary',
    ) -> List[dict]:
        """Extract entities — unified for all covenant types."""
        from app.services.graph_storage import GraphStorage

        prompt = GraphStorage.build_entity_list_prompt(questions, universe.raw_text)
        logger.info(f"Entity extraction prompt: {len(prompt)} chars")

        response = self._call_claude_extract(
            prompt=prompt,
            model=model,
            deal_id=deal_id,
            step_name="entity_list",
            cost_summary=cost_summary,
        )

        extraction = GraphStorage.parse_extraction_response(response)
        return extraction.answers

    async def _extract_scalars_dynamic(
        self,
        universe: CovenantUniverse,
        questions_by_cat: Dict[str, List],
        model: str,
        deal_id: str,
        cost_summary: 'ExtractionCostSummary',
    ) -> List[dict]:
        """Dynamic batched scalar extraction."""
        from app.services.graph_storage import GraphStorage

        batches = self._batch_questions_by_category(questions_by_cat)
        all_answers = []

        for i, batch_questions in enumerate(batches):
            batch_cats = sorted(batch_questions.keys())
            batch_count = sum(len(qs) for qs in batch_questions.values())

            prompt = GraphStorage.build_scalar_prompt(batch_questions, universe.raw_text)
            logger.info(
                f"Scalar batch {i+1}/{len(batches)} ({','.join(batch_cats)}): "
                f"{batch_count} questions, {len(prompt)} chars"
            )

            response = self._call_claude_extract(
                prompt=prompt,
                model=model,
                deal_id=deal_id,
                step_name=f"scalar_batch_{i+1}",
                cost_summary=cost_summary,
            )

            extraction = GraphStorage.parse_extraction_response(response)
            all_answers.extend(extraction.answers)
            logger.info(f"Batch {i+1} complete: {len(extraction.answers)} answers")

        return all_answers

    def _call_claude_extract(
        self,
        prompt: str,
        model: str,
        deal_id: str,
        step_name: str,
        cost_summary: 'ExtractionCostSummary',
    ) -> str:
        """Unified Claude API call with cost tracking for extraction."""
        from app.services.cost_tracker import extract_usage

        start = time.time()
        response = self.client.messages.create(
            model=model,
            max_tokens=16000,
            messages=[{"role": "user", "content": prompt}],
            timeout=600.0,
        )
        duration = time.time() - start

        usage = extract_usage(response, model, step_name, deal_id=deal_id, duration=duration)
        cost_summary.add(usage)

        text = response.content[0].text.strip()
        logger.info(
            f"Claude {step_name}: {len(text)} chars, "
            f"stop_reason={response.stop_reason}, {duration:.1f}s"
        )

        return text

    def _batch_questions_by_category(self, questions_by_cat: Dict[str, List], max_tokens_budget: int = 10000) -> List[Dict[str, List]]:
        """Split scalar/multiselect questions into batches that fit within output token budget.

        Keeps entire categories together (never splits a category across batches).
        Estimates output tokens based on ~600 chars per answer at ~3.5 chars per token.

        Args:
            questions_by_cat: Dict of {category_id: [question_dicts]}
            max_tokens_budget: Max estimated output tokens per batch

        Returns:
            List of question_by_cat dicts, each fitting within the budget
        """
        CHARS_PER_ANSWER = 600
        CHARS_PER_TOKEN = 3.5

        batches = []
        current_batch = {}
        current_est_tokens = 0

        for cat_id in sorted(questions_by_cat.keys()):
            cat_questions = questions_by_cat[cat_id]
            est_tokens = len(cat_questions) * CHARS_PER_ANSWER / CHARS_PER_TOKEN

            if current_est_tokens + est_tokens > max_tokens_budget and current_batch:
                batches.append(current_batch)
                current_batch = {}
                current_est_tokens = 0

            current_batch[cat_id] = cat_questions
            current_est_tokens += est_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def _load_entity_list_questions(self, covenant_type: str) -> List[Dict]:
        """Load entity_list questions from TypeDB (no category relations).

        These questions have answer_type "entity_list" and include
        target_entity_type and target_relation_type attributes.
        """
        if not typedb_client.driver:
            logger.warning("TypeDB not connected")
            return []

        try:
            from typedb.driver import TransactionType
            tx = typedb_client.driver.transaction(
                settings.typedb_database, TransactionType.READ
            )
            try:
                query = f"""
                    match
                        $q isa ontology_question,
                            has question_id $qid,
                            has question_text $qt,
                            has answer_type "entity_list",
                            has covenant_type "{covenant_type}",
                            has target_entity_type $tet,
                            has target_relation_type $trt,
                            has display_order $order;
                    select $qid, $qt, $tet, $trt, $order;
                """
                result = tx.query(query).resolve()
                questions = []
                for row in result.as_concept_rows():
                    qid = _safe_get_value(row, "qid")
                    if not qid:
                        continue
                    q = {
                        "question_id": qid,
                        "question_text": _safe_get_value(row, "qt", ""),
                        "answer_type": "entity_list",
                        "target_entity_type": _safe_get_value(row, "tet", ""),
                        "target_relation_type": _safe_get_value(row, "trt", ""),
                        "display_order": _safe_get_value(row, "order", 0),
                    }

                    # Load extraction_prompt if available
                    prompt = self._get_extraction_prompt(tx, qid)
                    if prompt:
                        q["extraction_prompt"] = prompt

                    questions.append(q)

                logger.info(f"Loaded {len(questions)} entity_list questions for {covenant_type}")
                return questions
            finally:
                tx.close()
        except Exception as e:
            logger.error(f"Error loading entity_list questions: {e}")
            return []


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
