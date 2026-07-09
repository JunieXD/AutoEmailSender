from __future__ import annotations


def safe_exception_message(exc: BaseException) -> str:
    try:
        message = str(exc)
    except Exception as format_exc:
        return (
            f"{type(exc).__name__} raised while formatting exception: "
            f"{type(format_exc).__name__}"
        )
    return message
