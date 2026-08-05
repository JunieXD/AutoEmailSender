param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$CliDir = Join-Path $RepoRoot "cli"

Push-Location $CliDir
try {
  if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
      (Join-Path $CliDir "build"), `
      (Join-Path $CliDir "dist")
  }

  uv sync --dev
  $BuildIdentityHook = Join-Path $CliDir "build\generated\cli_build_identity_hook.py"
  uv run python (Join-Path $RepoRoot "scripts\generate_cli_build_identity.py") `
    --repo-root $RepoRoot `
    --output $BuildIdentityHook
  uv run pyinstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name auto-email-sender `
    --distpath (Join-Path $CliDir "dist") `
    --workpath (Join-Path $CliDir "build\work") `
    --specpath (Join-Path $CliDir "build") `
    --paths (Join-Path $CliDir "src") `
    --copy-metadata auto-email-sender-cli `
    --runtime-hook $BuildIdentityHook `
    (Join-Path $CliDir "src\auto_email_sender_cli\__main__.py")

  $CliExecutable = Join-Path $CliDir "dist\auto-email-sender.exe"
  & $CliExecutable --format json version
  & $CliExecutable --format json capabilities
} finally {
  Pop-Location
}
