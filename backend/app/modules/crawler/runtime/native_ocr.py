from __future__ import annotations

import asyncio
import os
from pathlib import Path
import platform
import struct
import sys
import tempfile

from ..pages.tools import CrawlToolContext, PageSnapshot, fetch_binary_resource
from .profile_fallbacks import (
    EmailEvidence,
    extract_email_evidence,
    resolve_profile_image_urls,
)


MAX_OCR_IMAGES_PER_PAGE = 6
MAX_OCR_OUTPUT_BYTES = 64 * 1024
OCR_PROCESS_TIMEOUT_SECONDS = 12


async def extract_ocr_email_evidence(
    ctx: CrawlToolContext,
    snapshot: PageSnapshot,
) -> tuple[EmailEvidence, ...]:
    evidence: list[EmailEvidence] = []
    attempted_images = 0
    for image_url, context in resolve_profile_image_urls(snapshot):
        try:
            final_url, content_type, image_bytes = await fetch_binary_resource(
                ctx, image_url
            )
        except Exception:
            continue
        if not _looks_like_raster_image(content_type, image_bytes):
            continue
        dimensions = image_dimensions(image_bytes)
        if dimensions is not None and not _is_small_horizontal_image(*dimensions):
            continue
        attempted_images += 1
        try:
            recognized_text = await recognize_image_text(
                image_bytes,
                content_type=content_type,
            )
        except Exception:
            recognized_text = ""
        if recognized_text:
            recognized_evidence = extract_email_evidence(
                recognized_text,
                source_url=final_url,
                source_kind="ocr_image",
            )
            for item in recognized_evidence:
                image_context = " ".join(
                    part for part in (context, item.context) if part
                )
                evidence.append(
                    EmailEvidence(
                        email=item.email,
                        context=image_context[:500],
                        source_url=item.source_url,
                        source_kind=item.source_kind,
                    )
                )
        if attempted_images >= MAX_OCR_IMAGES_PER_PAGE:
            break
    return _deduplicate_email_evidence(evidence)


async def recognize_image_text(image_bytes: bytes, *, content_type: str) -> str:
    command = _native_ocr_command()
    if command is None or not image_bytes:
        return ""
    suffix = _image_suffix(content_type)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_file.write(image_bytes)
            temporary_path = temporary_file.name
        process = await asyncio.create_subprocess_exec(
            *command,
            temporary_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=OCR_PROCESS_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return ""
        if process.returncode != 0 or len(stdout) > MAX_OCR_OUTPUT_BYTES:
            return ""
        return stdout.decode("utf-8", errors="replace").strip()
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        return struct.unpack(">II", image_bytes[16:24])
    if image_bytes[:6] in {b"GIF87a", b"GIF89a"} and len(image_bytes) >= 10:
        return struct.unpack("<HH", image_bytes[6:10])
    if image_bytes.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(image_bytes)
    return None


def _jpeg_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(image_bytes):
        if image_bytes[index] != 0xFF:
            index += 1
            continue
        marker = image_bytes[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(image_bytes):
            return None
        segment_length = int.from_bytes(image_bytes[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(image_bytes):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(image_bytes[index + 3 : index + 5], "big")
            width = int.from_bytes(image_bytes[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None


def _is_small_horizontal_image(width: int, height: int) -> bool:
    return 32 <= width <= 1600 and 8 <= height <= 240 and width >= height * 1.4


def _looks_like_raster_image(content_type: str, image_bytes: bytes) -> bool:
    if content_type.startswith("image/") and content_type != "image/svg+xml":
        return True
    return image_dimensions(image_bytes) is not None


def _native_ocr_command() -> tuple[str, ...] | None:
    override = os.getenv("AUTO_EMAIL_SENDER_NATIVE_OCR_HELPER", "").strip()
    if override:
        return (override,)

    system = platform.system()
    if system == "Darwin":
        helper = _packaged_resource_path("native/ocr/email-ocr")
        if helper is None:
            helper = _backend_root() / "build" / "native-ocr" / "email-ocr"
        return (str(helper),) if helper.is_file() else None
    if system == "Windows":
        script = _packaged_resource_path("native/ocr/windows-media-ocr.ps1")
        if script is None:
            script = (
                _backend_root() / "native" / "ocr" / "windows" / "windows-media-ocr.ps1"
            )
        if not script.is_file():
            return None
        return (
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        )
    return None


def _packaged_resource_path(relative_path: str) -> Path | None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root) / relative_path if bundle_root else None


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _image_suffix(content_type: str) -> str:
    return {
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".img")


def _deduplicate_email_evidence(
    values: list[EmailEvidence],
) -> tuple[EmailEvidence, ...]:
    deduplicated: list[EmailEvidence] = []
    seen: set[str] = set()
    for value in values:
        if value.email in seen:
            continue
        seen.add(value.email)
        deduplicated.append(value)
    return tuple(deduplicated)
