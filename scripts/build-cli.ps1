$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "build\build-cli.ps1") @args
