$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "build\build-backend.ps1") @args
