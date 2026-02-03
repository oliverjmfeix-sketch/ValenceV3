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

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse_universe_extraction(response.content[0].text)
        except Exception as e:
            logger.error(f"RP universe extraction error: {e}")
            return RPUniverse()

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

        # Extract from first half (expect definitions)
        prompt1 = self._build_universe_extraction_prompt(first_half, part=1, focus="definitions")
        try:
            response1 = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt1}]
            )
            universe1 = self._parse_universe_extraction(response1.content[0].text)
            logger.info(f"Part 1: definitions={len(universe1.definitions)} chars")
        except Exception as e:
            logger.error(f"Part 1 extraction error: {e}")
            universe1 = RPUniverse()

        # Extract from second half (expect covenants)
        prompt2 = self._build_universe_extraction_prompt(second_half, part=2, focus="covenants")
        try:
            response2 = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt2}]
            )
            universe2 = self._parse_universe_extraction(response2.content[0].text)
            logger.info(f"Part 2: dividend_covenant={len(universe2.dividend_covenant)} chars")
        except Exception as e:
            logger.error(f"Part 2 extraction error: {e}")
            universe2 = RPUniverse()

        # Merge: definitions from part 1, covenants from part 2 (or whichever has them)
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
        merged.raw_text = self._build_combined_context(merged)
        logger.info(f"Merged RP universe: {len(merged.raw_text)} chars")

        return merged

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

DO NOT summarize. Extract the ACTUAL TEXT with page numbers.

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

## IMPORTANT

- Extract VERBATIM - do not summarize or paraphrase
- Include ALL subsections, especially all permitted payment baskets ((a) through (z) or more)
- Include page numbers from [PAGE X] markers in the document
- If a section doesn't exist, write "NOT FOUND" for that section
- For definitions, include the COMPLETE definition even if very long
- When in doubt, include more rather than less
- Use the exact section markers shown above (=== SECTION NAME ===)"""

    def _parse_universe_extraction(self, response_text: str) -> RPUniverse:
        """Parse the RP universe extraction response."""
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
        """Load questions from TypeDB grouped by category."""
        if not typedb_client.driver:
            logger.warning("TypeDB not connected")
            return {}

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
                    parts = qid.split("_")
                    if len(parts) >= 2 and len(parts[1]) >= 1:
                        cat_letter = parts[1][0].upper()
                    else:
                        cat_letter = "Z"

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

    def _get_question_target(self, tx, question_id: str) -> Dict[str, Any]:
        """Get target field or concept type for a question."""
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
                options.append({
                    "id": row.get("cid").as_attribute().get_value(),
                    "name": row.get("name").as_attribute().get_value()
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
        category_name: str
    ) -> List[AnsweredQuestion]:
        """Answer a category's questions against the RP universe context."""
        questions_text = self._format_questions_for_prompt(questions)

        prompt = f"""Answer covenant analysis questions using the extracted RP-relevant content.

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
        """Store extraction results to TypeDB with individual transactions."""
        if not typedb_client.driver:
            logger.error("TypeDB not connected, cannot store results")
            return False

        provision_id = f"{deal_id}_rp"

        try:
            from typedb.driver import TransactionType

            self._ensure_provision_exists(deal_id, provision_id)

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

    def _store_scalar_attribute_safe(
        self,
        provision_id: str,
        attribute_name: str,
        value: Any,
        answer_type: str
    ) -> bool:
        """Store a scalar answer with its own transaction."""
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
                clean_value = str(value).strip('"\'')
                return str(int(float(clean_value)))
            except (ValueError, TypeError):
                return None

        if answer_type in ("double", "decimal", "percentage", "currency", "float"):
            try:
                clean_value = str(value).strip('"\'')
                return str(float(clean_value))
            except (ValueError, TypeError):
                return None

        if answer_type in ("string", "text"):
            escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'

        # Default: try to detect type from value
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
            rp_universe_chars=universe_chars
        )


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
