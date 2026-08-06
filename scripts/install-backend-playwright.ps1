$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "build\install-backend-playwright.ps1") @args
