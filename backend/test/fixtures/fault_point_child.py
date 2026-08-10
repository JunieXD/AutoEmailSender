from __future__ import annotations

import asyncio
import sys

from app.core.fault_injection import wait_at_fault_point


async def main() -> None:
    await wait_at_fault_point(sys.argv[1])
    print("fault point released", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
