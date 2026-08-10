from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def _apply_file_size_limit() -> None:
    if os.name == "nt":
        raise RuntimeError("RLIMIT_FSIZE fault fixture is POSIX-only")

    import resource

    limit_bytes = int(os.environ["AUTO_EMAIL_SENDER_TEST_FILE_SIZE_LIMIT_BYTES"])
    if limit_bytes <= 0:
        raise RuntimeError("file size limit must be positive")
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit_bytes, limit_bytes))


_apply_file_size_limit()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop_entry import main  # noqa: E402  (limit must precede app imports)


if __name__ == "__main__":
    main()
