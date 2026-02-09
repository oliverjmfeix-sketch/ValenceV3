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
    # STEP 2: Extract RP-Relevant Universe (Format-Agnostic)
    # =========================================================================

    def extract_rp_universe(self, document_text: str) -> RPUniverse:
        """
        Extract the complete RP-relevant universe from the document.

        Uses CONTENT PATTERNS (not section numbers) to find:
        - All relevant definitions (Restricted Payment, CNI, Cumulative Amount, etc.)
        - Complete dividend/RP covenant with ALL baskets
        - Related covenants (Investment, Asset Sale, RDP)
        - Mechanics (Unrestricted Sub designation, Pro forma)

        For docs > 400k chars, uses 2-call merge strategy.
        """
        doc_len = len(document_text)
        logger.info(f"Extracting RP universe from {doc_len} char document")

        if doc_len <= 400000:
            # Single call for smaller documents
            return self._extract_rp_universe_single(document_text)
        else:
            # Two-call merge for large documents
            return self._extract_rp_universe_merge(document_text)

    def _extract_rp_universe_single(self, document_text: str) -> RPUniverse:
        """Extract RP universe with a single Claude call."""
        prompt = self._build_universe_extraction_prompt(document_text)
        response_text = self._call_claude_streaming(prompt, max_tokens=32000)
        if response_text:
            return self._parse_universe_extraction(response_text)
        return RPUniverse()

    def _call_claude_streaming(self, prompt: str, max_tokens: int = 16000) -> str:
        """Call Claude with streaming to handle long operations."""
        try:
            collected_text = []
            chunk_count = 0
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    collected_text.append(text)
                    chunk_count += 1
            logger.info(f"Streaming complete: {chunk_count} chunks received")
            result = "".join(collected_text)
            logger.info(f"Response assembled: {len(result)} chars")
            return result
        except Exception as e:
            logger.error(f"Claude streaming error: {e}")
            return ""

    def _extract_rp_universe_merge(self, document_text: str) -> RPUniverse:
        """Extract RP universe with two calls for large documents, then merge."""
        doc_len = len(document_text)
        midpoint = doc_len // 2
        overlap = 50000  # 50k overlap to catch content at boundaries

        # First half (definitions are usually here)
        first_half = document_text[:midpoint + overlap]
        # Second half (covenants are usually here)
        second_half = document_text[midpoint - overlap:]

        logger.info(f"Large doc ({doc_len} chars): extracting in two parts")
        logger.info(f"  Part 1: chars 0-{midpoint + overlap} (definitions)")
        logger.info(f"  Part 2: chars {midpoint - overlap}-{doc_len} (covenants)")

        # Extract from first half (expect definitions) - use streaming for long operations
        prompt1 = self._build_universe_extraction_prompt(first_half, part=1, focus="definitions")
        logger.info("Extracting Part 1 (definitions)...")
        response1 = self._call_claude_streaming(prompt1, max_tokens=32000)
        if response1:
            universe1 = self._parse_universe_extraction(response1)
            logger.info(f"Part 1: definitions={len(universe1.definitions)} chars")
        else:
            logger.error("Part 1 extraction returned empty")
            universe1 = RPUniverse()

        # Extract from second half (expect covenants) - use streaming for long operations
        prompt2 = self._build_universe_extraction_prompt(second_half, part=2, focus="covenants")
        logger.info("Extracting Part 2 (covenants)...")
        response2 = self._call_claude_streaming(prompt2, max_tokens=32000)
        logger.info(f"Part 2 response received: {len(response2) if response2 else 0} chars")
        if response2:
            try:
                universe2 = self._parse_universe_extraction(response2)
                logger.info(f"Part 2: dividend_covenant={len(universe2.dividend_covenant)} chars")
            except Exception as e:
                logger.exception(f"Part 2 parse error: {e}")
                universe2 = RPUniverse()
        else:
            logger.error("Part 2 extraction returned empty")
            universe2 = RPUniverse()

        # Merge: definitions from part 1, covenants from part 2 (or whichever has them)
        logger.info("Merging Part 1 and Part 2 universes...")
        try:
            merged = RPUniverse(
                definitions=universe1.definitions or universe2.definitions,
                dividend_covenant=universe2.dividend_covenant or universe1.dividend_covenant,
                investment_covenant=universe2.investment_covenant or universe1.investment_covenant,
                asset_sale_covenant=universe2.asset_sale_covenant or universe1.asset_sale_covenant,
                rdp_covenant=universe2.rdp_covenant or universe1.rdp_covenant,
                unsub_mechanics=universe2.unsub_mechanics or universe1.unsub_mechanics,
                pro_forma_mechanics=universe1.pro_forma_mechanics or universe2.pro_forma_mechanics,
            )

            # Build combined raw text
            logger.info("Building combined context...")
            merged.raw_text = self._build_combined_context(merged)
            logger.info(f"Merged RP universe: {len(merged.raw_text)} chars")

            return merged
        except Exception as e:
            logger.exception(f"Error merging RP universe: {e}")
            return RPUniverse()

    def _build_universe_extraction_prompt(
        self,
        document_text: str,
        part: int = 0,
        focus: str = "all"
    ) -> str:
        """Build the format-agnostic RP universe extraction prompt."""

        if part == 1:
            focus_instruction = """
FOCUS: This is the FIRST HALF of a large document. Definitions are usually here.
Prioritize extracting ALL DEFINITIONS. Covenants may be partial or missing - that's OK.
"""
        elif part == 2:
            focus_instruction = """
FOCUS: This is the SECOND HALF of a large document. Covenants are usually here.
Prioritize extracting COMPLETE COVENANTS with all baskets. Definitions may be missing - that's OK.
"""
        else:
            focus_instruction = ""

        return f"""You are extracting Restricted Payments-relevant content from a US credit agreement.
{focus_instruction}
## YOUR TASK

Extract the COMPLETE VERBATIM TEXT of everything needed to analyze the Restricted Payments covenant.
This includes definitions, covenant sections, and related mechanics.

CRITICAL: DO NOT SUMMARIZE. Copy the ACTUAL TEXT word-for-word from the document.
The dividend/RP covenant alone is typically 15,000-30,000 characters with 20+ permitted baskets.
Your output should be 50,000-100,000 characters of verbatim text.

## WHAT TO EXTRACT

### 1. DEFINITIONS
Find the definitions section (usually Article I, Section 1.01, or just "DEFINITIONS").
Extract COMPLETE definitions for these terms (include variants/similar terms):

**Core Restricted Payment terms:**
- "Restricted Payment" or "Dividend" or "Distribution" (however defined)
- "Cumulative Amount" or "Available Amount" or "Cumulative Credit" or "Builder Basket"
- "Consolidated Net Income" or "Net Income"
- "Excess Cash Flow" or "ECF" or "Available Retained ECF"
- "Retained Excess Cash Flow Amount"

**Unrestricted Subsidiary terms:**
- "Unrestricted Subsidiary" or "Excluded Subsidiary" or "Non-Guarantor Subsidiary"
- "Intellectual Property" or "Material IP" or "Principal Property"
- "Investment" (the defined term, not casual usage)
- "Permitted Investment"

**Ratio terms:**
- "First Lien Leverage Ratio" or "Secured Leverage Ratio" or "Total Leverage Ratio"
- "Consolidated EBITDA" or "EBITDA" or "Adjusted EBITDA"
- "Fixed Charge Coverage Ratio" or "Interest Coverage Ratio"

**Equity terms:**
- "Qualified Capital Stock" or "Qualified Equity Interests"
- "Disqualified Capital Stock" or "Disqualified Equity Interests"
- "Equity Interests"

**Person/Entity terms:**
- Any definition of covered persons (management, employees, directors, etc.)
- "Credit Party" or "Loan Party"
- "Subsidiary Guarantor"
- "Permitted Holder" or "Sponsor"

### 2. NEGATIVE COVENANTS
Find and extract COMPLETE sections (all subsections) that restrict:

- **Dividends/Distributions** - Look for language like "shall not declare or pay any dividend"
  or "shall not make any Restricted Payment"
  INCLUDE ALL PERMITTED BASKETS/EXCEPTIONS (usually labeled (a), (b), (c)... through the end)
  This is typically the longest covenant - extract it COMPLETELY

- **Restricted Debt Payments** - Look for language about prepaying subordinated/junior debt
  or "prepay, redeem, purchase, defease" any Junior Debt

- **Investments** - Look for language about making loans, advances, or investments
  ESPECIALLY subsections about Unrestricted Subsidiaries

- **Asset Sales** - Look for language about selling or disposing of assets
  or "Disposition" restrictions

### 3. RELATED PROVISIONS
- **Unrestricted Subsidiary designation mechanics** - how subs are designated/redesignated
- **Pro forma calculation provisions** - how ratios are calculated
- **Limited Condition Transaction provisions** - LCT mechanics
- **Any J.Crew blocker language** - restrictions on IP transfers to unrestricted subs

## DOCUMENT

{document_text}

## OUTPUT FORMAT

Return your extraction in this EXACT format with clear section markers:

```
=== DEFINITIONS ===

"Restricted Payment" means [VERBATIM TEXT]
[PAGE X]

"Cumulative Amount" means [VERBATIM TEXT]
[PAGE Y]

[... continue for all relevant definitions ...]

=== DIVIDEND/RESTRICTED PAYMENT COVENANT ===

[SECTION HEADER AS IT APPEARS IN DOCUMENT]

[COMPLETE VERBATIM TEXT OF SECTION INCLUDING ALL SUBSECTIONS AND BASKETS]

[PAGES X-Y]

=== INVESTMENT COVENANT ===

[COMPLETE VERBATIM TEXT]

[PAGES X-Y]

=== ASSET SALE COVENANT ===

[COMPLETE VERBATIM TEXT]

[PAGES X-Y]

=== RESTRICTED DEBT PAYMENT COVENANT ===

[COMPLETE VERBATIM TEXT]

[PAGES X-Y]

=== UNRESTRICTED SUBSIDIARY MECHANICS ===

[COMPLETE VERBATIM TEXT]

[PAGES X-Y]

=== PRO FORMA / CALCULATION MECHANICS ===

[RELEVANT PROVISIONS]

[PAGES X-Y]
```

## CRITICAL INSTRUCTIONS

1. COPY THE ACTUAL TEXT WORD-FOR-WORD - do not summarize, paraphrase, or abbreviate
2. The Restricted Payment covenant typically has 20-30 permitted baskets labeled (a) through (z)
   - COPY EVERY SINGLE BASKET IN FULL
   - Each basket may be 500-2000 characters
3. Include page numbers from [PAGE X] markers in the document
4. For definitions, include the COMPLETE definition even if it spans multiple paragraphs
5. If a section doesn't exist, write "NOT FOUND"
6. Use the exact section markers shown above (=== SECTION NAME ===)

REMEMBER: Your output should be 50,000-100,000 characters of verbatim extracted text.
If your output is less than 30,000 characters, you are likely summarizing instead of extracting."""

    def _parse_universe_extraction(self, response_text: str) -> RPUniverse:
        """Parse the RP universe extraction response."""
        logger.info(f"Parsing universe extraction response: {len(response_text)} chars")
        universe = RPUniverse()

        # Parse each section using markers
        sections = {
            "definitions": "=== DEFINITIONS ===",
            "dividend_covenant": "=== DIVIDEND/RESTRICTED PAYMENT COVENANT ===",
            "investment_covenant": "=== INVESTMENT COVENANT ===",
            "asset_sale_covenant": "=== ASSET SALE COVENANT ===",
            "rdp_covenant": "=== RESTRICTED DEBT PAYMENT COVENANT ===",
            "unsub_mechanics": "=== UNRESTRICTED SUBSIDIARY MECHANICS ===",
            "pro_forma_mechanics": "=== PRO FORMA / CALCULATION MECHANICS ===",
        }

        for field_name, marker in sections.items():
            content = self._extract_section(response_text, marker, list(sections.values()))
            if content and content.strip().upper() != "NOT FOUND":
                setattr(universe, field_name, content.strip())

        # Build combined raw text for QA
        universe.raw_text = self._build_combined_context(universe)

        logger.info(
            f"Parsed RP universe: definitions={len(universe.definitions)}, "
            f"dividend={len(universe.dividend_covenant)}, "
            f"investment={len(universe.investment_covenant)}, "
            f"rdp={len(universe.rdp_covenant)} chars"
        )

        return universe

    def _extract_section(self, text: str, start_marker: str, all_markers: List[str]) -> str:
        """Extract content between a marker and the next marker."""
        start_idx = text.find(start_marker)
        if start_idx == -1:
            return ""

        content_start = start_idx + len(start_marker)

        # Find the next section marker
        end_idx = len(text)
        for marker in all_markers:
            if marker == start_marker:
                continue
            marker_idx = text.find(marker, content_start)
            if marker_idx != -1 and marker_idx < end_idx:
                end_idx = marker_idx

        return text[content_start:end_idx].strip()

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

    def answer_questions_against_universe(
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
- source_text: EXACT quote from the context supporting your answer (max 500 chars)
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

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse_qa_response(response.content[0].text, questions)
        except Exception as e:
            logger.error(f"QA error for {category_name}: {e}")
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

                found.append(document_text[line_start:end].strip())

        if found:
            result = "\n\n---\n\n".join(found)
            logger.info(f"Extracted {len(found)} definitions ({len(result)} chars) for J.Crew analysis")
            return result

        logger.warning("No definitions found for J.Crew analysis — will fall back to RP universe definitions")
        return ""

    async def run_jcrew_deep_analysis(
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
    # MAIN EXTRACTION FLOW
    # =========================================================================

    async def extract_rp_provision(
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
        universe_chars = len(rp_universe.raw_text)
        logger.info(f"RP Universe extracted: {universe_chars} chars")

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
        category_answers = self.answer_questions_against_universe(
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

        return ExtractionResultV4(
            deal_id=deal_id,
            extraction=extraction,
            storage_result=storage_result,
            extraction_time_seconds=extraction_time,
            rp_universe_chars=universe_chars,
            model_used=model
        )

    def _call_claude_v4(self, prompt: str, model: str) -> str:
        """Call Claude API for V4 structured extraction."""
        system_prompt = """You are a legal analyst extracting covenant data from credit agreements.

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
    A cross-reference IS a definition. When asked if a term is defined, answer true for both inline and cross-reference definitions. When asked to extract a definition, return the cross-reference text verbatim — do not say "NOT DEFINED" for cross-referenced terms. When a term is defined by cross-reference, note which document contains the full definition."""

        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                timeout=300.0  # 5 minute timeout for extraction
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
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


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
