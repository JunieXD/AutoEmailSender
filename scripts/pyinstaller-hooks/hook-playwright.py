"""Collect Playwright runtime files without bundling its independent Node executable."""

from PyInstaller.utils.hooks import collect_all


datas, binaries, hiddenimports = collect_all(
    "playwright",
    include_py_files=False,
    exclude_datas=["driver/node", "driver/node.exe"],
    on_error="raise",
)
