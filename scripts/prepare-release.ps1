$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "release\prepare-release.ps1") @args
