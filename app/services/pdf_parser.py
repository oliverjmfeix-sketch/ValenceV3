"""
PDF Parser for Credit Agreement Documents.

Uses PyMuPDF (fitz) for text extraction - faster than pdfplumber.
Includes page markers for source tracking.
"""
import re
import logging
from dataclasses import dataclass
from typing import List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Content from a single PDF page."""
    page_number: int
    text: str


class PDFParser:
    """
    Extract text from PDF with page tracking.
    
    Uses PyMuPDF (fitz) which is 10-20x faster than pdfplumber
    for large documents.
    """
    
    def __init__(self):
        self.section_pattern = re.compile(
            r'Section\s+(\d+\.?\d*[a-z]?)\s*[:\.]?\s*([^\n]+)?',
            re.IGNORECASE
        )
    
    def extract_pages(self, pdf_path: str) -> List[PageContent]:
        """Extract text from each page of the PDF."""
        pages = []
        
        try:
            doc = fitz.open(pdf_path)
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text()
                
                pages.append(PageContent(
                    page_number=page_num + 1,  # 1-indexed
                    text=text
                ))
            
            doc.close()
            logger.info(f"Extracted {len(pages)} pages from {pdf_path}")
            return pages
            
        except Exception as e:
            logger.error(f"Error extracting PDF: {e}")
            raise
    
    def get_full_text(self, pages: List[PageContent]) -> str:
        """
        Combine all pages into a single text with page markers.
        
        Page markers help Claude identify source locations.
        """
        parts = []
        for page in pages:
            parts.append(f"\n[PAGE {page.page_number}]\n")
            parts.append(page.text)
        
        return '\n'.join(parts)
    
    def find_section(
        self,
        pages: List[PageContent],
        section_id: str
    ) -> Optional[tuple]:
        """
        Find a section by ID.
        
        Returns:
            Tuple of (page_number, section_text) or None
        """
        for page in pages:
            if section_id.lower() in page.text.lower():
                # Find the section start
                match = re.search(
                    rf'Section\s+{re.escape(section_id)}[:\.]?\s*([^\n]+)?',
                    page.text,
                    re.IGNORECASE
                )
                
                if match:
                    # Extract text from section start to next section or page end
                    start_pos = match.start()
                    section_text = page.text[start_pos:start_pos + 5000]  # Limit
                    
                    return (page.page_number, section_text)
        
        return None
    
    def extract_definitions_section(
        self,
        pages: List[PageContent]
    ) -> Optional[str]:
        """
        Extract the definitions section (usually Section 1.01).
        
        The definitions section is critical for understanding
        how terms like "Intellectual Property" are defined.
        """
        definitions_text = []
        in_definitions = False
        
        for page in pages:
            text = page.text
            
            # Check for definitions start
            if re.search(r'Section\s+1\.01[:\.]?\s*(Defined Terms|Definitions)', 
                        text, re.IGNORECASE):
                in_definitions = True
            
            if in_definitions:
                definitions_text.append(f"[PAGE {page.page_number}]\n{text}")
                
                # Check for end of definitions
                if re.search(r'Section\s+1\.02', text):
                    break
        
        if definitions_text:
            return '\n'.join(definitions_text)
        
        return None
    
    def get_page_count(self, pdf_path: str) -> int:
        """Get total page count of PDF."""
        try:
            doc = fitz.open(pdf_path)
            count = len(doc)
            doc.close()
            return count
        except Exception as e:
            logger.error(f"Error getting page count: {e}")
            return 0


# Global parser instance
pdf_parser = PDFParser()


def get_pdf_parser() -> PDFParser:
    """Dependency injection for PDF parser."""
    return pdf_parser
