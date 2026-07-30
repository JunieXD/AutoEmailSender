from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import uvicorn

from app.core.startup_logging import write_startup_phase_log


PACKAGED_RUNTIME_SELF_CHECK_MODULES = (
    "main",
    "aiosqlite",
    "tiktoken",
    "tiktoken_ext.openai_public",
    "socksio",
    "langchain_openai",
    "openai",
    "httpx",
    "tldextract",
    "app.services.document_extraction",
    "defusedxml",
    "lxml.etree",
    "mammoth",
    "pdfminer",
    "pdfplumber",
    "pypdf",
    "playwright.async_api",
    "playwright.sync_api",
    "docx",
    "openpyxl",
)


def parse_desktop_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Auto Email Sender desktop backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--document-self-check", type=Path)
    return parser.parse_args(argv)


def build_uvicorn_options(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = parse_desktop_args(argv)
    return {
        "app": "main:app",
        "host": args.host,
        "port": args.port,
        "reload": False,
    }


def run_packaged_runtime_self_check() -> int:
    for module_name in PACKAGED_RUNTIME_SELF_CHECK_MODULES:
        importlib.import_module(module_name)

    import tiktoken

    tiktoken.get_encoding("o200k_base").encode("packaged runtime self check")
    print("packaged runtime self-check ok")
    return 0


def run_packaged_document_self_check(fixture_dir: Path) -> int:
    from app.services.file_storage import extract_text_from_document

    cases = {
        "borderless_form_resume.pdf": ("FORM-TABLE-SENTINEL", "| 时间", "| ----"),
        "docx_named_pdf.pdf": ("RICH-DOCX-SENTINEL",),
        "equation_resume.docx": ("EQUATION-DOCX-SENTINEL", "$x+1=2$", "$$E=mc\\^2$$"),
        "pdf_named_docx.docx": ("研究生申请简历",),
    }
    for file_name, sentinels in cases.items():
        path = fixture_dir / file_name
        content = extract_text_from_document(path.as_posix()) or ""
        missing = [sentinel for sentinel in sentinels if sentinel not in content]
        if missing:
            raise RuntimeError(f"packaged document self-check failed for {file_name}: {missing}")

    print("packaged document self-check ok")
    return 0


def main() -> None:
    args = parse_desktop_args()
    if args.self_check:
        raise SystemExit(run_packaged_runtime_self_check())
    if args.document_self_check is not None:
        raise SystemExit(run_packaged_document_self_check(args.document_self_check))

    options = build_uvicorn_options()
    app_path = options.pop("app")
    write_startup_phase_log(
        "desktop_entry.start",
        detail=f"host={options['host']} port={options['port']}",
    )
    uvicorn.run(app_path, **options)


if __name__ == "__main__":
    main()
