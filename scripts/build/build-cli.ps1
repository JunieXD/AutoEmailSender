param(
  [switch]$Clean,
  [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$CliDir = Join-Path $RepoRoot "cli"

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Operation)

  if ($LASTEXITCODE -ne 0) {
    throw "$Operation failed with exit code $LASTEXITCODE"
  }
}

function Remove-CleanBuildDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  try {
    Remove-Item -Recurse -Force -ErrorAction Stop -LiteralPath $Path
  } catch {
    throw "Unable to clean build output '$Path'. Stop processes using this directory and retry. $($_.Exception.Message)"
  }
  if (Test-Path -LiteralPath $Path) {
    throw "Build output still exists after cleanup: $Path"
  }
}

Push-Location $CliDir
try {
  if ($Clean) {
    Remove-CleanBuildDirectory -Path (Join-Path $CliDir "build")
    Remove-CleanBuildDirectory -Path (Join-Path $CliDir "dist")
  }

  if (-not $SkipSync) {
    uv sync --dev
    Assert-NativeSuccess "CLI dependency sync"
  }
  $BuildIdentityHook = Join-Path $CliDir "build\generated\cli_build_identity_hook.py"
  uv run python (Join-Path $RepoRoot "scripts\build\generate_cli_build_identity.py") `
    --repo-root $RepoRoot `
    --output $BuildIdentityHook
  Assert-NativeSuccess "CLI build identity generation"
  uv run pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --console `
    --name auto-email-sender `
    --distpath (Join-Path $CliDir "dist") `
    --workpath (Join-Path $CliDir "build\work") `
    --specpath (Join-Path $CliDir "build") `
    --paths (Join-Path $CliDir "src") `
    --runtime-hook $BuildIdentityHook `
    (Join-Path $CliDir "src\auto_email_sender_cli\__main__.py")
  Assert-NativeSuccess "CLI PyInstaller build"

  $CliExecutable = Join-Path $CliDir "dist\auto-email-sender\auto-email-sender.exe"
  uv run python (Join-Path $RepoRoot "scripts\build\verify_cli_binary.py") `
    --executable $CliExecutable
  Assert-NativeSuccess "frozen CLI verification"
} finally {
  Pop-Location
}
