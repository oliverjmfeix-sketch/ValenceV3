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
import re
import time
from dataclasses import dataclass, field
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
class RPUniverse:
    """Complete RP-relevant universe extracted from a document."""
    definitions: str = ""           # All relevant definitions
    dividend_covenant: str = ""     # Complete dividend/RP covenant with all baskets
    investment_covenant: str = ""   # Investment restrictions
    asset_sale_covenant: str = ""   # Asset sale restrictions
    rdp_covenant: str = ""          # Restricted Debt Payment covenant
    unsub_mechanics: str = ""       # Unrestricted subsidiary designation
    pro_forma_mechanics: str = ""   # Pro forma calculation provisions
    raw_text: str = ""              # Combined focused context for QA


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
    rp_universe: Optional['RPUniverse'] = None  # Retained for J.Crew pipeline
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

    def extract_rp_universe(self, document_text: str) -> RPUniverse:
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

    def _build_rp_universe_from_segments(
        self, document_text: str, segment_map: dict
    ) -> RPUniverse:
        """Build RPUniverse by slicing document at segment page boundaries."""
        from app.services.segment_introspector import get_rp_segment_mapping

        rp_mapping = get_rp_segment_mapping()
        universe = RPUniverse()

        segments_by_id = {
            s["segment_type_id"]: s
            for s in segment_map.get("segments", [])
            if s.get("found", True)
        }

        for seg_id, rp_field in rp_mapping.items():
            seg = segments_by_id.get(seg_id)
            if seg:
                sliced = self._slice_by_pages(
                    document_text, seg["start_page"], seg["end_page"]
                )
                if sliced:
                    setattr(universe, rp_field, sliced)

        universe.raw_text = self._build_combined_context(universe)
        return universe

    def _build_mfn_universe_from_segments(
        self, document_text: str, segment_map: dict
    ) -> Optional[str]:
        """
        Build MFN universe by slicing document at segment page boundaries.
        Returns plain text string (same interface as extract_mfn_universe).

        Mirrors _build_rp_universe_from_segments but:
        - Uses get_mfn_segment_mapping() instead of get_rp_segment_mapping()
        - Returns concatenated string instead of RPUniverse object
        """
        from app.services.segment_introspector import get_mfn_segment_mapping

        mfn_mapping = get_mfn_segment_mapping()

        segments_by_id = {
            s["segment_type_id"]: s
            for s in segment_map.get("segments", [])
            if s.get("found", True)
        }

        parts = []
        for seg_id, mfn_field in mfn_mapping.items():
            seg = segments_by_id.get(seg_id)
            if seg:
                sliced = self._slice_by_pages(
                    document_text, seg["start_page"], seg["end_page"]
                )
                if sliced:
                    parts.append(f"=== {mfn_field.upper()} ===\n{sliced}")

        if not parts:
            return None

        result = "\n\n".join(parts)
        logger.info(f"MFN universe from segments: {len(result)} chars")
        return result

    def _call_claude_streaming(self, prompt: str, max_tokens: int = 16000,
                               step: str = "legacy_extraction",
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
            self._last_streaming_usage = extract_usage(
                final_message, self.model, step, deal_id, duration
            )
            return result
        except Exception as e:
            logger.error(f"Claude streaming error: {e}")
            self._last_streaming_usage = None
            return ""

    def _build_combined_context(self, universe: RPUniverse) -> str:
        """Build combined context string for QA."""
        parts = []

        if universe.definitions:
            parts.append("## DEFINITIONS\n")
            parts.append(universe.definitions)
            parts.append("\n")

        if universe.dividend_covenant:
            parts.append("## DIVIDEND/RESTRICTED PAYMENT COVENANT\n")
            parts.append(universe.dividend_covenant)
            parts.append("\n")

        if universe.investment_covenant:
            parts.append("## INVESTMENT COVENANT\n")
            parts.append(universe.investment_covenant)
            parts.append("\n")

        if universe.asset_sale_covenant:
            parts.append("## ASSET SALE COVENANT\n")
            parts.append(universe.asset_sale_covenant)
            parts.append("\n")

        if universe.rdp_covenant:
            parts.append("## RESTRICTED DEBT PAYMENT COVENANT\n")
            parts.append(universe.rdp_covenant)
            parts.append("\n")

        if universe.unsub_mechanics:
            parts.append("## UNRESTRICTED SUBSIDIARY MECHANICS\n")
            parts.append(universe.unsub_mechanics)
            parts.append("\n")

        if universe.pro_forma_mechanics:
            parts.append("## PRO FORMA / CALCULATION MECHANICS\n")
            parts.append(universe.pro_forma_mechanics)
            parts.append("\n")

        return "\n".join(parts)

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

    def _retired_answer_questions_against_universe(
        self,
        rp_universe: RPUniverse,
        questions_by_category: Dict[str, List[Dict]]
    ) -> List[CategoryAnswers]:
        """Answer ALL questions against the focused RP universe."""
        category_answers = []

        for cat_id, questions in sorted(questions_by_category.items()):
            cat_name = questions[0]["category_name"] if questions else cat_id
            logger.info(f"Answering {len(questions)} questions for {cat_name}")

            answers = self._answer_category_questions(
                rp_universe.raw_text,
                questions,
                cat_name
            )

            category_answers.append(CategoryAnswers(
                category_id=cat_id,
                category_name=cat_name,
                answers=answers
            ))

        return category_answers

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
- confidence:
  - "high" = explicit answer found in text
  - "medium" = answer inferred from context
  - "low" = uncertain, partial information
  - "not_found" = no relevant information found
- reasoning: Brief explanation (1 sentence)

## OUTPUT

Return JSON array:
```json
[
  {{"question_id": "rp_a1", "value": true, "source_text": "...", "source_pages": [89], "confidence": "high", "reasoning": "..."}}
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
                    confidence=item.get("confidence") or "not_found",
                    reasoning=item.get("reasoning")
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
                                    confidence=answer.confidence,
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

    async def _retired_run_jcrew_deep_analysis(
        self,
        deal_id: str,
        rp_universe: RPUniverse,
        document_text: str,
    ) -> Dict[str, Any]:
        """
        Run J.Crew 3-tier deep analysis on a deal.

        Pass 1 (Tier 1 — Structural): JC1 questions against RP universe text
        Pass 2 (Tier 2 — Definition Quality): JC2 questions against definitions text
        Pass 3 (Tier 3 — Cross-Reference): JC3 questions against both + prior answers

        Reuses existing _answer_category_questions and store_extraction_result.
        Loads its own copy of JC questions from TypeDB (SSoT).

        Args:
            deal_id: The deal being analyzed
            rp_universe: Already-extracted RP universe from the main pipeline
            document_text: Full parsed document text (for definitions extraction)

        Returns:
            Summary dict with counts and timing
        """
        start_time = time.time()

        # Load all RP questions and filter to J.Crew tiers
        all_questions = self.load_questions_by_category("RP")
        jc1_questions = all_questions.get("JC1", [])
        jc2_questions = all_questions.get("JC2", [])
        jc3_questions = all_questions.get("JC3", [])

        jc_total = len(jc1_questions) + len(jc2_questions) + len(jc3_questions)
        if jc_total == 0:
            logger.info("No J.Crew questions loaded — skipping deep analysis")
            return {"skipped": True, "reason": "no_jcrew_questions"}

        logger.info(
            f"J.Crew deep analysis: {len(jc1_questions)} T1, "
            f"{len(jc2_questions)} T2, {len(jc3_questions)} T3 questions"
        )

        all_category_answers: List[CategoryAnswers] = []
        prior_answers_summary: List[str] = []
        definitions_text = ""

        _XREF_T1 = (
            "DEFINITIONS RULE: A term defined by cross-reference (e.g., "
            "'shall have the meaning assigned in the Security Agreement') "
            "IS a defined term. Return cross-reference text verbatim when "
            "asked to extract definitions. Answer true when asked if a term "
            "is defined, even if defined by cross-reference."
        )
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

        # ── Pass 1: Tier 1 (Structural) against RP universe ──────────────
        if jc1_questions:
            logger.info("J.Crew Pass 1: Tier 1 structural analysis against RP universe...")
            t1_answers = self._answer_category_questions(
                rp_universe.raw_text,
                jc1_questions,
                "J.Crew Tier 1 — Structural Vulnerability",
                system_instruction=_XREF_T1,
            )
            all_category_answers.append(CategoryAnswers(
                category_id="JC1",
                category_name="J.Crew Tier 1 — Structural Vulnerability",
                answers=t1_answers,
            ))
            # Summarize T1 answers for T3 cross-reference
            for a in t1_answers:
                if a.confidence in ("high", "medium"):
                    prior_answers_summary.append(
                        f"[{a.question_id}] = {a.value} ({a.confidence})"
                    )
            logger.info(f"Pass 1 complete: {len(t1_answers)} answers")

        # ── Pass 2: Tier 2 (Definition Quality) against definitions text ──
        if jc2_questions:
            logger.info("J.Crew Pass 2: Tier 2 definition quality analysis...")
            definitions_text = self.extract_definitions_section(document_text)

            # Fall back to RP universe definitions if regex extraction found nothing
            if not definitions_text:
                logger.warning(
                    "No definitions extracted — falling back to RP universe definitions"
                )
                definitions_text = rp_universe.definitions or rp_universe.raw_text

            t2_answers = self._answer_category_questions(
                definitions_text,
                jc2_questions,
                "J.Crew Tier 2 — Definition Quality",
                system_instruction=_XREF_T2,
            )
            all_category_answers.append(CategoryAnswers(
                category_id="JC2",
                category_name="J.Crew Tier 2 — Definition Quality",
                answers=t2_answers,
            ))
            # Summarize T2 answers for T3 cross-reference
            for a in t2_answers:
                if a.confidence in ("high", "medium"):
                    prior_answers_summary.append(
                        f"[{a.question_id}] = {a.value} ({a.confidence})"
                    )
            logger.info(f"Pass 2 complete: {len(t2_answers)} answers")

        # ── Pass 3: Tier 3 (Cross-Reference) against both + prior answers ─
        if jc3_questions:
            logger.info("J.Crew Pass 3: Tier 3 cross-reference analysis...")

            # Build combined context: RP universe + definitions + prior answers
            combined_parts = [rp_universe.raw_text]

            if definitions_text:
                combined_parts.append("\n\n## DEFINITIONS (from document)\n\n")
                combined_parts.append(definitions_text)

            if prior_answers_summary:
                combined_parts.append("\n\n## PRIOR TIER 1 & 2 FINDINGS\n\n")
                combined_parts.append(
                    "These findings from earlier analysis tiers are "
                    "provided for cross-reference:\n"
                )
                combined_parts.append("\n".join(prior_answers_summary))

            combined_context = "\n".join(combined_parts)

            t3_answers = self._answer_category_questions(
                combined_context,
                jc3_questions,
                "J.Crew Tier 3 — Cross-Reference Interactions",
                system_instruction=_XREF_T3,
            )
            all_category_answers.append(CategoryAnswers(
                category_id="JC3",
                category_name="J.Crew Tier 3 — Cross-Reference Interactions",
                answers=t3_answers,
            ))
            logger.info(f"Pass 3 complete: {len(t3_answers)} answers")

        # ── Store all J.Crew answers ──────────────────────────────────────
        logger.info("Storing J.Crew deep analysis results...")
        self.store_extraction_result(deal_id, all_category_answers)

        elapsed = time.time() - start_time
        total_answers = sum(len(ca.answers) for ca in all_category_answers)
        high_conf = sum(
            1 for ca in all_category_answers
            for a in ca.answers if a.confidence == "high"
        )

        logger.info(
            f"J.Crew deep analysis complete in {elapsed:.1f}s: "
            f"{total_answers} answers ({high_conf} high confidence)"
        )

        return {
            "total_answers": total_answers,
            "high_confidence": high_conf,
            "tier_counts": {
                "JC1": len(jc1_questions),
                "JC2": len(jc2_questions),
                "JC3": len(jc3_questions),
            },
            "elapsed_seconds": round(elapsed, 1),
        }

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
  "source_section": "as found in this document",
  "confidence": "high"
}}

For multiselect questions, value is an array of concept_ids:
{{
  "question_id": "mfn_08",
  "value": ["incremental_term_loans", "ratio_debt"],
  "source_text": "...",
  "source_page": 45,
  "source_section": "e.g., Section X.XX(a)",
  "confidence": "high"
}}

IMPORTANT: Respond with ONLY the JSON object. Do not include any analysis, explanation, or preamble before or after the JSON."""

        from app.services.cost_tracker import extract_usage
        try:
            context_chars = len(context_text)
            logger.info(
                f"MFN batch {cat_id}: {len(questions)} questions, "
                f"context={context_chars} chars"
            )

            mfn_model = "claude-sonnet-4-5-20250929"
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

        Stores answers via _store_mfn_answers (existing SSoT storage pattern).
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
            logger.info(f"MFN Batch B complete: {len(batch_b_answers)} answers")

        all_answers = batch_a_answers + batch_b_answers
        logger.info(f"MFN consolidated extraction complete: {len(all_answers)}/{total_questions} answers")

        # Store answers
        if all_answers:
            self._store_mfn_answers(deal_id, all_answers)

        return {
            "answers": all_answers,
            "errors": [],
            "total_questions": total_questions,
            "answered": len(all_answers),
        }

    async def _retired_run_mfn_extraction(
        self,
        deal_id: str,
        mfn_universe_text: str,
        document_text: str
    ) -> dict:
        """
        RETIRED: Old 6-batch MFN extraction. Replaced by run_mfn_extraction_consolidated().

        Extract MFN provision data by answering 42 ontology questions.

        Splits into domain-driven batches by category (MFN1-MFN6).
        Each batch gets focused context from the relevant universe sections.
        MFN6 (Loopholes) receives entity data from earlier batches.
        """
        # Load MFN questions from TypeDB (SSoT)
        questions_by_cat = self.load_questions_by_category("MFN")
        if not questions_by_cat:
            logger.error("No MFN questions found in TypeDB")
            return {"answers": [], "errors": ["No MFN questions in TypeDB"]}

        total_questions = sum(len(qs) for qs in questions_by_cat.values())
        logger.info(
            f"MFN extraction: {total_questions} questions in "
            f"{len(questions_by_cat)} batches"
        )

        # Parse universe into sections for focused context per batch
        universe_sections = self._parse_mfn_universe_sections(mfn_universe_text)

        all_answers = []
        prior_analysis_parts = []  # Accumulated for MFN6

        for cat_id in sorted(questions_by_cat.keys()):
            cat_questions = questions_by_cat[cat_id]
            cat_questions.sort(key=lambda q: q.get("display_order", 0))

            # MFN6 gets prior analysis from batches 2-4
            entity_context = None
            if cat_id == "MFN6" and prior_analysis_parts:
                entity_context = "\n\n".join(prior_analysis_parts)

            batch_answers = self._extract_mfn_batch(
                cat_id, cat_questions, mfn_universe_text,
                universe_sections, entity_context=entity_context
            )
            all_answers.extend(batch_answers)

            # Accumulate entity-relevant answers for MFN6
            if cat_id in ("MFN2", "MFN3", "MFN4"):
                summary = self._summarize_batch_answers(cat_id, batch_answers)
                if summary:
                    prior_analysis_parts.append(summary)

        logger.info(f"MFN extraction complete: {len(all_answers)}/{total_questions} answers")

        return {
            "answers": all_answers,
            "errors": [],
            "total_questions": total_questions,
            "answered": len(all_answers)
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
            # 1. Create mfn_provision + link to deal
            self._ensure_mfn_provision_exists(deal_id, provision_id)

            # 2. Store answers using GraphStorage (SSoT pattern)
            storage = GraphStorage(deal_id)
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

                # Multiselect → concept_applicability
                if isinstance(value, list):
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
                            confidence=confidence,
                        )
                        stored_scalar += 1
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
    # MFN PATTERN FLAG COMPUTATION
    # =========================================================================

    def _compute_mfn_pattern_flags(self, deal_id: str, provision_id: str):
        """Compute pattern flags by calling TypeDB functions."""
        from typedb.driver import TransactionType

        flag_functions = {
            "yield_exclusion_pattern_detected": "detect_mfn_yield_exclusion_pattern",
            "reclassification_loophole_detected": "detect_mfn_reclassification_loophole",
            "mfn_amendment_vulnerable": "detect_mfn_amendment_vulnerable",
            "mfn_exclusion_stacking_detected": "detect_mfn_exclusion_stacking",
            "sunset_timing_loophole_detected": "detect_mfn_sunset_timing_loophole",
            "bridge_to_term_loophole_detected": "detect_mfn_bridge_to_term_loophole",
            "currency_arbitrage_detected": "detect_mfn_currency_arbitrage",
            "freebie_oversized_detected": "detect_mfn_freebie_oversized",
            "mfn_margin_only_weakness_detected": "detect_mfn_margin_only_weakness",
            "mfn_comprehensive_protection_detected": "detect_mfn_comprehensive_protection",
        }

        for flag_attr, func_name in flag_functions.items():
            try:
                # Call function in READ transaction
                tx = typedb_client.driver.transaction(
                    settings.typedb_database, TransactionType.READ
                )
                try:
                    query = f'''
                        match
                            let $detected = {func_name}("{provision_id}");
                        select $detected;
                    '''
                    result = list(tx.query(query).resolve().as_concept_rows())
                    # TypeDB 3.x `return check`: rows present = true, empty = false
                    detected_bool = len(result) > 0
                finally:
                    tx.close()

                # Write flag in WRITE transaction
                tx = typedb_client.driver.transaction(
                    settings.typedb_database, TransactionType.WRITE
                )
                try:
                    write_query = f'''
                        match $p isa mfn_provision,
                            has provision_id "{provision_id}";
                        insert $p has {flag_attr} {str(detected_bool).lower()};
                    '''
                    tx.query(write_query).resolve()
                    tx.commit()
                    logger.info(f"Pattern flag {flag_attr} = {detected_bool}")
                except Exception as e:
                    if tx.is_open():
                        tx.close()
                    logger.warning(f"Failed to write {flag_attr}: {e}")

            except Exception as e:
                logger.warning(f"Pattern detection {func_name} failed: {e}")

    # =========================================================================
    # CROSS-REFERENCES
    # =========================================================================

    def _create_cross_references(self, deal_id: str):
        """Create provision_cross_reference between MFN and RP provisions."""
        from typedb.driver import TransactionType

        mfn_id = f"{deal_id}_mfn"

        # Check both provisions exist
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.READ
        )
        try:
            query = f"""
                match
                    $d isa deal, has deal_id "{deal_id}";
                    $mfn isa mfn_provision, has provision_id "{mfn_id}";
                    $rp isa rp_provision, has provision_id $rpid;
                    (deal: $d, provision: $mfn) isa deal_has_provision;
                    (deal: $d, provision: $rp) isa deal_has_provision;
                select $rpid;
            """
            result = list(tx.query(query).resolve().as_concept_rows())
            has_both = len(result) > 0
        finally:
            tx.close()

        if not has_both:
            logger.debug(f"No cross-reference needed for {deal_id} (missing MFN or RP)")
            return

        # Create cross-reference
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
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
                        has cross_reference_explanation "MFN exclusion capacity shares debt incurrence covenant capacity with RP ratio baskets. Incremental equivalent debt incurred under ratio test avoids MFN but may consume RP debt incurrence capacity.";
            """
            tx.query(query).resolve()
            tx.commit()
            logger.info(f"Created MFN↔RP cross-reference for deal {deal_id}")
        except Exception as e:
            if tx.is_open():
                tx.close()
            raise

    # =========================================================================
    # MFN ENTITY EXTRACTION (Channel 3)
    # =========================================================================

    async def run_mfn_entity_extraction(
        self,
        deal_id: str,
        mfn_universe_text: str,
    ) -> dict:
        """
        Extract MFN Channel 3 entities using extraction_metadata from TypeDB.

        Loads metadata for mfn_exclusion, mfn_yield_definition,
        mfn_sunset_provision, and mfn_freebie_basket. For each metadata
        entry, calls Claude with the extraction prompt and MFN universe text,
        then stores the resulting entities via GraphStorage.

        Returns:
            dict with entity counts and errors
        """
        from app.services.graph_storage import GraphStorage

        provision_id = f"{deal_id}_mfn"
        storage = GraphStorage(deal_id)

        # 1. Load MFN extraction metadata from TypeDB (SSoT)
        metadata_list = GraphStorage.load_mfn_extraction_metadata()
        if not metadata_list:
            logger.warning("No MFN extraction metadata found in TypeDB")
            return {"entities_stored": 0, "errors": ["No MFN metadata in TypeDB"]}

        logger.info(
            f"MFN entity extraction: {len(metadata_list)} metadata entries loaded"
        )

        total_entities = 0
        errors = []

        # 2. For each metadata entry, extract entities
        for meta in metadata_list:
            entity_type = meta["target_entity_type"]
            meta_id = meta["metadata_id"]
            prompt_text = meta["extraction_prompt"]
            section_hint = meta.get("extraction_section_hint", "")

            logger.info(f"Extracting {entity_type} ({meta_id})...")

            user_prompt = f"""Extract structured entities from the MFN universe text below.

## ENTITY TYPE: {entity_type}

## EXTRACTION INSTRUCTIONS
{prompt_text}

## SECTION HINT
Focus on: {section_hint}

## RESPONSE FORMAT

Return a JSON object with an "entities" array. Each entity is a JSON object
with the fields described in the instructions above. Example:

{{
  "entities": [
    {{
      "exclusion_type": "acquisition",
      "exclusion_has_cap": false,
      ...
      "section_reference": "as found in this document",
      "source_text": "verbatim quote (30-500 chars)",
      "source_page": 45,
      "confidence": "high"
    }}
  ]
}}

If no entities of this type exist, return: {{"entities": []}}

## MFN UNIVERSE TEXT

{mfn_universe_text}"""

            from app.services.cost_tracker import extract_usage
            try:
                mfn_entity_model = "claude-sonnet-4-5-20250929"
                start = time.time()
                response = self.client.messages.create(
                    model=mfn_entity_model,
                    max_tokens=8000,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                duration = time.time() - start
                extract_usage(
                    response, mfn_entity_model, "mfn_entity_extraction",
                    deal_id=deal_id, duration=duration
                )

                text = response.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]

                result = json.loads(text)
                entities = result.get("entities", [])

                if not entities:
                    logger.info(f"  {entity_type}: no entities found")
                    continue

                # 3. Store each entity via the appropriate GraphStorage method
                store_method_name = storage.MFN_ENTITY_STORE_MAP.get(entity_type)
                if not store_method_name:
                    errors.append(f"No store method for {entity_type}")
                    continue

                store_method = getattr(storage, store_method_name)
                stored = 0
                for entity_data in entities:
                    try:
                        store_method(provision_id, entity_data)
                        stored += 1
                    except Exception as e:
                        errors.append(f"{entity_type} store error: {e}")
                        logger.warning(f"  {entity_type} store error: {e}")

                total_entities += stored
                logger.info(f"  {entity_type}: stored {stored}/{len(entities)}")

            except json.JSONDecodeError as e:
                errors.append(f"{meta_id} JSON parse error: {e}")
                logger.warning(f"  {meta_id}: JSON parse error")
            except Exception as e:
                errors.append(f"{meta_id} extraction error: {e}")
                logger.warning(f"  {meta_id}: extraction error: {e}")

        logger.info(
            f"MFN entity extraction complete: {total_entities} entities stored"
        )
        return {
            "entities_stored": total_entities,
            "metadata_entries": len(metadata_list),
            "errors": errors,
        }

    # =========================================================================
    # MAIN EXTRACTION FLOW
    # =========================================================================

    async def _retired_extract_rp_provision(
        self,
        pdf_path: str,
        deal_id: str,
        questions_by_category: Optional[Dict[str, List[Dict]]] = None,
        store_results: bool = True
    ) -> ExtractionResult:
        """
        Full RP extraction pipeline using format-agnostic universe extraction.

        1. Parse PDF
        2. Extract RP-Relevant Universe (definitions + covenants + mechanics)
        3. Load questions from TypeDB
        4. Answer ALL questions against the focused RP universe
        5. Store results to TypeDB
        """
        start_time = time.time()

        # Step 1: Parse PDF
        document_text = self.parse_document(pdf_path)

        # Step 2: Extract RP Universe
        logger.info("Step 2: Extracting RP-Relevant Universe...")
        rp_universe = self.extract_rp_universe(document_text)
        segment_map = getattr(self, '_last_segment_map', None)
        universe_chars = len(rp_universe.raw_text)
        logger.info(f"RP Universe extracted: {universe_chars} chars")

        # Cache RP universe text to disk for eval pipeline reuse
        try:
            import os
            universe_path = os.path.join("/app/uploads", f"{deal_id}_rp_universe.txt")
            os.makedirs("/app/uploads", exist_ok=True)
            with open(universe_path, "w", encoding="utf-8") as f:
                f.write(rp_universe.raw_text)
            logger.info(f"Cached RP universe text: {universe_path} ({universe_chars} chars)")
        except Exception as e:
            logger.warning(f"Could not cache RP universe text: {e}")

        if universe_chars < 1000:
            logger.warning("RP Universe extraction yielded minimal content - extraction may fail")

        # Step 3: Load questions from TypeDB
        if questions_by_category is None:
            logger.info("Step 3: Loading questions from TypeDB...")
            questions_by_category = self.load_questions_by_category("RP")

        total_questions = sum(len(qs) for qs in questions_by_category.values())
        logger.info(f"Loaded {total_questions} questions in {len(questions_by_category)} categories")

        # Step 4: Answer ALL questions against RP Universe
        logger.info("Step 4: Answering questions against RP Universe...")
        category_answers = self._retired_answer_questions_against_universe(
            rp_universe,
            questions_by_category
        )

        # Count high confidence answers
        high_conf_count = sum(
            1 for cat in category_answers
            for ans in cat.answers
            if ans.confidence == "high"
        )

        # Step 5: Store results to TypeDB
        if store_results:
            logger.info("Step 5: Storing results to TypeDB...")
            self.store_extraction_result(deal_id, category_answers)

        extraction_time = time.time() - start_time
        logger.info(
            f"Extraction complete in {extraction_time:.1f}s: "
            f"{high_conf_count}/{total_questions} HIGH confidence"
        )

        return ExtractionResult(
            deal_id=deal_id,
            covenant_type="RP",
            category_answers=category_answers,
            extraction_time_seconds=extraction_time,
            chunks_processed=1,  # Universe extraction counts as 1
            total_questions=total_questions,
            high_confidence_answers=high_conf_count,
            rp_universe_chars=universe_chars,
            rp_universe=rp_universe,
            document_text=document_text,
            segment_map=segment_map,
        )

    # =========================================================================
    # V4 GRAPH-NATIVE EXTRACTION
    # =========================================================================

    async def extract_rp_v4(
        self,
        pdf_path: str,
        deal_id: str,
        model: Optional[str] = None
    ) -> 'ExtractionResultV4':
        """
        V4 Graph-Native RP extraction pipeline.

        1. Parse PDF
        2. Extract RP Universe (same as V3)
        3. Load extraction metadata from TypeDB (SSoT)
        4. Build Claude prompt with metadata + JSON schema
        5. Call Claude, parse into RPExtractionV4 Pydantic model
        6. Store as graph entities and relations

        Returns:
            ExtractionResultV4 with typed extraction and storage results
        """
        from app.services.graph_storage import GraphStorage
        from app.schemas.extraction_output_v4 import RPExtractionV4

        start_time = time.time()
        model = model or settings.claude_model

        # Step 1: Parse PDF
        logger.info(f"V4 Extraction starting for deal {deal_id}")
        document_text = self.parse_document(pdf_path)

        # Step 2: Extract RP Universe (reuse existing logic)
        logger.info("Step 2: Extracting RP Universe...")
        rp_universe = self.extract_rp_universe(document_text)
        universe_chars = len(rp_universe.raw_text)
        logger.info(f"RP Universe extracted: {universe_chars} chars")

        if universe_chars < 1000:
            logger.warning("RP Universe extraction yielded minimal content")

        # Step 3: Load extraction metadata from TypeDB (SSoT)
        logger.info("Step 3: Loading extraction metadata from TypeDB...")
        metadata = GraphStorage.load_extraction_metadata()
        logger.info(f"Loaded {len(metadata)} extraction instructions")

        # Step 4: Build Claude prompt
        logger.info("Step 4: Building V4 Claude prompt...")
        prompt = GraphStorage.build_claude_prompt(metadata, rp_universe.raw_text)
        logger.info(f"Prompt built: {len(prompt)} chars")

        # Step 5: Call Claude
        logger.info(f"Step 5: Calling Claude ({model})...")
        response_text = self._call_claude_v4(prompt, model)
        logger.info(f"Response received: {len(response_text)} chars")

        # Collect cost tracking
        from app.services.cost_tracker import ExtractionCostSummary
        cost_summary = ExtractionCostSummary(deal_id=deal_id)
        if getattr(self, '_last_v4_usage', None):
            self._last_v4_usage.deal_id = deal_id
            cost_summary.add(self._last_v4_usage)

        # Step 6: Parse response into Pydantic model
        logger.info("Step 6: Parsing response...")
        extraction = GraphStorage.parse_claude_response(response_text)

        # Create storage instance and summarize
        storage = GraphStorage(deal_id)
        summary = storage.summarize_extraction(extraction)
        logger.info(f"Parsed extraction: {summary}")

        # Step 7: Store in TypeDB
        logger.info("Step 7: Storing V4 extraction to TypeDB...")
        storage_result = storage.store_rp_extraction_v4(extraction)
        logger.info(f"Storage complete: {storage_result}")

        extraction_time = time.time() - start_time
        logger.info(f"V4 Extraction complete in {extraction_time:.1f}s")

        cost_summary.log_summary()

        return ExtractionResultV4(
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

    # =========================================================================
    # V4 UNIFIED EXTRACTION (single Claude call for entities + answers)
    # =========================================================================

    _V4_SYSTEM_PROMPT = """You are a legal analyst extracting covenant data from credit agreements.

RULES:
1. Return ONLY valid JSON - no markdown code blocks, no explanation before/after
2. Include provenance (section_reference, source_page) for every major finding
3. Quote verbatim_text exactly as it appears (max 500 chars)
4. If a field is not found, omit it entirely (don't include null)
5. For percentages, use decimals (50% = 0.5, 140% = 1.4)
6. For dollar amounts, use raw numbers (130000000 not "130M")
7. Be precise about "no worse" test - this is CRITICAL for risk analysis
8. For ratio thresholds, use the decimal number (5.75x = 5.75)
9. For reallocation_cap: use null (not "unlimited") if there is no cap
10. DEFINITIONS: Terms can be defined three ways in credit agreements:
    (a) INLINE — full definition text appears in this document (e.g., "Material Intellectual Property" means...)
    (b) CROSS-REFERENCE — defined by reference to another document (e.g., "Intellectual Property" shall have the meaning assigned to such term in the Security Agreement)
    (c) NOT DEFINED — term does not appear in the defined terms section at all
    A cross-reference IS a definition. When asked if a term is defined, answer true for both inline and cross-reference definitions. When asked to extract a definition, return the cross-reference text verbatim — do not say "NOT DEFINED" for cross-referenced terms. When a term is defined by cross-reference, note which document contains the full definition.
11. For flat answers: value types must match answer_type exactly. Booleans are true/false (not strings). Numbers are numeric. Multiselect values are arrays of concept IDs.
12. source_text in answers MUST be exact verbatim quotes from the document text, not paraphrases or references."""

    async def extract_rp_v4_unified(
        self,
        deal_id: str,
        document_text: str,
        rp_universe: 'RPUniverse',
        segment_map: Optional[dict] = None,
        model: Optional[str] = None,
    ) -> 'ExtractionResultV4':
        """
        V4 Unified extraction pipeline — single Claude call for entities + flat answers.

        Replaces the old per-category extraction loop (10-24 Claude calls)
        with a single call that produces typed entities AND provision_has_answer records.

        After the main call, runs JC Tiers 2 & 3 separately (different context needed).

        Args:
            deal_id: The deal being extracted
            document_text: Full parsed document text
            rp_universe: Already-extracted RP universe
            segment_map: Segment map from segmentation (for reuse)
            model: Claude model to use (default from settings)

        Returns:
            ExtractionResultV4 with extraction, storage results, and cost data
        """
        from app.services.graph_storage import GraphStorage
        from app.schemas.extraction_output_v4 import RPExtractionV4

        start_time = time.time()
        model = model or settings.claude_model
        universe_chars = len(rp_universe.raw_text)

        # Step 1: Load extraction metadata from TypeDB (SSoT)
        logger.info(f"Unified V4 extraction starting for deal {deal_id}")
        metadata = GraphStorage.load_extraction_metadata()
        logger.info(f"Loaded {len(metadata)} extraction metadata instructions")

        # Step 2: Load all RP questions (including JC1, excluding JC2/JC3)
        all_questions = self.load_questions_by_category("RP")

        # JC1 goes into the unified call (same RP universe context).
        # JC2/JC3 need separate context, handled after.
        jc2_questions = all_questions.pop("JC2", [])
        jc3_questions = all_questions.pop("JC3", [])

        total_questions = sum(len(qs) for qs in all_questions.values())
        logger.info(
            f"Loaded {total_questions} questions for unified call "
            f"({len(all_questions)} categories, JC2={len(jc2_questions)}, JC3={len(jc3_questions)} deferred)"
        )

        # Step 3: Build unified prompt (entities + questions)
        prompt = GraphStorage.build_claude_prompt(
            metadata, rp_universe.raw_text, questions=all_questions
        )
        logger.info(f"Unified prompt built: {len(prompt)} chars")

        # Step 4: Call Claude (single call for entities + all RP/JC1 answers)
        logger.info(f"Calling Claude ({model}) for unified extraction...")
        response_text = self._call_claude_v4_unified(prompt, model, deal_id)
        logger.info(f"Response received: {len(response_text)} chars")

        # Collect cost tracking
        from app.services.cost_tracker import ExtractionCostSummary
        cost_summary = ExtractionCostSummary(deal_id=deal_id)
        if getattr(self, '_last_v4_usage', None):
            self._last_v4_usage.deal_id = deal_id
            cost_summary.add(self._last_v4_usage)

        # Step 5: Parse response into Pydantic model
        logger.info("Parsing unified response...")
        extraction = GraphStorage.parse_claude_response(response_text)
        logger.info(
            f"Parsed: {len(extraction.answers)} answers, "
            f"builder={extraction.builder_basket is not None}, "
            f"ratio={extraction.ratio_basket is not None}, "
            f"blocker={extraction.jcrew_blocker is not None}"
        )

        # Step 6: Store in TypeDB (entities + answers in one pass)
        storage = GraphStorage(deal_id)
        logger.info("Storing unified extraction to TypeDB...")
        storage_result = storage.store_rp_extraction_v4(extraction, deal_id=deal_id)
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
            for step_usage in (getattr(self, '_last_qa_usage', None),):
                if step_usage:
                    cost_summary.add(step_usage)

        extraction_time = time.time() - start_time
        logger.info(f"Unified V4 extraction complete in {extraction_time:.1f}s")
        cost_summary.log_summary()

        return ExtractionResultV4(
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

    def _call_claude_v4_unified(self, prompt: str, model: str, deal_id: Optional[str] = None) -> str:
        """Call Claude API for V4 unified extraction (entities + answers).

        Uses higher max_tokens (16384) and longer timeout (600s) than
        entity-only extraction since the response includes both entities and answers.
        """
        from app.services.cost_tracker import extract_usage
        try:
            start = time.time()
            response = self.client.messages.create(
                model=model,
                max_tokens=16384,
                system=self._V4_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=600.0  # 10 minute timeout for unified extraction
            )
            duration = time.time() - start
            self._last_v4_usage = extract_usage(
                response, model, "rp_unified_v4", deal_id=deal_id, duration=duration
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error (unified): {e}")
            self._last_v4_usage = None
            raise

    async def _run_jcrew_tiers_2_3(
        self,
        deal_id: str,
        provision_id: str,
        document_text: str,
        rp_universe: 'RPUniverse',
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

        # Tier 3: Cross-reference against combined context + prior answers
        if jc3_questions:
            logger.info(f"J.Crew Tier 3: {len(jc3_questions)} questions (cross-reference)...")
            combined_parts = [rp_universe.raw_text]
            if definitions_text:
                combined_parts.append("\n\n## DEFINITIONS (from document)\n\n")
                combined_parts.append(definitions_text)
            if prior_answers_summary:
                combined_parts.append("\n\n## PRIOR TIER 1 & 2 FINDINGS\n\n")
                combined_parts.append(
                    "These findings from earlier analysis tiers are "
                    "provided for cross-reference:\n"
                )
                combined_parts.append("\n".join(prior_answers_summary))

            t3_answers = self._answer_category_questions(
                "\n".join(combined_parts),
                jc3_questions,
                "J.Crew Tier 3 — Cross-Reference Interactions",
                system_instruction=_XREF_T3,
            )
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

    def _call_claude_v4(self, prompt: str, model: str) -> str:
        """Call Claude API for V4 structured extraction (entity-only, legacy)."""
        from app.services.cost_tracker import extract_usage
        try:
            start = time.time()
            response = self.client.messages.create(
                model=model,
                max_tokens=8192,
                system=self._V4_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                timeout=300.0  # 5 minute timeout for extraction
            )
            duration = time.time() - start
            self._last_v4_usage = extract_usage(
                response, model, "rp_extraction", deal_id=None, duration=duration
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            self._last_v4_usage = None
            raise


@dataclass
class ExtractionResultV4:
    """Result of V4 graph-native extraction."""
    deal_id: str
    extraction: Any  # RPExtractionV4
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
