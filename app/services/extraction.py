"""
Smart Chunked Extraction Pipeline for Covenant Analysis.

Flow:
1. Parse PDF → raw text with page markers
2. Create overlapping chunks (250k chars, 50k overlap)
3. Load questions from TypeDB (SSoT)
4. For each chunk, ask UNANSWERED questions:
   - HIGH confidence → mark as answered, stop searching
   - MEDIUM/LOW → keep best answer, try next chunk
   - NOT_FOUND → try next chunk
5. Early exit when all questions answered OR chunks exhausted
6. Store best answers to TypeDB

Key insight: Questions are answered DIRECTLY against chunks with early exit.
This minimizes Claude calls while ensuring complete document coverage.
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
class RPExtraction:
    """All extracted RP content from a document."""
    dividend_prohibition: Optional[ExtractedContent] = None
    permitted_baskets: List[ExtractedContent] = field(default_factory=list)
    rdp_restrictions: Optional[ExtractedContent] = None
    definitions: List[ExtractedContent] = field(default_factory=list)
    raw_json: Optional[Dict] = None


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
    chunk_index: int = 0  # Which chunk this answer came from


@dataclass
class CategoryAnswers:
    """All answers for a category."""
    category_id: str
    category_name: str
    answers: List[AnsweredQuestion]


class AnswerTracker:
    """Track best answer per question across chunks."""

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
    extracted_content: Optional[RPExtraction] = None  # Legacy, kept for compatibility


# =============================================================================
# EXTRACTION SERVICE
# =============================================================================

class ExtractionService:
    """
    Smart chunked extraction pipeline with early exit.

    Processes document in chunks, asking questions directly against each chunk.
    Tracks best answer per question, stopping early when all questions have
    HIGH confidence answers.
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
    # STEP 2: Extract RP Content (multi-chunk for complete coverage)
    # =========================================================================

    def extract_rp_content(self, document_text: str) -> RPExtraction:
        """
        Extract ALL RP-related content verbatim from the document.

        For long documents (>250k chars), splits into overlapping chunks to ensure
        complete coverage - definitions at front, covenants in middle/back.

        This is format-agnostic - just extracts the actual text with page numbers.
        Does NOT try to answer questions or interpret the content.
        """
        doc_length = len(document_text)
        chunk_size = 250000  # ~250k chars per chunk (safe for Claude's context)
        overlap = 50000      # 50k overlap to avoid cutting mid-section

        # If document fits in one chunk, extract directly
        if doc_length <= chunk_size:
            logger.info(f"Document fits in single chunk ({doc_length} chars)")
            return self._extract_rp_content_chunk(document_text, chunk_num=1, total_chunks=1)

        # Split into overlapping chunks
        chunks = []
        start = 0
        while start < doc_length:
            end = min(start + chunk_size, doc_length)
            chunks.append({
                "start": start,
                "end": end,
                "text": document_text[start:end]
            })
            # Move start forward, but with overlap
            start = end - overlap
            # Stop if we've covered everything
            if end >= doc_length:
                break

        logger.info(f"Document {doc_length} chars split into {len(chunks)} chunks")
        for i, chunk in enumerate(chunks):
            logger.info(f"  Chunk {i+1}: chars {chunk['start']}-{chunk['end']}")

        # Extract from each chunk
        extractions = []
        for i, chunk in enumerate(chunks):
            logger.info(f"Extracting chunk {i+1}/{len(chunks)}...")
            extraction = self._extract_rp_content_chunk(
                chunk["text"],
                chunk_num=i+1,
                total_chunks=len(chunks)
            )
            extractions.append(extraction)
            logger.info(f"  Chunk {i+1}: {len(extraction.permitted_baskets)} baskets, {len(extraction.definitions)} definitions")

        # Merge all extractions
        merged = self._merge_extractions(extractions)
        logger.info(f"Merged: {len(merged.permitted_baskets)} baskets, {len(merged.definitions)} definitions")

        return merged

    def _extract_rp_content_chunk(self, chunk_text: str, chunk_num: int, total_chunks: int) -> RPExtraction:
        """Extract RP content from a single document chunk."""
        prompt = self._build_content_extraction_prompt(chunk_text, chunk_num, total_chunks)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse_content_extraction(response.content[0].text)
        except Exception as e:
            logger.error(f"Content extraction error (chunk {chunk_num}): {e}")
            return RPExtraction()

    def _merge_extractions(self, extractions: List[RPExtraction]) -> RPExtraction:
        """
        Merge multiple chunk extractions into one, deduplicating by section_reference.

        Priority: Take the first non-null dividend_prohibition and rdp_restrictions.
        For baskets and definitions, deduplicate by section_reference or basket_name.
        """
        merged = RPExtraction()

        # Take first non-null dividend prohibition
        for ext in extractions:
            if ext.dividend_prohibition and not merged.dividend_prohibition:
                merged.dividend_prohibition = ext.dividend_prohibition
                break

        # Take first non-null RDP restrictions
        for ext in extractions:
            if ext.rdp_restrictions and not merged.rdp_restrictions:
                merged.rdp_restrictions = ext.rdp_restrictions
                break

        # Merge baskets - deduplicate by section_reference or section_type
        seen_baskets = set()
        for ext in extractions:
            for basket in ext.permitted_baskets:
                # Use section_reference if available, else section_type
                key = basket.section_reference or basket.section_type
                if key and key not in seen_baskets:
                    seen_baskets.add(key)
                    merged.permitted_baskets.append(basket)
                elif not key:
                    # No key - add anyway but may have duplicates
                    merged.permitted_baskets.append(basket)

        # Merge definitions - deduplicate by section_type (which includes term name)
        seen_definitions = set()
        for ext in extractions:
            for defn in ext.definitions:
                if defn.section_type not in seen_definitions:
                    seen_definitions.add(defn.section_type)
                    merged.definitions.append(defn)

        # Combine raw JSON from all extractions
        merged.raw_json = {
            "merged_from_chunks": len(extractions),
            "extractions": [ext.raw_json for ext in extractions if ext.raw_json]
        }

        return merged

    def _build_content_extraction_prompt(self, document_text: str, chunk_num: int = 1, total_chunks: int = 1) -> str:
        """Build prompt for verbatim content extraction."""
        doc_excerpt = document_text

        # Add chunk context for multi-chunk extraction
        if total_chunks > 1:
            chunk_context = f"""
NOTE: This is chunk {chunk_num} of {total_chunks} from a large document.
- Chunk 1 typically contains: definitions, early sections
- Middle chunks contain: negative covenants including RP sections
- Later chunks contain: remaining covenants, schedules

Extract ALL relevant content you find in THIS chunk. Duplicates will be merged later.
"""
        else:
            chunk_context = ""

        return f"""You are extracting Restricted Payments (RP) covenant content from a credit agreement.
{chunk_context}

FIND AND EXTRACT VERBATIM the following sections. Do NOT interpret or summarize - extract the actual text with page numbers.

## WHAT TO EXTRACT

1. **DIVIDEND/RESTRICTED PAYMENT PROHIBITION**
   - The main prohibition clause (typically Section 6.06 or 7.06)
   - Who is restricted (Holdings, Borrower, Restricted Subsidiaries)

2. **PERMITTED BASKETS/EXCEPTIONS** (extract EACH basket separately)
   - Intercompany dividends
   - Management equity repurchase basket
   - Tax distribution basket
   - Builder basket / Cumulative Amount
   - Ratio-based dividend basket
   - General / fixed dollar baskets
   - Any other permitted dividend exceptions

3. **RESTRICTED DEBT PAYMENT (RDP) RESTRICTIONS** (if separate)
   - Section 6.09 or similar
   - Permitted RDP baskets

4. **REFERENCED DEFINITIONS** (extract each verbatim)
   - "Restricted Payment" definition
   - "Cumulative Amount" or "Available Amount" definition
   - "Unrestricted Subsidiary" definition
   - "Qualified Equity Interests" / "Qualified Stock" definition
   - "Consolidated Net Income" definition
   - "Material Intellectual Property" or IP definitions
   - Any other definitions referenced in RP sections

## DOCUMENT

{doc_excerpt}

## OUTPUT FORMAT

Return JSON:
```json
{{
  "dividend_prohibition": {{
    "text": "[VERBATIM TEXT]",
    "pages": [89, 90],
    "section_reference": "Section 6.06"
  }},
  "permitted_baskets": [
    {{
      "basket_name": "Management Equity",
      "text": "[VERBATIM TEXT]",
      "pages": [91],
      "section_reference": "Section 6.06(c)"
    }}
  ],
  "rdp_restrictions": {{
    "text": "[VERBATIM TEXT]",
    "pages": [95],
    "section_reference": "Section 6.09"
  }},
  "definitions": [
    {{
      "term": "Restricted Payment",
      "text": "[VERBATIM TEXT]",
      "pages": [42]
    }}
  ]
}}
```

IMPORTANT:
- Extract VERBATIM text, do not summarize
- Include ALL permitted baskets, even small ones
- Page numbers from [PAGE X] markers
- If a section doesn't exist, use null
- Return ONLY the JSON"""

    def _parse_content_extraction(self, response_text: str) -> RPExtraction:
        """Parse content extraction response."""
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start == -1 or end == 0:
                logger.warning("No JSON object found in content extraction response")
                return RPExtraction()

            json_str = response_text[start:end]
            logger.debug(f"Parsing content extraction JSON: {json_str[:500]}...")

            data = json.loads(json_str)
            logger.info(f"Content extraction keys: {list(data.keys())}")

            # Log raw values to debug extraction issues
            raw_baskets = data.get("permitted_baskets")
            raw_definitions = data.get("definitions")
            baskets_info = "None" if raw_baskets is None else (f"[{len(raw_baskets)} items]" if isinstance(raw_baskets, list) else "not a list")
            definitions_info = "None" if raw_definitions is None else (f"[{len(raw_definitions)} items]" if isinstance(raw_definitions, list) else "not a list")
            logger.info(f"Raw permitted_baskets: {type(raw_baskets).__name__}, {baskets_info}")
            logger.info(f"Raw definitions: {type(raw_definitions).__name__}, {definitions_info}")

            dividend_prohibition = None
            if data.get("dividend_prohibition"):
                dp = data["dividend_prohibition"]
                dividend_prohibition = ExtractedContent(
                    section_type="dividend_prohibition",
                    text=dp.get("text", ""),
                    pages=(dp.get("pages") or []),
                    section_reference=dp.get("section_reference")
                )
                logger.info(f"Dividend prohibition found: {dp.get('section_reference', 'no ref')}")

            permitted_baskets = []
            # Handle null from Claude - use 'or []' pattern
            for basket in (raw_baskets or []):
                permitted_baskets.append(ExtractedContent(
                    section_type=f"basket_{basket.get('basket_name', 'unknown')}",
                    text=basket.get("text", ""),
                    pages=(basket.get("pages") or []),
                    section_reference=basket.get("section_reference")
                ))

            rdp_restrictions = None
            if data.get("rdp_restrictions"):
                rdp = data["rdp_restrictions"]
                rdp_restrictions = ExtractedContent(
                    section_type="rdp_restrictions",
                    text=rdp.get("text", ""),
                    pages=(rdp.get("pages") or []),
                    section_reference=rdp.get("section_reference")
                )

            definitions = []
            # Handle null from Claude - use 'or []' pattern
            for defn in (data.get("definitions") or []):
                definitions.append(ExtractedContent(
                    section_type=f"definition_{defn.get('term', 'unknown')}",
                    text=defn.get("text", ""),
                    pages=(defn.get("pages") or [])
                ))

            logger.info(f"Parsed: {len(permitted_baskets)} baskets, {len(definitions)} definitions")

            return RPExtraction(
                dividend_prohibition=dividend_prohibition,
                permitted_baskets=permitted_baskets,
                rdp_restrictions=rdp_restrictions,
                definitions=definitions,
                raw_json=data
            )
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in content extraction: {e}")
            logger.error(f"Response text: {response_text[:500]}")
            return RPExtraction()

    # =========================================================================
    # STEP 3: Load Questions from TypeDB (SSoT)
    # =========================================================================

    def load_questions_by_category(self, covenant_type: str) -> Dict[str, List[Dict]]:
        """Load questions from TypeDB grouped by category."""
        if not typedb_client.driver:
            logger.warning("TypeDB not connected")
            return {}

        # Category names mapping (derived from question_id prefix like rp_a1 -> A)
        category_names = {
            "A": "Dividend Restrictions - General Structure",
            "B": "Intercompany Dividends",
            "C": "Management Equity Basket",
            "D": "Tax Distribution Basket",
            "E": "Equity Awards",
            "F": "Builder Basket / Cumulative Amount",
            "G": "Ratio-Based Dividend Basket",
            "H": "Holding Company Overhead",
            "I": "Basket Reallocation",
            "J": "Unrestricted Subsidiaries",
            "K": "J.Crew Blocker",
            "S": "Restricted Debt Payments - General",
            "T": "RDP Baskets",
            "Z": "Pattern Detection",
        }

        try:
            from typedb.driver import TransactionType
            tx = typedb_client.driver.transaction(
                settings.typedb_database, TransactionType.READ
            )
            try:
                # Query questions directly - category derived from question_id
                query = f"""
                    match
                        $q isa ontology_question,
                            has question_id $qid,
                            has question_text $qt,
                            has answer_type $at,
                            has covenant_type "{covenant_type}",
                            has display_order $order;
                    select $qid, $qt, $at, $order;
                """

                result = tx.query(query).resolve()
                questions_by_cat: Dict[str, List[Dict]] = {}

                for row in result.as_concept_rows():
                    qid = row.get("qid").as_attribute().get_value()
                    # Extract category from question_id: "rp_a1" -> "A", "rp_k2" -> "K"
                    # Format: {prefix}_{category_letter}{number}
                    parts = qid.split("_")
                    if len(parts) >= 2 and len(parts[1]) >= 1:
                        cat_letter = parts[1][0].upper()
                    else:
                        cat_letter = "Z"  # Default to Pattern Detection

                    if cat_letter not in questions_by_cat:
                        questions_by_cat[cat_letter] = []

                    questions_by_cat[cat_letter].append({
                        "question_id": qid,
                        "question_text": row.get("qt").as_attribute().get_value(),
                        "answer_type": row.get("at").as_attribute().get_value(),
                        "display_order": row.get("order").as_attribute().get_value(),
                        "category_id": cat_letter,
                        "category_name": category_names.get(cat_letter, f"Category {cat_letter}")
                    })

                # Load target field/concept mappings AND multiselect options
                for cat_id, questions in questions_by_cat.items():
                    for q in questions:
                        target_info = self._get_question_target(tx, q["question_id"])
                        q["target_type"] = target_info["type"]  # "field" or "concept"
                        q["target_field_name"] = target_info["name"]
                        q["target_concept_type"] = target_info.get("concept_type")
                        q["concept_options"] = target_info.get("options", [])

                logger.info(f"Loaded {sum(len(qs) for qs in questions_by_cat.values())} {covenant_type} questions in {len(questions_by_cat)} categories")
                return questions_by_cat

            finally:
                tx.close()
        except Exception as e:
            logger.error(f"Error loading questions: {e}")
            return {}

    def _get_question_target(self, tx, question_id: str) -> Dict[str, Any]:
        """
        Get target field or concept type for a question.

        For multiselect questions (target_concept_type), also loads the valid
        concept options so the QA prompt can show them.

        Returns:
            {
                "type": "field" | "concept",
                "name": field_name or concept_type,
                "concept_type": concept_type (if multiselect),
                "options": [{"id": "...", "name": "..."}, ...] (if multiselect)
            }
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
                return {
                    "type": "field",
                    "name": result[0].get("fn").as_attribute().get_value()
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
                concept_type = result[0].get("ct").as_attribute().get_value()

                # Load the concept options for this type
                options = self._load_concept_options(tx, concept_type)

                return {
                    "type": "concept",
                    "name": concept_type,
                    "concept_type": concept_type,
                    "options": options
                }

        except Exception as e:
            logger.debug(f"Error getting target for {question_id}: {e}")

        return {"type": "unknown", "name": ""}

    def _load_concept_options(self, tx, concept_type: str) -> List[Dict[str, str]]:
        """
        Load all concept instances for a given concept type.

        Returns list of {"id": concept_id, "name": display_name}
        """
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
                options.append({
                    "id": row.get("cid").as_attribute().get_value(),
                    "name": row.get("name").as_attribute().get_value()
                })
            logger.debug(f"Loaded {len(options)} options for {concept_type}")
        except Exception as e:
            logger.warning(f"Error loading concept options for {concept_type}: {e}")
        return options

    # =========================================================================
    # STEP 4: Answer Questions Using Extracted Content
    # =========================================================================

    def answer_category_questions(
        self,
        category_id: str,
        category_name: str,
        questions: List[Dict[str, Any]],
        extracted_content: RPExtraction
    ) -> CategoryAnswers:
        """Answer all questions in a category using the extracted content."""
        context = self._build_qa_context(extracted_content)
        questions_text = self._format_questions_for_prompt(questions)
        prompt = self._build_qa_prompt(category_name, questions_text, context)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            answers = self._parse_qa_response(response.content[0].text, questions)
            return CategoryAnswers(category_id=category_id, category_name=category_name, answers=answers)
        except Exception as e:
            logger.error(f"QA error for {category_name}: {e}")
            return CategoryAnswers(category_id=category_id, category_name=category_name, answers=[])

    def _build_qa_context(self, content: RPExtraction) -> str:
        """Build context string from extracted content."""
        parts = []

        if content.dividend_prohibition:
            parts.append("## DIVIDEND PROHIBITION")
            parts.append(f"[Pages {content.dividend_prohibition.pages}]")
            parts.append(content.dividend_prohibition.text)
            parts.append("")

        if content.permitted_baskets:
            parts.append("## PERMITTED BASKETS")
            for basket in content.permitted_baskets:
                parts.append(f"### {basket.section_type}")
                parts.append(f"[Pages {basket.pages}]")
                parts.append(basket.text)
                parts.append("")

        if content.rdp_restrictions:
            parts.append("## RDP RESTRICTIONS")
            parts.append(f"[Pages {content.rdp_restrictions.pages}]")
            parts.append(content.rdp_restrictions.text)
            parts.append("")

        if content.definitions:
            parts.append("## DEFINITIONS")
            for defn in content.definitions:
                parts.append(f"### {defn.section_type}")
                parts.append(f"[Pages {defn.pages}]")
                parts.append(defn.text)
                parts.append("")

        return "\n".join(parts)

    def _format_questions_for_prompt(self, questions: List[Dict]) -> str:
        """Format questions for the QA prompt, including multiselect options."""
        lines = []
        for i, q in enumerate(questions, 1):
            answer_type = q.get("answer_type", "boolean")
            target = q.get("target_field_name", "")
            target_type = q.get("target_type", "field")
            concept_options = q.get("concept_options", [])

            type_hint = {
                "boolean": "(yes/no)",
                "integer": "(number)",
                "double": "(decimal)",
                "percentage": "(decimal)",
                "currency": "(dollar amount)",
                "multiselect": "(select from options below)"
            }.get(answer_type, "")

            lines.append(f"{i}. [{q['question_id']}] {q['question_text']} {type_hint}")

            if target_type == "concept" and concept_options:
                # Show valid options for multiselect
                lines.append(f"   → Concept: {target}")
                option_strs = [f"{opt['id']} ({opt['name']})" for opt in concept_options]
                lines.append(f"   → Valid options: {', '.join(option_strs)}")
            elif target:
                lines.append(f"   → Field: {target}")

        return "\n".join(lines)

    def _build_qa_prompt(self, category_name: str, questions_text: str, context: str) -> str:
        """Build the QA prompt for a category."""
        return f"""Answer covenant analysis questions using extracted credit agreement content.

## CATEGORY: {category_name}

## QUESTIONS

{questions_text}

## EXTRACTED CONTENT

{context}

## INSTRUCTIONS

For EACH question, answer based ONLY on the extracted content:
- question_id: The ID in brackets
- value: The answer (true/false, number, or array for multiselect)
- source_text: EXACT quote supporting your answer
- source_pages: Page numbers
- confidence: "high" (explicit), "medium" (inferred), "low" (uncertain), "not_found" (cannot answer)
- reasoning: Brief explanation (1 sentence)

## OUTPUT

Return JSON array:
```json
[
  {{"question_id": "rp_q1", "value": true, "source_text": "...", "source_pages": [89], "confidence": "high", "reasoning": "..."}}
]
```

Return ONLY the JSON array."""

    def _parse_qa_response(self, response_text: str, questions: List[Dict]) -> List[AnsweredQuestion]:
        """Parse QA response, handling both scalar and multiselect answers."""
        try:
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start == -1 or end == 0:
                logger.warning(f"No JSON array found in QA response: {response_text[:200]}")
                return []

            # Clean JSON - fix trailing commas
            json_str = response_text[start:end]
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)

            data = json.loads(json_str)
            logger.info(f"Parsed {len(data)} answers from QA response")

            # Log first answer for debugging
            if data:
                logger.debug(f"First answer: {data[0]}")

            q_lookup = {q['question_id']: q for q in questions}
            answers = []

            for item in data:
                qid = item.get("question_id")
                if not qid or qid not in q_lookup:
                    continue

                q = q_lookup[qid]
                target_type = q.get("target_type", "field")

                # For multiselect (concept), use concept_type as attribute_name
                # For scalar (field), use target_field_name
                if target_type == "concept":
                    attr_name = q.get("target_concept_type", q.get("target_field_name", ""))
                else:
                    attr_name = q.get("target_field_name", "")

                # Validate multiselect values against known options
                value = item.get("value")
                if target_type == "concept" and isinstance(value, list):
                    valid_ids = {opt["id"] for opt in q.get("concept_options", [])}
                    if valid_ids:
                        validated = [v for v in value if v in valid_ids]
                        invalid = [v for v in value if v not in valid_ids]
                        if invalid:
                            logger.warning(f"Invalid concept_ids for {qid}: {invalid}")
                        value = validated

                answers.append(AnsweredQuestion(
                    question_id=qid,
                    attribute_name=attr_name,
                    answer_type=q.get("answer_type", "string"),
                    value=value,
                    source_text=item.get("source_text") or "",
                    source_pages=(item.get("source_pages") or []),
                    confidence=item.get("confidence") or "medium",
                    reasoning=item.get("reasoning")
                ))

            return answers
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in QA: {e}")
            logger.error(f"QA response text: {response_text[:500]}")
            return []

    # =========================================================================
    # STEP 5: Store Results to TypeDB
    # =========================================================================

    def store_extraction_result(
        self,
        deal_id: str,
        category_answers: List[CategoryAnswers]
    ) -> bool:
        """
        Store extraction results to TypeDB.

        - Creates rp_provision entity if not exists
        - Stores scalar answers as provision attributes (individual transactions)
        - Stores multiselect answers as concept_applicability relations

        Uses individual transactions per write to avoid one failure poisoning all writes.
        """
        if not typedb_client.driver:
            logger.error("TypeDB not connected, cannot store results")
            return False

        provision_id = f"{deal_id}_rp"

        try:
            from typedb.driver import TransactionType

            # Step 5a: Create rp_provision if not exists
            self._ensure_provision_exists(deal_id, provision_id)

            # Step 5b & 5c: Store answers (individual transactions to avoid cascade failures)
            scalar_count = 0
            scalar_failed = 0
            multiselect_count = 0
            multiselect_failed = 0

            for cat_answers in category_answers:
                logger.debug(f"Processing category {cat_answers.category_id}: {len(cat_answers.answers)} answers")
                for answer in cat_answers.answers:
                    if answer.value is None:
                        continue
                    if answer.confidence == "not_found":
                        continue

                    if isinstance(answer.value, list):
                        # Multiselect answer → concept_applicability
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
                        # Scalar answer → provision attribute
                        success = self._store_scalar_attribute_safe(
                            provision_id, answer.attribute_name,
                            answer.value, answer.answer_type
                        )
                        if success:
                            scalar_count += 1
                        else:
                            scalar_failed += 1

            logger.info(
                f"Stored {scalar_count} scalar ({scalar_failed} failed), "
                f"{multiselect_count} multiselect ({multiselect_failed} failed)"
            )
            return True

        except Exception as e:
            logger.error(f"Storage error: {e}")
            return False

    def _store_scalar_attribute_safe(
        self,
        provision_id: str,
        attribute_name: str,
        value: Any,
        answer_type: str
    ) -> bool:
        """Store a scalar answer with its own transaction (safe from cascade failures)."""
        from typedb.driver import TransactionType

        formatted_value = self._format_typedb_value(value, answer_type)
        if formatted_value is None:
            logger.warning(f"Skipping {attribute_name}: could not format value {value}")
            return False

        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match $p isa rp_provision, has provision_id "{provision_id}";
                insert $p has {attribute_name} {formatted_value};
            """
            tx.query(query).resolve()
            tx.commit()
            logger.debug(f"Stored {attribute_name} = {formatted_value}")
            return True
        except Exception as e:
            tx.close()
            logger.warning(f"Could not store {attribute_name} = {formatted_value}: {e}")
            return False

    def _store_concept_applicability_safe(
        self,
        provision_id: str,
        concept_type: str,
        concept_id: str,
        source_text: str,
        source_page: int
    ) -> bool:
        """Store a concept applicability with its own transaction (safe from cascade failures)."""
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

    def _ensure_provision_exists(self, deal_id: str, provision_id: str):
        """Create rp_provision entity linked to deal if not exists."""
        from typedb.driver import TransactionType

        # Check if provision exists
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

        # Create provision
        tx = typedb_client.driver.transaction(
            settings.typedb_database, TransactionType.WRITE
        )
        try:
            query = f"""
                match $d isa deal, has deal_id "{deal_id}";
                insert
                    $p isa rp_provision, has provision_id "{provision_id}";
                    (deal: $d, provision: $p) isa deal_has_provision;
            """
            tx.query(query).resolve()
            tx.commit()
            logger.info(f"Created rp_provision: {provision_id}")
        except Exception as e:
            tx.close()
            logger.error(f"Error creating provision: {e}")
            raise

    def _format_typedb_value(self, value: Any, answer_type: str) -> Optional[str]:
        """Format a Python value for TypeQL insertion."""
        if value is None:
            return None

        # Handle "not_found" or similar non-values
        if isinstance(value, str) and value.lower() in ("not_found", "n/a", "none", "null"):
            return None

        if answer_type == "boolean":
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, str):
                return "true" if value.lower() in ("true", "yes", "1") else "false"
            return None

        if answer_type in ("integer", "int", "number"):
            try:
                # Strip quotes if Claude wrapped the number in quotes
                clean_value = str(value).strip('"\'')
                return str(int(float(clean_value)))  # Handle "4.0" -> 4
            except (ValueError, TypeError):
                return None

        if answer_type in ("double", "decimal", "percentage", "currency", "float"):
            try:
                # Strip quotes if Claude wrapped the number in quotes
                clean_value = str(value).strip('"\'')
                return str(float(clean_value))
            except (ValueError, TypeError):
                return None

        if answer_type in ("string", "text"):
            escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'

        # Default: try to detect type from value
        # If it looks numeric, don't quote it
        try:
            clean_value = str(value).strip('"\'')
            float_val = float(clean_value)
            return str(float_val)
        except (ValueError, TypeError):
            pass

        # Fall back to string
        escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'

    # =========================================================================
    # MAIN EXTRACTION FLOW - Smart Chunked with Early Exit
    # =========================================================================

    async def extract_rp_provision(
        self,
        pdf_path: str,
        deal_id: str,
        questions_by_category: Optional[Dict[str, List[Dict]]] = None,
        store_results: bool = True
    ) -> ExtractionResult:
        """
        Smart chunked RP extraction pipeline with early exit.

        1. Parse PDF
        2. Create overlapping chunks (250k chars, 50k overlap)
        3. Load questions from TypeDB
        4. For each chunk, ask only UNANSWERED questions
           - HIGH confidence → stop searching for that question
           - MEDIUM/LOW → keep best, try next chunk
        5. Early exit when all questions have HIGH confidence
        6. Store best answers to TypeDB
        """
        start_time = time.time()

        # Step 1: Parse PDF
        document_text = self.parse_document(pdf_path)

        # Step 2: Create overlapping chunks
        chunks = self._create_chunks(document_text, chunk_size=250000, overlap=50000)
        logger.info(f"Created {len(chunks)} chunks from {len(document_text)} char document")

        # Step 3: Load questions from TypeDB
        if questions_by_category is None:
            logger.info("Step 3: Loading questions from TypeDB...")
            questions_by_category = self.load_questions_by_category("RP")

        all_questions = [q for qs in questions_by_category.values() for q in qs]
        total_questions = len(all_questions)
        logger.info(f"Loaded {total_questions} questions")

        # Step 4: Process chunks with early exit
        answer_tracker = AnswerTracker()
        chunks_processed = 0

        for chunk_idx, chunk_text in enumerate(chunks):
            # Get questions that still need answers
            unanswered = answer_tracker.get_unanswered_questions(all_questions)

            if not unanswered:
                logger.info(f"All questions have HIGH confidence answers, stopping at chunk {chunk_idx}")
                break

            chunks_processed += 1
            logger.info(f"Chunk {chunk_idx + 1}/{len(chunks)}: {len(unanswered)} questions remaining")

            # Group unanswered by category for batching
            unanswered_by_cat = self._group_by_category(unanswered)

            # Ask questions against this chunk
            for cat_id, cat_questions in unanswered_by_cat.items():
                cat_name = cat_questions[0]["category_name"] if cat_questions else cat_id
                logger.info(f"  Asking {len(cat_questions)} questions from {cat_name}")

                answers = self._answer_questions_against_chunk(
                    chunk_text, cat_questions, chunk_idx
                )
                for answer in answers:
                    answer_tracker.update(answer)

            # Log progress
            high_conf = answer_tracker.get_high_confidence_count()
            logger.info(f"After chunk {chunk_idx + 1}: {high_conf}/{total_questions} HIGH confidence")

        # Convert tracked answers to CategoryAnswers format
        category_answers = self._organize_answers_by_category(
            answer_tracker.get_all_answers(),
            questions_by_category
        )

        # Step 5: Store results to TypeDB
        if store_results:
            logger.info("Step 5: Storing results to TypeDB...")
            self.store_extraction_result(deal_id, category_answers)

        extraction_time = time.time() - start_time
        high_conf_count = answer_tracker.get_high_confidence_count()
        logger.info(
            f"Extraction complete in {extraction_time:.1f}s: "
            f"{chunks_processed} chunks, {high_conf_count}/{total_questions} HIGH confidence"
        )

        return ExtractionResult(
            deal_id=deal_id,
            covenant_type="RP",
            category_answers=category_answers,
            extraction_time_seconds=extraction_time,
            chunks_processed=chunks_processed,
            total_questions=total_questions,
            high_confidence_answers=high_conf_count
        )

    def _create_chunks(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """Split document into overlapping chunks."""
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - overlap
            if end >= len(text):
                break
        return chunks

    def _group_by_category(self, questions: List[Dict]) -> Dict[str, List[Dict]]:
        """Group questions by category_id."""
        by_cat: Dict[str, List[Dict]] = {}
        for q in questions:
            cat_id = q.get("category_id", "Z")
            if cat_id not in by_cat:
                by_cat[cat_id] = []
            by_cat[cat_id].append(q)
        return by_cat

    def _answer_questions_against_chunk(
        self,
        chunk_text: str,
        questions: List[Dict],
        chunk_idx: int
    ) -> List[AnsweredQuestion]:
        """Ask questions directly against a document chunk."""
        prompt = self._build_chunk_qa_prompt(chunk_text, questions, chunk_idx)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse_chunk_qa_response(response.content[0].text, questions, chunk_idx)
        except Exception as e:
            logger.error(f"QA error for chunk {chunk_idx}: {e}")
            return []

    def _build_chunk_qa_prompt(self, chunk_text: str, questions: List[Dict], chunk_idx: int) -> str:
        """Build prompt for answering questions against a document chunk."""
        questions_text = self._format_questions_for_prompt(questions)

        return f"""Answer covenant analysis questions using this document excerpt.

## DOCUMENT EXCERPT (Chunk {chunk_idx + 1})

{chunk_text}

## QUESTIONS

{questions_text}

## INSTRUCTIONS

For EACH question, analyze the document excerpt:
- question_id: The ID in brackets
- value: The answer (true/false, number, or array for multiselect)
- source_text: EXACT quote from the document supporting your answer (max 500 chars)
- source_pages: Page numbers from [PAGE X] markers
- confidence:
  - "high" = explicit answer found in text
  - "medium" = answer inferred from context
  - "low" = uncertain, partial information
  - "not_found" = no relevant information in this excerpt
- reasoning: Brief explanation (1 sentence)

IMPORTANT: If information is NOT in this excerpt, use "not_found" - it may be in another part of the document.

## OUTPUT

Return JSON array:
```json
[
  {{"question_id": "rp_a1", "value": true, "source_text": "...", "source_pages": [89], "confidence": "high", "reasoning": "..."}}
]
```

Return ONLY the JSON array."""

    def _parse_chunk_qa_response(
        self,
        response_text: str,
        questions: List[Dict],
        chunk_idx: int
    ) -> List[AnsweredQuestion]:
        """Parse QA response from chunk, including chunk_index."""
        try:
            start = response_text.find('[')
            end = response_text.rfind(']') + 1
            if start == -1 or end == 0:
                logger.warning(f"No JSON array in chunk {chunk_idx} response")
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
                    reasoning=item.get("reasoning"),
                    chunk_index=chunk_idx
                ))

            logger.debug(f"Chunk {chunk_idx}: parsed {len(answers)} answers")
            return answers

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in chunk {chunk_idx}: {e}")
            return []

    def _organize_answers_by_category(
        self,
        answers: List[AnsweredQuestion],
        questions_by_category: Dict[str, List[Dict]]
    ) -> List[CategoryAnswers]:
        """Organize flat answer list into CategoryAnswers structure."""
        # Build question_id -> category mapping
        qid_to_cat: Dict[str, str] = {}
        cat_names: Dict[str, str] = {}

        for cat_id, questions in questions_by_category.items():
            if questions:
                cat_names[cat_id] = questions[0].get("category_name", cat_id)
            for q in questions:
                qid_to_cat[q["question_id"]] = cat_id

        # Group answers by category
        answers_by_cat: Dict[str, List[AnsweredQuestion]] = {}
        for answer in answers:
            cat_id = qid_to_cat.get(answer.question_id, "Z")
            if cat_id not in answers_by_cat:
                answers_by_cat[cat_id] = []
            answers_by_cat[cat_id].append(answer)

        # Build CategoryAnswers list
        return [
            CategoryAnswers(
                category_id=cat_id,
                category_name=cat_names.get(cat_id, f"Category {cat_id}"),
                answers=cat_answers
            )
            for cat_id, cat_answers in sorted(answers_by_cat.items())
        ]


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
