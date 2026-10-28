; Macros expand inside the generated installer script, so capture this file's directory here.
!define HOOKS_DIR "${__FILEDIR__}"

; Updates uninstall with /UPDATE and reinstall in place, so they keep the login item and the CLI.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    ; tauri-plugin-autostart names the login item after the bundle identifier.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${BUNDLEID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${BUNDLEID}"

    ; The settings page's own script removes only the PATH entry it recorded. Like the settings
    ; page it runs as command text, which script execution policies do not block.
    InitPluginsDir
    File "/oname=$PLUGINSDIR\cli-path.ps1" "${HOOKS_DIR}\..\..\api\src\agent_settings\cli\windows.ps1"
    System::Call 'kernel32::SetEnvironmentVariable(t "AUTOEMAIL_CLI_SCRIPT", t "$PLUGINSDIR\cli-path.ps1")'
    System::Call 'kernel32::SetEnvironmentVariable(t "AUTOEMAIL_CLI_ACTION", t "uninstall")'
    System::Call 'kernel32::SetEnvironmentVariable(t "AUTOEMAIL_CLI_DIRECTORY", t "$INSTDIR")'
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -Command "& ([ScriptBlock]::Create([IO.File]::ReadAllText($$env:AUTOEMAIL_CLI_SCRIPT)))"'
    Pop $0
    ${If} $0 != 0
      MessageBox MB_ICONEXCLAMATION "没能把命令行工具从 PATH 里移除。请在开始菜单搜索“编辑账户的环境变量”，从 Path 里删掉 $INSTDIR。" /SD IDOK
    ${EndIf}
  ${EndIf}
!macroend
