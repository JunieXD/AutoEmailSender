from __future__ import annotations

SQLITE_LOCK_DIAGNOSTIC = "sqlite_database_locked"
SQLITE_LOCK_DIAGNOSTIC_LINE = (
    f"diagnostic={SQLITE_LOCK_DIAGNOSTIC} database_lock=1"
)
SQLITE_LOCK_USER_MESSAGE = (
    "本地数据库正忙，当前操作在有限重试后仍未完成。"
    "请稍候重试；如果问题持续出现，请确认没有多个软件实例同时运行。"
)


def is_sqlite_database_lock_error(exc: BaseException) -> bool:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        try:
            message = str(current).lower()
        except Exception:
            message = ""
        if "database is locked" in message or "database table is locked" in message:
            return True
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def sqlite_lock_diagnostic_line(exc: BaseException) -> str | None:
    if not is_sqlite_database_lock_error(exc):
        return None
    return SQLITE_LOCK_DIAGNOSTIC_LINE


def sqlite_lock_user_message(exc: BaseException) -> str | None:
    if not is_sqlite_database_lock_error(exc):
        return None
    return SQLITE_LOCK_USER_MESSAGE
