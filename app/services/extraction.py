"""
Claude Extraction Service - SSoT Compliant.

Extracts TYPED PRIMITIVES from credit agreements.
Field names are loaded dynamically from TypeDB ontology questions,
ensuring the extraction prompt always matches the schema.

Each extracted value has:
- attribute_name (from question_targets_field.target_field_name)
- value (typed: boolean, integer, double, string)
- source_text (the quote from the document)
- source_page (page number)
- confidence (high/medium/low)
"""
import json
import logging
import time
from typing import List, Dict, Optional
from dataclasses import dataclass

from anthropic import Anthropic

from app.config import settings
from app.schemas.models import ExtractedPrimitive, ExtractionResult
from app.services.pdf_parser import PDFParser, get_pdf_parser
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


@dataclass
class OntologyField:
    """A field to extract, loaded from TypeDB ontology."""
    question_id: str
    question_text: str
    target_field_name: str
    target_entity_type: str
    answer_type: str


class ExtractionService:
    """
    Extract typed primitives from credit agreements using Claude.

    Field names are loaded dynamically from TypeDB ontology questions,
    ensuring SSoT compliance - the prompt always matches the schema.
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
        self._field_cache: Dict[str, List[OntologyField]] = {}

    def _load_ontology_fields(self, covenant_type: str) -> List[OntologyField]:
        """
        Load extraction fields from TypeDB ontology.

        Queries question_targets_field to get exact field names from SSoT.
        """
        cache_key = covenant_type
        if cache_key in self._field_cache:
            return self._field_cache[cache_key]

        if not typedb_client.driver:
            logger.warning("TypeDB not connected, using empty field list")
            return []

        try:
            from typedb.driver import TransactionType
            tx = typedb_client.driver.transaction(
                settings.typedb_database,
                TransactionType.READ
            )
            try:
                # Query for questions with field targets (not concept targets)
                query = f"""
                    match
                        $q isa ontology_question,
                            has question_id $qid,
                            has question_text $qt,
                            has answer_type $at,
                            has covenant_type "{covenant_type}";
                        (question: $q) isa question_targets_field,
                            has target_field_name $fn,
                            has target_entity_type $et;
                    select $qid, $qt, $fn, $et, $at;
                """

                result = tx.query(query).resolve()
                fields = []

                for row in result.as_concept_rows():
                    fields.append(OntologyField(
                        question_id=row.get("qid").as_attribute().get_value(),
                        question_text=row.get("qt").as_attribute().get_value(),
                        target_field_name=row.get("fn").as_attribute().get_value(),
                        target_entity_type=row.get("et").as_attribute().get_value(),
                        answer_type=row.get("at").as_attribute().get_value()
                    ))

                logger.info(f"Loaded {len(fields)} {covenant_type} fields from ontology")
                self._field_cache[cache_key] = fields
                return fields

            finally:
                tx.close()

        except Exception as e:
            logger.error(f"Error loading ontology fields: {e}")
            return []

    async def extract_document(
        self,
        pdf_path: str,
        deal_id: str
    ) -> ExtractionResult:
        """
        Extract all primitives from a credit agreement.

        Returns:
            ExtractionResult with MFN and RP primitives
        """
        start_time = time.time()

        # Parse PDF
        logger.info(f"Parsing PDF: {pdf_path}")
        pages = self.parser.extract_pages(pdf_path)
        full_text = self.parser.get_full_text(pages)

        logger.info(f"Extracted {len(pages)} pages, {len(full_text)} chars")

        # Extract MFN primitives
        logger.info("Extracting MFN primitives...")
        mfn_primitives = await self._extract_covenant(full_text, "MFN")

        # Extract RP primitives
        logger.info("Extracting RP primitives...")
        rp_primitives = await self._extract_covenant(full_text, "RP")

        extraction_time = time.time() - start_time

        logger.info(
            f"Extraction complete: {len(mfn_primitives)} MFN, "
            f"{len(rp_primitives)} RP primitives in {extraction_time:.1f}s"
        )

        return ExtractionResult(
            deal_id=deal_id,
            mfn_primitives=mfn_primitives,
            rp_primitives=rp_primitives,
            extraction_time_seconds=extraction_time
        )

    async def _extract_covenant(
        self,
        document_text: str,
        covenant_type: str
    ) -> List[ExtractedPrimitive]:
        """Extract primitives for a covenant type using ontology-driven prompt."""

        # Load fields from TypeDB ontology
        fields = self._load_ontology_fields(covenant_type)

        if not fields:
            logger.warning(f"No {covenant_type} fields found in ontology")
            return []

        # Build prompt dynamically from ontology
        prompt = self._build_prompt(document_text, covenant_type, fields)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text
            return self._parse_extraction_response(response_text, fields)

        except Exception as e:
            logger.error(f"{covenant_type} extraction error: {e}")
            return []

    def _build_prompt(
        self,
        document_text: str,
        covenant_type: str,
        fields: List[OntologyField]
    ) -> str:
        """Build extraction prompt dynamically from ontology fields."""

        covenant_desc = {
            "MFN": "Most Favored Nation (MFN) provision",
            "RP": "Restricted Payments covenant (dividends and debt payments)"
        }.get(covenant_type, covenant_type)

        # Build field list from ontology
        field_lines = []
        for f in fields:
            type_hint = self._get_type_hint(f.answer_type)
            field_lines.append(f"- {f.target_field_name} ({type_hint}): {f.question_text}")

        fields_section = "\n".join(field_lines)

        return f"""You are a legal document analyst extracting {covenant_desc} details from a credit agreement.

Extract TYPED PRIMITIVES with source provenance. For each fact you extract, provide:
- attribute_name: The EXACT attribute name from the list below (must match exactly)
- value: The typed value (boolean true/false, integer, double, or string)
- source_text: The exact quote from the document supporting this answer
- source_page: The page number where you found this (look for [PAGE N] markers)
- confidence: "high", "medium", or "low"

## ATTRIBUTES TO EXTRACT (use these EXACT names)

{fields_section}

## DOCUMENT

{document_text[:200000]}

## OUTPUT FORMAT

Return a JSON array of primitives:
```json
[
  {{
    "attribute_name": "example_field_name",
    "value": true,
    "source_text": "The exact quote from the document...",
    "source_page": 47,
    "confidence": "high"
  }}
]
```

IMPORTANT:
- Use ONLY the exact attribute_name values listed above
- Extract ALL primitives you can find evidence for
- If a primitive cannot be determined from the document, omit it
- Return ONLY the JSON array, no other text"""

    def _get_type_hint(self, answer_type: str) -> str:
        """Convert answer_type to prompt type hint."""
        type_map = {
            "boolean": "boolean",
            "integer": "integer",
            "double": "double",
            "currency": "double",
            "percentage": "double",
            "string": "string",
            "text": "string",
            "multiselect": "skip"  # Handled separately via concept_applicability
        }
        return type_map.get(answer_type, "string")

    def _parse_extraction_response(
        self,
        response_text: str,
        fields: List[OntologyField]
    ) -> List[ExtractedPrimitive]:
        """Parse Claude's JSON response into ExtractedPrimitive objects."""
        primitives = []

        # Build set of valid field names for validation
        valid_fields = {f.target_field_name for f in fields}

        try:
            # Find JSON array in response
            start = response_text.find('[')
            end = response_text.rfind(']') + 1

            if start == -1 or end == 0:
                logger.warning("No JSON array found in response")
                return []

            json_str = response_text[start:end]
            data = json.loads(json_str)

            for item in data:
                attr_name = item.get("attribute_name")
                if not attr_name:
                    continue

                # Validate field name against ontology
                if attr_name not in valid_fields:
                    logger.warning(f"Skipping unknown field: {attr_name}")
                    continue

                primitives.append(ExtractedPrimitive(
                    attribute_name=attr_name,
                    value=item.get("value"),
                    source_text=item.get("source_text") or "",
                    source_page=item.get("source_page") or 0,
                    source_section=item.get("source_section"),
                    confidence=item.get("confidence", "medium")
                ))

            logger.info(f"Parsed {len(primitives)} valid primitives")
            return primitives

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response text: {response_text[:500]}")
            return []
        except Exception as e:
            logger.error(f"Error parsing extraction response: {e}")
            return []


# Global extraction service instance
extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
