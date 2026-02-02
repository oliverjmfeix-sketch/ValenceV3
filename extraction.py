"""
Claude Extraction Service.

Extracts TYPED PRIMITIVES from credit agreements.
Each extracted value has:
- attribute_name (maps to TypeDB attribute)
- value (typed: boolean, integer, double, string)
- source_text (the quote from the document)
- source_page (page number)
- source_section (section reference if found)

NO JSON BLOBS - outputs structured primitives.
"""
import json
import logging
import time
from typing import List, Tuple, Optional

from anthropic import Anthropic

from app.config import settings
from app.schemas.models import ExtractedPrimitive, ExtractionResult
from app.services.pdf_parser import PDFParser, get_pdf_parser

logger = logging.getLogger(__name__)


class ExtractionService:
    """
    Extract typed primitives from credit agreements using Claude.
    
    The extraction prompt asks Claude to output structured primitives
    with source provenance for each value extracted.
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
        mfn_primitives = await self._extract_mfn(full_text)
        
        # Extract RP primitives
        logger.info("Extracting RP primitives...")
        rp_primitives = await self._extract_rp(full_text)
        
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
    
    async def _extract_mfn(self, document_text: str) -> List[ExtractedPrimitive]:
        """Extract MFN provision primitives."""
        prompt = self._build_mfn_prompt(document_text)
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = response.content[0].text
            return self._parse_extraction_response(response_text)
            
        except Exception as e:
            logger.error(f"MFN extraction error: {e}")
            return []
    
    async def _extract_rp(self, document_text: str) -> List[ExtractedPrimitive]:
        """Extract RP provision primitives."""
        prompt = self._build_rp_prompt(document_text)
        
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = response.content[0].text
            return self._parse_extraction_response(response_text)
            
        except Exception as e:
            logger.error(f"RP extraction error: {e}")
            return []
    
    def _build_mfn_prompt(self, document_text: str) -> str:
        """Build the MFN extraction prompt."""
        return f"""You are a legal document analyst extracting Most Favored Nation (MFN) provision details from a credit agreement.

Extract TYPED PRIMITIVES with source provenance. For each fact you extract, provide:
- attribute_name: The exact attribute name from the list below
- value: The typed value (boolean true/false, integer, or string)
- source_text: The exact quote from the document supporting this answer
- source_page: The page number where you found this
- confidence: "high", "medium", or "low"

## ATTRIBUTES TO EXTRACT

### Existence
- mfn_exists (boolean): Does an MFN provision exist?
- mfn_section_reference (string): Section reference (e.g., "Section 2.14(d)")

### Sunset
- sunset_exists (boolean): Is there a sunset provision?
- sunset_period_months (integer): Sunset period in months
- sunset_tied_to_maturity (boolean): Is sunset tied to maturity date?

### Threshold
- threshold_bps (integer): MFN threshold in basis points (e.g., 50 = 0.50%)
- threshold_applies_to_margin_only (boolean): Does threshold apply only to margin?
- threshold_applies_to_all_in_yield (boolean): Does threshold apply to all-in yield?

### Yield Components
- oid_included_in_yield (boolean): Is OID included in yield calculation?
- floor_included_in_yield (boolean): Is interest rate floor included?
- upfront_fees_included_in_yield (boolean): Are upfront fees included?

### Debt Coverage
- covers_term_loan_a (boolean): Does MFN cover Term Loan A?
- covers_term_loan_b (boolean): Does MFN cover Term Loan B?
- covers_incremental_facilities (boolean): Does MFN cover incremental facilities?
- covers_ratio_debt (boolean): Does MFN cover ratio debt?

### Exclusions
- excludes_acquisition_debt (boolean): Is acquisition debt excluded?
- excludes_bridge_loans (boolean): Are bridge loans excluded?

## DOCUMENT

{document_text[:200000]}

## OUTPUT FORMAT

Return a JSON array of primitives:
```json
[
  {{
    "attribute_name": "mfn_exists",
    "value": true,
    "source_text": "Section 2.14(d) provides that if any Incremental Term Loan...",
    "source_page": 47,
    "confidence": "high"
  }},
  {{
    "attribute_name": "threshold_bps",
    "value": 50,
    "source_text": "...exceeds the Applicable Rate by more than 50 basis points...",
    "source_page": 47,
    "confidence": "high"
  }}
]
```

Extract ALL available primitives. If a primitive cannot be determined from the document, omit it.
Return ONLY the JSON array, no other text."""
    
    def _build_rp_prompt(self, document_text: str) -> str:
        """Build the RP extraction prompt."""
        return f"""You are a legal document analyst extracting Restricted Payments covenant details from a credit agreement.

Extract TYPED PRIMITIVES with source provenance. Focus on dividend restrictions, J.Crew risk factors, and builder baskets.

## ATTRIBUTES TO EXTRACT

### Existence
- rp_exists (boolean): Does a Restricted Payments covenant exist?
- dividend_covenant_section (string): Section reference for dividends

### Dividend Scope
- dividend_applies_to_holdings (boolean): Does covenant apply to Holdings?
- dividend_applies_to_borrower (boolean): Does covenant apply to Borrower?

### Builder Basket
- builder_basket_exists (boolean): Is there a builder/cumulative basket?
- builder_starter_amount_usd (double): Starter amount in USD (e.g., 50000000.0)
- builder_starter_ebitda_pct (double): Starter as % of EBITDA (e.g., 0.15 for 15%)
- builder_includes_retained_ecf (boolean): Does builder include retained ECF?
- builder_includes_sub_returns (boolean): Does builder include subsidiary returns?

### J.Crew Risk - Unrestricted Subsidiaries
- unrestricted_sub_designation_permitted (boolean): Can subs be designated unrestricted?
- unrestricted_sub_requires_no_default (boolean): Does designation require no Default?
- unrestricted_sub_requires_no_payment_default (boolean): Does designation require no Payment Default only?
- unrestricted_sub_has_ebitda_cap (boolean): Is there an EBITDA cap on unrestricted subs?
- unrestricted_sub_ebitda_cap_pct (double): EBITDA cap percentage

### J.Crew Risk - IP Transfers
- ip_transfers_to_subs_permitted (boolean): Are IP transfers to subsidiaries permitted?
- ip_transfers_require_fair_value (boolean): Do IP transfers require fair value?

### J.Crew Blocker
- jcrew_blocker_present (boolean): Is there a J.Crew blocker?
- jcrew_blocker_covers_ip (boolean): Does blocker cover IP specifically?
- jcrew_blocker_covers_material_assets (boolean): Does blocker cover material assets?
- jcrew_blocker_binds_loan_parties (boolean): Does blocker bind Loan Parties?
- jcrew_blocker_binds_restricted_subs (boolean): Does blocker bind Restricted Subs?

### IP Definition Quality
- ip_definition_includes_trademarks (boolean): Does IP definition include trademarks?
- ip_definition_includes_patents (boolean): Does IP definition include patents?
- ip_definition_includes_trade_secrets (boolean): Does IP definition include trade secrets?
- ip_definition_includes_know_how (boolean): Does IP definition include know-how?

### Ratio Dividend Basket
- ratio_dividend_basket_exists (boolean): Is there a leverage-based unlimited basket?
- ratio_dividend_leverage_threshold (double): Leverage ratio threshold (e.g., 3.0)
- ratio_dividend_is_unlimited (boolean): Is basket unlimited if ratio satisfied?

### General Basket
- general_dividend_basket_usd (double): General basket dollar amount
- general_dividend_basket_ebitda_pct (double): General basket as % of EBITDA

## DOCUMENT

{document_text[:200000]}

## OUTPUT FORMAT

Return a JSON array of primitives:
```json
[
  {{
    "attribute_name": "unrestricted_sub_designation_permitted",
    "value": true,
    "source_text": "The Borrower may designate any Restricted Subsidiary as an Unrestricted Subsidiary...",
    "source_page": 89,
    "confidence": "high"
  }},
  {{
    "attribute_name": "jcrew_blocker_present",
    "value": false,
    "source_text": null,
    "source_page": null,
    "confidence": "high"
  }}
]
```

Extract ALL available primitives. If a primitive cannot be determined, omit it.
Return ONLY the JSON array, no other text."""
    
    def _parse_extraction_response(
        self, 
        response_text: str
    ) -> List[ExtractedPrimitive]:
        """Parse Claude's JSON response into ExtractedPrimitive objects."""
        primitives = []
        
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
                if not item.get("attribute_name"):
                    continue
                
                primitives.append(ExtractedPrimitive(
                    attribute_name=item["attribute_name"],
                    value=item.get("value"),
                    source_text=item.get("source_text") or "",
                    source_page=item.get("source_page") or 0,
                    source_section=item.get("source_section"),
                    confidence=item.get("confidence", "medium")
                ))
            
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
