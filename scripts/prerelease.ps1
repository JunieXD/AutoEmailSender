$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "release\prerelease.ps1") @args
