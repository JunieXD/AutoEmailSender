; Updates uninstall with /UPDATE and reinstall in place, so they keep the login item and the CLI.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    ; tauri-plugin-autostart names the login item after the bundle identifier.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${BUNDLEID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${BUNDLEID}"

    ; The settings page's own script removes only the PATH entry it recorded.
    InitPluginsDir
    File "/oname=$PLUGINSDIR\cli-path.ps1" "${__FILEDIR__}\..\..\api\src\agent_settings\cli\windows.ps1"
    System::Call 'kernel32::SetEnvironmentVariable(t "AUTOEMAIL_CLI_ACTION", t "uninstall")'
    System::Call 'kernel32::SetEnvironmentVariable(t "AUTOEMAIL_CLI_DIRECTORY", t "$INSTDIR")'
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\cli-path.ps1"'
    Pop $0
  ${EndIf}
!macroend
