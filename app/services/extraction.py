"""
Claude Extraction Service - SSoT Compliant.

Extracts TYPED PRIMITIVES from credit agreements.
Field names are loaded dynamically from TypeDB ontology questions,
ensuring the extraction prompt always matches the schema.

Supports two question types:
- Scalar questions → question_targets_field → provision attributes
- Multiselect questions → question_targets_concept → concept_applicability relations
"""
import json
import logging
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from anthropic import Anthropic

from app.config import settings
from app.schemas.models import ExtractedPrimitive, ExtractionResult, MultiselectAnswer as MultiselectAnswerModel
from app.services.pdf_parser import PDFParser, get_pdf_parser
from app.services.typedb_client import typedb_client

logger = logging.getLogger(__name__)


@dataclass
class OntologyField:
    """A scalar field to extract, loaded from TypeDB ontology."""
    question_id: str
    question_text: str
    target_field_name: str
    target_entity_type: str
    answer_type: str


@dataclass
class ConceptOption:
    """A concept instance that can be selected in a multiselect."""
    concept_id: str
    name: str


@dataclass
class MultiselectField:
    """A multiselect field to extract, loaded from TypeDB ontology."""
    question_id: str
    question_text: str
    target_concept_type: str
    options: List[ConceptOption] = field(default_factory=list)


@dataclass
class MultiselectAnswer:
    """Answer to a multiselect question."""
    concept_type: str
    included: List[str]
    excluded: List[str]
    source_text: str
    source_page: int


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
        self._scalar_cache: Dict[str, List[OntologyField]] = {}
        self._multiselect_cache: Dict[str, List[MultiselectField]] = {}

    def _load_scalar_fields(self, covenant_type: str) -> List[OntologyField]:
        """Load scalar extraction fields from TypeDB ontology."""
        cache_key = covenant_type
        if cache_key in self._scalar_cache:
            return self._scalar_cache[cache_key]

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

                logger.info(f"Loaded {len(fields)} scalar {covenant_type} fields from ontology")
                self._scalar_cache[cache_key] = fields
                return fields

            finally:
                tx.close()

        except Exception as e:
            logger.error(f"Error loading scalar ontology fields: {e}")
            return []

    def _load_multiselect_fields(self, covenant_type: str) -> List[MultiselectField]:
        """Load multiselect fields and their concept options from TypeDB ontology."""
        cache_key = covenant_type
        if cache_key in self._multiselect_cache:
            return self._multiselect_cache[cache_key]

        if not typedb_client.driver:
            logger.warning("TypeDB not connected, using empty multiselect list")
            return []

        try:
            from typedb.driver import TransactionType
            tx = typedb_client.driver.transaction(
                settings.typedb_database,
                TransactionType.READ
            )
            try:
                # Get multiselect questions
                query = f"""
                    match
                        $q isa ontology_question,
                            has question_id $qid,
                            has question_text $qt,
                            has covenant_type "{covenant_type}";
                        (question: $q) isa question_targets_concept,
                            has target_concept_type $ct;
                    select $qid, $qt, $ct;
                """

                result = tx.query(query).resolve()
                fields = []

                for row in result.as_concept_rows():
                    concept_type = row.get("ct").as_attribute().get_value()

                    ms_field = MultiselectField(
                        question_id=row.get("qid").as_attribute().get_value(),
                        question_text=row.get("qt").as_attribute().get_value(),
                        target_concept_type=concept_type,
                        options=[]
                    )

                    # Load concept options for this type
                    options_query = f"""
                        match
                            $c isa {concept_type},
                                has concept_id $cid,
                                has name $name;
                        select $cid, $name;
                    """

                    try:
                        options_result = tx.query(options_query).resolve()
                        for opt_row in options_result.as_concept_rows():
                            ms_field.options.append(ConceptOption(
                                concept_id=opt_row.get("cid").as_attribute().get_value(),
                                name=opt_row.get("name").as_attribute().get_value()
                            ))
                    except Exception as e:
                        logger.warning(f"Error loading options for {concept_type}: {e}")

                    fields.append(ms_field)

                logger.info(f"Loaded {len(fields)} multiselect {covenant_type} fields from ontology")
                self._multiselect_cache[cache_key] = fields
                return fields

            finally:
                tx.close()

        except Exception as e:
            logger.error(f"Error loading multiselect ontology fields: {e}")
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
        mfn_result = await self._extract_covenant(full_text, "MFN", deal_id)

        # Extract RP primitives
        logger.info("Extracting RP primitives...")
        rp_result = await self._extract_covenant(full_text, "RP", deal_id)

        extraction_time = time.time() - start_time

        logger.info(
            f"Extraction complete: {len(mfn_result['primitives'])} MFN, "
            f"{len(rp_result['primitives'])} RP primitives, "
            f"{len(mfn_result['multiselect'])} MFN multiselect, "
            f"{len(rp_result['multiselect'])} RP multiselect in {extraction_time:.1f}s"
        )

        return ExtractionResult(
            deal_id=deal_id,
            mfn_primitives=mfn_result['primitives'],
            rp_primitives=rp_result['primitives'],
            mfn_multiselect=mfn_result['multiselect'],
            rp_multiselect=rp_result['multiselect'],
            extraction_time_seconds=extraction_time
        )

    async def _extract_covenant(
        self,
        document_text: str,
        covenant_type: str,
        deal_id: str
    ) -> Dict[str, Any]:
        """Extract primitives and multiselect answers for a covenant type."""

        # Load fields from TypeDB ontology
        scalar_fields = self._load_scalar_fields(covenant_type)
        multiselect_fields = self._load_multiselect_fields(covenant_type)

        if not scalar_fields and not multiselect_fields:
            logger.warning(f"No {covenant_type} fields found in ontology")
            return {'primitives': [], 'multiselect': []}

        # Build combined prompt
        prompt = self._build_combined_prompt(
            document_text, covenant_type, scalar_fields, multiselect_fields
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                messages=[{"role": "user", "content": prompt}]
            )

            response_text = response.content[0].text

            # Parse response
            primitives, multiselect_answers = self._parse_combined_response(
                response_text, scalar_fields, multiselect_fields
            )

            # Convert internal MultiselectAnswer to model for return
            multiselect_models = [
                MultiselectAnswerModel(
                    concept_type=ans.concept_type,
                    included=ans.included,
                    excluded=ans.excluded,
                    source_text=ans.source_text,
                    source_page=ans.source_page
                )
                for ans in multiselect_answers
            ]

            return {
                'primitives': primitives,
                'multiselect': multiselect_models
            }

        except Exception as e:
            logger.error(f"{covenant_type} extraction error: {e}")
            return {'primitives': [], 'multiselect': []}

    def _build_combined_prompt(
        self,
        document_text: str,
        covenant_type: str,
        scalar_fields: List[OntologyField],
        multiselect_fields: List[MultiselectField]
    ) -> str:
        """Build extraction prompt with both scalar and multiselect questions."""

        covenant_desc = {
            "MFN": "Most Favored Nation (MFN) provision",
            "RP": "Restricted Payments covenant (dividends and debt payments)"
        }.get(covenant_type, covenant_type)

        # Build scalar field list
        scalar_lines = []
        for f in scalar_fields:
            type_hint = self._get_type_hint(f.answer_type)
            scalar_lines.append(f"- {f.target_field_name} ({type_hint}): {f.question_text}")
        scalar_section = "\n".join(scalar_lines) if scalar_lines else "(none)"

        # Build multiselect field list
        multiselect_lines = []
        for f in multiselect_fields:
            option_ids = [opt.concept_id for opt in f.options]
            options_str = ", ".join(option_ids) if option_ids else "(no options defined)"
            multiselect_lines.append(f"- {f.target_concept_type}: {f.question_text}")
            multiselect_lines.append(f"  Options: {options_str}")
        multiselect_section = "\n".join(multiselect_lines) if multiselect_lines else "(none)"

        return f"""You are a legal document analyst extracting {covenant_desc} details from a credit agreement.

## DOCUMENT

{document_text[:200000]}

## EXTRACTION INSTRUCTIONS

Extract information for both SCALAR questions and MULTISELECT questions.

### SCALAR QUESTIONS
For each scalar question, provide:
- attribute_name: The EXACT attribute name (must match exactly)
- value: The typed value (boolean true/false, integer, double, or string)
- source_text: The exact quote from the document supporting this answer
- source_page: The page number (look for [PAGE N] markers)
- confidence: "high", "medium", or "low"

SCALAR FIELDS TO EXTRACT:
{scalar_section}

### MULTISELECT QUESTIONS
For each multiselect question, identify which concept_ids from the options list apply.
Return ONLY concept_ids that are explicitly mentioned or clearly implied in the document.
If a concept is NOT mentioned, do not include it.

MULTISELECT FIELDS TO EXTRACT:
{multiselect_section}

## OUTPUT FORMAT

Return a JSON object with this EXACT structure:
```json
{{
  "scalar_answers": [
    {{
      "attribute_name": "example_field",
      "value": true,
      "source_text": "The exact quote...",
      "source_page": 45,
      "confidence": "high"
    }}
  ],
  "multiselect_answers": {{
    "concept_type_name": {{
      "included": ["concept_id_1", "concept_id_2"],
      "source_text": "The exact quote showing these apply...",
      "source_page": 48
    }}
  }}
}}
```

IMPORTANT:
- Use ONLY the exact attribute_name values listed above for scalar questions
- Use ONLY the exact concept_id values from the Options lists for multiselect questions
- Extract ALL information you can find evidence for
- If a question cannot be answered from the document, omit it entirely
- Return ONLY the JSON object, no other text"""

    def _get_type_hint(self, answer_type: str) -> str:
        """Convert answer_type to prompt type hint."""
        type_map = {
            "boolean": "boolean",
            "integer": "integer",
            "double": "double",
            "currency": "double",
            "percentage": "double",
            "number": "double",
            "string": "string",
            "text": "string"
        }
        return type_map.get(answer_type, "string")

    def _parse_combined_response(
        self,
        response_text: str,
        scalar_fields: List[OntologyField],
        multiselect_fields: List[MultiselectField]
    ) -> tuple:
        """Parse Claude's JSON response into primitives and multiselect answers."""
        primitives = []
        multiselect_answers = []

        # Build validation sets
        valid_scalar_fields = {f.target_field_name for f in scalar_fields}
        valid_concept_types = {f.target_concept_type for f in multiselect_fields}
        valid_concept_ids = {}
        for f in multiselect_fields:
            valid_concept_ids[f.target_concept_type] = {opt.concept_id for opt in f.options}

        try:
            # Find JSON object in response
            start = response_text.find('{')
            end = response_text.rfind('}') + 1

            if start == -1 or end == 0:
                logger.warning("No JSON object found in response")
                return [], []

            json_str = response_text[start:end]
            data = json.loads(json_str)

            # Parse scalar answers
            for item in data.get("scalar_answers", []):
                attr_name = item.get("attribute_name")
                if not attr_name:
                    continue

                if attr_name not in valid_scalar_fields:
                    logger.warning(f"Skipping unknown scalar field: {attr_name}")
                    continue

                primitives.append(ExtractedPrimitive(
                    attribute_name=attr_name,
                    value=item.get("value"),
                    source_text=item.get("source_text") or "",
                    source_page=item.get("source_page") or 0,
                    source_section=item.get("source_section"),
                    confidence=item.get("confidence", "medium")
                ))

            # Parse multiselect answers
            for concept_type, answer_data in data.get("multiselect_answers", {}).items():
                if concept_type not in valid_concept_types:
                    logger.warning(f"Skipping unknown concept type: {concept_type}")
                    continue

                included_ids = answer_data.get("included", [])
                valid_ids = valid_concept_ids.get(concept_type, set())

                # Filter to only valid concept IDs
                validated_included = [cid for cid in included_ids if cid in valid_ids]
                invalid_ids = [cid for cid in included_ids if cid not in valid_ids]

                if invalid_ids:
                    logger.warning(f"Skipping invalid concept_ids for {concept_type}: {invalid_ids}")

                if validated_included:
                    multiselect_answers.append(MultiselectAnswer(
                        concept_type=concept_type,
                        included=validated_included,
                        excluded=answer_data.get("excluded", []),
                        source_text=answer_data.get("source_text", ""),
                        source_page=answer_data.get("source_page", 0)
                    ))

            logger.info(f"Parsed {len(primitives)} scalar, {len(multiselect_answers)} multiselect answers")
            return primitives, multiselect_answers

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response text: {response_text[:500]}")
            return [], []
        except Exception as e:
            logger.error(f"Error parsing extraction response: {e}")
            return [], []

# Global extraction service instance
extraction_service = ExtractionService()


def get_extraction_service() -> ExtractionService:
    """Dependency injection for extraction service."""
    return extraction_service
