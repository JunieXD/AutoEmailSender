$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "release\prepare-prerelease.ps1") @args
