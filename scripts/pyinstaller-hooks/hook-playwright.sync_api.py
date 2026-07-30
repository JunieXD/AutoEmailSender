"""Prevent PyInstaller's default sync hook from recollecting Playwright's Node executable."""

datas = []
binaries = []
hiddenimports = []
