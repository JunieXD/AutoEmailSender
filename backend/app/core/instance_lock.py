from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


class BackendInstanceAlreadyRunningError(RuntimeError):
    pass


class BackendWorkerAlreadyRunningError(RuntimeError):
    pass


class DatabaseMigrationAlreadyRunningError(RuntimeError):
    pass


class _ExclusiveDataDirectoryLock:
    def __init__(
        self,
        data_dir: Path,
        *,
        file_name: str,
        error_type: type[RuntimeError],
        conflict_message: str,
    ) -> None:
        self.path = data_dir / file_name
        self._error_type = error_type
        self._conflict_message = conflict_message
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        try:
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            _lock_file(lock_file)
        except OSError as exc:
            lock_file.close()
            raise self._error_type(self._conflict_message) from exc
        self._file = lock_file

    def release(self) -> None:
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        try:
            lock_file.seek(0)
            _unlock_file(lock_file)
        finally:
            lock_file.close()

    def __enter__(self) -> _ExclusiveDataDirectoryLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class BackendInstanceLock(_ExclusiveDataDirectoryLock):
    """Hold the runtime-group leader lock for one data directory."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(
            data_dir,
            file_name="backend.instance.lock",
            error_type=BackendInstanceAlreadyRunningError,
            conflict_message=(
                "检测到另一个 Auto Email Sender 后端仍在使用当前数据目录。"
                "请先完全退出旧实例，再重新启动软件。"
            ),
        )


class BackendWorkerLock(_ExclusiveDataDirectoryLock):
    """Hold the single Worker-role lock for one data directory."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(
            data_dir,
            file_name="backend.worker.lock",
            error_type=BackendWorkerAlreadyRunningError,
            conflict_message=(
                "检测到另一个 Auto Email Sender Worker 仍在使用当前数据目录。"
                "请先等待旧 Worker 完全退出，再重新启动。"
            ),
        )


class DatabaseMigrationLock(_ExclusiveDataDirectoryLock):
    """Prevent accidental concurrent Alembic execution across processes."""

    def __init__(self, data_dir: Path) -> None:
        super().__init__(
            data_dir,
            file_name="database.migration.lock",
            error_type=DatabaseMigrationAlreadyRunningError,
            conflict_message="另一个进程正在升级 Auto Email Sender 数据库。",
        )


def _lock_file(lock_file: BinaryIO) -> None:
    if _is_windows():
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file: BinaryIO) -> None:
    if _is_windows():
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"


__all__ = [
    "BackendInstanceAlreadyRunningError",
    "BackendInstanceLock",
    "BackendWorkerAlreadyRunningError",
    "BackendWorkerLock",
    "DatabaseMigrationAlreadyRunningError",
    "DatabaseMigrationLock",
]
