param(
  [Parameter(Mandatory = $true)][string]$RunnerArgumentsPath,
  [Parameter(Mandatory = $true)][string]$StatusPath,
  [Parameter(Mandatory = $true)][string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$exitCode = 1
$failure = ""
try {
  $arguments = @(
    [System.IO.File]::ReadAllLines(
      $RunnerArgumentsPath,
      [System.Text.UTF8Encoding]::new($false, $true)
    )
  )
  if ($arguments.Count -eq 0) {
    throw "Windows QA wrapper received no runner arguments."
  }
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    # Native stderr from the nested Windows runner is diagnostic output.  It
    # must be preserved without turning a successful native process into a
    # terminating wrapper error under the wrapper's fail-fast policy.
    $ErrorActionPreference = "Continue"
    & powershell.exe @arguments 2>&1 | Tee-Object -FilePath $OutputPath
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
} catch {
  $failure = $_.Exception.Message
  $exitCode = 1
} finally {
  $status = [ordered]@{
    schema_version = 1
    exit_code = $exitCode
    failure = $failure
    completed_at = [datetime]::UtcNow.ToString("o")
  }
  $temporaryStatusPath = "$StatusPath.tmp-$PID"
  [System.IO.File]::WriteAllText(
    $temporaryStatusPath,
    ($status | ConvertTo-Json -Compress),
    [System.Text.UTF8Encoding]::new($false)
  )
  Move-Item -LiteralPath $temporaryStatusPath -Destination $StatusPath -Force
}

exit $exitCode
