param(
  [Parameter(Mandatory = $true, Position = 0)]
  [string]$Version,
  [Parameter(Mandatory = $true)]
  [string]$CandidateRun,
  [Parameter(Mandatory = $true)]
  [string]$PromotionRun,
  [string]$Repository = "JunieXD/AutoEmailSender",
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
)

& node (Join-Path $PSScriptRoot "verify-release.mjs") `
  $Version `
  --candidate-run $CandidateRun `
  --promotion-run $PromotionRun `
  --repository $Repository `
  --repo-root $RepoRoot
exit $LASTEXITCODE
