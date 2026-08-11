!include LogicLib.nsh
!include getProcessInfo.nsh

Var pid

!macro RemovePackagedBrowserRuntime
  ${If} ${FileExists} "$INSTDIR\resources\ms-playwright\*.*"
    InitPluginsDir
    File /oname=$PLUGINSDIR\windows-remove-packaged-browser-runtime.ps1 "${BUILD_RESOURCES_DIR}\windows-remove-packaged-browser-runtime.ps1"
    DetailPrint "正在安全清理内置浏览器运行文件…"
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\windows-remove-packaged-browser-runtime.ps1" -InstallRoot "$INSTDIR"'
    Pop $R0
    ${If} $R0 != "0"
      DetailPrint "内置浏览器运行文件清理失败（退出码 $R0）。"
      ${IfNot} ${Silent}
        MessageBox MB_ICONSTOP "无法安全清理旧版内置浏览器文件，操作已停止。请关闭正在使用 Auto Email Sender 文件的程序后重试。"
      ${EndIf}
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}
!macroend

!macro customCheckAppRunning
  !insertmacro IS_POWERSHELL_AVAILABLE
  !insertmacro _CHECK_APP_RUNNING
  !insertmacro RemovePackagedBrowserRuntime
!macroend

!macro customInstall
  ${IfNot} ${FileExists} "$INSTDIR\resources\runtime\vc_redist.x64.exe"
    MessageBox MB_ICONSTOP "缺少 Microsoft Visual C++ 运行库，无法完成安装。请重新下载安装包。"
    Abort
  ${EndIf}

  DetailPrint "正在安装 Microsoft Visual C++ x64 运行库…"
  nsExec::ExecToLog '"$INSTDIR\resources\runtime\vc_redist.x64.exe" /install /quiet /norestart'
  Pop $R0
  ${If} $R0 == "0"
    DetailPrint "Microsoft Visual C++ 运行库安装完成。"
  ${ElseIf} $R0 == "1638"
    DetailPrint "系统中已有兼容的 Microsoft Visual C++ 运行库。"
  ${ElseIf} $R0 == "3010"
    DetailPrint "Microsoft Visual C++ 运行库安装完成；系统稍后可能需要重启。"
  ${Else}
    MessageBox MB_ICONSTOP "Microsoft Visual C++ 运行库安装失败（退出码 $R0）。Auto Email Sender 尚未完成安装。"
    Abort
  ${EndIf}
!macroend

!ifdef BUILD_UNINSTALLER
Var /GLOBAL UninstallShouldDeleteAppData
!endif

!macro customUnInit
  StrCpy $UninstallShouldDeleteAppData "0"
  ${GetParameters} $R0
  ${GetOptions} $R0 "--delete-app-data" $R1
  ${Unless} ${Errors}
    StrCpy $UninstallShouldDeleteAppData "1"
  ${EndUnless}
!macroend

!macro customUnInstall
  SetShellVarContext current
  !insertmacro RemovePackagedBrowserRuntime
  ${If} ${FileExists} "$INSTDIR\resources\agent-support\windows-uninstall.ps1"
    DetailPrint "正在安全移除命令行与 Agent 支持…"
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-support\windows-uninstall.ps1"'
    Pop $R0
    ${If} $R0 != "0"
      DetailPrint "命令行与 Agent 支持清理未完全执行（退出码 $R0）；为避免误删，剩余文件已保留。"
    ${EndIf}
  ${EndIf}
!macroend

!macro customUnInstallSection
Section /o "un.删除本地数据（数据库、材料、缓存和本地配置）"
  Call un.ConfirmAndDeleteAutoEmailSenderAppData
SectionEnd

Section "un.-DeleteAutoEmailSenderAppDataFromFlag"
  Call un.DeleteAutoEmailSenderAppDataFromFlag
SectionEnd
!macroend

!ifdef BUILD_UNINSTALLER
Function un.ConfirmAndDeleteAutoEmailSenderAppData
  ${IfNot} ${Silent}
    MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "这将永久删除 Auto Email Sender 的本地数据，包括数据库、上传材料、缓存和本地配置。删除后无法通过重新安装恢复。是否继续？" IDYES delete_data IDNO skip_delete
  ${EndIf}

  delete_data:
    Call un.DeleteAutoEmailSenderAppData
    Return

  skip_delete:
    Return
FunctionEnd

Function un.DeleteAutoEmailSenderAppDataFromFlag
  ${If} $UninstallShouldDeleteAppData == "1"
    Call un.DeleteAutoEmailSenderAppData
  ${EndIf}
FunctionEnd

Function un.DeleteAutoEmailSenderAppData
  SetShellVarContext current
  StrCpy $R0 "$APPDATA\auto-email-sender-desktop"

  ${If} $R0 == ""
    Return
  ${EndIf}

  ${If} $R0 == "$APPDATA"
    Return
  ${EndIf}

  ${If} ${FileExists} "$R0\*.*"
    RMDir /r "$R0"
  ${EndIf}
FunctionEnd
!endif
