$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "release\release.ps1") @args
