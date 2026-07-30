"""Prevent PyInstaller's default async hook from recollecting Playwright's Node executable."""

datas = []
binaries = []
hiddenimports = []
