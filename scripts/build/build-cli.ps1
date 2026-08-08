param(
  [switch]$Clean,
  [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$CliDir = Join-Path $RepoRoot "cli"

Push-Location $CliDir
try {
  if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
      (Join-Path $CliDir "build"), `
      (Join-Path $CliDir "dist")
  }

  if (-not $SkipSync) {
    uv sync --dev
  }
  $BuildIdentityHook = Join-Path $CliDir "build\generated\cli_build_identity_hook.py"
  uv run python (Join-Path $RepoRoot "scripts\build\generate_cli_build_identity.py") `
    --repo-root $RepoRoot `
    --output $BuildIdentityHook
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

  $CliExecutable = Join-Path $CliDir "dist\auto-email-sender\auto-email-sender.exe"
  uv run python (Join-Path $RepoRoot "scripts\build\verify_cli_binary.py") `
    --executable $CliExecutable
} finally {
  Pop-Location
}
