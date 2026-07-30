# Portions adapted from Microsoft MarkItDown 0.1.5 (MIT).
# See MARKITDOWN_NOTICE.txt in this directory.
from __future__ import annotations

import zipfile
from pathlib import Path

import mammoth
from bs4 import BeautifulSoup

from .custom_markdownify import _CustomMarkdownify
from .docx_pre_process import pre_process_docx
from .pdf_converter import PdfConverter, StreamInfo


PDF_HEADER_SCAN_BYTES = 1024
DOCX_CONTENT_TYPES = "[Content_Types].xml"
DOCX_MAIN_DOCUMENT = "word/document.xml"


def detect_document_type(path: Path) -> str | None:
    with path.open("rb") as stream:
        header = stream.read(PDF_HEADER_SCAN_BYTES)
    if b"%PDF-" in header:
        return "pdf"
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    if DOCX_CONTENT_TYPES in names and DOCX_MAIN_DOCUMENT in names:
        return "docx"
    return None


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html.encode("utf-8"), "html.parser", from_encoding="utf-8")
    for element in soup(["script", "style"]):
        element.extract()
    content = soup.find("body") or soup
    return _CustomMarkdownify().convert_soup(content).strip()


def _extract_docx(path: Path) -> str:
    with path.open("rb") as stream:
        preprocessed = pre_process_docx(stream)
    html = mammoth.convert_to_html(preprocessed).value
    return _html_to_markdown(html)


def _extract_pdf(path: Path) -> str:
    with path.open("rb") as stream:
        result = PdfConverter().convert(
            stream,
            StreamInfo(mimetype="application/pdf", extension=".pdf"),
        )
    return result.markdown


def extract_document(path: Path) -> str:
    detected_type = detect_document_type(path)
    if detected_type == "pdf":
        return _extract_pdf(path)
    if detected_type == "docx":
        return _extract_docx(path)
    raise ValueError(f"Unsupported or damaged document: {path.name}")
