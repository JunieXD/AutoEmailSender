from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from app.services.document_extraction import detect_document_type, extract_document
from app.services.file_storage import extract_text_from_document


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "document_extraction"


def _normalized_digest(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class DocumentExtractionGoldenTests(unittest.TestCase):
    EXPECTED_DOCUMENTS = {
        "blank.docx": ("docx", "e3b0c44298fc1c14"),
        "blank.pdf": ("pdf", "e3b0c44298fc1c14"),
        "borderless_form_resume.pdf": ("pdf", "abc27411a7ccebca"),
        "docx_named_pdf.pdf": ("docx", "3cbc4b5a2a519db7"),
        "encrypted_empty_password.pdf": ("pdf", "3574496c705ab66a"),
        "equation_resume.docx": ("docx", "a544b3425fe1ff8b"),
        "image_only_resume.pdf": ("pdf", "e3b0c44298fc1c14"),
        "leading_bytes.docx": ("docx", "3cbc4b5a2a519db7"),
        "leading_bytes.pdf": ("pdf", "3574496c705ab66a"),
        "merged_table_resume.docx": ("docx", "3ed9abaeac9761b3"),
        "mixed_text_image_resume.pdf": ("pdf", "4e7e310b88e86118"),
        "multicolumn_resume.pdf": ("pdf", "b9d7523987459206"),
        "pdf_named_docx.docx": ("pdf", "3574496c705ab66a"),
        "plain_resume.pdf": ("pdf", "3574496c705ab66a"),
        "rich_resume.docx": ("docx", "3cbc4b5a2a519db7"),
        "rotated_resume.pdf": ("pdf", "d13800d55d039ee1"),
        "rotated_source.pdf": ("pdf", "e9bfc9330cdde0ec"),
        "tabular_resume.pdf": ("pdf", "54ecad868bd1d95e"),
        "taoci.docx": ("docx", "97b3e0297858259a"),
        "uppercase.DOCX": ("docx", "3cbc4b5a2a519db7"),
        "uppercase.PDF": ("pdf", "3574496c705ab66a"),
    }
    REJECTED_DOCUMENTS = {
        "encrypted_secret_password.pdf": "pdf",
        "fake.pdf": None,
        "ordinary_zip.docx": None,
        "truncated.docx": None,
        "truncated.pdf": "pdf",
    }

    def test_matches_markitdown_0_1_5_golden_outputs(self) -> None:
        for file_name, (expected_type, expected_digest) in self.EXPECTED_DOCUMENTS.items():
            with self.subTest(file_name=file_name):
                path = FIXTURE_DIR / file_name

                self.assertEqual(detect_document_type(path), expected_type)
                self.assertEqual(_normalized_digest(extract_document(path)), expected_digest)

    def test_rejects_encrypted_damaged_and_disguised_non_documents(self) -> None:
        for file_name, expected_type in self.REJECTED_DOCUMENTS.items():
            with self.subTest(file_name=file_name):
                path = FIXTURE_DIR / file_name

                self.assertEqual(detect_document_type(path), expected_type)
                with self.assertRaises(Exception):
                    extract_document(path)

    def test_pdf_table_structure_survives_file_storage_integration(self) -> None:
        text = extract_text_from_document((FIXTURE_DIR / "borderless_form_resume.pdf").as_posix())

        self.assertIn("FORM-TABLE-SENTINEL", text or "")
        self.assertIn("| 时间", text or "")
        self.assertIn("| ----", text or "")

    def test_docx_formulas_survive_file_storage_integration(self) -> None:
        text = extract_text_from_document((FIXTURE_DIR / "equation_resume.docx").as_posix())

        self.assertIn("$x+1=2$", text or "")
        self.assertIn("$$E=mc\\^2$$", text or "")

    def test_content_detection_handles_exchanged_extensions(self) -> None:
        docx_as_pdf = FIXTURE_DIR / "docx_named_pdf.pdf"
        pdf_as_docx = FIXTURE_DIR / "pdf_named_docx.docx"

        self.assertEqual(detect_document_type(docx_as_pdf), "docx")
        self.assertEqual(detect_document_type(pdf_as_docx), "pdf")
        self.assertIn("RICH-DOCX-SENTINEL", extract_document(docx_as_pdf))
        self.assertIn("研究生申请简历", extract_document(pdf_as_docx))


if __name__ == "__main__":
    unittest.main()
