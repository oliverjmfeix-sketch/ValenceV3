"""
Simplified Extraction Pipeline for Covenant Analysis.

Flow:
1. Parse PDF → raw text with page markers
2. ONE Claude call to extract RP content (format-agnostic verbatim extraction)
3. Query questions from TypeDB by category (SSoT)
4. For each category, Claude answers using extracted content
5. Store as typed attributes + concept_applicability

Key insight: Separate CONTENT EXTRACTION (step 2) from QUESTION ANSWERING (step 4).
Step 2 extracts ALL RP content verbatim. Steps 3-4 query/answer against it.
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


@dataclass
class CategoryAnswers:
    """All answers for a category."""
    category_id: str
    category_name: str
    answers: List[AnsweredQuestion]


@dataclass
class ExtractionResult:
    """Complete extraction result."""
    deal_id: str
    covenant_type: str
    extracted_content: RPExtraction
    category_answers: List[CategoryAnswers]
    extraction_time_seconds: float


# =============================================================================
# EXTRACTION SERVICE
# =============================================================================

class ExtractionService:
    """
    Simplified extraction pipeline.

    Separates CONTENT EXTRACTION (getting verbatim text) from
    QUESTION ANSWERING (interpreting that text to answer ontology questions).
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
    # STEP 2: Extract RP Content (ONE Claude call - verbatim extraction)
    # =========================================================================

    def extract_rp_content(self, document_text: str) -> RPExtraction:
        """
        Extract ALL RP-related content verbatim from the document.

        This is format-agnostic - just extracts the actual text with page numbers.
        Does NOT try to answer questions or interpret the content.
        """
        prompt = self._build_content_extraction_prompt(document_text)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}]
            )
            return self._parse_content_extraction(response.content[0].text)
        except Exception as e:
            logger.error(f"Content extraction error: {e}")
            return RPExtraction()

    def _build_content_extraction_prompt(self, document_text: str) -> str:
        """Build prompt for verbatim content extraction."""
        return f"""You are extracting Restricted Payments (RP) covenant content from a credit agreement.

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

{document_text[:250000]}

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
                return RPExtraction()

            data = json.loads(response_text[start:end])

            dividend_prohibition = None
            if data.get("dividend_prohibition"):
                dp = data["dividend_prohibition"]
                dividend_prohibition = ExtractedContent(
                    section_type="dividend_prohibition",
                    text=dp.get("text", ""),
                    pages=dp.get("pages", []),
                    section_reference=dp.get("section_reference")
                )

            permitted_baskets = []
            for basket in data.get("permitted_baskets", []):
                permitted_baskets.append(ExtractedContent(
                    section_type=f"basket_{basket.get('basket_name', 'unknown')}",
                    text=basket.get("text", ""),
                    pages=basket.get("pages", []),
                    section_reference=basket.get("section_reference")
                ))

            rdp_restrictions = None
            if data.get("rdp_restrictions"):
                rdp = data["rdp_restrictions"]
                rdp_restrictions = ExtractedContent(
                    section_type="rdp_restrictions",
                    text=rdp.get("text", ""),
                    pages=rdp.get("pages", []),
                    section_reference=rdp.get("section_reference")
                )

            definitions = []
            for defn in data.get("definitions", []):
                definitions.append(ExtractedContent(
                    section_type=f"definition_{defn.get('term', 'unknown')}",
                    text=defn.get("text", ""),
                    pages=defn.get("pages", [])
                ))

            return RPExtraction(
                dividend_prohibition=dividend_prohibition,
                permitted_baskets=permitted_baskets,
                rdp_restrictions=rdp_restrictions,
                definitions=definitions,
                raw_json=data
            )
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error in content extraction: {e}")
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
                return []

            # Clean JSON
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
                    source_text=item.get("source_text", ""),
                    source_pages=item.get("source_pages", []),
                    confidence=item.get("confidence", "medium"),
                    reasoning=item.get("reasoning")
                ))

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
        """
        Store extraction results to TypeDB.

        - Creates rp_provision entity if not exists
        - Stores scalar answers as provision attributes
        - Stores multiselect answers as concept_applicability relations
        """
        if not typedb_client.driver:
            logger.error("TypeDB not connected, cannot store results")
            return False

        provision_id = f"{deal_id}_rp"

        try:
            from typedb.driver import TransactionType

            # Step 5a: Create rp_provision if not exists
            self._ensure_provision_exists(deal_id, provision_id)

            # Step 5b & 5c: Store answers
            tx = typedb_client.driver.transaction(
                settings.typedb_database, TransactionType.WRITE
            )
            try:
                scalar_count = 0
                multiselect_count = 0

                for cat_answers in category_answers:
                    for answer in cat_answers.answers:
                        if answer.value is None or answer.confidence == "not_found":
                            continue

                        if isinstance(answer.value, list):
                            # Multiselect answer → concept_applicability
                            for concept_id in answer.value:
                                self._store_concept_applicability(
                                    tx, provision_id, answer.attribute_name,
                                    concept_id, answer.source_text,
                                    answer.source_pages[0] if answer.source_pages else 0
                                )
                                multiselect_count += 1
                        else:
                            # Scalar answer → provision attribute
                            self._store_scalar_attribute(
                                tx, provision_id, answer.attribute_name,
                                answer.value, answer.answer_type
                            )
                            scalar_count += 1

                tx.commit()
                logger.info(f"Stored {scalar_count} scalar, {multiselect_count} multiselect answers")
                return True

            except Exception as e:
                tx.close()
                logger.error(f"Error storing answers: {e}")
                return False

        except Exception as e:
            logger.error(f"Storage error: {e}")
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

    def _store_scalar_attribute(
        self,
        tx,
        provision_id: str,
        attribute_name: str,
        value: Any,
        answer_type: str
    ):
        """Store a scalar answer as a provision attribute."""
        # Format value based on type
        formatted_value = self._format_typedb_value(value, answer_type)
        if formatted_value is None:
            logger.warning(f"Skipping {attribute_name}: could not format value {value}")
            return

        try:
            query = f"""
                match $p isa rp_provision, has provision_id "{provision_id}";
                insert $p has {attribute_name} {formatted_value};
            """
            tx.query(query).resolve()
            logger.debug(f"Stored {attribute_name} = {formatted_value}")
        except Exception as e:
            # Attribute may already exist or not be defined in schema
            logger.warning(f"Could not store {attribute_name}: {e}")

    def _store_concept_applicability(
        self,
        tx,
        provision_id: str,
        concept_type: str,
        concept_id: str,
        source_text: str,
        source_page: int
    ):
        """Store a multiselect answer as a concept_applicability relation."""
        # Escape source text for TypeQL
        escaped_text = source_text.replace('\\', '\\\\').replace('"', '\\"')[:500]

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
            logger.debug(f"Stored applicability: {concept_type}/{concept_id}")
        except Exception as e:
            logger.warning(f"Could not store applicability for {concept_id}: {e}")

    def _format_typedb_value(self, value: Any, answer_type: str) -> Optional[str]:
        """Format a Python value for TypeQL insertion."""
        if value is None:
            return None

        if answer_type == "boolean":
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, str):
                return "true" if value.lower() in ("true", "yes", "1") else "false"
            return None

        if answer_type in ("integer",):
            try:
                return str(int(value))
            except (ValueError, TypeError):
                return None

        if answer_type in ("double", "percentage", "currency"):
            try:
                return str(float(value))
            except (ValueError, TypeError):
                return None

        if answer_type in ("string", "text"):
            escaped = str(value).replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'

        # Default: try as string
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
        Full RP extraction pipeline.

        1. Parse PDF
        2. Extract ALL RP content (ONE Claude call)
        3. Load questions from TypeDB (if not provided)
        4. Answer questions by category
        5. Store results to TypeDB (if store_results=True)
        """
        start_time = time.time()

        # Step 1: Parse PDF
        document_text = self.parse_document(pdf_path)

        # Step 2: Extract ALL RP content (ONE Claude call)
        logger.info("Step 2: Extracting RP content...")
        extracted_content = self.extract_rp_content(document_text)
        logger.info(
            f"Extracted: prohibition={extracted_content.dividend_prohibition is not None}, "
            f"baskets={len(extracted_content.permitted_baskets)}, "
            f"definitions={len(extracted_content.definitions)}"
        )

        # Step 3: Load questions from TypeDB
        if questions_by_category is None:
            logger.info("Step 3: Loading questions from TypeDB...")
            questions_by_category = self.load_questions_by_category("RP")

        # Step 4: Answer questions by category
        logger.info(f"Step 4: Answering {len(questions_by_category)} categories...")
        category_answers = []

        for cat_id, questions in questions_by_category.items():
            cat_name = questions[0]["category_name"] if questions else cat_id
            logger.info(f"  {cat_name}: {len(questions)} questions")

            answers = self.answer_category_questions(
                category_id=cat_id,
                category_name=cat_name,
                questions=questions,
                extracted_content=extracted_content
            )
            category_answers.append(answers)

        # Step 5: Store results to TypeDB
        if store_results:
            logger.info("Step 5: Storing results to TypeDB...")
            self.store_extraction_result(deal_id, category_answers)

        extraction_time = time.time() - start_time
        logger.info(f"Extraction complete in {extraction_time:.1f}s")

        return ExtractionResult(
            deal_id=deal_id,
            covenant_type="RP",
            extracted_content=extracted_content,
            category_answers=category_answers,
            extraction_time_seconds=extraction_time
        )


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
