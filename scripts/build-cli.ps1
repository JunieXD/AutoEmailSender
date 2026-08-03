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
    (Join-Path $CliDir "src\auto_email_sender_cli\__main__.py")

  $CliExecutable = Join-Path $CliDir "dist\auto-email-sender.exe"
  & $CliExecutable --format json version
  & $CliExecutable --format json guide --topic overview
} finally {
  Pop-Location
}
