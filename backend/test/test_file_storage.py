from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.file_storage import delete_file, extract_text_from_document


class FileStorageExtractionTests(unittest.TestCase):
    def test_delete_file_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "material.txt"
            path.write_text("material", encoding="utf-8")

            self.assertTrue(delete_file(path.as_posix()))
            self.assertTrue(delete_file(path.as_posix()))
            self.assertFalse(path.exists())

    def test_delete_file_reports_cleanup_debt_without_raising(self) -> None:
        from app.services import file_storage

        with (
            patch.object(file_storage, "Path") as path_factory,
            patch.object(file_storage.logger, "exception") as log_exception,
        ):
            path_factory.return_value.unlink.side_effect = OSError("file is busy")
            path_factory.return_value.as_posix.return_value = (
                "material-cleanup-test.txt"
            )
            deleted = file_storage.delete_file("material-cleanup-test.txt")

        self.assertFalse(deleted)
        path_factory.return_value.unlink.assert_called_once_with(missing_ok=True)
        log_exception.assert_called_once_with(
            "材料记录已删除，但文件清理失败: %s",
            "material-cleanup-test.txt",
        )

    def test_import_keeps_heavy_document_dependencies_lazy(self) -> None:
        script = """
import sys
import app.services.file_storage

heavy_roots = {"lxml", "mammoth", "pdfminer", "pdfplumber"}
loaded = sorted(name for name in sys.modules if name.split(".", 1)[0] in heavy_roots)
if loaded:
    raise SystemExit(f"heavy document modules loaded eagerly: {loaded}")
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_extract_docx_text_falls_back_when_structured_converter_fails(self) -> None:
        from docx import Document

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.docx"
            document = Document()
            document.add_paragraph("DOCX fallback sentinel information extraction")
            document.save(path)

            with patch(
                "app.services.file_storage._extract_text_with_structured_converter",
                return_value=None,
            ):
                text = extract_text_from_document(path.as_posix())

        self.assertIn("DOCX fallback sentinel", text or "")

    def test_extract_pdf_text_falls_back_when_structured_converter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.pdf"
            path.write_bytes(
                _build_minimal_pdf("PDF fallback sentinel information extraction")
            )

            with patch(
                "app.services.file_storage._extract_text_with_structured_converter",
                return_value=None,
            ):
                text = extract_text_from_document(path.as_posix())

        self.assertIn("PDF fallback sentinel", text or "")


def _build_minimal_pdf(text: str) -> bytes:
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped_text}) Tj ET".encode("ascii")
    objects.append(
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream"
    )

    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii",
        ),
    )
    return bytes(content)


if __name__ == "__main__":
    unittest.main()
