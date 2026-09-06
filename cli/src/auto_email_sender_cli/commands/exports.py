from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.output import CliContext


def export_collection_if_requested(
    data: Any,
    context: CliContext,
) -> tuple[Any, str | None]:
    destination_value = context.output_file
    if not destination_value:
        return data, None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise CliError(
            code="OUTPUT_FILE_REQUIRES_COLLECTION",
            message="--output-file 只能用于返回 items 集合的读取命令。",
            exit_code=2,
        )
    destination = Path(destination_value).expanduser().resolve()
    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp",
        )
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="\n") as file:
            for item in data["items"]:
                file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                file.write("\n")
        _publish_export_temporary(temporary, destination, force=context.force_output)
    except FileExistsError as exc:
        raise CliError(
            code="OUTPUT_EXISTS",
            message=f"输出文件已存在：{destination}；如确实要覆盖请加 --force-output。",
            exit_code=2,
        ) from exc
    except OSError as exc:
        raise CliError(
            code="OUTPUT_WRITE_FAILED",
            message=f"无法写入输出文件：{destination}。",
            exit_code=8,
            details={"reason": type(exc).__name__},
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    summary = {
        "output_file": destination.as_posix(),
        "item_count": len(data["items"]),
        "next_cursor": data.get("next_cursor"),
        "has_more": bool(data.get("has_more")),
        "selected_fields": data.get("selected_fields"),
    }
    return summary, destination.as_posix()


def _publish_export_temporary(
    temporary: Path,
    destination: Path,
    *,
    force: bool,
) -> None:
    """Publish a mode-0600 export atomically, with a cross-platform fallback.

    ``os.link`` is the simplest no-overwrite primitive on POSIX, but it is
    unavailable on some Windows, network, and sync-backed filesystems.  The
    fallback uses each supported platform's atomic no-replace rename instead
    of exposing an empty reservation or opening a check-then-replace race.
    """

    if force:
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return
    try:
        os.link(temporary, destination)
    except (AttributeError, NotImplementedError, OSError):
        _rename_export_noreplace(temporary, destination)
    else:
        temporary.unlink()
    os.chmod(destination, 0o600)


def _rename_export_noreplace(temporary: Path, destination: Path) -> None:
    """Atomically rename without overwriting an existing destination."""

    if os.name == "nt":
        # Windows' os.rename fails with FileExistsError when dst exists.
        os.rename(temporary, destination)
        return

    source_bytes = os.fsencode(temporary)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renamex_np", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            -100,  # AT_FDCWD
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,  # RENAME_NOREPLACE
        )
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")

    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), destination)


def write_export_bytes(destination: Path, content: bytes, *, force: bool) -> Path:
    """Write a non-collection export with the same secure publish contract."""

    resolved = destination.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(
        f".{resolved.name}.{secrets.token_hex(8)}.tmp",
    )
    try:
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(temporary_fd, "wb") as output:
            output.write(content)
        _publish_export_temporary(temporary, resolved, force=force)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return resolved
