"""Compatibility exports for migrated professor import/export management."""

from app.modules.professors.management import (
    ALLOWED_TITLES,
    PROFESSOR_EXPORT_COLUMNS,
    PROFESSOR_LEGACY_TEMPLATE_COLUMNS,
    PROFESSOR_TEMPLATE_COLUMNS,
    ParsedProfessorImport,
    build_professor_export,
    build_professor_template,
    is_valid_professor_email,
    normalize_professor_email,
    normalize_professor_payload,
    normalize_professor_title,
    parse_professor_import_file,
)

__all__ = [
    "ALLOWED_TITLES",
    "PROFESSOR_EXPORT_COLUMNS",
    "PROFESSOR_LEGACY_TEMPLATE_COLUMNS",
    "PROFESSOR_TEMPLATE_COLUMNS",
    "ParsedProfessorImport",
    "build_professor_export",
    "build_professor_template",
    "is_valid_professor_email",
    "normalize_professor_email",
    "normalize_professor_payload",
    "normalize_professor_title",
    "parse_professor_import_file",
]
