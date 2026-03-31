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
    """Complete extraction result."""
    deal_id: str
    covenant_type: str
    category_answers: List[CategoryAnswers]
    extraction_time_seconds: float
    chunks_processed: int = 0
    total_questions: int = 0
    high_confidence_answers: int = 0
    rp_universe_chars: int = 0
    rp_universe: Optional['CovenantUniverse'] = None  # Retained for J.Crew pipeline
    document_text: Optional[str] = None         # Retained for J.Crew pipeline
    segment_map: Optional[dict] = None          # Reused for MFN universe extraction


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

            self._ensure_provision_exists(deal_id, provision_id)
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

    def _ensure_provision_exists(self, deal_id: str, provision_id: str):
        """Create rp_provision entity linked to deal if not exists."""
        from typedb.driver import TransactionType
        from datetime import datetime, timezone

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            query = f"""
                match $p isa rp_provision, has provision_id "{provision_id}";
                select $p;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            exists = len(result) > 0
        finally:
            tx.close()

        if exists:
            logger.debug(f"Provision {provision_id} already exists")
            return

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                insert
                    $p isa rp_provision,
                        has provision_id "{provision_id}",
                        has extracted_at {now_iso};
                    (deal: $d, provision: $p) isa deal_has_provision;
            """
            tx.query(query).resolve()
            tx.commit()
            logger.info(f"Created rp_provision: {provision_id}")
        except Exception as e:
            tx.close()
            logger.error(f"Error creating provision: {e}")
            raise

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
    # MFN EXTRACTION PIPELINE
    # =========================================================================

    def extract_mfn_universe(self, document_text: str) -> Optional[str]:
        """
        Extract the MFN-relevant universe from a credit agreement.

        MFN provisions are in the INCREMENTAL FACILITY section of the
        agreement, NOT in the covenants section. The MFN universe
        is much shorter than RP — typically 5-15 pages vs 20+.
        """
        MFN_UNIVERSE_PROMPT = """You are a senior leveraged finance attorney. Extract the complete MFN
(Most Favored Nation) universe from this credit agreement.

## WHAT TO EXTRACT

Return the COMPLETE text of all sections relevant to MFN analysis:

1. **The Incremental Facility Section** — Extract the ENTIRE section,
   not just the MFN sub-clause. The full incremental facility mechanics
   provide context for which debt types are subject to MFN. This section
   is titled variations of "Incremental Facilities", "Incremental
   Commitments and Loans", or "Additional Credit Extensions". The section
   number varies by document.

2. **The Effective Yield / All-In Yield Definition** (in the Definitions
   section) — This defines exactly which components are included in the
   yield calculation for MFN comparison.

3. **Amendment Provisions** — Only the subsections about what constitutes
   a "sacred right" or what requires all-lender consent vs. Required
   Lender consent. This determines how easily MFN can be waived.

4. **Debt Incurrence Covenant** — Only the portions relevant to
   "Incremental Equivalent Debt", "Ratio Debt", or similar defined terms
   that describe debt incurred outside the credit agreement framework.
   This is needed to assess the reclassification loophole.

5. **Related Definitions** from the Definitions section:
   - "Incremental Facility", "Incremental Term Loan", "Incremental
     Revolving Commitment"
   - "Incremental Equivalent Debt" or "Credit Agreement Refinancing
     Indebtedness"
   - "Effective Yield" or "All-In Yield"
   - "Required Lenders"
   - "Applicable Rate" or "Applicable Margin"

## WHAT NOT TO EXTRACT

- Restricted Payments covenant — not relevant to MFN
- Financial covenants — not relevant
- Representations and warranties — not relevant
- Events of default — not relevant (unless cross-referenced by MFN)
- Administrative provisions — not relevant

## OUTPUT FORMAT

Return the extracted text preserving:
- Section numbers and headings
- Page markers ([PAGE X])
- Defined term capitalization
- Cross-references to other sections

The MFN universe is typically 5-15 pages (much shorter than RP)."""

        try:
            # MFN sections are shorter so we can send more document context
            trimmed = document_text[:600000]

            prompt = f"{MFN_UNIVERSE_PROMPT}\n\n## DOCUMENT TEXT\n\n{trimmed}"
            raw_text = self._call_claude_streaming(
                prompt, max_tokens=30000, step="mfn_universe_extraction"
            )

            if not raw_text:
                logger.warning("MFN universe extraction returned empty response")
                return None

            logger.info(f"MFN universe extracted: {len(raw_text)} chars")
            return raw_text

        except Exception as e:
            logger.error(f"MFN universe extraction failed: {e}")
            return None

    # ── MFN Batch Configuration ─────────────────────────────────────────────
    # Batch sections and hints loaded from TypeDB (SSoT) via
    # ontology_category.extraction_context_sections and extraction_batch_hint.
    # See _get_batch_metadata().
    _mfn_batch_metadata_cache: Dict[str, Dict[str, str]] = {}

    def _get_batch_metadata(self, cat_id: str) -> Dict[str, str]:
        """Load extraction_context_sections and extraction_batch_hint for a category from TypeDB."""
        if cat_id in self._mfn_batch_metadata_cache:
            return self._mfn_batch_metadata_cache[cat_id]

        from typedb.driver import TransactionType
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            result = tx.query(f"""
                match
                    $cat isa ontology_category,
                        has category_id "{cat_id}";
                    try {{ $cat has extraction_context_sections $secs; }};
                    try {{ $cat has extraction_batch_hint $hint; }};
                select $secs, $hint;
            """).resolve()

            for row in result.as_concept_rows():
                secs = row.get("secs")
                hint = row.get("hint")
                metadata = {
                    "extraction_context_sections": secs.as_attribute().get_value() if secs else "",
                    "extraction_batch_hint": hint.as_attribute().get_value() if hint else "",
                }
                self._mfn_batch_metadata_cache[cat_id] = metadata
                return metadata
            return {"extraction_context_sections": "", "extraction_batch_hint": ""}
        finally:
            tx.close()

    _MFN_EXTRACTION_SYSTEM_PROMPT = """You are a senior leveraged finance attorney specializing in credit agreement
analysis. You are extracting Most Favored Nation (MFN) provision data from
a credit agreement.

## WHERE TO FIND THE MFN PROVISION

MFN clauses are located in the INCREMENTAL FACILITY section of a credit
agreement, NOT in the covenants section. Look in these locations:

MFN clauses are in the INCREMENTAL FACILITY section of a credit
agreement, NOT in the covenants section. This section is titled
variations of "Incremental Facilities", "Incremental Commitments and
Loans", "Incremental Term Loans", or "Additional Credit Extensions".
It is in the Loans/Commitments article, not the Negative Covenants
article. The section number varies by document — do NOT assume any
specific number.

Within the incremental facility section, MFN is typically a sub-clause
that starts with language like:
- "the Effective Yield applicable to any Incremental Term Loan shall not
   exceed..."
- "if the All-In Yield applicable to any Incremental Term Loan exceeds..."
- "the Applicable Rate for any Incremental Term Loan shall not be more
   than [X] basis points greater than..."

## KEY TERMS TO RECOGNIZE

- **Effective Yield / All-In Yield**: The total annualized return to a
  lender, including spread, floor benefit, OID, and fees. The definition
  of what's included is the most contested part of MFN.

- **Applicable Rate / Applicable Margin**: The contractual interest rate
  spread over the reference rate (SOFR/LIBOR). Some MFN clauses compare
  only the Applicable Rate (margin-only, borrower-friendly) rather than
  all-in yield.

- **MFN Threshold**: The permitted pricing differential (e.g., 50bps).
  New incremental debt can be priced UP TO this amount above existing
  debt without triggering MFN adjustment.

- **Incremental Equivalent Debt**: Debt incurred outside the credit
  agreement (e.g., under the debt incurrence covenant in the Negative
  Covenants article)
  that is treated as equivalent to incremental facility debt. Whether
  this is subject to MFN is critical — if not, it creates a major
  reclassification loophole.

- **Sunset**: A time limit after which MFN protection expires.
  Common periods: 6, 12, 18, 24 months from closing.

## EXTRACTION RULES

1. Extract ONLY what the document states. Do not infer or assume.
2. For boolean questions: answer true/false based on document language.
3. For string questions: extract verbatim language where possible.
4. For integer questions: extract exact numbers.
5. For multiselect questions: return an array of concept_ids that apply.
6. Always provide source_text — a verbatim quote (30-500 chars).
7. Always provide source_page — use the [PAGE X] markers in the text.
8. Always provide source_section — the section reference as it appears in THIS document.
9. If the MFN provision does not exist, answer mfn_01 as false and
   answer remaining questions as null.
10. If information is genuinely not specified, answer null."""

    def _parse_mfn_universe_sections(self, mfn_universe_text: str) -> Dict[str, str]:
        """Parse MFN universe text into sections by === HEADER === markers."""
        sections: Dict[str, str] = {}
        current_key: Optional[str] = None
        current_lines: list = []

        for line in mfn_universe_text.split('\n'):
            if line.startswith('=== ') and line.endswith(' ==='):
                if current_key:
                    sections[current_key] = '\n'.join(current_lines)
                current_key = line.strip('= ').strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_key:
            sections[current_key] = '\n'.join(current_lines)

        return sections

    def _build_mfn_batch_context(
        self,
        cat_id: str,
        full_universe: str,
        sections: Dict[str, str]
    ) -> str:
        """Build focused MFN universe context for a specific batch."""
        metadata = self._get_batch_metadata(cat_id)
        sections_str = metadata.get("extraction_context_sections", "")
        needed = sections_str.split(",") if sections_str else None
        if not needed or not sections:
            return full_universe

        parts = []
        for section_key in needed:
            if section_key in sections:
                parts.append(f"=== {section_key} ===\n{sections[section_key]}")

        if not parts:
            return full_universe

        return "\n\n".join(parts)

    def _summarize_batch_answers(self, cat_id: str, answers: list) -> str:
        """Summarize batch answers for cross-reference by MFN6."""
        if not answers:
            return ""
        lines = [f"## Prior Analysis: {cat_id}"]
        for a in answers:
            qid = a.get("question_id", "")
            val = a.get("value")
            if val is not None:
                lines.append(f"- {qid}: {val}")
        return "\n".join(lines)

    def _extract_mfn_batch(
        self,
        cat_id: str,
        questions: List[Dict],
        full_universe: str,
        universe_sections: Dict[str, str],
        entity_context: Optional[str] = None
    ) -> list:
        """Extract answers for one MFN category batch."""
        context_text = self._build_mfn_batch_context(
            cat_id, full_universe, universe_sections
        )
        questions_text = self._format_questions_for_prompt(questions)
        metadata = self._get_batch_metadata(cat_id)
        batch_hint = metadata.get("extraction_batch_hint", "")

        entity_section = ""
        if entity_context:
            entity_section = f"\n\n## PRIOR ANALYSIS (from earlier batches)\n\n{entity_context}"

        user_prompt = f"""## MFN UNIVERSE TEXT

{context_text}
{entity_section}

---

## BATCH FOCUS: {cat_id}
{batch_hint}

## QUESTIONS

{questions_text}

## RESPONSE FORMAT

Return a JSON object with an "answers" array. Each answer:
{{
  "question_id": "mfn_01",
  "value": true,
  "source_text": "verbatim quote from document (30-500 chars)",
  "source_page": 45,
  "source_section": "e.g., Section X.XX(a)"
}}

For multiselect questions, value is an array of concept_ids:
{{
  "question_id": "mfn_08",
  "value": ["incremental_term_loans", "ratio_debt"],
  "source_text": "...",
  "source_page": 45,
  "source_section": "e.g., Section X.XX(a)"
}}

IMPORTANT: Respond with ONLY the JSON object. Do not include any analysis, explanation, or preamble before or after the JSON."""

        from app.services.cost_tracker import extract_usage
        try:
            context_chars = len(context_text)
            logger.info(
                f"MFN batch {cat_id}: {len(questions)} questions, "
                f"context={context_chars} chars"
            )

            mfn_model = "claude-sonnet-4-6"
            start = time.time()
            response = self.client.messages.create(
                model=mfn_model,
                max_tokens=8000,
                system=self._MFN_EXTRACTION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )
            duration = time.time() - start
            self._last_mfn_batch_usage = extract_usage(
                response, mfn_model, "mfn_extraction", deal_id=None, duration=duration
            )

            text = response.content[0].text.strip()
            stop = response.stop_reason
            logger.info(
                f"MFN batch {cat_id}: response {len(text)} chars, "
                f"stop_reason={stop}"
            )

            if not text:
                logger.error(
                    f"MFN batch {cat_id}: empty response "
                    f"(stop_reason={stop})"
                )
                return []

            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(text)
            answers = result.get("answers", [])
            logger.info(f"MFN batch {cat_id}: {len(answers)}/{len(questions)} answers")
            return answers

        except json.JSONDecodeError as e:
            logger.error(
                f"MFN batch {cat_id} JSON parse failed: {e}. "
                f"Raw response (first 500 chars): {text[:500] if text else 'EMPTY'}"
            )
            return []
        except Exception as e:
            logger.error(f"MFN batch {cat_id} failed: {e}")
            return []

    async def run_mfn_extraction_consolidated(
        self,
        deal_id: str,
        mfn_universe_text: str,
        document_text: str,
    ) -> dict:
        """
        Consolidated MFN extraction: 2 Claude calls instead of 6.

        Batch A (MFN1-MFN4): Structural questions — factual extraction of MFN
            existence, prongs, floor, yield mechanics, exclusions. Independent
            questions that don't need prior answers.

        Batch B (MFN5-MFN6): Pattern detection — loophole identification and
            cross-reference analysis. Receives Batch A answers as prior context
            so it can reason about patterns (e.g., "given the MFN floor is X
            and OID is excluded, does this create a timing loophole?").

        Returns answers — scalar storage deferred to caller (after entity extraction).
        """
        # Load MFN questions from TypeDB (SSoT)
        questions_by_cat = self.load_questions_by_category("MFN")
        if not questions_by_cat:
            logger.error("No MFN questions found in TypeDB")
            return {"answers": [], "errors": ["No MFN questions in TypeDB"], "total_questions": 0, "answered": 0}

        total_questions = sum(len(qs) for qs in questions_by_cat.values())
        logger.info(
            f"MFN consolidated extraction: {total_questions} questions in "
            f"{len(questions_by_cat)} categories → 2 batches"
        )

        # Parse universe into sections for focused context
        universe_sections = self._parse_mfn_universe_sections(mfn_universe_text)

        # ── Batch A: Structural (MFN1-MFN4) ──────────────────────────────
        self._mfn_batch_usages = []  # Accumulate per-batch usage for cost summary
        batch_a_questions = []
        for cat_id in ["MFN1", "MFN2", "MFN3", "MFN4"]:
            cat_qs = questions_by_cat.get(cat_id, [])
            cat_qs.sort(key=lambda q: q.get("display_order", 0))
            batch_a_questions.extend(cat_qs)

        batch_a_answers = []
        if batch_a_questions:
            logger.info(f"MFN Batch A (structural): {len(batch_a_questions)} questions")
            batch_a_answers = self._extract_mfn_batch(
                "MFN_structural",
                batch_a_questions,
                mfn_universe_text,
                universe_sections,
            )
            if getattr(self, '_last_mfn_batch_usage', None):
                self._mfn_batch_usages.append(self._last_mfn_batch_usage)
            logger.info(f"MFN Batch A complete: {len(batch_a_answers)} answers")

        # ── Batch B: Patterns (MFN5-MFN6) with prior context ─────────────
        batch_b_questions = []
        for cat_id in ["MFN5", "MFN6"]:
            cat_qs = questions_by_cat.get(cat_id, [])
            cat_qs.sort(key=lambda q: q.get("display_order", 0))
            batch_b_questions.extend(cat_qs)

        batch_b_answers = []
        if batch_b_questions:
            # Build prior context from Batch A answers
            entity_context = self._summarize_batch_answers("MFN_structural", batch_a_answers)
            logger.info(
                f"MFN Batch B (patterns): {len(batch_b_questions)} questions, "
                f"prior context={len(entity_context)} chars"
            )
            batch_b_answers = self._extract_mfn_batch(
                "MFN_patterns",
                batch_b_questions,
                mfn_universe_text,
                universe_sections,
                entity_context=entity_context if entity_context else None,
            )
            if getattr(self, '_last_mfn_batch_usage', None):
                self._mfn_batch_usages.append(self._last_mfn_batch_usage)
            logger.info(f"MFN Batch B complete: {len(batch_b_answers)} answers")

        all_answers = batch_a_answers + batch_b_answers
        logger.info(f"MFN consolidated extraction complete: {len(all_answers)}/{total_questions} answers")

        # Ensure provision exists (needed before entity extraction can link to it)
        # Scalar storage deferred to after entity extraction — caller handles ordering
        if all_answers:
            provision_id = f"{deal_id}_mfn"
            self._ensure_mfn_provision_exists(deal_id, provision_id)

        # Aggregate cost tracking for MFN pipeline
        from app.services.cost_tracker import ExtractionCostSummary
        cost_summary = ExtractionCostSummary(deal_id=deal_id)
        # Add MFN universe streaming usage (if Claude-based extraction was used)
        for usage in getattr(self, '_streaming_usages', []):
            usage.deal_id = deal_id
            cost_summary.add(usage)
        self._streaming_usages = []
        for usage in getattr(self, '_mfn_batch_usages', []):
            usage.deal_id = deal_id
            cost_summary.add(usage)
        if cost_summary.steps:
            cost_summary.log_summary()

        return {
            "answers": all_answers,
            "errors": [],
            "total_questions": total_questions,
            "answered": len(all_answers),
        }
    def _ensure_mfn_provision_exists(self, deal_id: str, provision_id: str):
        """Create mfn_provision entity linked to deal if not exists."""
        from typedb.driver import TransactionType

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            query = f"""
                match $p isa mfn_provision, has provision_id "{provision_id}";
                select $p;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            exists = len(result) > 0
        finally:
            tx.close()

        if exists:
            logger.debug(f"MFN provision {provision_id} already exists")
            return

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                insert
                    $p isa mfn_provision,
                        has provision_id "{provision_id}",
                        has extracted_at {now_iso};
                    (deal: $d, provision: $p) isa deal_has_provision;
            """
            tx.query(query).resolve()
            tx.commit()
            logger.info(f"Created mfn_provision: {provision_id}")
        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.error(f"Error creating MFN provision: {e}")
            raise

    def _store_mfn_answers(self, deal_id: str, answers: list):
        """
        Store MFN extraction answers in TypeDB.

        Creates mfn_provision (if not exists), links to deal via
        deal_has_provision, then creates provision_has_answer relations
        for each answer. Uses existing GraphStorage.store_scalar_answer
        for the SSoT storage pattern.
        """
        from app.services.graph_storage import GraphStorage

        provision_id = f"{deal_id}_mfn"

        try:
            # Provision must already exist (created by caller before entity extraction)
            # Store answers using GraphStorage (SSoT pattern)
            storage = GraphStorage(deal_id)
            q_to_entity = storage._load_question_to_entity_map()
            stored_scalar = 0
            stored_concept = 0
            errors = 0

            for ans in answers:
                qid = ans.get("question_id")
                value = ans.get("value")
                if value is None:
                    continue

                confidence = ans.get("confidence", "medium")
                if confidence == "not_found":
                    continue

                source_text = ans.get("source_text", "")
                source_page = ans.get("source_page")
                source_section = ans.get("source_section", "")

                # Multiselect → unified concept routing + flat scalar
                if isinstance(value, list):
                    # Try entity boolean routing (same as RP store_extraction)
                    concept_routing = GraphStorage._load_concept_routing_map()
                    routed_any = False
                    for concept_id in value:
                        routes = concept_routing.get(concept_id, [])
                        if routes:
                            for entity_type, attr_name in routes:
                                storage._set_entity_attribute(
                                    provision_id, entity_type, attr_name, True
                                )
                            routed_any = True

                    # Always store as flat scalar (comma-separated string)
                    flat_value = ", ".join(str(v) for v in value)
                    try:
                        storage.store_scalar_answer(
                            provision_id=provision_id,
                            question_id=qid,
                            value=flat_value,
                            source_text=source_text or None,
                            source_page=source_page,
                            source_section=source_section or None,
                        )
                        stored_scalar += 1
                    except Exception as e:
                        errors += 1
                        if errors <= 3:
                            logger.warning(f"MFN multiselect scalar store error ({qid}): {e}")

                    # Write concept_applicability only for unmapped concepts
                    if not routed_any:
                        for concept_id in value:
                            success = self._store_concept_applicability_for_provision(
                                provision_id, concept_id, source_text,
                                source_page or 0
                            )
                            if success:
                                stored_concept += 1
                    continue

                # Scalar → provision_has_answer via GraphStorage
                try:
                    coerced = self._coerce_answer_value(
                        value, self._infer_answer_type(value)
                    )
                    if coerced is not None:
                        storage.store_scalar_answer(
                            provision_id=provision_id,
                            question_id=qid,
                            value=coerced,
                            source_text=source_text or None,
                            source_page=source_page,
                            source_section=source_section or None,
                        )
                        stored_scalar += 1
                        # Annotation routing: populate entity attribute if mapped
                        routing = q_to_entity.get(qid)
                        if routing:
                            entity_type, attr_name = routing
                            if attr_name not in ("_exists", "_entity_list"):
                                storage._set_entity_attribute(
                                    provision_id, entity_type, attr_name, coerced
                                )
                except Exception as e:
                    errors += 1
                    if errors <= 3:  # Only log first few errors
                        logger.warning(f"MFN answer store error ({qid}): {e}")

            logger.info(
                f"Stored {stored_scalar} MFN scalar + "
                f"{stored_concept} concept answers"
            )

        except Exception as e:
            logger.error(f"MFN answer storage failed: {e}")

    def _store_concept_applicability_for_provision(
        self,
        provision_id: str,
        concept_id: str,
        source_text: str,
        source_page: int
    ) -> bool:
        """Store concept_applicability for any provision type (uses parent type)."""
        from typedb.driver import TransactionType

        escaped_text = source_text.replace('\\', '\\\\').replace('"', '\\"')[:500]

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match
                    $p isa provision, has provision_id "{provision_id}";
                    $c isa concept, has concept_id "{concept_id}";
                insert
                    (provision: $p, concept: $c) isa concept_applicability,
                        has applicability_status "INCLUDED",
                        has source_text "{escaped_text}",
                        has source_page {source_page};
            """
            tx.query(query).resolve()
            tx.commit()
            return True
        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.warning(f"MFN concept store error ({concept_id}): {e}")
            return False

    @staticmethod
    def _infer_answer_type(value) -> str:
        """Infer answer type from Python value type."""
        if isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "double"
        return "string"

    # =========================================================================
    # CROSS-REFERENCES
    # =========================================================================

    def _create_cross_references(self, deal_id: str):
        """Create provision_cross_reference between MFN and RP provisions.

        Idempotent: skips if edge already exists.
        Loud: logs explicitly at every branch.
        """
        from typedb.driver import TransactionType

        mfn_id = f"{deal_id}_mfn"

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            # Check if cross-reference already exists
            existing = list(tx.query(f'''
                match
                    $mfn isa mfn_provision, has provision_id "{mfn_id}";
                    (source_provision: $mfn, target_provision: $rp) isa provision_cross_reference;
                select $rp;
            ''').resolve().as_concept_rows())
            if existing:
                logger.info(f"Cross-reference already exists for {deal_id} — skipping")
                return

            # Check both provisions exist
            result = list(tx.query(f'''
                match
                    $d isa deal, has deal_id "{deal_id}";
                    $mfn isa mfn_provision, has provision_id "{mfn_id}";
                    $rp isa rp_provision, has provision_id $rpid;
                    (deal: $d, provision: $mfn) isa deal_has_provision;
                    (deal: $d, provision: $rp) isa deal_has_provision;
                select $rpid;
            ''').resolve().as_concept_rows())
            has_both = len(result) > 0
        finally:
            tx.close()

        if not has_both:
            logger.warning(f"Cannot create cross-reference for {deal_id}: missing MFN or RP provision")
            return

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            tx.query(f'''
                match
                    $d isa deal, has deal_id "{deal_id}";
                    $mfn isa mfn_provision, has provision_id "{mfn_id}";
                    $rp isa rp_provision, has provision_id $rpid;
                    (deal: $d, provision: $mfn) isa deal_has_provision;
                    (deal: $d, provision: $rp) isa deal_has_provision;
                insert
                    (source_provision: $mfn, target_provision: $rp)
                        isa provision_cross_reference,
                        has cross_reference_type "depends_on",
                        has cross_reference_explanation "MFN exclusions interact with RP debt incurrence capacity.";
            ''').resolve()
            tx.commit()
            logger.info(f"Created MFN→RP cross-reference for deal {deal_id}")
        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.error(f"Failed to create cross-reference for {deal_id}: {e}")
            raise

    # =========================================================================
    # MFN ENTITY EXTRACTION
    # =========================================================================

    async def run_mfn_entity_extraction(
        self,
        deal_id: str,
        mfn_universe_text: str,
    ) -> dict:
        """Extract MFN entities via unified entity_list pipeline.

        Same flow as RP: load entity_list questions → build_entity_list_prompt
        (schema introspection) → Claude → parse → store_extraction.
        """
        from app.services.graph_storage import GraphStorage

        provision_id = f"{deal_id}_mfn"

        # 1. Load MFN entity_list questions
        entity_list_questions = self._load_entity_list_questions("MFN")
        if not entity_list_questions:
            logger.warning("No MFN entity_list questions found")
            return {"entities_stored": 0, "errors": ["No MFN entity_list questions"]}

        # 2. Build prompt (schema introspection adds fields automatically)
        entity_prompt = GraphStorage.build_entity_list_prompt(
            entity_list_questions, mfn_universe_text
        )
        logger.info(
            f"MFN entity list prompt: {len(entity_prompt)} chars, "
            f"{len(entity_list_questions)} questions"
        )

        # 3. Call Claude (synchronous — matches RP pattern)
        model = settings.claude_model
        entity_response = self._call_claude_unified(
            entity_prompt, model, deal_id, step_name="mfn_entity_list"
        )
        logger.info(f"MFN entity list response: {len(entity_response)} chars")

        # 4. Parse and store via unified store_extraction
        entity_extraction = GraphStorage.parse_extraction_response(entity_response)
        storage = GraphStorage(deal_id)
        result = storage.store_extraction(deal_id, provision_id, entity_extraction)
        logger.info(f"MFN entity storage: {result}")

        return {
            "entities_stored": result.get("entities_created", 0),
            "answers_stored": result.get("answers_stored", 0),
        }

    async def extract_rp_unified(
        self,
        deal_id: str,
        document_text: str,
        rp_universe: 'CovenantUniverse',
        segment_map: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> 'ExtractionResult':
        """
        Batched extraction pipeline — multiple Claude calls, unified answer format.

        Splits extraction into batched calls sharing the same document text:
        - Call 0: Entity list extraction (dedicated call for full entity coverage)
        - Calls 1-N: Scalar/multiselect questions in category-grouped batches
        - JC2/JC3: Separate calls (different context needed)

        ALL answers flow through {"answers": [...]} response format.
        Entity field lists come from TypeDB schema introspection.

        Args:
            deal_id: The deal being extracted
            document_text: Full parsed document text
            rp_universe: Already-extracted RP universe
            segment_map: Segment map from segmentation (for reuse)
            model: Claude model to use (default from settings)

        Returns:
            ExtractionResult with extraction, storage results, and cost data
        """
        from app.services.graph_storage import GraphStorage

        start_time = time.time()
        model = model or settings.claude_model
        universe_chars = len(rp_universe.raw_text)

        logger.info(f"Unified V4 extraction starting for deal {deal_id}")

        # Step 1: Load all RP questions (scalar/multiselect, via category_has_question)
        all_questions = self.load_questions_by_category("RP")

        # JC1 goes into the unified call (same RP universe context).
        # JC2/JC3 need separate context, handled after.
        jc2_questions = all_questions.pop("JC2", [])
        jc3_questions = all_questions.pop("JC3", [])

        # Step 2: Load entity_list questions (separate — no category relations)
        entity_list_questions = self._load_entity_list_questions("RP")

        total_scalar = sum(len(qs) for qs in all_questions.values())
        logger.info(
            f"Loaded {total_scalar} scalar/multiselect questions "
            f"({len(all_questions)} categories), "
            f"{len(entity_list_questions)} entity_list questions, "
            f"JC2={len(jc2_questions)}, JC3={len(jc3_questions)} deferred"
        )

        # Collect cost tracking
        from app.services.cost_tracker import ExtractionCostSummary
        from app.schemas.extraction_response import ExtractionResponse
        cost_summary = ExtractionCostSummary(deal_id=deal_id)
        for usage in getattr(self, '_streaming_usages', []):
            usage.deal_id = deal_id
            cost_summary.add(usage)
        self._streaming_usages = []

        all_answers = []

        # Step 3: Call 0 — Entity list extraction (dedicated call)
        if entity_list_questions:
            entity_prompt = GraphStorage.build_entity_list_prompt(
                entity_list_questions, rp_universe.raw_text
            )
            logger.info(f"Entity list prompt built: {len(entity_prompt)} chars")
            entity_response = self._call_claude_unified(
                entity_prompt, model, deal_id, step_name="rp_entity_list"
            )
            logger.info(f"Entity list response: {len(entity_response)} chars")
            if getattr(self, '_last_usage', None):
                self._last_usage.deal_id = deal_id
                cost_summary.add(self._last_usage)

            entity_extraction = GraphStorage.parse_extraction_response(entity_response)
            all_answers.extend(entity_extraction.answers)
            logger.info(f"Call 0 (entity_list): {len(entity_extraction.answers)} answers")

        # Step 4: Calls 1-N — Scalar/multiselect in batches
        batches = self._batch_questions_by_category(all_questions)
        logger.info(f"Scalar extraction: {len(batches)} batches from {total_scalar} questions")

        for i, batch_questions in enumerate(batches):
            batch_prompt = GraphStorage.build_scalar_prompt(
                batch_questions, rp_universe.raw_text
            )
            batch_q_count = sum(len(qs) for qs in batch_questions.values())
            batch_cats = sorted(batch_questions.keys())
            logger.info(
                f"Batch {i+1}/{len(batches)} ({','.join(batch_cats)}): "
                f"{batch_q_count} questions, {len(batch_prompt)} chars prompt"
            )

            batch_response = self._call_claude_unified(
                batch_prompt, model, deal_id, step_name=f"rp_scalar_batch_{i+1}"
            )
            if getattr(self, '_last_usage', None):
                self._last_usage.deal_id = deal_id
                cost_summary.add(self._last_usage)

            batch_extraction = GraphStorage.parse_extraction_response(batch_response)
            all_answers.extend(batch_extraction.answers)
            logger.info(
                f"Batch {i+1}/{len(batches)} ({','.join(batch_cats)}): "
                f"{len(batch_extraction.answers)}/{batch_q_count} answers"
            )

        # Merge all answers
        extraction = ExtractionResponse(answers=all_answers)
        entity_count = sum(1 for a in extraction.answers if a.answer_type == "entity_list")
        scalar_count = len(extraction.answers) - entity_count
        logger.info(f"Total: {scalar_count} scalar/multiselect, {entity_count} entity_list answers from {1 + len(batches)} calls")

        # Step 6: Ensure provision exists, then store
        storage = GraphStorage(deal_id)
        provision_id = f"{deal_id}_rp"
        storage._ensure_rp_provision_v4(provision_id)
        logger.info("Storing unified extraction to TypeDB...")
        storage_result = storage.store_extraction(deal_id, provision_id, extraction)
        logger.info(f"Storage complete: {storage_result}")

        # Step 7: Run JC Tiers 2 & 3 (separate Claude calls — different context)
        jc_result = await self._run_jcrew_tiers_2_3(
            deal_id=deal_id,
            provision_id=f"{deal_id}_rp",
            document_text=document_text,
            rp_universe=rp_universe,
            jc2_questions=jc2_questions,
            jc3_questions=jc3_questions,
        )
        if jc_result:
            if getattr(self, '_last_jc2_usage', None):
                cost_summary.add(self._last_jc2_usage)
            if getattr(self, '_last_jc3_usage', None):
                cost_summary.add(self._last_jc3_usage)

        extraction_time = time.time() - start_time
        logger.info(f"Unified V4 extraction complete in {extraction_time:.1f}s")
        cost_summary.log_summary()

        return ExtractionResult(
            deal_id=deal_id,
            extraction=extraction,
            storage_result=storage_result,
            extraction_time_seconds=extraction_time,
            rp_universe_chars=universe_chars,
            model_used=model,
            total_cost_usd=round(cost_summary.total_cost_usd, 4),
            cost_breakdown={
                "num_api_calls": len(cost_summary.steps),
                "total_input_tokens": cost_summary.total_input_tokens,
                "total_output_tokens": cost_summary.total_output_tokens,
                "steps": [
                    {"step": s.step, "model": s.model, "cost_usd": round(s.cost_usd, 4)}
                    for s in cost_summary.steps
                ],
            },
        )

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

    def _call_claude_unified(self, prompt: str, model: str, deal_id: Optional[str] = None, step_name: str = "rp_unified") -> str:
        """Call Claude API for V4 extraction (entities or scalar batch).

        Uses max_tokens=16384 and 10-minute timeout.

        Args:
            prompt: The formatted extraction prompt
            model: Claude model to use
            deal_id: Deal being extracted (for cost tracking)
            step_name: Label for cost tracking (e.g. "rp_entity_list", "rp_scalar_batch_1")
        """
        from app.services.cost_tracker import extract_usage
        try:
            start = time.time()
            response = self.client.messages.create(
                model=model,
                max_tokens=16384,
                system=self._V4_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=600.0  # 10 minute timeout
            )
            duration = time.time() - start
            self._last_usage = extract_usage(
                response, model, step_name, deal_id=deal_id, duration=duration
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error ({step_name}): {e}")
            self._last_usage = None
            raise

    async def _run_jcrew_tiers_2_3(
        self,
        deal_id: str,
        provision_id: str,
        document_text: str,
        rp_universe: 'CovenantUniverse',
        jc2_questions: List[Dict],
        jc3_questions: List[Dict],
    ) -> Optional[Dict[str, Any]]:
        """Run J.Crew Tiers 2 & 3 as separate Claude calls.

        Tier 2 needs definitions section context (not RP universe).
        Tier 3 needs combined context + prior Tier 1/2 answers.
        JC Tier 1 is absorbed into the unified extraction call.

        Returns summary dict or None if no JC2/JC3 questions.
        """
        if not jc2_questions and not jc3_questions:
            logger.info("No JC2/JC3 questions — skipping tiers 2-3")
            return None

        start_time = time.time()
        all_category_answers: List[CategoryAnswers] = []
        prior_answers_summary: List[str] = []
        definitions_text = ""

        _XREF_T2 = (
            "CRITICAL DEFINITIONS RULE: Terms can be defined three ways:\n"
            "(a) INLINE — full text in this document ('X means...')\n"
            "(b) CROSS-REFERENCE — defined by reference to another document "
            "('X shall have the meaning assigned in the Security Agreement')\n"
            "(c) NOT DEFINED — term does not appear at all\n"
            "A cross-reference IS a definition. When extracting definitions, "
            "return the cross-reference language verbatim. When asked if a "
            "term is defined, answer true for both inline and cross-reference. "
            "When asked to analyze what a definition includes or excludes, and "
            "the definition is a cross-reference, state that the analysis "
            "requires the referenced document and answer SILENT for all "
            "inclusion/exclusion checks (not EXCLUDED — we don't know what's "
            "excluded without reading the other document)."
        )
        _XREF_T3 = (
            "DEFINITIONS RULE: Cross-reference definitions (defined by "
            "reference to another document) are definitions but cannot be "
            "fully analyzed from this document alone. When a Tier 2 answer "
            "shows a cross-reference definition, note this as a limitation — "
            "the definition quality cannot be assessed without the referenced "
            "document. Do not treat cross-reference definitions as gaps; "
            "treat them as unknowns."
        )

        # Tier 2: Definition quality against definitions text
        if jc2_questions:
            logger.info(f"J.Crew Tier 2: {len(jc2_questions)} questions against definitions...")
            definitions_text = self.extract_definitions_section(document_text)
            if not definitions_text:
                definitions_text = rp_universe.definitions or rp_universe.raw_text

            t2_answers = self._answer_category_questions(
                definitions_text,
                jc2_questions,
                "J.Crew Tier 2 — Definition Quality",
                system_instruction=_XREF_T2,
            )
            self._last_jc2_usage = getattr(self, '_last_qa_usage', None)
            all_category_answers.append(CategoryAnswers(
                category_id="JC2",
                category_name="J.Crew Tier 2 — Definition Quality",
                answers=t2_answers,
            ))
            for a in t2_answers:
                if a.confidence in ("high", "medium"):
                    prior_answers_summary.append(
                        f"[{a.question_id}] = {a.value} ({a.confidence})"
                    )
            logger.info(f"Tier 2 complete: {len(t2_answers)} answers")

        # Tier 3: Cross-reference against definitions + prior answers only
        # (NOT full RP universe — T3 analyzes interactions between extracted
        # provisions, not raw covenant language. Full text would exceed 200K tokens.)
        if jc3_questions:
            logger.info(f"J.Crew Tier 3: {len(jc3_questions)} questions (cross-reference)...")
            combined_parts = []
            if definitions_text:
                combined_parts.append("## DEFINITIONS (from document)\n\n")
                combined_parts.append(definitions_text)
            if prior_answers_summary:
                combined_parts.append("\n\n## PRIOR TIER 1 & 2 FINDINGS\n\n")
                combined_parts.append(
                    "These findings from earlier analysis tiers are "
                    "provided for cross-reference:\n"
                )
                combined_parts.append("\n".join(prior_answers_summary))
            if not combined_parts:
                combined_parts.append("No prior findings available.")

            t3_answers = self._answer_category_questions(
                "\n".join(combined_parts),
                jc3_questions,
                "J.Crew Tier 3 — Cross-Reference Interactions",
                system_instruction=_XREF_T3,
            )
            self._last_jc3_usage = getattr(self, '_last_qa_usage', None)
            all_category_answers.append(CategoryAnswers(
                category_id="JC3",
                category_name="J.Crew Tier 3 — Cross-Reference Interactions",
                answers=t3_answers,
            ))
            logger.info(f"Tier 3 complete: {len(t3_answers)} answers")

        # Store JC2/JC3 answers
        if all_category_answers:
            self.store_extraction_result(deal_id, all_category_answers)

        elapsed = time.time() - start_time
        total = sum(len(ca.answers) for ca in all_category_answers)
        logger.info(f"J.Crew Tiers 2-3 complete in {elapsed:.1f}s: {total} answers")

        return {
            "total_answers": total,
            "elapsed_seconds": round(elapsed, 1),
        }

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


@dataclass
class ExtractionResult:
    """Result of V4 graph-native extraction."""
    deal_id: str
    extraction: Any  # ExtractionResponse
    storage_result: Dict[str, Any]
    extraction_time_seconds: float
    rp_universe_chars: int
    model_used: str
    total_cost_usd: Optional[float] = None
    cost_breakdown: Optional[Dict] = None


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
