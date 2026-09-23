from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import pymupdf
import pytesseract
from docx import Document as DocxDocument
from PIL import Image
from pypdf import PdfReader


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class ParsedChunk:
    text: str
    page_number: int | None
    section_name: str | None
    extraction_method: str


class OcrFallback(Protocol):
    def extract(self, content: bytes) -> list[ParsedChunk]: ...


class TesseractPdfOcrFallback:
    def extract(self, content: bytes) -> list[ParsedChunk]:
        pdf = pymupdf.open(stream=content, filetype="pdf")
        chunks: list[ParsedChunk] = []
        for index, page in enumerate(pdf, start=1):
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            text = pytesseract.image_to_string(
                Image.open(BytesIO(pixmap.tobytes("png"))), config="--psm 6"
            ).strip()
            if text:
                chunks.append(ParsedChunk(text, index, None, "ocr"))
        if not chunks:
            raise ParseError("No readable text was found after OCR.")
        return chunks


def parse_document(
    content: bytes, content_type: str, ocr: OcrFallback | None = None
) -> list[ParsedChunk]:
    if content_type == "application/pdf":
        reader = PdfReader(BytesIO(content))
        chunks = [
            ParsedChunk((page.extract_text() or "").strip(), index, None, "native")
            for index, page in enumerate(reader.pages, start=1)
        ]
        non_empty = [chunk for chunk in chunks if chunk.text]
        if non_empty:
            return non_empty
        return (ocr or TesseractPdfOcrFallback()).extract(content)

    if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        document = DocxDocument(BytesIO(content))
        chunks = [
            ParsedChunk(paragraph.text.strip(), None, f"paragraph-{index}", "native")
            for index, paragraph in enumerate(document.paragraphs, start=1)
            if paragraph.text.strip()
        ]
        if chunks:
            return chunks
        raise ParseError("The DOCX document contains no readable paragraphs.")

    if content_type == "text/plain":
        decoded = content.decode("utf-8-sig", errors="replace")
        chunks = [
            ParsedChunk(section.strip(), None, f"section-{index}", "native")
            for index, section in enumerate(decoded.split("\n\n"), start=1)
            if section.strip()
        ]
        if chunks:
            return chunks
        raise ParseError("The text document is empty.")

    raise ParseError(f"Unsupported content type: {content_type}")
